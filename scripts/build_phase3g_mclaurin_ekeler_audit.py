#!/usr/bin/env python3
"""Phase 3G Part 4 (Ekeler) + Part 5 (McLaurin) detailed audits.
Run with a single --budget-scenario per process to keep clean state.

Usage: python3 scripts/build_phase3g_mclaurin_ekeler_audit.py <primary|conversions> <out_dir>
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


def _pool_to_exact_df(pool, exclude_names):
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


def scenario_solve(players, sam, name, price, budget):
    candidate = players[name]
    pool_minus = {n: p for n, p in players.items() if n != name}
    df_a = _pool_to_exact_df(pool_minus, set())
    roster_with = sam.roster + [(candidate.name, candidate.position, float(price), candidate.projected_points)]
    result_a = exact_roster_solver.solve_exact_roster(
        df_a, budget=max(0.0, budget - price),
        n_auction_spots=max(0, N_AUCTION_SPOTS - 1), keepers=_keepers_to_exact_df(roster_with),
    )
    df_b = _pool_to_exact_df(players, {name})
    result_b = exact_roster_solver.solve_exact_roster(
        df_b, budget=budget, n_auction_spots=N_AUCTION_SPOTS, keepers=_keepers_to_exact_df(sam.roster),
    )
    return result_a, result_b


def main():
    budget_scenario = sys.argv[1]
    out_dir = Path(sys.argv[2])
    scen_label = "primary_223" if budget_scenario == "primary" else "conversions_221"
    budget_num = 223 if budget_scenario == "primary" else 221

    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario=budget_scenario)
    sam = teams["Sam"]

    # ---- McLaurin ladder $40-$60 ----
    mclaurin_rows = []
    for price in range(40, 61):
        result_a, result_b = scenario_solve(players, sam, "Terry McLaurin", price, sam.budget_remaining)
        purchase_names = set(result_a.selected["player"]) if not result_a.selected.empty else set()
        pass_names = set(result_b.selected["player"]) if not result_b.selected.empty else set()
        displaced = sorted(pass_names - purchase_names - {"Terry McLaurin"})
        surplus = None
        if _status_ok(result_a.status) and _status_ok(result_b.status):
            surplus = round(result_a.starting_points - result_b.starting_points, 2)
        mclaurin_rows.append({
            "budget_scenario": scen_label, "price": price,
            "purchase_objective_starting_points": result_a.starting_points,
            "pass_objective_starting_points": result_b.starting_points,
            "surplus": surplus,
            "purchase_roster": ";".join(sorted(purchase_names)),
            "pass_roster": ";".join(sorted(pass_names)),
            "displaced_players": ";".join(displaced),
            "remaining_cash_purchase": round(sam.budget_remaining - price, 2),
            "bench_points_purchase": result_a.bench_points, "bench_points_pass": result_b.bench_points,
            "unused_cash_purchase": result_a.unused_cash, "unused_cash_pass": result_b.unused_cash,
            "solver_status_purchase": result_a.status, "solver_status_pass": result_b.status,
        })
    with open(out_dir / f"terry_mclaurin_budget_sensitivity_{budget_scenario}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(mclaurin_rows[0].keys()))
        w.writeheader(); w.writerows(mclaurin_rows)
    print(f"[{budget_scenario}] wrote McLaurin $40-$60 ladder ({len(mclaurin_rows)} rows)", flush=True)

    # ---- Ekeler audit: $97, $75, $60, $50, $40 ----
    ekeler_rows = []
    for price in [97, 75, 60, 50, 40]:
        result_a, result_b = scenario_solve(players, sam, "Austin Ekeler", price, sam.budget_remaining)
        purchase_names = set(result_a.selected["player"]) if not result_a.selected.empty else set()
        pass_names = set(result_b.selected["player"]) if not result_b.selected.empty else set()
        displaced = sorted(pass_names - purchase_names - {"Austin Ekeler"})
        surplus = None
        if _status_ok(result_a.status) and _status_ok(result_b.status):
            surplus = round(result_a.starting_points - result_b.starting_points, 2)
        ekeler_rows.append({
            "budget_scenario": scen_label, "price": price, "surplus": surplus,
            "purchase_starting_points": result_a.starting_points, "pass_starting_points": result_b.starting_points,
            "displaced_players": ";".join(displaced),
            "solver_status_purchase": result_a.status, "solver_status_pass": result_b.status,
        })
    with open(out_dir / f"austin_ekeler_price_scan_{budget_scenario}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(ekeler_rows[0].keys()))
        w.writeheader(); w.writerows(ekeler_rows)
    print(f"[{budget_scenario}] wrote Ekeler price scan ({len(ekeler_rows)} rows)", flush=True)


if __name__ == "__main__":
    main()
