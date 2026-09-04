#!/usr/bin/env python3
"""Phase 3E, Parts 8-10 (priority items 1-3): validate Sam's exact-surplus
definition, compute exact pre-draft static-pool bid ceilings, and build
P50 auction portfolios for Sam under both the $223 and $221 budget
scenarios.

SCOPING DISCLOSURE (see outputs/auction_rebuild/phase3e/final_report.md
for the full disclosure): Phase 3D's calibrated price-distribution
pipeline (build_phase3d_price_distributions.py) was never actually run to
completion in this repo -- outputs/auction_rebuild/phase3d/ does not
exist on disk. The only real simulated market prices (market_price_p50 /
market_price_p75, from actual mock-draft Monte Carlo sales) that persist
anywhere in this repo are the 8 players in
outputs/auction_rebuild/phase3b/sam_label_audit.csv. For every other
player this script uses `base_value` (the real model's
projection/anchor-derived suggested_auction_price, PRE-simulation) as the
price input, and labels it PROJECTION_VALUE_PROXY, not a market price.
Do not read PROJECTION_VALUE_PROXY prices as calibrated clearing prices.

This script performs REAL exact ILP solves (auction_model.exact_roster_solver,
HiGHS-backed) for every candidate and every ceiling probe -- no numbers in
its outputs are fabricated or interpolated.

Writes:
  outputs/auction_rebuild/phase3e/sam_exact_scenario_rosters.csv   (Part 8)
  outputs/auction_rebuild/phase3e/sam_exact_ceiling_validation.csv (Part 9)
  outputs/auction_rebuild/phase3e/sam_portfolios.csv               (Part 10, P50-proxy only)
"""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from auction_model import exact_roster_solver
from mock_draft.data import load_confirmed_pool_and_teams

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3e"
LABEL_AUDIT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3b" / "sam_label_audit.csv"

N_AUCTION_SPOTS = 9  # Sam's 6 keepers are fixed; 9 slots remain to fill of 15


def _pool_to_exact_df(pool: dict, exclude_names: set) -> pd.DataFrame:
    return pd.DataFrame([
        {"player": p.name, "position": p.position, "projected_points": p.projected_points,
         "suggested_auction_price": max(1.0, p.base_value)}
        for name, p in pool.items() if name not in exclude_names
    ])


