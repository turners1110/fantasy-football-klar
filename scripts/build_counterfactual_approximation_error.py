#!/usr/bin/env python3
"""Phase 3A item 7 (speed instructions #4-5): compare the fast greedy
roster-completion approximation (mock_draft/counterfactual.py) against
the existing exact ILP solver (auction_model/exact_roster_solver.py) on a
sample of real auction states, and report the approximation error.

Compares on STARTING LINEUP POINTS (the well-defined, comparable
quantity both methods solve for) rather than mock_draft's own tiered-
bench "total_roster_utility" (which the exact solver doesn't compute in
that exact form) -- an apples-to-apples comparison on the piece both
approaches are actually trying to get right.

Writes outputs/auction_rebuild/phase3a/counterfactual_approximation_error.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from auction_model import exact_roster_solver
from mock_draft.counterfactual import clear_cache, greedy_complete_roster
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.legal_lineup import build_production_lineup

OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "counterfactual_approximation_error.csv"
N_SAMPLES = 15


def _pool_to_exact_df(pool: dict, exclude_names: set) -> pd.DataFrame:
    rows = [
        {"player": p.name, "position": p.position, "projected_points": p.projected_points,
         "suggested_auction_price": max(1.0, p.base_value)}
        for name, p in pool.items() if name not in exclude_names
    ]
    return pd.DataFrame(rows)


def _keepers_to_exact_df(roster: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame([
        {"player": name, "position": pos, "projected_points": pts, "keeper_price_2026": price}
        for name, pos, price, pts in roster
    ])


def main() -> None:
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    rng = np.random.default_rng(0)
    team_names = list(teams.keys())
    candidate_names = [n for n in players if players[n].position in ("QB", "RB", "WR", "TE")]

    rows = []
    for i in range(N_SAMPLES):
        team_name = team_names[i % len(team_names)]
        team = teams[team_name]
        candidate_name = candidate_names[rng.integers(0, len(candidate_names))]
        candidate = players[candidate_name]
        price = float(rng.choice([1, 5, 15, 30, 60]))

        clear_cache()
        pool_minus = {n: p for n, p in players.items() if n != candidate_name}
        roster_with = team.roster + [(candidate.name, candidate.position, price, candidate.projected_points)]
        slots_after = max(0, team.slots_needed - 1)
        greedy_roster = greedy_complete_roster(roster_with, pool_minus, slots_after, team.budget_remaining - price)
        greedy_lineup = build_production_lineup(greedy_roster)

        exact_candidates_df = _pool_to_exact_df(pool_minus, {candidate_name})
        exact_keepers_df = _keepers_to_exact_df(roster_with)
        exact_result = exact_roster_solver.solve_exact_roster(
            exact_candidates_df, budget=max(0.0, team.budget_remaining - price),
            n_auction_spots=slots_after, keepers=exact_keepers_df,
        )

        rows.append({
            "sample": i, "team": team_name, "candidate": candidate_name, "price": price,
            "greedy_starting_points": greedy_lineup.starting_lineup_points,
            "exact_starting_points": exact_result.starting_points,
            "exact_status": exact_result.status,
            "absolute_error": abs(greedy_lineup.starting_lineup_points - exact_result.starting_points)
                              if exact_result.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL") else None,
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    valid = [r for r in rows if r["absolute_error"] is not None]
    print(f"Wrote {OUT_PATH} ({len(rows)} samples, {len(valid)} with a valid exact solve)")
    if valid:
        errors = [r["absolute_error"] for r in valid]
        print(f"Mean absolute error (starting lineup points): {sum(errors)/len(errors):.2f}")
        print(f"Max absolute error: {max(errors):.2f}")
    n_infeasible = len(rows) - len(valid)
    if n_infeasible:
        print(f"{n_infeasible} samples had no valid exact solve (solver status other than OPTIMAL/FEASIBLE) -- "
              f"see the exact_status column.")


if __name__ == "__main__":
    main()
