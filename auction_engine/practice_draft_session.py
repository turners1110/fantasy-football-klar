"""V3 Part 13 -- true, interactive Practice Mode.

Not the old isolated-scenario sandbox (auction_engine/practice_scenarios.py,
which seeds a static before/after state and never runs a real draft).
This is a genuine turn-by-turn mock auction: Sam controls his own team
interactively; the other 11 teams are autonomous agents using the SAME
roster-aware dynamic valuation (mock_draft.valuation.compute_willingness)
already proven for Sam's own engine -- their willingness responds to
their OWN current roster saturation, open slots, and budget, not just a
fixed personality multiplier (Sam's addendum to Part 11, folded in here
too).

Design: every sale, whether won by Sam or an AI team, is committed
through the SAME real event-sourced engine (AuctionCLI.cmd_sale) a
production sale would use -- so the 16-player cap, legal-max-bid
reserve, protected-player refusal, undo, and save/load are ALL the
real, already-tested reducer logic, never reimplemented here. This
module's only job is nomination rotation and deciding WHO wins A
NOMINATION AT WHAT PRICE, using mock_draft's real per-team dynamic
ceiling -- then it hands that decision to cli.cmd_sale like any other
caller.

Isolation: uses its own AuctionCLI instance with its own log path
(PRACTICE_DRAFT_LOG_DIR), completely separate from both production
(live_auction_cli.DEFAULT_LOG_PATH) and the existing scenario sandbox
(auction_engine.practice_scenarios.PRACTICE_LOG_DIR).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mock_draft.archetypes import ARCHETYPE_NAMES, ARCHETYPES
from mock_draft.feasibility import check_roster_completion_feasibility
from mock_draft.legal_lineup import partial_lineup_value
from mock_draft.models import Player as MDPlayer, Team as MDTeam
from mock_draft.nomination import choose_nomination
from mock_draft import config_bridge as cfg

PRACTICE_DRAFT_LOG_DIR = Path(__file__).parent.parent / "outputs" / "auction_rebuild" / "live_v3" / "practice_draft"


def _practice_draft_log_path(session_id: str) -> Path:
    return PRACTICE_DRAFT_LOG_DIR / f"session_{session_id}.jsonl"


def _incremental_utility(team: MDTeam, candidate: MDPlayer, price: float) -> float:
    before = partial_lineup_value(team.roster)
    after = partial_lineup_value(team.roster + [(candidate.name, candidate.position, price, candidate.projected_points)])
    return after - before


@dataclass
class PendingNomination:
    player: str
    position: str
    nominator: str
    ai_current_price: float
    ai_leading_team: str | None
    ai_second_price: float
    sam_recommended_stop: float
    sam_legal_max_bid: float


@dataclass
class PracticeReviewEntry:
    player: str
    position: str
    price_paid: float
    recommended_stop_at_purchase: float
    surplus_or_overpay: float  # negative = overpaid relative to the stop
    best_alternative_at_purchase: str | None


class PracticeDraftSession:
    """One complete, real, interactive mock auction. Sam calls
    `pending_nomination()` to see what's up for bid, then `sam_pass()` or
    `sam_bid(amount)` to act; the session resolves the sale through the
    real event engine and advances to the next nomination automatically
    (auto-resolving any AI-vs-AI nominations where Sam has already
    passed on his turn to nominate, until it's Sam's turn to react
    again or the draft completes)."""

    def __init__(self, session_id: str, seed: int = 909001):
        from live_auction_cli import AuctionCLI  # local import: avoid import cycle

        self.session_id = session_id
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.cli = AuctionCLI(log_path=_practice_draft_log_path(session_id), resume=False)
        self.status = "IN_PROGRESS"  # IN_PROGRESS | PAUSED | COMPLETE
        self.pending: PendingNomination | None = None
        self.review_log: list[PracticeReviewEntry] = []
        self.marginal_value_history: list[dict] = []  # snapshots for the post-draft review
        self.nomination_count = 0

        team_ids = sorted(self.cli.store.state.teams.keys())
        self.ai_team_ids = [t for t in team_ids if t != "Sam"]
        # Fixed, transparent, distinct archetype per AI team -- "owner
        # diversity" per Part 11, reused unchanged from mock_draft.
        self.archetype_by_team = {
            tid: ARCHETYPE_NAMES[i % len(ARCHETYPE_NAMES)] for i, tid in enumerate(self.ai_team_ids)
        }
        self.nomination_order = team_ids[:]  # round robin, Sam included
        self.rng.shuffle(self.nomination_order)
        self._nom_pointer = 0
        # Players no legal bidder (AI or Sam) will take at any price this
        # session -- excluded from future nomination choices so the
        # draft can always make forward progress toward completion
        # rather than looping on an unsellable player forever. This is a
        # practice-session-local bookkeeping list, not a real auction
        # event (an unsold player stays genuinely available in the real
        # pool/event log; this only affects what THIS session nominates
        # next).
        self._dead_players: set[str] = set()

        self._advance_to_next_sam_decision()

    # ---- internal helpers -------------------------------------------------

    def _build_md_team(self, team_id: str) -> MDTeam:
        t = self.cli.store.state.teams[team_id]
        roster = [(p["player_id"], p["position"], p["price"], p.get("projected_points", 0.0)) for p in t.roster]
        # V3.1 REPAIR 5: pass the REAL official protected-but-unlisted
        # occupancy through to MDTeam's explicit override -- without
        # this, every AI team (and Sam) looked 2 (or 1, for Brad/Reid)
        # slots more open than they officially are, understating
        # max_bid_cap's reserve and slots_needed's feasibility gate
        # throughout the whole practice draft. This is the likely direct
        # cause of the 106/113 stall found in the prior pass: teams
        # thought they had more room than they legally did, so budgets/
        # feasibility drifted out of sync with the real 16-slot cap as
        # the draft progressed.
        return MDTeam(
            name=team_id, budget_remaining=t.budget_remaining, roster=roster,
            archetype=self.archetype_by_team.get(team_id, "value_purist"),
            protected_but_unlisted=t.college_rights_count,
        )

    def _build_md_pool(self) -> dict[str, MDPlayer]:
        # V3.1 GATE 6 FIX: was hardcoded tier=1 for every player
        # regardless of their real tier -- now reads the same canonical
        # self.cli.players[name].tier source api_targets/_tier_label
        # already use, so tier-based archetype behavior (tier_cliff
        # bonuses, etc.) during a practice draft matches the real
        # player's actual tier, not a flat placeholder.
        pool = {}
        for name, info in self.cli.store.state.available_pool.items():
            real_player = self.cli.players.get(name)
            tier = real_player.tier if real_player is not None else 1
            tier_size = real_player.tier_size if real_player is not None else 1
            tier_rank = real_player.tier_rank if real_player is not None else 1
            pool[name] = MDPlayer(
                name=name, position=info["position"], base_value=max(1.0, info.get("base_value", 1.0)),
                tier=tier, tier_size=tier_size, tier_rank=tier_rank, projected_points=info.get("projected_points", 0.0),
            )
        return pool

    def _ai_ceiling(self, team_id: str, candidate: MDPlayer, pool: dict, draft_progress: float) -> float | None:
        """A single-shot (not iterative-ascending) willingness ceiling for
        one AI team on one candidate -- gated by the same feasibility and
        zero-utility checks mock_draft.auction.resolve_bid uses, so an AI
        team can never be handed a player it couldn't legally or usefully
        take. Returns None if this team cannot legally take the candidate
        at any price."""
        from mock_draft.valuation import compute_willingness
        team = self._build_md_team(team_id)
        feas = check_roster_completion_feasibility(
            team.roster, team.budget_remaining, team.slots_needed, pool,
            candidate_player=candidate, candidate_price=float(cfg.MIN_PRICE), position_max=None,
        )
        if not feas.is_feasible:
            return None
        willingness = compute_willingness(team, candidate, self.rng, draft_progress, available=pool)
        cap = team.max_bid_cap()
        max_can_pay = min(willingness, cap)
        if _incremental_utility(team, candidate, max_can_pay) <= 0:
            max_can_pay = min(max_can_pay, float(cfg.MIN_PRICE))
        feas2 = check_roster_completion_feasibility(
            team.roster, team.budget_remaining, team.slots_needed, pool,
            candidate_player=candidate, candidate_price=max_can_pay, position_max=None,
        )
        if not feas2.is_feasible:
            return None
        return max(float(cfg.MIN_PRICE), max_can_pay)

    def _draft_progress(self) -> float:
        total_slots = 16 * 12
        filled = sum(len(t.roster) + t.college_rights_count for t in self.cli.store.state.teams.values())
        return min(1.0, filled / total_slots)

    def _sam_recommended_stop(self, player: str, position: str) -> float:
        from auction_engine.live_values import compute_live_sam_values
        sam = self.cli._sam()
        info = self.cli.store.state.available_pool[player]
        rows = compute_live_sam_values(sam.roster, {player: info})
        marginal_value = rows[0].marginal_value if rows else 0.0
        expected_role = rows[0].expected_role if rows else "unknown"
        governed = self.cli._governed_ceiling(player, position, marginal_value, expected_role, max(1.0, info.get("base_value", 1.0)))
        return governed.dollar_ceiling

    def _snapshot_marginal_values(self):
        from auction_engine.live_values import compute_live_sam_values
        sam = self.cli._sam()
        pool = self.cli.store.state.available_pool
        rows = compute_live_sam_values(sam.roster, pool)
        by_pos = {}
        for pos in ("QB", "RB", "WR", "TE"):
            vals = [r.marginal_value for r in rows if r.position == pos]
            by_pos[pos] = round(sum(vals) / len(vals), 2) if vals else None
        self.marginal_value_history.append({"nomination_count": self.nomination_count, "avg_marginal_value_by_position": by_pos})

    def _resolve_ai_only_nomination(self, player: str, position: str):
        """Resolves a nomination WITHOUT Sam as a bidder (used when the
        pool is empty for Sam, i.e. Sam's roster is already full) -- the
        highest AI ceiling wins at the second-highest AI ceiling + $1
        (or $1 uncontested), same second-price rule used everywhere else
        in this repair."""
        pool = self._build_md_pool()
        candidate = pool[player]
        progress = self._draft_progress()
        ceilings = []
        for tid in self.ai_team_ids:
            c = self._ai_ceiling(tid, candidate, pool, progress)
            if c is not None:
                ceilings.append((tid, c))
        if not ceilings:
            self._dead_players.add(player)  # no legal bidder at all -- stop re-nominating it this session
            return
        ceilings.sort(key=lambda x: -x[1])
        winner_id, winner_ceiling = ceilings[0]
        second = ceilings[1][1] if len(ceilings) > 1 else 0.0
        price = min(winner_ceiling, round(second + 1) if second > 0 else 1.0)
        self.cli.cmd_sale(player, winner_id, str(int(price)), confirmed=True)

    def _advance_to_next_sam_decision(self):
        """Auto-resolves AI-vs-AI nominations (or nominations where Sam's
        own roster is already full) until either Sam has a real decision
        to make or the draft is complete."""
        while True:
            live_pool_names = [n for n in self.cli.store.state.available_pool if n not in self._dead_players]
            all_teams_full = all(t.open_slots == 0 for t in self.cli.store.state.teams.values())
            if not live_pool_names or all_teams_full:
                self.status = "COMPLETE"
                self.pending = None
                return

            sam = self.cli._sam()
            pool_names = list(self.cli.store.state.available_pool.keys())
            nominator = self.nomination_order[self._nom_pointer % len(self.nomination_order)]
            self._nom_pointer += 1

            md_pool_full = self._build_md_pool()
            md_pool = {n: v for n, v in md_pool_full.items() if n not in self._dead_players} or md_pool_full
            md_teams = {tid: self._build_md_team(tid) for tid in self.cli.store.state.teams.keys()}
            player = choose_nomination(nominator, md_teams, md_pool, self.rng)
            info = self.cli.store.state.available_pool[player]
            position = info["position"]
            self.nomination_count += 1

            if sam.open_slots == 0:
                # Sam's roster is already full -- this nomination can
                # never involve Sam; resolve it purely among the AI teams.
                self._resolve_ai_only_nomination(player, position)
                if self.nomination_count % 8 == 0:
                    self._snapshot_marginal_values()
                continue

            candidate = md_pool[player]
            progress = self._draft_progress()
            ceilings = []
            for tid in self.ai_team_ids:
                c = self._ai_ceiling(tid, candidate, md_pool, progress)
                if c is not None:
                    ceilings.append((tid, c))
            ceilings.sort(key=lambda x: -x[1])
            leading_team = ceilings[0][0] if ceilings else None
            leading_ceiling = ceilings[0][1] if ceilings else 0.0
            second_ceiling = ceilings[1][1] if len(ceilings) > 1 else 0.0
            ai_current_price = min(leading_ceiling, round(second_ceiling + 1)) if second_ceiling > 0 else (leading_ceiling if leading_ceiling else 1.0)

            stop = self._sam_recommended_stop(player, position)
            self.pending = PendingNomination(
                player=player, position=position, nominator=nominator,
                ai_current_price=round(ai_current_price, 0) if leading_team else 0.0,
                ai_leading_team=leading_team, ai_second_price=round(second_ceiling, 0),
                sam_recommended_stop=round(stop, 0), sam_legal_max_bid=sam.legal_max_bid,
            )
            if self.nomination_count % 8 == 0:
                self._snapshot_marginal_values()
            return

    # ---- public API ---------------------------------------------------

    def pending_nomination(self) -> dict | None:
        if self.pending is None:
            return None
        p = self.pending
        return {
            "player": p.player, "position": p.position, "nominator": p.nominator,
            "ai_current_price": p.ai_current_price, "ai_leading_team": p.ai_leading_team,
            "sam_recommended_stop": p.sam_recommended_stop, "sam_legal_max_bid": p.sam_legal_max_bid,
        }

    def sam_pass(self) -> dict:
        if self.pending is None:
            return {"status": self.status}
        p = self.pending
        if p.ai_leading_team:
            price = max(1, int(p.ai_current_price))
            self.cli.cmd_sale(p.player, p.ai_leading_team, str(price), confirmed=True)
        else:
            # Nobody (AI or Sam) wanted this player at all -- stop
            # re-nominating it this session so the draft keeps making
            # forward progress (matches PLAYER_UNSOLD semantics; the
            # player remains genuinely available in the real pool).
            self._dead_players.add(p.player)
        self.pending = None
        self._advance_to_next_sam_decision()
        return {"status": self.status}

    def sam_bid(self, amount: float) -> dict:
        if self.pending is None:
            return {"status": self.status, "error": "no pending nomination"}
        p = self.pending
        amount = int(amount)
        best_alt = self._best_alternative(p.player, p.position)
        if amount > p.ai_current_price - 1 and (p.ai_leading_team is None or amount > (p.ai_second_price if p.ai_second_price else 0)):
            # Sam wins -- price is the standard second-price rule: one
            # dollar above the best OTHER bid (the leading AI ceiling if
            # Sam's bid clears it, else uncontested at $1), never above
            # Sam's own stated bid or legal max.
            other_best = p.ai_current_price - 1 if p.ai_leading_team else 0
            price = max(1, min(amount, max(other_best + 1, 1), int(p.sam_legal_max_bid)))
            result = self.cli.cmd_sale(p.player, "Sam", str(price), confirmed=True)
            self.review_log.append(PracticeReviewEntry(
                player=p.player, position=p.position, price_paid=price,
                recommended_stop_at_purchase=p.sam_recommended_stop,
                surplus_or_overpay=round(p.sam_recommended_stop - price, 2),
                best_alternative_at_purchase=best_alt,
            ))
        elif p.ai_leading_team:
            self.cli.cmd_sale(p.player, p.ai_leading_team, str(max(1, int(p.ai_current_price))), confirmed=True)
        # else: Sam's bid didn't beat anyone and there's no AI leader --
        # Sam wins uncontested at $1 (matches everywhere else's rule).
        else:
            self.cli.cmd_sale(p.player, "Sam", "1", confirmed=True)
        self.pending = None
        self._advance_to_next_sam_decision()
        return {"status": self.status}

    def _best_alternative(self, exclude_player: str, position: str) -> str | None:
        pool = self.cli.store.state.available_pool
        candidates = [(n, v) for n, v in pool.items() if v["position"] == position and n != exclude_player]
        if not candidates:
            return None
        candidates.sort(key=lambda x: -x[1].get("base_value", 0))
        return candidates[0][0]

    def undo(self) -> str:
        msg = self.cli.cmd_undo()
        # Undo can revert the pointer/pending state inconsistently since
        # this session tracks its own nomination cursor separately from
        # the event log -- simplest correct fix: recompute the next
        # decision fresh from the (now-reverted) real state.
        self.pending = None
        self._advance_to_next_sam_decision()
        return msg

    def post_draft_review(self) -> dict:
        sam = self.cli._sam()
        total_spend = sum(p["price"] for p in sam.roster if not p.get("is_keeper"))
        purchases = [p for p in sam.roster if not p.get("is_keeper")]
        return {
            "status": self.status,
            "sam_roster": [{"player": p["display_name"], "position": p["position"], "price": p["price"],
                            "is_keeper": bool(p.get("is_keeper"))} for p in sam.roster],
            "total_spend_on_purchases": total_spend,
            "unused_cash": sam.budget_remaining,
            "purchases_vs_recommended_stops": [
                {"player": e.player, "position": e.position, "price_paid": e.price_paid,
                 "recommended_stop_at_purchase": e.recommended_stop_at_purchase,
                 "surplus_or_overpay": e.surplus_or_overpay,
                 "best_alternative_at_purchase": e.best_alternative_at_purchase}
                for e in self.review_log
            ],
            "missed_bargains_or_overpays": [
                {"player": e.player, "verdict": "OVERPAID" if e.surplus_or_overpay < 0 else "BARGAIN",
                 "amount": abs(e.surplus_or_overpay)}
                for e in self.review_log if abs(e.surplus_or_overpay) > 5
            ],
            "positional_marginal_value_evolution": self.marginal_value_history,
            "total_league_sales": len(self.cli.store.state.sold_players),
        }