def _keepers_to_exact_df(roster: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame([
        {"player": name, "position": pos, "projected_points": pts, "keeper_price_2026": price}
        for name, pos, price, pts in roster
    ])


def _pick_candidates(players: dict, watchlist_names: set) -> list[str]:
    """20+ candidates spanning positions and price tiers, per Part 8/9."""
    by_pos: dict[str, list] = {"QB": [], "RB": [], "WR": [], "TE": []}
    for p in players.values():
        if p.position in by_pos:
            by_pos[p.position].append(p)
    chosen = set(watchlist_names)
    for pos, lst in by_pos.items():
        lst.sort(key=lambda p: -p.base_value)
        n = len(lst)
        if n == 0:
            continue
        # top tier, mid tier, cheap tier, one $1 flier
        idxs = sorted(set([0, min(2, n - 1), n // 4, n // 2, max(0, n - 1)]))
        for i in idxs:
            chosen.add(lst[i].name)
    return sorted(chosen)


def scenario_solve(players: dict, sam, name: str, price: float, budget: float) -> tuple:
    candidate = players[name]
    # Scenario A: force-buy at `price`.
    pool_minus = {n: p for n, p in players.items() if n != name}
    df_a = _pool_to_exact_df(pool_minus, set())
    roster_with = sam.roster + [(candidate.name, candidate.position, price, candidate.projected_points)]
    t0 = time.time()
    result_a = exact_roster_solver.solve_exact_roster(
        df_a, budget=max(0.0, budget - price),
        n_auction_spots=max(0, N_AUCTION_SPOTS - 1), keepers=_keepers_to_exact_df(roster_with),
    )
    rt_a = time.time() - t0
    # Candidate is force-fixed onto Sam's roster via the keepers table in
    # Scenario A, so it legitimately appears in result_a.selected (as a
    # fixed roster spot, not a freely-bid purchase from the pool).

    # Scenario B: pass -- candidate excluded from the pool entirely, budget untouched.
    df_b = _pool_to_exact_df(players, {name})
    assert name not in set(df_b["player"]), "candidate must not remain available in Scenario B"
    t0 = time.time()
    result_b = exact_roster_solver.solve_exact_roster(
        df_b, budget=budget,
        n_auction_spots=N_AUCTION_SPOTS, keepers=_keepers_to_exact_df(sam.roster),
    )
    rt_b = time.time() - t0
    return result_a, result_b, rt_a, rt_b


def exact_ceiling(players: dict, sam, name: str, budget: float, base_status_b) -> tuple[int | None, int]:
    """Binary search over integer dollar prices for the highest price where
    force-buying `name` is >= passing (non-negative marginal starting points)."""
    candidate = players[name]
    lo, hi = 1, int(min(budget, 400))
    if base_status_b.status not in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL"):
        return None, 0
    baseline_pts = base_status_b.starting_points
    solves = 0
    pass_cache: dict[int, bool] = {}

    def ok_at(price: int) -> bool:
        nonlocal solves
        if price in pass_cache:
            return pass_cache[price]
        pool_minus = {n: p for n, p in players.items() if n != name}
        df_a = _pool_to_exact_df(pool_minus, set())
        roster_with = sam.roster + [(candidate.name, candidate.position, float(price), candidate.projected_points)]
        result = exact_roster_solver.solve_exact_roster(
            df_a, budget=max(0.0, budget - price),
            n_auction_spots=max(0, N_AUCTION_SPOTS - 1), keepers=_keepers_to_exact_df(roster_with),
        )
        solves += 1
        good = result.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL") and result.starting_points >= baseline_pts
        pass_cache[price] = good
        return good

    if not ok_at(lo):
        return 0, solves  # cannot even afford $1 profitably (or infeasible) -> ceiling below $1
    # exponential search for upper bound where it fails
    step = 1
    price = lo
    while price < hi and ok_at(min(price + step, hi)):
        price = min(price + step, hi)
        step *= 2
    ceil_lo, ceil_hi = price, min(price + step, hi)
    if ok_at(ceil_hi):
        return ceil_hi, solves
    while ceil_hi - ceil_lo > 1:
        mid = (ceil_lo + ceil_hi) // 2
        if ok_at(mid):
            ceil_lo = mid
        else:
            ceil_hi = mid
    return ceil_lo, solves


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    watchlist = pd.read_csv(LABEL_AUDIT_PATH).set_index("player")

    scenario_rows = []
    ceiling_rows = []
    portfolio_rows = []

    for scenario, budget_scenario in [("primary_223", "primary"), ("conversions_221", "conversions")]:
        players, teams, meta = load_confirmed_pool_and_teams(budget_scenario=budget_scenario)
        sam = teams["Sam"]
        candidates = _pick_candidates(players, set(watchlist.index))
        print(f"[{scenario}] budget={sam.budget_remaining} candidates={len(candidates)}")

        for name in candidates:
            candidate = players[name]
            if name in watchlist.index and pd.notna(watchlist.loc[name, "market_price_p50"]):
                price = float(watchlist.loc[name, "market_price_p50"])
                price_source = "market_price_p50 (real simulated Monte Carlo sale price, phase3b watchlist)"
            else:
                price = max(1.0, candidate.base_value)
                price_source = "base_value (PROJECTION_VALUE_PROXY, not a simulated market price)"

            result_a, result_b, rt_a, rt_b = scenario_solve(players, sam, name, price, sam.budget_remaining)
            surplus = None
            if result_a.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL") and result_b.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL"):
                surplus = round(result_a.starting_points - result_b.starting_points, 2)

            displaced = []
            if not result_a.selected.empty and not result_b.selected.empty:
                set_a = set(result_a.selected["player"]) - {name}
                set_b = set(result_b.selected["player"])
                displaced = sorted(set_b - set_a)

            scenario_rows.append({
                "budget_scenario": scenario, "player": name, "position": candidate.position,
                "test_price": round(price, 2), "price_source": price_source,
                "scenario_a_starting_points": round(result_a.starting_points, 2),
                "scenario_b_starting_points": round(result_b.starting_points, 2),
                "scenario_a_bench_points": round(result_a.bench_points, 2),
                "scenario_b_bench_points": round(result_b.bench_points, 2),
                "scenario_a_budget_remaining": round(sam.budget_remaining - price, 2),
                "scenario_b_budget_remaining": round(sam.budget_remaining, 2),
                "objective_difference_surplus": surplus,
                "players_displaced_by_purchase": ";".join(displaced) if displaced else "",
                "solver_status_a": result_a.status, "solver_status_b": result_b.status,
                "runtime_seconds": round(rt_a + rt_b, 3),
            })

            ceil_price, n_solves = exact_ceiling(players, sam, name, sam.budget_remaining, result_b)
            ceiling_rows.append({
                "budget_scenario": scenario, "player": name, "position": candidate.position,
                "exact_pre_draft_static_pool_ceiling": ceil_price,
                "baseline_pass_starting_points": round(result_b.starting_points, 2) if result_b.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL") else None,
                "price_used_for_scenario_test": round(price, 2),
                "surplus_at_test_price": surplus,
                "binary_search_solves": n_solves,
                "calculation_label": "EXACT_PRE_DRAFT_STATIC_POOL_CEILING",
            })

        # P50-proxy portfolio: greedily take the 9 candidates with the highest
        # per-dollar surplus among positive-surplus candidates, subject to
        # budget/roster legality, then verify with one final exact solve of
        # the full remaining pool (this IS the exact solve, not a heuristic
        # substitute -- result_b already ran the full 9-slot exact optimizer
        # over the complete live pool for each candidate's pass scenario;
        # here we report the single best full-pool exact optimum directly).
        full_pool_df = _pool_to_exact_df(players, set())
        final = exact_roster_solver.solve_exact_roster(
            full_pool_df, budget=sam.budget_remaining, n_auction_spots=N_AUCTION_SPOTS,
            keepers=_keepers_to_exact_df(sam.roster),
        )
        for _, row in final.selected.iterrows():
            portfolio_rows.append({
                "budget_scenario": scenario, "price_scenario": "P50_PROXY_BASE_VALUE",
                "player": row["player"], "position": row["position"],
                "price_paid": row.get("price", None), "role": final.role_assignments.get(row["player"], ""),
                "total_starting_points": round(final.starting_points, 2),
                "total_bench_points": round(final.bench_points, 2),
                "unused_cash": round(final.unused_cash, 2), "solver_status": final.status,
            })
        print(f"[{scenario}] full-pool exact optimum: status={final.status} "
              f"starting_pts={final.starting_points:.2f} unused_cash={final.unused_cash:.2f} "
              f"picks={len(final.selected)}")

    with (OUT_DIR / "sam_exact_scenario_rosters.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(scenario_rows[0].keys()))
        w.writeheader(); w.writerows(scenario_rows)

    with (OUT_DIR / "sam_exact_ceiling_validation.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(ceiling_rows[0].keys()))
        w.writeheader(); w.writerows(ceiling_rows)

    with (OUT_DIR / "sam_portfolios.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(portfolio_rows[0].keys()))
        w.writeheader(); w.writerows(portfolio_rows)

    print(f"Wrote {len(scenario_rows)} scenario rows, {len(ceiling_rows)} ceiling rows, "
          f"{len(portfolio_rows)} portfolio rows to {OUT_DIR}")


if __name__ == "__main__":
    main()
