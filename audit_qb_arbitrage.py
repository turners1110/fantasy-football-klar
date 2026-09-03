#!/usr/bin/env python3
"""Reproduce the prior 'winning genome' (gen15_elite0) exactly as tested by
run_best_response.py (same seed, same match count) and audit whether its
apparent QB-arbitrage edge survives a legal-lineup-aware objective instead
of the old sum-all-15-players objective.

Part of outputs/auction_rebuild/audit/ per the rebuild spec. Writes:
  outputs/auction_rebuild/audit/prior_winning_genome_rosters.csv
  outputs/auction_rebuild/audit/prior_qb_arbitrage_decomposition.csv
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from mock_draft.auction import run_single_auction
from mock_draft.data import load_pool_and_teams
from mock_draft.evolution import genome_from_dict
from mock_draft.legal_lineup import select_legal_lineup

BASE_DIR = Path(__file__).parent
OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "audit"
OUT_DIR.mkdir(parents=True, exist_ok=True)

GENOME_NAME = "gen15_elite0"
N_MATCHES = 40  # matches run_best_response.py's original test exactly
SEED = 0        # matches run_best_response.py's default --seed exactly


def main() -> None:
    players, teams = load_pool_and_teams()
    data = json.loads((BASE_DIR / "mock_draft_learned_population.json").read_text())
    genome = genome_from_dict([g for g in data["population"] if g["name"] == GENOME_NAME][0])
    print(f"Reproducing {GENOME_NAME} exactly as tested (seed={SEED}, n={N_MATCHES} matches).")
    print(f"Data source note: this uses output_mock_draft_snapshot/ (fallback_neutral "
          f"keeper heuristic), the SAME data every prior mock_draft run used -- NOT "
          f"the user's real confirmed keepers. See current_architecture.md for why "
          f"that matters and how different Sam's real roster is.")

    rng = np.random.default_rng(SEED)
    team_names = list(teams.keys())

    roster_rows = []
    decomposition_rows = []

    for i in range(N_MATCHES):
        my_team = team_names[i % len(team_names)]
        strategies = {my_team: genome}
        log, final_teams = run_single_auction(players, teams, rng, strategies=strategies)

        team = final_teams[my_team]
        roster = team.roster  # (name, position, price, points)

        old_objective_points = sum(pts for _n, _p, _pr, pts in roster)
        legal = select_legal_lineup(roster)

        n_qb = sum(1 for _n, pos, _pr, _pts in roster if pos == "QB")
        qb_spend = sum(pr for _n, pos, pr, _pts in roster if pos == "QB")
        qb_points_old = sum(pts for _n, pos, _pr, pts in roster if pos == "QB")
        starting_qb_pts = next((pts for n, pos, _pr, pts in roster if pos == "QB" and n == legal.starting_QB), 0.0)
        bench_qb_pts_included = sum(
            pts * (0.075 if idx == 0 else 0.0)
            for idx, (n, pos, _pr, pts) in enumerate(
                sorted([r for r in roster if r[1] == "QB" and r[0] != legal.starting_QB], key=lambda r: -r[3])
            )
        )

        forced_final_slot_price = None
        for entry in log:
            if entry["winner"] == my_team and entry.get("forced_final_slot") and entry["player"] in {n for n, *_ in roster}:
                forced_final_slot_price = entry["price"]

        for name, pos, price, pts in roster:
            roster_rows.append({
                "match": i, "seat": my_team, "genome": GENOME_NAME,
                "player": name, "position": pos, "price": price, "projected_points": pts,
                "is_starting": name in (
                    [legal.starting_QB] + legal.starting_RB + legal.starting_WR + [legal.starting_TE] + legal.starting_FLEX
                ),
            })

        decomposition_rows.append({
            "match": i, "seat": my_team,
            "n_quarterbacks_drafted": n_qb,
            "money_spent_on_qb": qb_spend,
            "qb_points_under_old_objective": round(qb_points_old, 2),
            "qb_points_under_new_objective": round(starting_qb_pts + bench_qb_pts_included, 2),
            "old_total_roster_score_sum_all_15": round(old_objective_points, 2),
            "new_legal_lineup_total_utility": legal.total_roster_utility,
            "new_starting_lineup_points_only": legal.starting_lineup_points,
            "diff_old_minus_new": round(old_objective_points - legal.total_roster_utility, 2),
            "diff_caused_by_bench_qb": round(qb_points_old - (starting_qb_pts + bench_qb_pts_included), 2),
            "bench_qb_count": legal.bench_QB_count,
            "roster_legality": legal.roster_legality,
            "forced_final_slot_price_this_draft": forced_final_slot_price,
            "any_nan_points": any(pd.isna(pts) for _n, _p, _pr, pts in roster),
        })

    rosters_df = pd.DataFrame(roster_rows)
    decomp_df = pd.DataFrame(decomposition_rows)

    rosters_path = OUT_DIR / "prior_winning_genome_rosters.csv"
    decomp_path = OUT_DIR / "prior_qb_arbitrage_decomposition.csv"
    rosters_df.to_csv(rosters_path, index=False)
    decomp_df.to_csv(decomp_path, index=False)

    print(f"\nWrote {rosters_path} ({len(rosters_df)} rows, {N_MATCHES} rosters)")
    print(f"Wrote {decomp_path} ({len(decomp_df)} rows)")

    print("\n=== SUMMARY: does QB arbitrage survive a legal-lineup objective? ===")
    print(f"Avg QBs drafted per roster: {decomp_df['n_quarterbacks_drafted'].mean():.2f}")
    print(f"Avg $ spent on QBs: {decomp_df['money_spent_on_qb'].mean():.2f}")
    print(f"Avg QB points counted, OLD objective (sum all 15): {decomp_df['qb_points_under_old_objective'].mean():.1f}")
    print(f"Avg QB points counted, NEW objective (legal lineup + bench discount): {decomp_df['qb_points_under_new_objective'].mean():.1f}")
    print(f"Avg OLD total roster score: {decomp_df['old_total_roster_score_sum_all_15'].mean():.1f}")
    print(f"Avg NEW legal-lineup utility: {decomp_df['new_legal_lineup_total_utility'].mean():.1f}")
    print(f"Avg gap (old-new), i.e. inflation from illegal bench-QB credit: {decomp_df['diff_old_minus_new'].mean():.1f}")
    print(f"Illegal/incomplete rosters found: {(decomp_df['roster_legality'] != 'LEGAL').sum()} / {len(decomp_df)}")
    print(f"NaN points found in any roster: {decomp_df['any_nan_points'].sum()} / {len(decomp_df)}")
    print(f"Forced-final-slot picks among these rosters: {decomp_df['forced_final_slot_price_this_draft'].notna().sum()} / {len(decomp_df)}")


if __name__ == "__main__":
    main()
