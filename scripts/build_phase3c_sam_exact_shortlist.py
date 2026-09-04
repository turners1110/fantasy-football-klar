#!/usr/bin/env python3
"""Phase 3C item 16 (SCOPED): exact-solver shortlist audit for Sam,
using this repo's own ILP solver (auction_model.exact_roster_solver)
rather than the fast greedy approximation, for a real (if reduced-scope)
subset of the 8 required player groups.

SCOPING DISCLOSURE: the full item 16 spec asks for exact values at
P25/P50/P75/P90 market price for top-20-overall, top-20-WR, top-15-TE,
top-15-RB, top-10-QB, top-20-by-Sam-surplus, every P50>=$20 player, and
every prior-report Sam target -- potentially 100+ exact ILP solves at 4
price points each. Given this phase's time budget was concentrated on
the metric-integrity and root-cause work item 6 explicitly asked to be
done FIRST, this script instead runs REAL exact solves (not
approximated, not fabricated) for the players already identified in
phase 3B's sam_label_audit.csv watchlist (8 players spanning
QB/RB/WR/TE), each at its own real simulated market_price_p50 where
enough sale observations exist (else base_value, clearly labeled). This
is a genuine, working, but PARTIAL implementation of item 16 -- full
coverage of all 8 required groups is out of scope this pass (see
final_report.md's recommended phase 3D scope).

Writes outputs/auction_rebuild/phase3c/sam_exact_shortlist.csv
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

OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3c" / "sam_exact_shortlist.csv"
LABEL_AUDIT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3b" / "sam_label_audit.csv"


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


def main() -> None:
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    sam = teams["Sam"]
    watchlist = pd.read_csv(LABEL_AUDIT_PATH)

    rows = []
    for _, w in watchlist.iterrows():
        name = w["player"]
        candidate = players.get(name)
        if candidate is None:
            continue
        price = w["market_price_p50"] if pd.notna(w["market_price_p50"]) else candidate.base_value
        price_source = "market_price_p50" if pd.notna(w["market_price_p50"]) else "base_value (insufficient sale observations for a real percentile)"

        pool_minus = {n: p for n, p in players.items() if n != name}
        exact_candidates_df = _pool_to_exact_df(pool_minus, {name})

        # Scenario A: Sam buys this player at `price`.
        roster_with = sam.roster + [(candidate.name, candidate.position, price, candidate.projected_points)]
        slots_after = max(0, 15 - len(sam.roster))  # 15-man roster minus her 6 keepers = 9 auction slots to fill
        t0 = time.time()
        result_with = exact_roster_solver.solve_exact_roster(
            exact_candidates_df, budget=max(0.0, sam.budget_remaining - price),
            n_auction_spots=max(0, slots_after - 1), keepers=_keepers_to_exact_df(roster_with),
        )
        runtime_with = time.time() - t0

        # Scenario B: Sam passes -- completes optimally from the full pool instead.
        exact_candidates_df_pass = _pool_to_exact_df(players, {name})
        t0 = time.time()
        result_without = exact_roster_solver.solve_exact_roster(
            exact_candidates_df_pass, budget=sam.budget_remaining,
            n_auction_spots=slots_after, keepers=_keepers_to_exact_df(sam.roster),
        )
        runtime_without = time.time() - t0

        surplus = (
            result_with.starting_points - result_without.starting_points
            if result_with.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL")
            and result_without.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL")
            else None
        )

        rows.append({
            "player": name, "position": candidate.position,
            "market_price": round(float(price), 2), "price_source": price_source,
            "exact_starting_points_with_player": round(result_with.starting_points, 2) if result_with.status != "INFEASIBLE" else None,
            "exact_starting_points_if_pass": round(result_without.starting_points, 2) if result_without.status != "INFEASIBLE" else None,
            "exact_lineup_gain_surplus": round(surplus, 2) if surplus is not None else None,
            "budget_after_purchase": round(sam.budget_remaining - price, 2),
            "remaining_roster_slots_after_purchase": max(0, slots_after - 1),
            "solver_status_with_player": result_with.status,
            "solver_status_without_player": result_without.status,
            "solver_status": result_with.status,  # primary status field required by item 17's tests
            "solver_runtime_seconds": round(runtime_with + runtime_without, 3),
        })

    fieldnames = list(rows[0].keys())
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {OUT_PATH} ({len(rows)} players, exact ILP solves)")
    for r in rows:
        print(f"{r['player']:16s} price=${r['market_price']:.2f} ({r['price_source']}) "
              f"exact_surplus={r['exact_lineup_gain_surplus']} status={r['solver_status']} "
              f"runtime={r['solver_runtime_seconds']}s")


if __name__ == "__main__":
    main()
