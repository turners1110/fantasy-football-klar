"""Shared positional-feasibility gate -- phase 2B.

Phase 2's smoke test proved the gap this closes: with no check on whether
a purchase still leaves a legal 1QB/2RB/2WR/1TE/3FLEX/15-total path, teams
bought themselves out of a position entirely (one seed-0 team ended with
11 WR, 3 RB, 1 QB, 0 TE -- 15 legal-COUNT players, an illegal roster).

This module answers exactly one question, before and after every
purchase: "does a legal final roster still exist from here?" It does not
decide WHO to bid on or HOW MUCH -- callers (auction.py) decide that and
consult this function as a hard gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config_bridge as cfg

REQUIRED_STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
FLEX_SLOTS = 3
FLEX_ELIGIBLE = ("RB", "WR", "TE")
TOTAL_ROSTER_SIZE = cfg.REQUIRED_ROSTER_SIZE  # 15
# Minimum RB+WR+TE bodies a legal roster needs in total (2+2+1+3).
MIN_SKILL_POSITION_TOTAL = REQUIRED_STARTERS["RB"] + REQUIRED_STARTERS["WR"] + REQUIRED_STARTERS["TE"] + FLEX_SLOTS

# Configurable soft roster-position caps -- see
# outputs/auction_rebuild/audit/historical_roster_position_counts.csv.
# Real 2025 final rosters ranged QB 1-4 (median 2, p90 ~2.9), TE 1-3
# (median 2, p90 3, max 3). The phase-2B spec suggested a QB max of 2 as a
# placeholder default, but that contradicts observed history -- one real
# team legitimately carried 4 QBs in 2025 -- so history overrides the
# placeholder per the spec's own "only when supported by league history"
# rule. TE max=3 matches both the suggested default and the observed
# historical max. Disabled by default: callers opt in via
# enable_position_max=True (see auction.py), and the smoke-test comparison
# runs the simulator with this both on and off.
DEFAULT_POSITION_MAX = {"QB": 4, "TE": 3}


@dataclass
class FeasibilityResult:
    is_feasible: bool
    failure_reason: str | None
    required_position_counts: dict
    available_position_counts: dict
    minimum_completion_cost: float
    remaining_flex_eligible_count: int
    # extra diagnostics -- not in the caller's minimal suggested return
    # shape, but needed by the urgency/scarcity reporting phase 2B also
    # requires, so exposed on the same result rather than a second call.
    qb_deficit: int = 0
    rb_deficit: int = 0
    wr_deficit: int = 0
    te_deficit: int = 0
    flex_deficit: int = 0
    additional_required_purchases: int = 0


def _position_counts(roster: list[tuple]) -> dict:
    counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    for entry in roster:
        pos = entry[1]
        counts[pos] = counts.get(pos, 0) + 1
    return counts


def check_roster_completion_feasibility(
    current_roster: list[tuple],
    remaining_budget: float,
    remaining_slots: int,
    available_players: dict,
    candidate_player=None,
    candidate_price: float | None = None,
    position_max: dict | None = None,
) -> FeasibilityResult:
    """current_roster: list of (name, position, price, points) -- the
    team's roster BEFORE the proposed purchase. available_players: dict of
    name -> Player still purchasable (already excludes keepers/college-
    rights holds and anyone already sold -- this function does not
    re-derive eligibility, it trusts the pool it's handed, but still
    rejects a candidate not present in that pool as PLAYER_INELIGIBLE).
    candidate_player/candidate_price: the purchase being evaluated, or
    None to check whether the CURRENT state (no new purchase) still has a
    legal path -- used for the after-sale and endgame-nomination checks.
    position_max: optional {position: max_count} soft cap (see
    DEFAULT_POSITION_MAX) -- disabled (None) unless a caller opts in.
    """
    roster_names = {entry[0] for entry in current_roster}

    if candidate_player is not None:
        if candidate_player.name in roster_names:
            return FeasibilityResult(
                False, "DUPLICATE_PLAYER", dict(REQUIRED_STARTERS), {}, 0.0, 0,
            )
        if candidate_player.name not in available_players:
            return FeasibilityResult(
                False, "PLAYER_INELIGIBLE", dict(REQUIRED_STARTERS), {}, 0.0, 0,
            )
        if position_max and candidate_player.position in position_max:
            current_count = sum(1 for e in current_roster if e[1] == candidate_player.position)
            if current_count + 1 > position_max[candidate_player.position]:
                return FeasibilityResult(
                    False, "POSITION_CAP_EXCEEDED", dict(REQUIRED_STARTERS), {}, 0.0, 0,
                )
        new_roster = current_roster + [(
            candidate_player.name, candidate_player.position,
            candidate_price if candidate_price is not None else 0.0,
            candidate_player.projected_points,
        )]
        new_budget = remaining_budget - (candidate_price if candidate_price is not None else 0.0)
        pool_after = {n: p for n, p in available_players.items() if n != candidate_player.name}
    else:
        new_roster = current_roster
        new_budget = remaining_budget
        pool_after = available_players

    # Trust the caller's remaining_slots as ground truth (it may reflect
    # state -- e.g. Team.slots_needed -- the roster list alone can't
    # always reconstruct) rather than re-deriving from len(new_roster);
    # a purchase consumes exactly one slot.
    new_slots_remaining = remaining_slots - (1 if candidate_player is not None else 0)
    if new_slots_remaining < 0:
        return FeasibilityResult(
            False, "ROSTER_FULL", dict(REQUIRED_STARTERS), {}, 0.0, 0,
        )

    counts = _position_counts(new_roster)
    qb_deficit = max(0, REQUIRED_STARTERS["QB"] - counts["QB"])
    rb_deficit = max(0, REQUIRED_STARTERS["RB"] - counts["RB"])
    wr_deficit = max(0, REQUIRED_STARTERS["WR"] - counts["WR"])
    te_deficit = max(0, REQUIRED_STARTERS["TE"] - counts["TE"])
    # FLEX can only be filled by RB/WR/TE bodies IN EXCESS of each
    # position's own required minimum -- a surplus of WRs can never
    # substitute for a missing TE, so this must be computed from the
    # per-position surplus, not the raw RB+WR+TE total (a bug caught in
    # testing: 3 RB + 10 WR + 0 TE looked "feasible" under a raw-total
    # check because 13 >= 8, even though 0 TE makes the roster illegal).
    rb_surplus = max(0, counts["RB"] - REQUIRED_STARTERS["RB"])
    wr_surplus = max(0, counts["WR"] - REQUIRED_STARTERS["WR"])
    te_surplus = max(0, counts["TE"] - REQUIRED_STARTERS["TE"])
    flex_pool_deficit = max(0, FLEX_SLOTS - (rb_surplus + wr_surplus + te_surplus))
    additional_required = qb_deficit + rb_deficit + wr_deficit + te_deficit + flex_pool_deficit

    available_position_counts = {
        pos: sum(1 for p in pool_after.values() if p.position == pos)
        for pos in ("QB", "RB", "WR", "TE")
    }
    remaining_flex_eligible_count = sum(available_position_counts[p] for p in FLEX_ELIGIBLE)

    def _infeasible(reason: str, cost: float = 0.0) -> FeasibilityResult:
        return FeasibilityResult(
            False, reason, dict(REQUIRED_STARTERS), available_position_counts,
            cost, remaining_flex_eligible_count,
            qb_deficit, rb_deficit, wr_deficit, te_deficit, flex_pool_deficit, additional_required,
        )

    # Not enough empty slots left to even fit the still-required pieces.
    if new_slots_remaining < additional_required:
        return _infeasible("POSITIONAL_INFEASIBILITY")

    # Not enough eligible players left in the pool at a position still needed.
    if qb_deficit > 0 and available_position_counts["QB"] < qb_deficit:
        return _infeasible("POSITIONAL_INFEASIBILITY")
    if rb_deficit > 0 and available_position_counts["RB"] < rb_deficit:
        return _infeasible("POSITIONAL_INFEASIBILITY")
    if wr_deficit > 0 and available_position_counts["WR"] < wr_deficit:
        return _infeasible("POSITIONAL_INFEASIBILITY")
    if te_deficit > 0 and available_position_counts["TE"] < te_deficit:
        return _infeasible("POSITIONAL_INFEASIBILITY")
    if flex_pool_deficit > 0 and remaining_flex_eligible_count < flex_pool_deficit:
        return _infeasible("POSITIONAL_INFEASIBILITY")

    # Cash: every remaining slot (required or not) needs at least $1 --
    # the auction never sells below MIN_PRICE, so this is the true floor.
    minimum_completion_cost = cfg.MIN_PRICE * new_slots_remaining
    if new_budget < minimum_completion_cost - 1e-9:
        return _infeasible("INSUFFICIENT_RESERVE", minimum_completion_cost)

    return FeasibilityResult(
        True, None, dict(REQUIRED_STARTERS), available_position_counts,
        minimum_completion_cost, remaining_flex_eligible_count,
        qb_deficit, rb_deficit, wr_deficit, te_deficit, flex_pool_deficit, additional_required,
    )


def position_urgency(current_roster: list[tuple], remaining_slots: int) -> dict:
    """Per the phase-2B spec: qb/rb/wr/te/flex deficits, slots_remaining,
    position_deadline (how many MORE optional/non-required purchases can
    happen before a required position becomes mandatory every remaining
    pick), and scarcity_risk (0-1, how tight the margin is)."""
    counts = _position_counts(current_roster)
    qb_deficit = max(0, REQUIRED_STARTERS["QB"] - counts["QB"])
    rb_deficit = max(0, REQUIRED_STARTERS["RB"] - counts["RB"])
    wr_deficit = max(0, REQUIRED_STARTERS["WR"] - counts["WR"])
    te_deficit = max(0, REQUIRED_STARTERS["TE"] - counts["TE"])
    # Same surplus-based FLEX accounting as check_roster_completion_feasibility --
    # a WR surplus can never cover a missing TE.
    rb_surplus = max(0, counts["RB"] - REQUIRED_STARTERS["RB"])
    wr_surplus = max(0, counts["WR"] - REQUIRED_STARTERS["WR"])
    te_surplus = max(0, counts["TE"] - REQUIRED_STARTERS["TE"])
    flex_deficit = max(0, FLEX_SLOTS - (rb_surplus + wr_surplus + te_surplus))
    required_remaining = qb_deficit + rb_deficit + wr_deficit + te_deficit + flex_deficit
    # How many more "optional" (non-required-position) purchases this team
    # can still make before every remaining slot must go to a required
    # position. 0 means the NEXT purchase must be required-position.
    position_deadline = max(0, remaining_slots - required_remaining)
    scarcity_risk = 0.0 if remaining_slots <= 0 else min(1.0, required_remaining / remaining_slots)
    return {
        "qb_deficit": qb_deficit, "rb_deficit": rb_deficit, "wr_deficit": wr_deficit,
        "te_deficit": te_deficit, "flex_deficit": flex_deficit,
        "slots_remaining": remaining_slots, "position_deadline": position_deadline,
        "scarcity_risk": scarcity_risk,
    }
