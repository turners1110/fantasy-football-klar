"""Phase 3A item 7: team-specific counterfactual bid ceiling.

For a proposed (team, candidate, price): compares
  Scenario A -- team wins the candidate at `price`, then completes its
    roster OPTIMALLY from the remaining pool
  Scenario B -- team passes, then completes its roster OPTIMALLY from the
    (still-full) remaining pool
`hard_bid_ceiling` is the highest price where Scenario A's utility is
still >= Scenario B's.

DESIGN NOTE (the honest, bounded scope of this implementation): "complete
its roster optimally" is answered two ways here, per the phase-3A
instruction to use the exact solver "where practical" and approximate for
simulation speed elsewhere:
  - `greedy_complete_roster` -- a fast, deterministic greedy fill (best
    remaining player at each still-needed position, by points, capped by
    a soft $1-per-slot budget check). Cached per (roster signature, pool
    signature, slots) so repeated bid-grid evaluations for the same
    candidate/state don't re-run the fill. This is what actually drives
    live bidding -- calling PuLP for every bid-loop iteration across a
    200-seed simulation (thousands of bid evaluations per auction) is not
    practical.
  - `auction_model.exact_roster_solver.solve_exact_roster` -- the real
    ILP solver, used ONLY for offline validation (see
    scripts/build_counterfactual_approximation_error.py) to measure how
    far the fast greedy approximation is from the true optimum on a
    sample of real auction states, per "compare the fast approximation
    against exact solves. Report approximation error."
KNOWN SIMPLIFICATION: greedy_complete_roster ignores price competition
from OTHER teams for the same future players (it assumes any remaining
pool player is available to this team for the roster-completion estimate)
-- a real game-theoretic solve would model rival bidding too. This is
documented, not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config_bridge as cfg
from .cash_value import marginal_dollar_value
from .legal_lineup import build_production_lineup

REQUIRED_STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
FLEX_ELIGIBLE = ("RB", "WR", "TE")

_completion_cache: dict = {}


def _roster_signature(roster: list[tuple]) -> tuple:
    return tuple(sorted(e[0] for e in roster))


def greedy_complete_roster(
    current_roster: list[tuple], pool: dict, slots_to_fill: int, budget: float,
) -> list[tuple]:
    """Fast, cached, price-agnostic-beyond-feasibility roster completion:
    fills `slots_to_fill` more spots by taking the highest-projected-points
    remaining pool player at each still-needed required position first
    (QB/RB/WR/TE minimums), then FLEX, then pure bench depth by points --
    the same "meet minimums, then best-available" logic already proven
    optimal for a fixed roster in mock_draft.legal_lineup (see that
    module's build_production_lineup docstring for the exchange-argument
    proof this greedy order is optimal for POINTS; it is NOT claimed
    optimal in DOLLARS here, since price competition from rivals isn't
    modeled -- see module docstring)."""
    if slots_to_fill <= 0:
        return list(current_roster)

    cache_key = (_roster_signature(current_roster), frozenset(pool.keys()), slots_to_fill, round(budget, 2))
    cached = _completion_cache.get(cache_key)
    if cached is not None:
        return cached

    used_names = {e[0] for e in current_roster}
    counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    for entry in current_roster:
        counts[entry[1]] = counts.get(entry[1], 0) + 1

    by_pos: dict[str, list] = {"QB": [], "RB": [], "WR": [], "TE": []}
    for name, p in pool.items():
        if name in used_names or p.position not in by_pos:
            continue
        by_pos[p.position].append(p)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda p: p.projected_points, reverse=True)

    completion: list[tuple] = []
    remaining_slots = slots_to_fill
    remaining_budget = budget

    def _take(p) -> None:
        nonlocal remaining_slots, remaining_budget
        used_names.add(p.name)
        completion.append((p.name, p.position, 1.0, p.projected_points))
        counts[p.position] = counts.get(p.position, 0) + 1
        remaining_slots -= 1
        remaining_budget -= cfg.MIN_PRICE

    # Fill required minimums first.
    for pos, need in REQUIRED_STARTERS.items():
        while remaining_slots > 0 and remaining_budget >= cfg.MIN_PRICE * remaining_slots and counts.get(pos, 0) < need:
            candidates = [p for p in by_pos[pos] if p.name not in used_names]
            if not candidates:
                break
            _take(candidates[0])

    # Fill remaining slots with best-available RB/WR/TE (FLEX-eligible)
    # then absolute best-available of anything, for pure bench depth.
    flex_pool = sorted(
        (p for pos in FLEX_ELIGIBLE for p in by_pos[pos] if p.name not in used_names),
        key=lambda p: p.projected_points, reverse=True,
    )
    fi = 0
    while remaining_slots > 0 and remaining_budget >= cfg.MIN_PRICE * remaining_slots and fi < len(flex_pool):
        if flex_pool[fi].name not in used_names:
            _take(flex_pool[fi])
        fi += 1

    all_pool = sorted(
        (p for pos in by_pos for p in by_pos[pos] if p.name not in used_names),
        key=lambda p: p.projected_points, reverse=True,
    )
    ai = 0
    while remaining_slots > 0 and remaining_budget >= cfg.MIN_PRICE * remaining_slots and ai < len(all_pool):
        if all_pool[ai].name not in used_names:
            _take(all_pool[ai])
        ai += 1

    result = list(current_roster) + completion
    _completion_cache[cache_key] = result
    return result


def clear_cache() -> None:
    _completion_cache.clear()


@dataclass
class CounterfactualResult:
    utility_with_player: float
    utility_after_pass: float
    marginal_utility: float
    future_budget_cost: float


def counterfactual_marginal_utility(
    team, candidate, price: float, pool: dict,
) -> CounterfactualResult:
    """team: mock_draft.models.Team. candidate: mock_draft.models.Player.
    pool: dict[name -> Player] of players still available (candidate
    itself should be IN this dict, as it is in resolve_bid's `available`).
    """
    slots_after_buy = max(0, team.slots_needed - 1)
    # Both scenarios exclude the candidate from the completion pool: in
    # Scenario A the team already holds him (he's on roster_with, not in
    # the remaining pool); in Scenario B, "pass" means this specific
    # player is gone from this team's reach (someone else likely wins
    # him, or he's simply not re-offered) -- a bug caught in testing: an
    # earlier version left the candidate IN Scenario B's pool, so the
    # greedy fill picked him up there too "for free," making buying him
    # now look worthless even for a clearly above-replacement player.
    pool_minus_candidate = {n: p for n, p in pool.items() if n != candidate.name}

    roster_with = team.roster + [(candidate.name, candidate.position, price, candidate.projected_points)]
    completed_with = greedy_complete_roster(
        roster_with, pool_minus_candidate, slots_after_buy, team.budget_remaining - price,
    )
    utility_with = build_production_lineup(completed_with).total_roster_utility

    completed_without = greedy_complete_roster(
        team.roster, pool_minus_candidate, team.slots_needed, team.budget_remaining,
    )
    utility_after_pass = build_production_lineup(completed_without).total_roster_utility

    # Item 8: without a terminal cash value, greedy_complete_roster's own
    # scoring (points only, no price) makes marginal_utility CONSTANT
    # across every price -- a bug caught in testing (a $1 bid and an $80
    # bid on the same player produced identical marginal utility, so no
    # price could ever be a real "ceiling"). Crediting each scenario's
    # LEFTOVER budget at this team's current marginal-dollar-value rate
    # gives spending a real opportunity cost and makes the ceiling
    # meaningfully price-sensitive.
    dollar_rate = marginal_dollar_value(team, pool_minus_candidate)
    utility_with += max(0.0, team.budget_remaining - price) * dollar_rate
    utility_after_pass += team.budget_remaining * dollar_rate

    return CounterfactualResult(
        utility_with_player=utility_with,
        utility_after_pass=utility_after_pass,
        marginal_utility=utility_with - utility_after_pass,
        future_budget_cost=price,
    )


def hard_bid_ceiling(team, candidate, pool: dict, price_cap: float) -> dict:
    """Coarse-to-fine grid search (per the phase-3A speed instructions) for
    the highest legal price where Scenario A (win at that price, complete
    optimally) remains >= Scenario B (pass, complete optimally). Returns
    a dict with the ceiling and the grid trace for auditability."""
    if price_cap < cfg.MIN_PRICE:
        return {"hard_bid_ceiling": 0.0, "grid": []}

    coarse_points = sorted(set(
        [cfg.MIN_PRICE] + [p for p in (2, 5, 10, 20, 40, 80, 160) if p <= price_cap] + [price_cap]
    ))
    grid = []
    last_ok = None
    last_bad = None
    for p in coarse_points:
        res = counterfactual_marginal_utility(team, candidate, p, pool)
        ok = res.marginal_utility >= 0
        grid.append({"price": p, "marginal_utility": res.marginal_utility, "feasible": ok})
        if ok:
            last_ok = p
        elif last_bad is None:
            last_bad = p

    if last_ok is None:
        return {"hard_bid_ceiling": 0.0, "grid": grid}
    if last_bad is None or last_bad <= last_ok:
        return {"hard_bid_ceiling": float(last_ok), "grid": grid}

    # Refine between the highest OK price and the lowest bad price.
    lo, hi = last_ok, last_bad
    while hi - lo > 1:
        mid = (lo + hi) // 2
        res = counterfactual_marginal_utility(team, candidate, mid, pool)
        grid.append({"price": mid, "marginal_utility": res.marginal_utility, "feasible": res.marginal_utility >= 0})
        if res.marginal_utility >= 0:
            lo = mid
        else:
            hi = mid

    return {"hard_bid_ceiling": float(lo), "grid": grid}
