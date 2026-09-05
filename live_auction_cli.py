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
from auction_model import exact_roster_solver
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

    def __init__(self, budget_scenario: str = "primary", log_path: Path | None = DEFAULT_LOG_PATH):
        self.budget_scenario = budget_scenario
        self.players, teams, _ = load_confirmed_pool_and_teams(budget_scenario=budget_scenario)
        self.initial_state = self._build_initial_state(teams)
        self.log_path = log_path
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists():
                self.log_path.unlink()  # fresh session log each launch
        self.store = AuctionStateStore(self.initial_state, log_path=self.log_path)
        self.market_state = MarketAdjustmentState()
        # exact-solve cache: keyed by (state_sequence_number, player, test_price) -- see
        # cmd_exact / _invalidate_exact_cache. Never shown as current if the state
        # has moved on (Stage 2 STALE_EXACT_RESULT requirement).
        self._exact_cache: dict = {}
        self._exact_cache_sequence: int = self.store.state.sequence_number
        if self.log_path is not None:
            DEFAULT_INITIAL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            DEFAULT_INITIAL_STATE_PATH.write_text(json.dumps(self.initial_state.to_dict()))

    def _build_initial_state(self, teams) -> AuctionState:
        st = AuctionState(auction_id="cli-session", rules_version="v1", model_version="live-mvp-cli-v1", sam_team_id="Sam")
        for team_id, t in teams.items():
            roster = [{"player_id": n, "display_name": n, "position": p, "price": pr, "is_keeper": True, "projected_points": pts}
                      for n, p, pr, pts in t.roster]
            st.teams[team_id] = TeamState(team_id=team_id, budget_remaining=t.budget_remaining, roster=roster,
                                           keeper_ids={n for n, p, pr, pts in t.roster})
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
        rec = compute_recommended_bid(
            player=player, safety_adjusted_ceiling=max(1.0, marginal_value), legal_max_bid=sam.legal_max_bid,
            portfolio_feasibility_limit=None, confidence=6, live_expected_price=market["live_expected_price"],
        )

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

    def cmd_targets(self, n: int = 10) -> str:
        """Stage 1 hardened ranking: a multi-factor decision score
        (auction_engine.live_target_scoring), not raw marginal value.
        Position need is capped so it cannot alone outrank a stronger,
        cheaper player -- see live_target_scoring.py's docstring."""
        pool = self._remaining_pool()
        if not pool:
            return "No remaining players in the pool."
        rows = compute_live_sam_values(self._sam().roster, pool)
        sam = self._sam()
        needs = sam.legal_starting_needs()

        # remaining alternatives per position (for scarcity / tier-cliff detection)
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
            score = compute_target_score(
                player=r.player, position=r.position, marginal_value=r.marginal_value, expected_role=r.expected_role,
                live_expected_price=pre_draft_price, exact_or_approx_ceiling=max(1.0, r.marginal_value),
                hard_max=None, remaining_alternatives_count=remaining_alts, is_last_legal_alternative=is_last,
                price_confidence=0.5, position_need_score=raw_need, portfolio_paths_broken_if_missed=0,
            )
            scored.append(score)

        scored_sorted = sorted(scored, key=lambda s: -s.total_score)[:n]
        lines = [f"Top {len(scored_sorted)} live targets by decision score (not raw marginal value):"]
        for s in scored_sorted:
            rec = compute_recommended_bid(
                player=s.player, safety_adjusted_ceiling=max(1.0, s.team_specific_value), legal_max_bid=sam.legal_max_bid,
                portfolio_feasibility_limit=None, confidence=6, live_expected_price=s.team_specific_value - s.expected_surplus_at_price,
            )
            lines.append(f"  {s.player:20s} {s.position:3s} score={s.total_score:.3f} [{s.recommendation_class}] stop=${rec.recommended_final_bid:.0f}")
            lines.append(f"      surplus=${s.expected_surplus_at_price:.2f} start_gain=${s.starting_lineup_gain:.2f} "
                        f"team_value=${s.team_specific_value:.2f} role_prob={s.role_probability_score} "
                        f"scarcity={s.scarcity_score} tier_cliff_bonus={s.tier_cliff_bonus} "
                        f"alts_left={s.remaining_alternatives_count} price_conf={s.price_confidence} "
                        f"need_contrib={s.position_need_score} price_evid={s.price_evidence_score} "
                        f"bench_prob={s.bench_probability}")
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
        n_auction_spots = max(0, 15 - len(sam.roster))

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

        rec = compute_recommended_bid(
            player=player, safety_adjusted_ceiling=max(1.0, surplus + test_price if surplus > 0 else test_price * 0.7),
            legal_max_bid=sam.legal_max_bid, portfolio_feasibility_limit=None, confidence=8,
            live_expected_price=max(1.0, info.get("base_value", 1.0)),
        )

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
        old = self.store.state.sold_players.get(player)
        if old is None:
            return f"ERROR: {player!r} has no recorded sale to correct."
        pos = None
        for t in self.store.state.teams.values():
            for p in t.roster:
                if p["player_id"] == player:
                    pos = p["position"]
        try:
            self.store.correct_sale(player, player, pos or "UNKNOWN", team, price_f, None)
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
        rec = compute_recommended_bid(
            player=player, safety_adjusted_ceiling=max(1.0, r.marginal_value), legal_max_bid=sam.legal_max_bid,
            portfolio_feasibility_limit=None, confidence=6, live_expected_price=market["live_expected_price"],
        )
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
