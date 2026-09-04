#!/usr/bin/env python3
"""Phase 3G Part 5/6: compute exact whole-dollar ceilings for the NARROWED
selected-player set only (not all 65 candidates), under ONE budget scenario
per process invocation, to guarantee zero shared mutable state between the
$223 and $221 runs (Part 5's clean-state requirement). Call this script
twice (once per --budget-scenario) via separate `python3` subprocess
invocations from the orchestrating script -- true process isolation, not
just a fresh function call, so any hidden module-level cache cannot leak
between the two budget scenarios.

Usage: python3 scripts/build_phase3g_selected_ceilings.py <primary|conversions> <out_csv>
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

N_AUCTION_SPOTS = 9

# NARROWED selected-player set (Part 6): 4 legacy targets + >=2 alternatives
# per required role group (starting TE, premium WR/FLEX, mid WR/FLEX, RB
# depth, QB upgrade, bench upside, one-dollar completion) + Ekeler under review.
SELECTED_PLAYERS = [
    # QB upgrade
    "Josh Allen", "Jalen Hurts",
    # premium WR/FLEX
    "Rashee Rice", "DeVonta Smith",
    # mid WR/FLEX
    "Terry McLaurin", "Jaylen Waddle", "Tee Higgins",
    # starting TE
    "George Kittle", "Mark Andrews", "TJ Hockenson",
    # RB depth
    "Bucky Irving", "Travis Etienne", "Jonathon Brooks", "Chuba Hubbard",
    # extreme-price review
    "Austin Ekeler",
    # bench upside
    "Dalton Kincaid", "Jake Ferguson",
    # one-dollar roster completion
    "Xavier Worthy", "Rachaad White", "Romeo Doubs", "Khalil Shakir",
]


def log(msg):
    print(f"[phase3g:{sys.argv[1] if len(sys.argv) > 1 else '?'}] {msg}", flush=True)


def _pool_to_exact_df(pool: dict, exclude_names: set) -> pd.DataFrame:
    return pd.DataFrame([
        {"player": p.name, "position": p.position, "projected_points": p.projected_points,
         "suggested_auction_price": max(1.0, p.base_value)}
        for name, p in pool.items() if name not in exclude_names
    ])


def _keepers_to_exact_df(roster):
    return pd.DataFrame([
        {"player": name, "position": pos, "projected_points": pts, "keeper_price_2026": price}
        for name, pos, price, pts in roster
    ])


def _status_ok(status):
    return status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL")


def cached_pass_solve(players, sam, name, budget, cache):
    if name in cache:
        return cache[name]
    df_b = _pool_to_exact_df(players, {name})
    result = exact_roster_solver.solve_exact_roster(
        df_b, budget=budget, n_auction_spots=N_AUCTION_SPOTS, keepers=_keepers_to_exact_df(sam.roster),
    )
    cache[name] = result
    return result


def integer_ceiling(players, sam, name, budget, baseline_pts):
    candidate = players[name]
    cache = {}
    solves = 0
    solver_failure = False

    def ok_at(price):
        nonlocal solves, solver_failure
        if price in cache:
            return cache[price]
        pool_minus = {n: p for n, p in players.items() if n != name}
        df_a = _pool_to_exact_df(pool_minus, set())
        roster_with = sam.roster + [(candidate.name, candidate.position, float(price), candidate.projected_points)]
        result = exact_roster_solver.solve_exact_roster(
            df_a, budget=max(0.0, budget - price),
            n_auction_spots=max(0, N_AUCTION_SPOTS - 1), keepers=_keepers_to_exact_df(roster_with),
        )
        solves += 1
        if result.status == "ERROR":
            solver_failure = True
            cache[price] = (False, "SOLVER_FAILURE")
            return cache[price]
        good = _status_ok(result.status) and result.starting_points >= baseline_pts
        cache[price] = (good, result.status)
        return cache[price]

    hi_bound = int(min(budget, 400))
    ok0, _ = ok_at(1)
    if not ok0:
        return 0, solves, True, solver_failure
    price, step = 1, 1
    while price < hi_bound:
        nxt = min(price + step, hi_bound)
        ok_nxt, _ = ok_at(nxt)
        if not ok_nxt:
            break
        price = nxt
        step *= 2
    lo, hi = price, min(price + max(step, 1), hi_bound)
    ok_hi, _ = ok_at(hi)
    if ok_hi:
        return hi, solves, True, solver_failure
    while hi - lo > 1:
        mid = (lo + hi) // 2
        ok_mid, _ = ok_at(mid)
        if ok_mid:
            lo = mid
        else:
            hi = mid
    ok_ceiling, _ = ok_at(lo)
    ok_plus1, _ = ok_at(min(lo + 1, hi_bound))
    monotonic_ok = ok_ceiling and (not ok_plus1 or lo + 1 > hi_bound)
    return lo, solves, monotonic_ok, solver_failure


def main():
    budget_scenario = sys.argv[1]
    out_csv = sys.argv[2]
    assert budget_scenario in ("primary", "conversions")

    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario=budget_scenario)
    sam = teams["Sam"]
    pass_cache = {}

    rows = []
    for i, name in enumerate(SELECTED_PLAYERS):
        if name not in players:
            rows.append({"player": name, "position": None, "exact_ceiling_whole_dollar": None,
                         "solves": 0, "monotonic": None, "solver_failure": True,
                         "calculation_label": "SOLVER_FAILURE", "note": "player not found in pool"})
            continue
        candidate = players[name]
        baseline = cached_pass_solve(players, sam, name, sam.budget_remaining, pass_cache)
        if not _status_ok(baseline.status):
            rows.append({"player": name, "position": candidate.position, "exact_ceiling_whole_dollar": None,
                         "solves": 0, "monotonic": None, "solver_failure": True,
                         "calculation_label": "SOLVER_FAILURE", "note": "pass-scenario baseline not OPTIMAL"})
            continue
        ceiling, solves, monotonic_ok, solver_failure = integer_ceiling(
            players, sam, name, sam.budget_remaining, baseline.starting_points)
        label = "SOLVER_FAILURE" if solver_failure else "EXACT_PRE_DRAFT_STATIC_POOL_CEILING"
        rows.append({"player": name, "position": candidate.position,
                     "exact_ceiling_whole_dollar": ceiling if not solver_failure else None,
                     "solves": solves, "monotonic": monotonic_ok, "solver_failure": solver_failure,
                     "calculation_label": label, "note": ""})
        log(f"{i+1}/{len(SELECTED_PLAYERS)} {name} ceiling={ceiling} monotonic={monotonic_ok}")

    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    log(f"DONE: wrote {len(rows)} rows to {out_csv}")


if __name__ == "__main__":
    main()
