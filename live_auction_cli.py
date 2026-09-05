#!/usr/bin/env python3
"""Live Auction MVP -- minimal CLI interface.

A thin REPL wrapper around the already-tested auction_engine backend
(auction_engine/live_values.py, market_adjustments.py,
live_recommendations.py, live_roster_paths.py, auction_state*.py). This
file does NOT reimplement or modify any of that logic -- it only calls
it and formats the results for a terminal.

Run: python3 live_auction_cli.py
Type `help` at the prompt for the command list.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from auction_engine.auction_state import AuctionState, TeamState
from auction_engine.auction_state_store import AuctionStateStore
from auction_engine.auction_reducer import IllegalEventError
from auction_engine.live_values import compute_live_sam_values
from auction_engine.live_roster_paths import compute_live_roster_paths
from auction_engine.market_adjustments import MarketAdjustmentState, live_expected_price
from auction_engine.live_recommendations import compute_recommended_bid
from auction_engine.live_target_scoring import compute_target_score
from auction_engine.recommendation_guardrails import compute_governed_dollar_ceiling
from auction_model import exact_roster_solver
from auction_model.roster_optimizer import assign_lineup
from auction_model.confirmed_keeper_pipeline import OFFICIAL_PROTECTED_COUNT
import pandas as pd
from mock_draft.data import load_confirmed_pool_and_teams

LIVE_MVP_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "live_mvp"
SNAPSHOT_DIR = LIVE_MVP_DIR / "cli_snapshots"
DEFAULT_LOG_PATH = LIVE_MVP_DIR / "cli_session.jsonl"
DEFAULT_INITIAL_STATE_PATH = LIVE_MVP_DIR / "cli_initial_state.json"
ERROR_LOG_PATH = LIVE_MVP_DIR / "cli_error.log"
SUNDAY_FINAL_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "sunday_final"

COLLEGE_RIGHTS = {"Fernando Mendoza", "Isaiah Bond"}

COMMANDS = {
    "status": "Show Sam's budget, open slots, position needs, reserve, legal max bid, roster.",
    "sale <player> <team> <price>": "Record a sale through the real event engine.",
    "check <player>": "Show live expected/conservative price, ceiling, recommended stop, and why.",
    "targets": "Show top live-recommended targets by multi-factor decision score (not raw value).",
    "exact <player> [price]": "Run a FRESH HiGHS purchase-vs-pass solve from current state (not fast/approximate).",
    "ladder <player>": "Show exact surplus at a short price ladder around the expected price.",
    "paths": "Show the 5 complete roster paths (spend, points, feasibility).",
    "undo": "Undo the last recorded sale.",
    "save <name>": "Save a named snapshot of the current auction state.",
    "load <name>": "Restore a named snapshot.",
    "search <partial_name>": "Find available players matching a partial name.",
    "last": "Show the last recorded sale.",
    "correct <player> <team> <price>": "Correct a previously recorded sale (reverses old accounting first).",
    "market": "Show league-wide and per-position observed-market spending ratios.",
    "position <QB|RB|WR|TE>": "List remaining players at one position by marginal value.",
    "why <player>": "Full explanation: points, role, value change, prior, adjustments, stop, confidence.",
    "prior": "Show which market prior is currently active (static or evolved).",
    "emergency": "Print the static emergency bid sheet / Sunday plan.",
    "help": "List all commands.",
    "quit / exit": "Exit the tool.",
}


class AuctionCLI:
    """All command handlers as plain methods (return a string) so they
    can be tested directly without going through the REPL loop."""

    def __init__(self, budget_scenario: str = "primary", log_path: Path | None = DEFAULT_LOG_PATH,
                 resume: bool | None = None):
        """resume controls what happens to an existing session log at
        `log_path` on launch:
          - resume=True  -> replay it via AuctionStateStore.recover() and
            keep appending to it (used after a Sam-confirmed "resume").
          - resume=False -> delete it and start fresh (the historical
            default behavior, used after a Sam-confirmed "clean").
          - resume=None  -> read the AUCTION_RESUME_MODE env var
            ("resume" or "clean"); if unset, defaults to "clean" (the
            historical default), so nothing changes for any code path
            that doesn't opt in (tests, practice mode, ad hoc scripts).
        This is the real mechanism start_sunday_live_tool.sh's
        resume/clean prompt drives -- see that script and
        tests/test_startup_recovery.py."""
        if resume is None:
            resume = os.environ.get("AUCTION_RESUME_MODE", "clean") == "resume"
        self.budget_scenario = budget_scenario
        self.players, teams, _ = load_confirmed_pool_and_teams(budget_scenario=budget_scenario)
        self.initial_state = self._build_initial_state(teams)
        self.log_path = log_path
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if resume and self.log_path.exists():
                self.store = AuctionStateStore.recover(self.initial_state, self.log_path)
            else:
                if self.log_path.exists():
                    self.log_path.unlink()  # fresh session log (clean mode, or nothing to resume)
                self.store = AuctionStateStore(self.initial_state, log_path=self.log_path)
        else:
            self.store = AuctionStateStore(self.initial_state, log_path=self.log_path)
        self.market_state = MarketAdjustmentState()
        # exact-solve cache: keyed by (state_sequence_number, player, test_price) -- see
        # cmd_exact / _invalidate_exact_cache. Never shown as current if the state
        # has moved on (Stage 2 STALE_EXACT_RESULT requirement).
        self._exact_cache: dict = {}
        self._exact_cache_sequence: int = self.store.state.sequence_number
        self._static_hard_max = self._load_static_hard_max()

    def _load_static_hard_max(self) -> dict:
        """Loads Phase 3G's per-player 'Safety-adjusted hard maximum'
        (real dollar figures, individually audited for ~21 players) --
        used as the frozen ceiling floor by the V2 recommendation
        guardrail (auction_engine/recommendation_guardrails.py). Most of
        the ~340-player pool is NOT in this sheet; those players fall
        back to the guardrail's conservative live-price multiplier
        instead, never to a raw points value."""
        sheet_path = SUNDAY_FINAL_DIR / "sam_final_auction_sheet.csv"
        if not sheet_path.exists():
            return {}
        try:
            import pandas as pd
            df = pd.read_csv(sheet_path)
            out = {}
            for _, row in df.iterrows():
                val = row.get("Safety-adjusted hard maximum")
                if pd.notna(val):
                    out[row["Player"]] = float(val)
            return out
        except Exception:
            return {}

    def _points_to_dollars_rate(self, player: str, position: str) -> float:
        """V3 Part 7: a transparent, per-player, market-calibrated
        dollars-per-point conversion rate -- reuses the player's OWN
        base_value/projected_points ratio (base_value already comes from
        the real, market-anchored pricing model in
        output_mock_draft_snapshot/veteran_auction_price_sheet.csv, not a
        fabricated scalar) rather than one fixed universal points-to-
        dollars constant applied to every player alike. Falls back to a
        position-average rate across the remaining pool when the
        player's own points/base_value are unavailable or degenerate
        (e.g. already sold, or a zero-projection edge case), and to a
        small fixed floor only if the pool itself has no usable data --
        never silently returns 0."""
        pool = self.store.state.available_pool
        info = pool.get(player)
        if info and info.get("projected_points", 0) > 1 and info.get("base_value", 0) > 0:
            return info["base_value"] / info["projected_points"]
        # Fallback: position-average rate across the remaining live pool.
        same_pos = [v for v in pool.values() if v["position"] == position
                    and v.get("projected_points", 0) > 1 and v.get("base_value", 0) > 0]
        if same_pos:
            return sum(v["base_value"] / v["projected_points"] for v in same_pos) / len(same_pos)
        return 0.20  # documented, conservative last-resort floor (~$1 per 5 points)

    def _governed_ceiling(self, player: str, position: str, marginal_value_points: float, expected_role: str,
                          live_price: float, exact_ceiling: float | None = None, exact_status: str | None = None,
                          exact_is_current: bool = False, precomputed_team_specific_dollar_value: float | None = None):
        """THE fix for the Josh Jacobs anomaly: never pass raw points
        (marginal_value) to compute_recommended_bid as a dollar ceiling.
        Every recommendation call site in this file must route through
        this method (or api_check/api_board, which also call it) instead.

        V3 REPAIR (Part 7): marginal_value_points used to be accepted
        here but never forwarded to compute_governed_dollar_ceiling --
        meaning Sam's own roster-aware marginal value never actually
        governed the recommended stop. Now it is converted to a real,
        transparent team-specific DOLLAR value (via
        _points_to_dollars_rate, a per-player market-calibrated
        conversion, not a fixed universal scalar) and passed through as
        a genuine candidate in the governing min() -- it can only ever
        LOWER the stop, never raise it. `precomputed_team_specific_dollar_value`
        lets a caller that has ALREADY computed a dollar-denominated
        team-specific value (e.g. api_targets, which reuses
        _scored_targets' own governed conversion) skip re-deriving it
        from a stale/mismatched points figure -- passing dollars into
        marginal_value_points directly would otherwise silently
        double-convert."""
        sam = self._sam()
        if precomputed_team_specific_dollar_value is not None:
            team_specific_dollar_value = precomputed_team_specific_dollar_value
        else:
            rate = self._points_to_dollars_rate(player, position)
            team_specific_dollar_value = marginal_value_points * rate
        return compute_governed_dollar_ceiling(
            player=player, position=position, live_expected_price=max(1.0, live_price),
            legal_max_bid=sam.legal_max_bid, static_hard_max=self._static_hard_max.get(player),
            exact_ceiling=exact_ceiling, exact_status=exact_status, exact_is_current=exact_is_current,
            expected_role=expected_role, sam_position_count=sam.position_counts.get(position, 0),
            sam_budget_remaining=sam.budget_remaining, open_slots=sam.open_slots,
            team_specific_dollar_value=team_specific_dollar_value,
        )
        if self.log_path is not None:
            DEFAULT_INITIAL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            DEFAULT_INITIAL_STATE_PATH.write_text(json.dumps(self.initial_state.to_dict()))

    def _build_initial_state(self, teams) -> AuctionState:
        st = AuctionState(auction_id="cli-session", rules_version="v1", model_version="live-mvp-cli-v1", sam_team_id="Sam")
        for team_id, t in teams.items():
            roster = [{"player_id": n, "display_name": n, "position": p, "price": pr, "is_keeper": True, "projected_points": pts}
                      for n, p, pr, pts in t.roster]
            # College-rights holds (Mendoza, Bond for Sam) occupy a real
            # 16-man roster slot per the official commissioner data, but
            # are never added to `roster` itself -- see TeamState's
            # college_rights_count docstring.
            #
            # GATE A CLOSURE (V3 repair): this field also now covers the
            # remaining Brad/Reid gap -- the official commissioner table
            # says each has 7 total protected players, but
            # keepers_2026_confirmed.csv only names 6 for either (no
            # college-rights row for them). The 7th player's identity is
            # UNKNOWN (commissioner has not supplied per-team protected
            # NAMES, only counts -- see
            # outputs/auction_rebuild/official_repair_v1/protected_eligibility_audit.md).
            # Rather than leave every team-level slot/reconciliation
            # total silently short by 1 for those two teams, the correct
            # SLOT COUNT is reflected here (computed from
            # OFFICIAL_PROTECTED_COUNT minus named veteran keepers, the
            # SAME mechanism Sam's college-rights count already uses) --
            # this affects only open_slots/legal_max_bid math, never adds
            # a fake named player to the visible roster or auction pool.
            official_total = OFFICIAL_PROTECTED_COUNT.get(team_id)
            n_named_keepers = len(t.roster)
            additional_protected = max(0, official_total - n_named_keepers) if official_total is not None else (
                len(COLLEGE_RIGHTS) if team_id == "Sam" else 0)
            st.teams[team_id] = TeamState(team_id=team_id, budget_remaining=t.budget_remaining, roster=roster,
                                           keeper_ids={n for n, p, pr, pts in t.roster},
                                           college_rights_count=additional_protected)
        st.available_pool = {name: {"display_name": name, "position": p.position, "projected_points": p.projected_points,
                                     "base_value": p.base_value} for name, p in self.players.items()}
        st.college_rights_excluded = set(COLLEGE_RIGHTS)
        return st

    # ---- helpers ----

    def _sam(self):
        return self.store.state.teams["Sam"]

    def _remaining_pool(self) -> dict:
        return dict(self.store.state.available_pool)

    def _avg_marginal_value(self, position: str) -> float | None:
        pool = {n: v for n, v in self._remaining_pool().items() if v["position"] == position}
        if not pool:
            return None
        rows = compute_live_sam_values(self._sam().roster, pool)
        if not rows:
            return None
        return sum(r.marginal_value for r in rows) / len(rows)

    # ---- commands ----

    def cmd_status(self) -> str:
        sam = self._sam()
        needs = sam.legal_starting_needs()
        counts = sam.position_counts
        lines = [
            f"Sam -- budget remaining: ${sam.budget_remaining:.2f}",
            f"Open roster slots: {sam.open_slots}  |  Min reserve: ${sam.min_reserve:.2f}  |  Legal max bid: ${sam.legal_max_bid:.2f}",
            f"Position needs (starters still open): {needs}",
            f"Current roster counts: {counts}",
            "Roster:",
        ]
        for p in sam.roster:
            lines.append(f"  {p['position']:3s} {p['display_name']:22s} ${p['price']:.0f}"
                         f"{'  (keeper)' if p.get('is_keeper') else ''}")
        return "\n".join(lines)

    def cmd_sale(self, player: str, team: str, price: str, confirmed: bool = False) -> str:
        # Check keeper/college-rights status BEFORE the "unknown player"
        # check -- keepers and college-rights players are deliberately
        # excluded from self.players / the available pool, so an
        # unknown-player check that runs first would misreport a refused
        # sale as "unknown" instead of "refused." (Bug found and fixed
        # while wiring this CLI up -- see final_report.md.)
        if player in COLLEGE_RIGHTS:
            return f"REFUSED: {player} is a college-rights asset and cannot enter the veteran auction."
        if any(player in t.keeper_ids for t in self.store.state.teams.values()):
            return f"REFUSED: {player} is a keeper and cannot be sold in the veteran auction."
        if player not in self.players and player not in self._remaining_pool():
            return f"ERROR: unknown player {player!r} -- check spelling (case-sensitive, matches the projections file)."
        try:
            price_f = float(price)
        except ValueError:
            return f"ERROR: price must be a number, got {price!r}."

        # GATE B (V3 repair, Part 5): the documented auction transaction
        # rules -- whole-dollar integer price, minimum $1, no $0/decimal/
        # negative sales -- were never actually enforced anywhere in the
        # real sale path (the reducer only rejected a strictly negative
        # price). Enforced here, at the single real entry point every
        # sale-recording caller (CLI `sale` command, website /api/sale)
        # funnels through, so both interfaces get it identically.
        if price_f != int(price_f):
            return f"ERROR: price must be a whole dollar amount, got {price_f} -- decimal prices are not legal in this auction."
        if price_f < 1:
            return f"ERROR: minimum sale price is $1, got ${price_f:.0f} -- $0 and negative sales are not legal."

        if not confirmed:
            reason = self._needs_large_sale_confirmation(player, team, price_f)
            if reason:
                return (f"CONFIRM: {player} to {team} for ${price_f:.0f} -- {reason}. "
                       f"Re-run the same command with 'confirm' appended to proceed, "
                       f"e.g. `sale {player.replace(' ', '_')} {team} {price} confirm`.")

        before_avg = None
        pos = self.players[player].position if player in self.players else self.store.state.available_pool.get(player, {}).get("position")
        if pos:
            before_avg = self._avg_marginal_value(pos)

        pts = self.players[player].projected_points if player in self.players else 0.0
        try:
            self.store.record("PLAYER_SOLD", {
                "player_id": player, "display_name": player, "position": pos, "winning_owner": team,
                "sale_price": price_f, "nominating_owner": None, "projected_points": pts,
            })
        except IllegalEventError as e:
            return f"REFUSED: {e}"

        self._invalidate_exact_cache()

        # feed the market-adjustment signal with this real observation
        pre_draft_price = max(1.0, self.players[player].base_value) if player in self.players else price_f
        self.market_state.add_observation(pos or "UNKNOWN", "t1", price_f, pre_draft_price)

        lines = [f"Recorded: {player} to {team} for ${price_f:.0f}."]
        if team == "Sam" and pos:
            after_avg = self._avg_marginal_value(pos)
            if before_avg is not None and after_avg is not None:
                delta = after_avg - before_avg
                if abs(delta) >= 1:
                    lines.append(f"Impact: {pos} marginal value for Sam changed by ${delta:+.2f} "
                                 f"(now averaging ${after_avg:.2f} across remaining {pos}s).")
                else:
                    lines.append(f"Impact: no meaningful change to remaining {pos} value for Sam.")
        else:
            lines.append("No impact on Sam's own roster (someone else's purchase).")
        lines.append("")
        lines.append(self.cmd_status())
        return "\n".join(lines)

    def cmd_check(self, player: str) -> str:
        info = self.store.state.available_pool.get(player)
        if info is None:
            if player in self.store.state.sold_players:
                return f"{player} has already been sold -- see `status` or the sale log."
            return f"ERROR: unknown or unavailable player {player!r}."
        pos = info["position"]
        pre_draft_price = max(1.0, info.get("base_value", 1.0))

        try:
            open_starter = sum(1 for t in self.store.state.teams.values() if t.legal_starting_needs().get(pos, 0) > 0)
            open_flex = sum(1 for t in self.store.state.teams.values() if t.legal_starting_needs().get("FLEX", 0) > 0)
            cash_teams = sum(1 for t in self.store.state.teams.values() if t.legal_max_bid > 10)
            supply = sum(1 for n, v in self.store.state.available_pool.items() if v["position"] == pos)
            market = live_expected_price(pre_draft_price, pos, "t1", self.market_state, open_starter, open_flex, cash_teams, supply)
        except Exception as e:
            self._log_error("check/market", e)
            market = {"live_expected_price": pre_draft_price, "calculation_label": "SOLVER_FAILURE_FALLBACK"}
            print("SOLVER_FAILURE -- falling back to the pre-draft price as the approximate expected price.")

        try:
            rows = compute_live_sam_values(self._sam().roster, {player: info})
            marginal_value = rows[0].marginal_value if rows else 0.0
            expected_role = rows[0].expected_role if rows else "unknown"
            calc_label = "APPROXIMATE_LIVE_ROSTER_VALUE"
        except Exception as e:
            self._log_error("check/values", e)
            marginal_value = 0.0
            expected_role = "unknown (SOLVER_FAILURE)"
            calc_label = "SOLVER_FAILURE"

        sam = self._sam()
        governed = self._governed_ceiling(player, pos, marginal_value, expected_role, market["live_expected_price"])
        rec = compute_recommended_bid(
            player=player, safety_adjusted_ceiling=governed.dollar_ceiling, legal_max_bid=sam.legal_max_bid,
            portfolio_feasibility_limit=None, confidence=6, live_expected_price=market["live_expected_price"],
        )
        if governed.critical_review_required:
            rec.recommendation_type = "CRITICAL_REVIEW_REQUIRED"
            rec.reason = f"CRITICAL_REVIEW_REQUIRED ({', '.join(governed.critical_reasons)}). " + rec.reason

        lines = [
            f"{player} ({pos})",
            f"  Live expected price: ${market['live_expected_price']:.0f}  [{market.get('calculation_label', '?')}]",
            f"  Conservative estimate: ${market['live_expected_price'] * 1.15:.0f} (heuristic markup)",
            f"  Sam marginal roster value: ${marginal_value:.2f}  [{calc_label}]  (expected role: {expected_role})",
            f"  Legal max bid: ${sam.legal_max_bid:.2f}",
            f"  RECOMMENDED STOP: ${rec.recommended_final_bid:.0f}  [{rec.recommendation_type}]",
            f"  Reason: {rec.reason}",
        ]
        return "\n".join(lines)

    def _scored_targets(self, n: int = 10):
        """Shared computation for cmd_targets() (CLI text) and api_targets()
        (website JSON) -- both call this SAME method, so the two surfaces
        can never disagree about ranking. Stage 1 hardened ranking: a
        multi-factor decision score (auction_engine.live_target_scoring),
        not raw marginal value. Position need is capped so it cannot alone
        outrank a stronger, cheaper player -- see live_target_scoring.py's
        docstring."""
        pool = self._remaining_pool()
        if not pool:
            return []
        rows = compute_live_sam_values(self._sam().roster, pool)
        sam = self._sam()
        needs = sam.legal_starting_needs()

        pos_supply = {}
        for v in pool.values():
            pos_supply[v["position"]] = pos_supply.get(v["position"], 0) + 1

        scored = []
        for r in rows:
            info = next((v for v in pool.values() if v["display_name"] == r.player), None)
            pre_draft_price = max(1.0, info.get("base_value", 1.0)) if info else 1.0
            remaining_alts = max(0, pos_supply.get(r.position, 1) - 1)
            is_last = pos_supply.get(r.position, 0) <= 1 and needs.get(r.position, 0) > 0
            raw_need = min(1.0, needs.get(r.position, 0) / 2.0) if r.position in ("RB", "WR") else min(1.0, needs.get(r.position, 0))
            # UNIT-INTEGRITY FIX (official data / V3 repair, Part 6): this
            # call previously passed r.marginal_value -- fantasy POINTS,
            # per compute_live_sam_values' own documented meaning -- into
            # compute_target_score's marginal_value and
            # exact_or_approx_ceiling parameters, both of which are
            # subtracted against or compared to live_expected_price (a
            # DOLLAR figure) inside that function (expected_surplus =
            # marginal_value - live_expected_price; price_evidence =
            # (ceiling - live_expected_price) / ceiling). That is the
            # exact same points-vs-dollars units bug already fixed for
            # the board/check/exact recommendation paths via
            # _governed_ceiling, but it had NOT been fixed on the Targets
            # ranking path -- meaning the Targets page's 0.40-weighted
            # "quality_component" term and price-evidence term were
            # comparing points to dollars, which could silently distort
            # target ranking (though NOT the recommended_stop dollar
            # figure shown per row, which already routes through the
            # correct governed-ceiling call a few lines below in
            # api_targets). Fixed by reusing the SAME governed-ceiling
            # dollar conversion already used everywhere else in this
            # file -- never a second, target-scorer-only formula.
            governed_for_score = self._governed_ceiling(r.player, r.position, r.marginal_value, r.expected_role, pre_draft_price)
            team_specific_dollar_value = governed_for_score.dollar_ceiling
            score = compute_target_score(
                player=r.player, position=r.position, marginal_value=team_specific_dollar_value, expected_role=r.expected_role,
                live_expected_price=pre_draft_price, exact_or_approx_ceiling=max(1.0, team_specific_dollar_value),
                hard_max=None, remaining_alternatives_count=remaining_alts, is_last_legal_alternative=is_last,
                price_confidence=0.5, position_need_score=raw_need, portfolio_paths_broken_if_missed=0,
            )
            scored.append(score)
        return sorted(scored, key=lambda s: -s.total_score)[:n]

    def api_targets(self, n: int = 25) -> list[dict]:
        sam = self._sam()
        out = []
        for s in self._scored_targets(n):
            live_px = max(1.0, s.team_specific_value - s.expected_surplus_at_price)
            expected_role_full = "required starter" if s.starting_lineup_gain == s.team_specific_value and s.team_specific_value > 0 else (
                "bench depth" if s.bench_probability > 0.5 else "FLEX starter")
            # s.team_specific_value is ALREADY a dollar-denominated governed
            # ceiling (see _scored_targets above) -- pass it through
            # precomputed_team_specific_dollar_value, never as
            # marginal_value_points, or it would be silently
            # double-converted from a dollar figure as if it were points.
            governed = self._governed_ceiling(s.player, s.position, 0.0, expected_role_full, live_px,
                                               precomputed_team_specific_dollar_value=s.team_specific_value)
            rec = compute_recommended_bid(
                player=s.player, safety_adjusted_ceiling=governed.dollar_ceiling, legal_max_bid=sam.legal_max_bid,
                portfolio_feasibility_limit=None, confidence=6, live_expected_price=live_px,
            )
            if governed.critical_review_required:
                rec.recommendation_type = "CRITICAL_REVIEW_REQUIRED"
            out.append({
                "player": s.player, "position": s.position, "total_score": s.total_score,
                "recommendation_class": s.recommendation_class if not governed.critical_review_required else "CRITICAL_REVIEW_REQUIRED",
                "critical_review_required": governed.critical_review_required, "critical_reasons": governed.critical_reasons,
                "recommended_stop": rec.recommended_final_bid,
                "expected_surplus_at_price": s.expected_surplus_at_price, "starting_lineup_gain": s.starting_lineup_gain,
                "team_specific_value": s.team_specific_value, "role_probability_score": s.role_probability_score,
                "scarcity_score": s.scarcity_score, "tier_cliff_bonus": s.tier_cliff_bonus,
                "remaining_alternatives_count": s.remaining_alternatives_count, "price_confidence": s.price_confidence,
                "position_need_score": s.position_need_score, "price_evidence_score": s.price_evidence_score,
                "bench_probability": s.bench_probability,
            })
        return out

    def cmd_targets(self, n: int = 10) -> str:
        rows = self.api_targets(n)
        if not rows:
            return "No remaining players in the pool."
        lines = [f"Top {len(rows)} live targets by decision score (not raw marginal value):"]
        for t in rows:
            lines.append(f"  {t['player']:20s} {t['position']:3s} score={t['total_score']:.3f} "
                        f"[{t['recommendation_class']}] stop=${t['recommended_stop']:.0f}")
            lines.append(f"      surplus=${t['expected_surplus_at_price']:.2f} start_gain=${t['starting_lineup_gain']:.2f} "
                        f"team_value=${t['team_specific_value']:.2f} role_prob={t['role_probability_score']} "
                        f"scarcity={t['scarcity_score']} tier_cliff_bonus={t['tier_cliff_bonus']} "
                        f"alts_left={t['remaining_alternatives_count']} price_conf={t['price_confidence']} "
                        f"need_contrib={t['position_need_score']} price_evid={t['price_evidence_score']} "
                        f"bench_prob={t['bench_probability']}"
                        + (f"  ** {'/'.join(t['critical_reasons'])} **" if t.get("critical_review_required") else ""))
        return "\n".join(lines)

    def cmd_paths(self) -> str:
        pool = self._remaining_pool()
        remaining_for_paths = {n: {"display_name": n, "position": v["position"], "projected_points": v["projected_points"],
                                    "expected_price": max(1.0, v.get("base_value", 1.0)),
                                    "conservative_price": max(1.0, v.get("base_value", 1.0) * 1.15)}
                                for n, v in pool.items()}
        try:
            paths = compute_live_roster_paths(self._sam(), remaining_for_paths)
        except Exception as e:
            self._log_error("paths", e)
            return "SOLVER_FAILURE -- roster paths unavailable right now. Use `emergency` for the static fallback plan."
        lines = ["Complete roster paths:"]
        for style, r in paths.items():
            pts = f"{r['starting_points']:.1f}" if r.get("starting_points") is not None else "n/a"
            lines.append(f"  {style:26s} status={r['status']:10s} spend=${r['spend']:<4} starting_pts={pts}")
        return "\n".join(lines)

    def cmd_undo(self) -> str:
        if not self.store.events or all(e.event_type == "EVENT_UNDONE" for e in self.store.events):
            return "Nothing to undo."
        try:
            self.store.undo_last()
        except ValueError as e:
            return f"Nothing to undo: {e}"
        self._invalidate_exact_cache()
        return "Undo complete.\n\n" + self.cmd_status()

    def _invalidate_exact_cache(self):
        self._exact_cache = {}
        self._exact_cache_sequence = self.store.state.sequence_number

    # ---- Stage 2: exact on-demand checks ----

    def _keepers_df_for_sam(self):
        return pd.DataFrame([
            {"player": p["player_id"], "position": p["position"], "projected_points": p.get("projected_points", 0.0),
             "keeper_price_2026": p["price"]}
            for p in self._sam().roster
        ])

    def _pool_df_excluding(self, exclude: set):
        rows = []
        for pid, info in self.store.state.available_pool.items():
            if pid in exclude:
                continue
            price = max(1.0, info.get("base_value", 1.0))
            rows.append({"player": pid, "position": info["position"], "projected_points": info["projected_points"],
                         "suggested_auction_price": price})
        return pd.DataFrame(rows)

    def _run_exact_purchase_vs_pass(self, player: str, test_price: float):
        """Real, fresh HiGHS purchase-vs-pass solve from the CURRENT
        auction state. Cached by (sequence_number, player, test_price) --
        invalidated on every sale/undo/correction via _invalidate_exact_cache."""
        cache_key = (self.store.state.sequence_number, player, round(test_price, 2))
        if cache_key in self._exact_cache:
            return self._exact_cache[cache_key], True  # (result_tuple, was_cached)

        info = self.store.state.available_pool.get(player)
        if info is None:
            return None, False
        sam = self._sam()
        n_auction_spots = max(0, 16 - len(sam.roster))

        pool_minus = self._pool_df_excluding(set())
        roster_with = sam.roster + [{"player_id": player, "position": info["position"], "price": test_price,
                                      "projected_points": info["projected_points"]}]
        keepers_with = pd.concat([self._keepers_df_for_sam(), pd.DataFrame([
            {"player": player, "position": info["position"], "projected_points": info["projected_points"],
             "keeper_price_2026": test_price}])], ignore_index=True)
        t0 = time.time()
        result_purchase = exact_roster_solver.solve_exact_roster(
            pool_minus[pool_minus["player"] != player], budget=max(0.0, sam.budget_remaining - test_price),
            n_auction_spots=max(0, n_auction_spots - 1), keepers=keepers_with,
        )
        pool_pass = self._pool_df_excluding({player})
        result_pass = exact_roster_solver.solve_exact_roster(
            pool_pass, budget=sam.budget_remaining, n_auction_spots=n_auction_spots, keepers=self._keepers_df_for_sam(),
        )
        runtime = time.time() - t0
        payload = (result_purchase, result_pass, runtime, self.store.state.sequence_number)
        self._exact_cache[cache_key] = payload
        return payload, False

    def cmd_exact(self, player: str, test_price: float | None = None) -> str:
        info = self.store.state.available_pool.get(player)
        if info is None:
            return f"ERROR: unknown or unavailable player {player!r}."
        if test_price is None:
            test_price = max(1.0, info.get("base_value", 1.0))
        try:
            payload, was_cached = self._run_exact_purchase_vs_pass(player, test_price)
        except Exception as e:
            self._log_error("exact", e)
            return f"SOLVER_FAILURE -- falling back to the fast `check` command's approximate value. ({e})"
        if payload is None:
            return f"ERROR: unknown or unavailable player {player!r}."
        result_purchase, result_pass, runtime, solved_at_sequence = payload

        stale = solved_at_sequence != self.store.state.sequence_number
        stale_label = " [STALE_EXACT_RESULT -- state has moved on since this was solved]" if stale else ""

        both_optimal = result_purchase.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL") and \
                       result_pass.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL")
        if not both_optimal:
            return (f"{player}: solver did not return OPTIMAL "
                   f"(purchase={result_purchase.status}, pass={result_pass.status}) -- "
                   f"SOLVER_FAILURE, falling back to the fast `check` command instead.")

        purchase_names = set(result_purchase.selected["player"]) if not result_purchase.selected.empty else set()
        pass_names = set(result_pass.selected["player"]) if not result_pass.selected.empty else set()
        assert player in purchase_names, "exact-check invariant violated: candidate absent from purchase roster"
        assert player not in pass_names, "exact-check invariant violated: candidate present in pass roster"
        displaced = sorted(pass_names - purchase_names - {player})
        surplus = round(result_purchase.starting_points - result_pass.starting_points, 2)
        bench_change = round(result_purchase.bench_points - result_pass.bench_points, 2)
        sam = self._sam()
        # NOTE (V2 fix): the old formula here mixed STARTING-POINTS surplus
        # with a DOLLAR test_price -- the same units-bug class as the Josh
        # Jacobs anomaly. `surplus > 0` is used only as a boolean "purchase
        # beats pass at this price" signal now; the actual dollar ceiling
        # always comes from the governed helper (static max / legal max /
        # conservative live-price multiple), never from points arithmetic.
        real_role_rows = compute_live_sam_values(sam.roster, {player: info})
        expected_role_guess = real_role_rows[0].expected_role if real_role_rows else "unknown"
        # V3 Part 7 fix: pass the REAL marginal points (not a 0.0
        # sentinel) now that _governed_ceiling actually converts and uses
        # this value -- a 0.0 here would wrongly inject a $1
        # team-specific-value candidate into the governing min().
        real_marginal_points = real_role_rows[0].marginal_value if real_role_rows else 0.0
        governed = self._governed_ceiling(player, info["position"], real_marginal_points, expected_role_guess,
                                          max(1.0, info.get("base_value", 1.0)))
        rec = compute_recommended_bid(
            player=player, safety_adjusted_ceiling=governed.dollar_ceiling,
            legal_max_bid=sam.legal_max_bid, portfolio_feasibility_limit=None, confidence=8,
            live_expected_price=max(1.0, info.get("base_value", 1.0)),
        )
        if governed.critical_review_required:
            rec.recommendation_type = "CRITICAL_REVIEW_REQUIRED"

        lines = [
            f"{player} -- EXACT purchase-vs-pass at test price ${test_price:.0f}{stale_label}",
            f"  Purchase roster size: {len(result_purchase.selected)}  Pass roster size: {len(result_pass.selected)}",
            f"  Starting-lineup change: {surplus:+.2f}  Bench change: {bench_change:+.2f}",
            f"  Displaced player(s): {', '.join(displaced) if displaced else 'none'}",
            f"  Exact surplus at ${test_price:.0f}: {surplus:+.2f}",
            f"  Legal max bid: ${sam.legal_max_bid:.2f}",
            f"  RECOMMENDED STOP: ${rec.recommended_final_bid:.0f}  [{rec.recommendation_type}]",
            f"  Solver status: purchase={result_purchase.status} pass={result_pass.status}  "
            f"Runtime: {runtime:.2f}s  State sequence: {solved_at_sequence}  (cached={was_cached})",
        ]
        return "\n".join(lines)

    def api_exact(self, player: str, test_price: float | None = None, expected_sequence: int | None = None) -> dict:
        """V2.1 Part 4: JSON version of cmd_exact -- calls the IDENTICAL
        _run_exact_purchase_vs_pass method (the same solver call cmd_exact
        uses), just returns structured data instead of text."""
        info = self.store.state.available_pool.get(player)
        if info is None:
            return {"error": f"unknown or unavailable player {player!r}", "player": player}
        if test_price is None:
            test_price = max(1.0, info.get("base_value", 1.0))
        if expected_sequence is not None and expected_sequence != self.store.state.sequence_number:
            return {"error": "STALE_REQUEST -- auction state has moved on since this request was built; refresh and retry.",
                   "player": player, "current_sequence": self.store.state.sequence_number}
        try:
            payload, was_cached = self._run_exact_purchase_vs_pass(player, test_price)
        except Exception as e:
            self._log_error("api_exact", e)
            return {"error": f"SOLVER_FAILURE: {e}", "player": player, "solver_status": "ERROR"}
        result_purchase, result_pass, runtime, solved_at_sequence = payload
        stale = solved_at_sequence != self.store.state.sequence_number
        both_optimal = result_purchase.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL") and \
                       result_pass.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL")
        if not both_optimal:
            return {"error": "SOLVER_FAILURE_NONOPTIMAL", "player": player,
                   "purchase_status": result_purchase.status, "pass_status": result_pass.status}

        purchase_names = set(result_purchase.selected["player"]) if not result_purchase.selected.empty else set()
        pass_names = set(result_pass.selected["player"]) if not result_pass.selected.empty else set()
        assert player in purchase_names, "exact-check invariant violated: candidate absent from purchase roster"
        assert player not in pass_names, "exact-check invariant violated: candidate present in pass roster"
        displaced = sorted(pass_names - purchase_names - {player})
        surplus = round(result_purchase.starting_points - result_pass.starting_points, 2)
        bench_change = round(result_purchase.bench_points - result_pass.bench_points, 2)
        cash_change = round(result_purchase.unused_cash - result_pass.unused_cash, 2)
        sam = self._sam()
        # BUG FIX (V2.1): the expected role must come from the real
        # lineup-competition computation (compute_live_sam_values), never
        # guessed from the surplus sign at one arbitrary test price -- a
        # negative surplus at an overpriced test price does NOT mean the
        # player is bench depth; it can mean the player is a legitimate
        # starter who's simply not worth THAT price. Guessing from surplus
        # sign caused a real false-positive BENCH_DEPTH_STOP_OVER_25
        # critical warning for Josh Jacobs (a true required starter) --
        # found while wiring the website's exact endpoint.
        real_role_rows = compute_live_sam_values(sam.roster, {player: info})
        expected_role_guess = real_role_rows[0].expected_role if real_role_rows else "unknown"
        # V3 Part 7 fix: real marginal points, not a 0.0 sentinel -- see
        # the identical fix/comment in cmd_exact above.
        real_marginal_points = real_role_rows[0].marginal_value if real_role_rows else 0.0

        # exact ceiling: binary search around test_price using the same
        # per-price solve method, so the panel can show a TRUE dollar
        # ceiling, not just a pass/fail at one price (addresses the
        # Josh Jacobs post-fix finding: the fast $78 approximation
        # differed from the true $66 exact ceiling by $12).
        exact_ceiling = self._binary_search_exact_ceiling(player, info["position"])

        governed = self._governed_ceiling(
            player, info["position"], real_marginal_points, expected_role_guess, max(1.0, info.get("base_value", 1.0)),
            exact_ceiling=exact_ceiling, exact_status="OPTIMAL", exact_is_current=not stale,
        )
        rec = compute_recommended_bid(
            player=player, safety_adjusted_ceiling=governed.dollar_ceiling,
            legal_max_bid=sam.legal_max_bid, portfolio_feasibility_limit=None, confidence=9,
            live_expected_price=max(1.0, info.get("base_value", 1.0)),
        )
        rec_type = "CRITICAL_REVIEW_REQUIRED" if governed.critical_review_required else rec.recommendation_type

        return {
            "player": player, "test_price": test_price, "purchase_objective": result_purchase.starting_points,
            "pass_objective": result_pass.starting_points, "exact_surplus": surplus,
            "exact_ceiling": exact_ceiling, "safety_adjusted_maximum": governed.dollar_ceiling,
            "purchase_roster": sorted(purchase_names), "pass_roster": sorted(pass_names),
            "starting_lineup_change": surplus, "bench_change": bench_change, "cash_change": cash_change,
            "displaced_player": displaced[0] if displaced else None, "displaced_players": displaced,
            "solver_status": result_purchase.status, "runtime": round(runtime, 3),
            "state_sequence": solved_at_sequence, "current_sequence": self.store.state.sequence_number,
            "cache_status": "CACHED" if was_cached else "FRESH",
            "stale_status": "STALE_EXACT_RESULT" if stale else "CURRENT",
            "recommended_stop": rec.recommended_final_bid, "recommendation": rec_type,
            "critical_review_required": governed.critical_review_required, "critical_reasons": governed.critical_reasons,
        }

    def _binary_search_exact_ceiling(self, player: str, position: str) -> int | None:
        """Real integer-dollar binary search for the TRUE exact ceiling,
        using the same _run_exact_purchase_vs_pass solver calls as every
        other exact check. Cached implicitly through that method's own
        (sequence, player, price) cache."""
        sam = self._sam()

        def ok_at(price):
            payload, _ = self._run_exact_purchase_vs_pass(player, float(price))
            rp, rpass, rt, seq = payload
            if rp.status not in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL") or rpass.status not in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL"):
                return None
            return (rp.starting_points - rpass.starting_points) >= 0

        hi_bound = int(min(sam.budget_remaining, 400))
        ok0 = ok_at(1)
        if ok0 is None:
            return None
        if not ok0:
            return 0
        lo, step, price = 1, 1, 1
        while price < hi_bound:
            nxt = min(price + step, hi_bound)
            res = ok_at(nxt)
            if res is None:
                break
            if not res:
                break
            price = nxt
            step *= 2
        lo, hi = price, min(price + max(step, 1), hi_bound)
        res_hi = ok_at(hi)
        if res_hi:
            return hi
        while hi - lo > 1:
            mid = (lo + hi) // 2
            res = ok_at(mid)
            if res is None:
                hi = mid
                continue
            if res:
                lo = mid
            else:
                hi = mid
        return lo

    def api_ladder(self, player: str) -> dict:
        info = self.store.state.available_pool.get(player)
        if info is None:
            return {"error": f"unknown or unavailable player {player!r}", "player": player}
        base = max(1.0, info.get("base_value", 1.0))
        prices = sorted(set(max(1, round(base * m)) for m in (0.7, 0.85, 1.0, 1.15, 1.3)))
        rows = []
        for p in prices:
            try:
                payload, was_cached = self._run_exact_purchase_vs_pass(player, float(p))
            except Exception as e:
                self._log_error("api_ladder", e)
                rows.append({"price": p, "exact_surplus": None, "purchase_status": "ERROR", "pass_status": "ERROR",
                            "roster_feasible": False, "recommended_action": "SOLVER_FAILURE"})
                continue
            rp, rpass, rt, seq = payload
            both_ok = rp.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL") and rpass.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL")
            surplus = round(rp.starting_points - rpass.starting_points, 2) if both_ok else None
            rows.append({
                "price": p, "exact_surplus": surplus, "purchase_status": rp.status, "pass_status": rpass.status,
                "roster_feasible": both_ok and len(rp.selected) == 16 and len(rpass.selected) == 16,
                "recommended_action": ("BUY" if surplus is not None and surplus >= 0 else
                                       "PASS" if surplus is not None else "SOLVER_FAILURE"),
            })
        return {"player": player, "ladder": rows, "state_sequence": self.store.state.sequence_number}

    def cmd_ladder(self, player: str) -> str:
        info = self.store.state.available_pool.get(player)
        if info is None:
            return f"ERROR: unknown or unavailable player {player!r}."
        base = max(1.0, info.get("base_value", 1.0))
        prices = sorted(set(max(1, round(base * m)) for m in (0.7, 0.85, 1.0, 1.15, 1.3)))
        lines = [f"Ladder for {player} around expected price ${base:.0f}:"]
        for p in prices:
            try:
                payload, _ = self._run_exact_purchase_vs_pass(player, float(p))
            except Exception as e:
                self._log_error("ladder", e)
                lines.append(f"  ${p}: SOLVER_FAILURE")
                continue
            if payload is None:
                continue
            result_purchase, result_pass, runtime, seq = payload
            if result_purchase.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL") and \
               result_pass.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL"):
                surplus = round(result_purchase.starting_points - result_pass.starting_points, 2)
                lines.append(f"  ${p}: surplus {surplus:+.2f}")
            else:
                lines.append(f"  ${p}: SOLVER_FAILURE")
        return "\n".join(lines)

    def cmd_save(self, name: str) -> str:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        target_log = SNAPSHOT_DIR / f"{name}.jsonl"
        target_initial = SNAPSHOT_DIR / f"{name}_initial.json"
        target_initial.write_text(json.dumps(self.initial_state.to_dict()))
        with target_log.open("w") as f:
            for e in self.store.events:
                f.write(json.dumps(e.to_dict()) + "\n")
        return f"Saved snapshot '{name}' ({len(self.store.events)} events) to {SNAPSHOT_DIR}/"

    def cmd_load(self, name: str) -> str:
        target_log = SNAPSHOT_DIR / f"{name}.jsonl"
        target_initial = SNAPSHOT_DIR / f"{name}_initial.json"
        if not target_log.exists() or not target_initial.exists():
            return f"ERROR: no snapshot named '{name}' found in {SNAPSHOT_DIR}/"
        initial_state = AuctionState.from_dict(json.loads(target_initial.read_text()))
        self.initial_state = initial_state
        self.store = AuctionStateStore.recover(initial_state, target_log)
        self.store.log_path = self.log_path  # continue logging new events to the live session log
        self._invalidate_exact_cache()
        return f"Loaded snapshot '{name}'.\n\n" + self.cmd_status()

    # ---- Stage 9: additional commands ----

    def _resolve_name(self, partial: str, pool: dict | None = None) -> tuple[str | None, list[str]]:
        """Case-insensitive exact/partial name resolution. Returns
        (resolved_name_or_None, list_of_candidates). If exactly one
        candidate matches, resolved_name is set; if multiple match,
        resolved_name is None and candidates lists all of them (for a
        numbered disambiguation message)."""
        pool = pool if pool is not None else self.store.state.available_pool
        if partial in pool:
            return partial, [partial]
        lowered = partial.lower()
        exact_ci = [n for n in pool if n.lower() == lowered]
        if len(exact_ci) == 1:
            return exact_ci[0], exact_ci
        substr = [n for n in pool if lowered in n.lower()]
        if len(substr) == 1:
            return substr[0], substr
        return None, substr

    def cmd_search(self, partial: str) -> str:
        _, candidates = self._resolve_name(partial)
        if not candidates:
            return f"No available players matching {partial!r}."
        lines = [f"{i+1}. {name} ({self.store.state.available_pool[name]['position']})" for i, name in enumerate(candidates)]
        return "\n".join(lines)

    def cmd_last(self) -> str:
        real_sales = [e for e in self.store.events if e.event_type == "PLAYER_SOLD"]
        undone_ids = {e.payload.get("undone_event_id") for e in self.store.events if e.event_type == "EVENT_UNDONE"}
        real_sales = [e for e in real_sales if e.event_id not in undone_ids]
        if not real_sales:
            return "No sales recorded yet."
        last = real_sales[-1]
        p = last.payload
        return (f"Last sale: {p['display_name']} ({p['position']}) to {p['winning_owner']} "
               f"for ${p['sale_price']:.0f}  [sequence {last.sequence_number}]")

    def cmd_correct(self, player: str, team: str, price: str) -> str:
        try:
            price_f = float(price)
        except ValueError:
            return f"ERROR: price must be a number, got {price!r}."
        # GATE B (V3 repair): same whole-dollar/minimum-$1 rule as
        # cmd_sale -- a correction must not be able to introduce an
        # illegal price the original sale path would have refused.
        if price_f != int(price_f):
            return f"ERROR: price must be a whole dollar amount, got {price_f} -- decimal prices are not legal in this auction."
        if price_f < 1:
            return f"ERROR: minimum sale price is $1, got ${price_f:.0f} -- $0 and negative sales are not legal."
        old = self.store.state.sold_players.get(player)
        if old is None:
            return f"ERROR: {player!r} has no recorded sale to correct."
        pos = None
        points = None
        for t in self.store.state.teams.values():
            for p in t.roster:
                if p["player_id"] == player:
                    pos = p["position"]
                    points = p.get("projected_points")
        # GATE B FIX (V3 repair, Part 4): a correction must preserve
        # projected_points (and thus every downstream valuation/lineup
        # calculation for this player), never silently recreate them as
        # a $0/0-point ghost. Prefer the CURRENT roster entry's own
        # points (most authoritative -- it's what's live right now);
        # fall back to the canonical projections dict, then the old sale
        # record itself, in that order.
        if points is None:
            points = self.players[player].projected_points if player in self.players else old.get("projected_points")
        try:
            self.store.correct_sale(player, player, pos or "UNKNOWN", team, price_f, None, projected_points=points)
        except IllegalEventError as e:
            return f"REFUSED: {e}"
        self._invalidate_exact_cache()
        return f"Corrected: {player} now sold to {team} for ${price_f:.0f}.\n\n" + self.cmd_status()

    def cmd_market(self) -> str:
        league_ratio, league_n = self.market_state.league_ratio()
        lines = [f"League-wide spending ratio: {league_ratio:.3f} (n={league_n} observed sales)",
                 "Active market prior: STATIC_PRE_DRAFT_MARKET_PRIOR" + (
                     " (see sunday_release_manifest.json for whether an evolved prior was ever validated)"),
                 "Position spending ratios:"]
        for pos in ("QB", "RB", "WR", "TE"):
            ratio, n = self.market_state.position_ratio(pos)
            lines.append(f"  {pos}: {ratio:.3f} (n={n})")
        return "\n".join(lines)

    def cmd_position(self, position: str) -> str:
        position = position.upper()
        pool = {n: v for n, v in self.store.state.available_pool.items() if v["position"] == position}
        if not pool:
            return f"No remaining players at position {position!r}."
        rows = compute_live_sam_values(self._sam().roster, pool)
        rows_sorted = sorted(rows, key=lambda r: -r.marginal_value)[:15]
        lines = [f"Remaining {position}s ({len(pool)} total), top {len(rows_sorted)} by marginal value:"]
        for r in rows_sorted:
            lines.append(f"  {r.player:22s} marginal=${r.marginal_value:6.2f} role={r.expected_role}")
        return "\n".join(lines)

    def cmd_why(self, player: str) -> str:
        info = self.store.state.available_pool.get(player)
        if info is None:
            return f"ERROR: unknown or unavailable player {player!r}."
        rows = compute_live_sam_values(self._sam().roster, {player: info})
        r = rows[0]
        pre_draft_price = max(1.0, info.get("base_value", 1.0))
        market = live_expected_price(pre_draft_price, info["position"], "t1", self.market_state, 0, 0, 6, 10)
        sam = self._sam()
        governed = self._governed_ceiling(player, info["position"], r.marginal_value, r.expected_role, market["live_expected_price"])
        rec = compute_recommended_bid(
            player=player, safety_adjusted_ceiling=governed.dollar_ceiling, legal_max_bid=sam.legal_max_bid,
            portfolio_feasibility_limit=None, confidence=6, live_expected_price=market["live_expected_price"],
        )
        if governed.critical_review_required:
            rec.recommendation_type = "CRITICAL_REVIEW_REQUIRED"
            rec.reason = f"CRITICAL_REVIEW_REQUIRED ({', '.join(governed.critical_reasons)}). " + rec.reason
        return "\n".join([
            f"WHY {player} ({info['position']}):",
            f"  Projected points: {info['projected_points']:.1f}",
            f"  Expected role: {r.expected_role}",
            f"  Fast roster-value change (marginal): ${r.marginal_value:.2f}  [APPROXIMATE_LIVE_ROSTER_VALUE]",
            f"  Exact result: not run this call -- use `exact {player.replace(' ', '_')}` for a fresh HiGHS solve",
            f"  Pre-draft market prior: ${pre_draft_price:.0f}  [STATIC_PRE_DRAFT_MARKET_PRIOR]",
            f"  Observed-market adjustment: league_ratio={market['league_spending_ratio']} tier_ratio={market['tier_spending_ratio']} "
            f"demand_mult={market['demand_multiplier']}",
            f"  Live expected price: ${market['live_expected_price']:.0f}  [{market['calculation_label']}]",
            f"  Legal max bid: ${sam.legal_max_bid:.2f}",
            f"  RECOMMENDED STOP: ${rec.recommended_final_bid:.0f}  [{rec.recommendation_type}]",
            f"  Confidence deductions: price is PRELIMINARY_NOT_FINAL / uncalibrated; roster value is fast-approximate, not exact-solved this call.",
        ])

    def cmd_prior(self) -> str:
        manifest_path = SUNDAY_FINAL_DIR / "sunday_release_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
                return (f"Active market prior source: {manifest.get('active_market_prior_source', 'STATIC_PRE_DRAFT_MARKET_PRIOR')}\n"
                       f"Evolved ensemble version: {manifest.get('evolved_ensemble_version', 'NONE')}")
            except Exception:
                pass
        return "Active market prior source: STATIC_PRE_DRAFT_MARKET_PRIOR (no evolved prior integrated)."

    def _needs_large_sale_confirmation(self, player: str, team: str, price_f: float) -> str | None:
        reasons = []
        if price_f > 50:
            reasons.append(f"price ${price_f:.0f} is above $50")
        info = self.store.state.available_pool.get(player)
        if info is not None:
            pre_draft_price = max(1.0, info.get("base_value", 1.0))
            p75 = pre_draft_price * 1.15
            if price_f > p75 + 10:
                reasons.append(f"price exceeds live P75 (~${p75:.0f}) by more than $10")
        if team == "Sam":
            reasons.append("winning team is Sam")
        if not reasons:
            return None
        return "; ".join(reasons)

    def cmd_emergency(self) -> str:
        parts = []
        sheet_path = LIVE_MVP_DIR / "static_emergency_bid_sheet.csv"
        plan_path = BASE_DIR / "outputs" / "auction_rebuild" / "phase4" / "sam_sunday_plan.txt"
        if plan_path.exists():
            parts.append("=== SAM SUNDAY PLAN ===\n" + plan_path.read_text())
        if sheet_path.exists():
            parts.append("=== STATIC EMERGENCY BID SHEET (raw CSV) ===\n" + sheet_path.read_text())
        if not parts:
            return "ERROR: no emergency sheet found on disk."
        return "\n\n".join(parts)

    # ---- Website API data methods (Live Auction Website) ----
    # These call the SAME underlying engine functions the CLI's own text
    # commands call (compute_live_sam_values, live_expected_price,
    # compute_recommended_bid, compute_live_roster_paths) -- they only
    # differ in returning structured dicts instead of formatted text, so
    # the website and the CLI can never disagree about a number.

    def api_status(self) -> dict:
        sam = self._sam()
        return {
            "budget_remaining": sam.budget_remaining, "open_slots": sam.open_slots,
            "min_reserve": sam.min_reserve, "legal_max_bid": sam.legal_max_bid,
            "position_needs": sam.legal_starting_needs(), "position_counts": sam.position_counts,
            "roster": [{"position": p["position"], "display_name": p["display_name"], "price": p["price"],
                       "is_keeper": bool(p.get("is_keeper"))} for p in sam.roster],
            "sequence_number": self.store.state.sequence_number,
        }

    def api_board(self) -> list[dict]:
        pool = self._remaining_pool()
        if not pool:
            return []
        sam = self._sam()
        rows = compute_live_sam_values(sam.roster, pool)
        needs = sam.legal_starting_needs()
        # one live_expected_price computation per POSITION (not per player) for speed --
        # same function the CLI's `check`/`market` commands call.
        pos_market = {}
        for pos in ("QB", "RB", "WR", "TE"):
            open_starter = sum(1 for t in self.store.state.teams.values() if t.legal_starting_needs().get(pos, 0) > 0)
            open_flex = sum(1 for t in self.store.state.teams.values() if t.legal_starting_needs().get("FLEX", 0) > 0)
            cash_teams = sum(1 for t in self.store.state.teams.values() if t.legal_max_bid > 10)
            supply = sum(1 for v in pool.values() if v["position"] == pos)
            pos_market[pos] = (open_starter, open_flex, cash_teams, supply)

        out = []
        for r in rows:
            info = next((v for v in pool.values() if v["display_name"] == r.player), None)
            pre_draft_price = max(1.0, info.get("base_value", 1.0)) if info else 1.0
            os_, of_, ct_, sup_ = pos_market.get(r.position, (0, 0, 6, 1))
            try:
                market = live_expected_price(pre_draft_price, r.position, "t1", self.market_state, os_, of_, ct_, sup_)
                live_price = market["live_expected_price"]
                calc_label = market["calculation_label"]
            except Exception:
                live_price = pre_draft_price
                calc_label = "SOLVER_FAILURE_FALLBACK"
            governed = self._governed_ceiling(r.player, r.position, r.marginal_value, r.expected_role, live_price)
            rec = compute_recommended_bid(
                player=r.player, safety_adjusted_ceiling=governed.dollar_ceiling, legal_max_bid=sam.legal_max_bid,
                portfolio_feasibility_limit=None, confidence=6, live_expected_price=live_price,
            )
            rec_type = "CRITICAL_REVIEW_REQUIRED" if governed.critical_review_required else rec.recommendation_type
            out.append({
                "player": r.player, "position": r.position, "projected_points": r.projected_points,
                "live_expected_price": round(live_price, 1), "conservative_price": round(live_price * 1.15, 1),
                "marginal_value": r.marginal_value, "expected_role": r.expected_role,
                "recommended_stop": rec.recommended_final_bid, "recommendation": rec_type,
                "critical_review_required": governed.critical_review_required, "critical_reasons": governed.critical_reasons,
                "calculation_label": calc_label + " | " + governed.calculation_label, "position_need": needs.get(r.position, 0),
            })
        return out

    def api_check(self, player: str) -> dict | None:
        info = self.store.state.available_pool.get(player)
        if info is None:
            return None
        pos = info["position"]
        pre_draft_price = max(1.0, info.get("base_value", 1.0))
        sam = self._sam()
        try:
            open_starter = sum(1 for t in self.store.state.teams.values() if t.legal_starting_needs().get(pos, 0) > 0)
            open_flex = sum(1 for t in self.store.state.teams.values() if t.legal_starting_needs().get("FLEX", 0) > 0)
            cash_teams = sum(1 for t in self.store.state.teams.values() if t.legal_max_bid > 10)
            supply = sum(1 for v in self.store.state.available_pool.values() if v["position"] == pos)
            market = live_expected_price(pre_draft_price, pos, "t1", self.market_state, open_starter, open_flex, cash_teams, supply)
        except Exception:
            market = {"live_expected_price": pre_draft_price, "calculation_label": "SOLVER_FAILURE_FALLBACK"}
        rows = compute_live_sam_values(sam.roster, {player: info})
        marginal_value = rows[0].marginal_value if rows else 0.0
        expected_role = rows[0].expected_role if rows else "unknown"
        governed = self._governed_ceiling(player, pos, marginal_value, expected_role, market["live_expected_price"])
        rec = compute_recommended_bid(
            player=player, safety_adjusted_ceiling=governed.dollar_ceiling, legal_max_bid=sam.legal_max_bid,
            portfolio_feasibility_limit=None, confidence=6, live_expected_price=market["live_expected_price"],
        )
        rec_type = "CRITICAL_REVIEW_REQUIRED" if governed.critical_review_required else rec.recommendation_type
        return {
            "player": player, "position": pos, "live_expected_price": round(market["live_expected_price"], 1),
            "conservative_price": round(market["live_expected_price"] * 1.15, 1),
            "critical_review_required": governed.critical_review_required, "critical_reasons": governed.critical_reasons,
            "governed_calculation_label": governed.calculation_label,
            "marginal_value": marginal_value, "expected_role": expected_role,
            "recommended_stop": rec.recommended_final_bid, "recommendation": rec_type,
            "reason": (f"CRITICAL_REVIEW_REQUIRED ({', '.join(governed.critical_reasons)}). " if governed.critical_review_required else "") + rec.reason,
            "legal_max_bid": sam.legal_max_bid,
            "calculation_label": market.get("calculation_label", "APPROXIMATE_LIVE_ROSTER_VALUE"),
        }

    def _has_current_exact(self, player: str) -> bool:
        seq = self.store.state.sequence_number
        return any(k[0] == seq and k[1] == player for k in self._exact_cache)

    def api_verdict(self, player: str, current_bid: float | None = None, leading_team: str | None = None) -> dict | None:
        """V3 Parts 9-10: the single, backend-authoritative nominee-panel
        verdict service. Replaces the ad hoc client-side JS verdict logic
        that used to live in live_web/static/app.js -- verdicts must come
        from the SAME governed service used everywhere else, never a
        second UI-only formula, per this repair's own repeated finding
        that duplicated valuation logic is where these bugs live.

        Required verdict taxonomy (exact spec wording, normalized to
        SCREAMING_SNAKE_CASE for a JSON field): BID; BID_BUT_RUN_EXACT_SOON;
        HOLD; ONE_MORE_DOLLAR; PASS; ILLEGAL; CRITICAL_REVIEW_REQUIRED.

        Every dollar-vs-points field is explicitly suffixed with its unit
        (e.g. marginal_lineup_points vs team_specific_value_dollars) so a
        UI can never accidentally render points with a $ sign again.
        """
        check = self.api_check(player)
        if check is None:
            return None
        sam = self._sam()
        exact_current = self._has_current_exact(player)
        stop = check["recommended_stop"]
        legal_max = check["legal_max_bid"]

        verdict = None
        reason = None
        if current_bid is not None and current_bid > legal_max + 1e-9:
            verdict = "ILLEGAL"
            reason = f"${current_bid:.0f} exceeds Sam's legal maximum bid of ${legal_max:.0f} -- this bid cannot legally be paid."
        elif check["critical_review_required"]:
            verdict = "CRITICAL_REVIEW_REQUIRED"
            reason = f"Critical guardrail(s) triggered: {', '.join(check['critical_reasons'])}. Review before bidding."
        elif current_bid is None:
            verdict = "HOLD"
            reason = f"No current bid entered yet. Reference stop: ${stop:.0f} ({check['governed_calculation_label']})."
        elif current_bid > stop + 1e-9:
            verdict = "PASS"
            reason = f"${current_bid:.0f} exceeds the recommended stop ${stop:.0f} -- pass."
        elif stop - current_bid <= 1 + 1e-9:
            verdict = "ONE_MORE_DOLLAR"
            reason = f"${current_bid:.0f} is within $1 of the stop ${stop:.0f} -- this should be your final raise."
        elif current_bid > 20 and not exact_current:
            verdict = "BID_BUT_RUN_EXACT_SOON"
            reason = f"${current_bid:.0f} is a meaningful bid with no current exact solve backing the ${stop:.0f} approximate stop -- run Exact before going further."
        else:
            verdict = "BID"
            reason = f"${current_bid:.0f} is safely below the stop ${stop:.0f} -- bid."

        return {
            "player": player, "position": check["position"],
            "current_bid_dollars": current_bid, "leading_team": leading_team,
            "expected_market_price_dollars": check["live_expected_price"],
            "market_p25_p50_p75_p90_dollars": None,  # not wired to a validated Monte Carlo batch this pass -- honestly None, not fabricated
            "marginal_lineup_points": round(check["marginal_value"], 2),
            "team_specific_value_dollars": check["recommended_stop"],  # the governed ceiling IS the team-specific-value-aware stop (V3 Part 7)
            "approximate_ceiling_dollars": check["recommended_stop"],
            "exact_ceiling_dollars": None,  # only populated by /api/exact; this endpoint is the fast path
            "exact_is_current": exact_current,
            "recommended_stop_dollars": stop,
            "legal_max_bid_dollars": legal_max,
            "money_remaining_if_sam_wins_dollars": (round(sam.budget_remaining - current_bid, 2) if current_bid is not None else None),
            "open_slots_after_purchase": max(0, sam.open_slots - 1),
            "surplus_or_deficit_dollars": (round(stop - current_bid, 2) if current_bid is not None else None),
            "confidence": "LOW" if not exact_current else "HIGH",
            "verdict": verdict,
            "reason": reason,
            "critical_review_required": check["critical_review_required"],
            "critical_reasons": check["critical_reasons"],
        }

    def api_paths(self) -> dict:
        pool = self._remaining_pool()
        remaining_for_paths = {n: {"display_name": n, "position": v["position"], "projected_points": v["projected_points"],
                                    "expected_price": max(1.0, v.get("base_value", 1.0)),
                                    "conservative_price": max(1.0, v.get("base_value", 1.0) * 1.15)}
                                for n, v in pool.items()}
        try:
            paths = compute_live_roster_paths(self._sam(), remaining_for_paths)
        except Exception as e:
            self._log_error("api_paths", e)
            return {"error": "SOLVER_FAILURE -- roster paths unavailable right now."}
        return paths

    def api_market(self) -> dict:
        league_ratio, league_n = self.market_state.league_ratio()
        positions = {}
        for pos in ("QB", "RB", "WR", "TE"):
            ratio, n = self.market_state.position_ratio(pos)
            positions[pos] = {"ratio": ratio, "n": n}
        return {"league_ratio": league_ratio, "league_n": league_n, "positions": positions,
                "active_prior": "STATIC_PRE_DRAFT_MARKET_PRIOR"}

    def api_log(self) -> list[dict]:
        undone_ids = {e.payload.get("undone_event_id") for e in self.store.events if e.event_type == "EVENT_UNDONE"}
        out = []
        for e in self.store.events:
            if e.event_type == "PLAYER_SOLD" and e.event_id not in undone_ids:
                p = e.payload
                out.append({"sequence": e.sequence_number, "player": p["display_name"], "position": p["position"],
                           "team": p["winning_owner"], "price": p["sale_price"]})
        return out

    def api_league(self, nominee: str | None = None) -> list[dict]:
        """V2 Part 4 / V2.1 Part 8: all-team auction room. Reads the SAME
        live auction_engine state as everything else -- reflects real
        sales immediately, not a static pre-draft snapshot. When
        `nominee` is given, each row also carries that team's demand
        label for the current nominee and its FLEX capacity, per the
        V2.1 spec's required League Room field list."""
        demand = self.api_nominee_demand(nominee) if nominee else None
        out = []
        for team_id, t in self.store.state.teams.items():
            needs = t.legal_starting_needs()
            purchases = [p for p in t.roster if not p.get("is_keeper")]
            latest = purchases[-1] if purchases else None
            row = {
                "team": team_id, "budget_remaining": t.budget_remaining, "open_slots": t.open_slots,
                "min_reserve": t.min_reserve, "legal_max_bid": t.legal_max_bid,
                "position_counts": t.position_counts, "position_needs": needs,
                "flex_capacity": needs.get("FLEX", 0),
                "keeper_count": len(t.keeper_ids), "is_sam": team_id == self.store.state.sam_team_id,
                "latest_purchase": (latest["display_name"] if latest else None),
                "current_nominee_demand": (demand["demand_by_team"].get(team_id) if demand else None),
            }
            out.append(row)
        return out

    def _roster_with_roles(self, roster: list[dict]) -> list[dict]:
        """V2.2 Request 3: attaches a real starter/bench role to every
        roster player using the SAME assign_lineup() function the
        auction-path roster optimizer uses elsewhere -- no duplicated
        lineup logic. Works for any team's roster, not just Sam's."""
        if not roster:
            return []
        df = pd.DataFrame([
            {"player": p["display_name"], "position": p["position"],
             "projected_points": p.get("projected_points", 0) or 0}
            for p in roster
        ])
        lineup = assign_lineup(df)
        out = []
        for p in roster:
            role = lineup.roles.get(p["display_name"], "UNKNOWN")
            slot_type = "BENCH" if role.startswith("BENCH") else "STARTER"
            out.append({
                "position": p["position"], "display_name": p["display_name"], "price": p["price"],
                "is_keeper": bool(p.get("is_keeper")), "lineup_role": role, "slot_type": slot_type,
            })
        return out

    def api_team_detail(self, team_id: str) -> dict | None:
        t = self.store.state.teams.get(team_id)
        if t is None:
            return None
        sold_by_team = [e for e in self.api_log() if e["team"] == team_id]
        college_rights_holdings = sorted(COLLEGE_RIGHTS) if team_id == self.store.state.sam_team_id else []
        return {
            "team": team_id, "budget_remaining": t.budget_remaining, "open_slots": t.open_slots,
            "legal_max_bid": t.legal_max_bid, "position_counts": t.position_counts,
            "position_needs": t.legal_starting_needs(),
            "roster": self._roster_with_roles(t.roster),
            "roster_count": len(t.roster),
            "sale_history": sold_by_team,
            # College-rights holdings (e.g. Mendoza, Bond) are deliberately
            # NOT part of any team's 16-man roster list above -- shown here
            # only as a separate, clearly-labeled note so nothing implies
            # they occupy a roster slot.
            "college_rights_holdings": college_rights_holdings,
        }

    def api_all_rosters(self) -> list[dict]:
        """V2.2 Request 3: every team's complete player-by-player roster
        in one call, reusing api_team_detail's per-team logic (which
        itself reuses the real live auction_engine state and the real
        assign_lineup roster optimizer -- no new data source, no
        duplicated roster logic)."""
        return [self.api_team_detail(team_id) for team_id in self.store.state.teams.keys()]

    def api_nominee_demand(self, player: str) -> dict | None:
        """V2 Part 4: transparent, roster/budget-facts-only demand label
        per team for the given nominee -- no inferred manager preference."""
        info = self.store.state.available_pool.get(player)
        if info is None:
            return None
        pos = info["position"]
        out = {}
        credible = 0
        for team_id, t in self.store.state.teams.items():
            try:
                needs = t.legal_starting_needs()
                has_cash = t.legal_max_bid > 1
                if not has_cash:
                    label = "NO_LEGAL_BID"
                elif needs.get(pos, 0) > 0:
                    label = "HIGH_REQUIRED_NEED"
                elif needs.get("FLEX", 0) > 0 and pos in ("RB", "WR", "TE"):
                    label = "MEDIUM_FLEX_OR_DEPTH"
                else:
                    label = "LOW_POSITION_FILLED"
            except Exception:
                # Roster/budget facts unavailable for some reason -- never
                # guess at manager psychology, just say so honestly.
                label = "UNKNOWN"
            if label in ("HIGH_REQUIRED_NEED", "MEDIUM_FLEX_OR_DEPTH"):
                credible += 1
            out[team_id] = label
        return {"player": player, "position": pos, "demand_by_team": out, "credible_bidder_count": credible}

    def api_search(self, query: str, include_protected: bool = False) -> list[dict]:
        """V2 Part 3: player search. Supports partial names, spaces/
        underscores, case-insensitivity. Searches available auction
        players by default; include_protected=True also returns keepers/
        college-rights (shown with status+owner, no sale controls --
        enforced by the caller, not this method)."""
        query_norm = query.replace("_", " ").strip().lower()
        if not query_norm:
            return []
        results = []
        for name, info in self.store.state.available_pool.items():
            if query_norm in name.lower():
                results.append({"player": name, "position": info["position"], "status": "AVAILABLE"})
        if include_protected:
            for team_id, t in self.store.state.teams.items():
                for p in t.roster:
                    if query_norm in p["display_name"].lower():
                        status = "KEEPER" if p.get("is_keeper") else "SOLD"
                        results.append({"player": p["display_name"], "position": p["position"], "status": status, "owner": team_id})
            for name in COLLEGE_RIGHTS:
                if query_norm in name.lower():
                    results.append({"player": name, "position": "?", "status": "COLLEGE_RIGHTS_HELD", "owner": "Sam"})
        return results

    def cmd_help(self) -> str:
        lines = ["Commands:"]
        for cmd, desc in COMMANDS.items():
            lines.append(f"  {cmd:28s} {desc}")
        return "\n".join(lines)

    def _log_error(self, where: str, exc: Exception):
        ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with ERROR_LOG_PATH.open("a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {where}: {exc}\n")
            f.write(traceback.format_exc() + "\n")

    def dispatch(self, line: str) -> str:
        """Parse and run one command line. Never raises -- catches and
        logs any unexpected exception, returning an error string instead."""
        line = line.strip()
        if not line:
            return ""
        parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]
        try:
            if cmd == "status":
                return self.cmd_status()
            if cmd == "sale":
                confirmed = False
                if args and args[-1].lower() == "confirm":
                    confirmed = True
                    args = args[:-1]
                if len(args) != 3:
                    return "Usage: sale <player> <team> <price> [confirm]  (player name may need quotes if it has spaces -- use underscores if unsure, e.g. Josh_Allen)"
                player = args[0].replace("_", " ")
                resolved, candidates = self._resolve_name(player)
                if resolved is None and len(candidates) > 1:
                    return "Multiple players match -- be more specific:\n" + "\n".join(
                        f"  {i+1}. {c}" for i, c in enumerate(candidates))
                if resolved:
                    player = resolved
                return self.cmd_sale(player, args[1], args[2], confirmed=confirmed)
            if cmd == "check":
                if len(args) < 1:
                    return "Usage: check <player>"
                return self.cmd_check(" ".join(args).replace("_", " "))
            if cmd == "targets":
                return self.cmd_targets()
            if cmd == "exact":
                if len(args) < 1:
                    return "Usage: exact <player> [price]"
                if len(args) >= 2 and args[-1].replace(".", "", 1).isdigit():
                    player = " ".join(args[:-1]).replace("_", " ")
                    return self.cmd_exact(player, float(args[-1]))
                return self.cmd_exact(" ".join(args).replace("_", " "))
            if cmd == "ladder":
                if len(args) < 1:
                    return "Usage: ladder <player>"
                return self.cmd_ladder(" ".join(args).replace("_", " "))
            if cmd == "paths":
                return self.cmd_paths()
            if cmd == "undo":
                return self.cmd_undo()
            if cmd == "save":
                if len(args) != 1:
                    return "Usage: save <name>"
                return self.cmd_save(args[0])
            if cmd == "load":
                if len(args) != 1:
                    return "Usage: load <name>"
                return self.cmd_load(args[0])
            if cmd == "search":
                if len(args) < 1:
                    return "Usage: search <partial_name>"
                return self.cmd_search(" ".join(args).replace("_", " "))
            if cmd == "last":
                return self.cmd_last()
            if cmd == "correct":
                if len(args) != 3:
                    return "Usage: correct <player> <team> <price>"
                return self.cmd_correct(args[0].replace("_", " "), args[1], args[2])
            if cmd == "market":
                return self.cmd_market()
            if cmd == "position":
                if len(args) != 1:
                    return "Usage: position <QB|RB|WR|TE>"
                return self.cmd_position(args[0])
            if cmd == "why":
                if len(args) < 1:
                    return "Usage: why <player>"
                return self.cmd_why(" ".join(args).replace("_", " "))
            if cmd == "prior":
                return self.cmd_prior()
            if cmd == "emergency":
                return self.cmd_emergency()
            if cmd == "help":
                return self.cmd_help()
            if cmd in ("quit", "exit"):
                return "__QUIT__"
            return f"Unknown command {cmd!r}. Type `help` for the command list."
        except Exception as e:
            self._log_error(f"dispatch({line!r})", e)
            return f"SOLVER_FAILURE / unexpected error (logged to {ERROR_LOG_PATH}): {e}"


def main():
    print("Live Auction MVP CLI -- type `help` for commands, `quit` to exit.")
    print("Building initial state from the confirmed pre-draft pool ($223 primary budget)...")
    t0 = time.time()
    cli = AuctionCLI()
    print(f"Ready in {time.time()-t0:.2f}s.\n")
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break
        result = cli.dispatch(line)
        if result == "__QUIT__":
            print("Goodbye.")
            break
        if result:
            print(result)
            print()


if __name__ == "__main__":
    main()
