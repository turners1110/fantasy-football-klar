#!/usr/bin/env python3
"""Phase 2B required re-check: at least 200 deterministic auction seeds
after the positional-feasibility/utility-gate/eligibility fixes, reported
by seed and team (not aggregate percentages alone).

Writes outputs/auction_rebuild/phase2b/{seed_team_results,failures}.csv
and phase2b_summary.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from mock_draft.auction import run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.feasibility import DEFAULT_POSITION_MAX
from mock_draft.legal_lineup import build_production_lineup

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase2b"
OUT_DIR.mkdir(parents=True, exist_ok=True)

N_SEEDS = 200


def _run(seeds, enable_position_max, confirmed_keeper_names):
    players, teams_template, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    team_rows, sale_rows, failure_rows = [], [], []

    for seed in seeds:
        rng = np.random.default_rng(seed)
        unsold = []
        log, final_teams = run_single_auction(
            players, teams_template, rng, unsold_log=unsold, enable_position_max=enable_position_max,
        )
        for entry in log:
            row = dict(entry)
            row["seed"] = seed
            sale_rows.append(row)

        for team_name, team in final_teams.items():
            names = [r[0] for r in team.roster]
            has_dupes = len(names) != len(set(names))
            counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
            for _n, p, _pr, _pts in team.roster:
                counts[p] = counts.get(p, 0) + 1
            legal_roster = (
                len(team.roster) == 15 and not has_dupes and team.budget_remaining >= -1e-6
                and counts["QB"] >= 1 and counts["RB"] >= 2 and counts["WR"] >= 2 and counts["TE"] >= 1
            )
            lineup = build_production_lineup(team.roster)
            sold_names = {e["player"] for e in log if e["winning_team"] == team_name}
            keeper_sold = bool(sold_names & confirmed_keeper_names)

            team_rows.append({
                "seed": seed, "team": team_name, "n_players": len(team.roster),
                "n_qb": counts["QB"], "n_rb": counts["RB"], "n_wr": counts["WR"], "n_te": counts["TE"],
                "legal_roster": legal_roster, "lineup_is_legal": lineup.lineup_is_legal,
                "lineup_failure_reason": lineup.lineup_failure_reason,
                "budget_remaining": team.budget_remaining, "has_duplicate_player": has_dupes,
                "keeper_sold_in_auction": keeper_sold,
                "enable_position_max": enable_position_max,
            })
            if not legal_roster:
                failure_rows.append({
                    "seed": seed, "team": team_name, "reason": "ILLEGAL_ROSTER",
                    "detail": counts, "n_players": len(team.roster), "budget_remaining": team.budget_remaining,
                })
            if not lineup.lineup_is_legal:
                failure_rows.append({
                    "seed": seed, "team": team_name, "reason": "ILLEGAL_LINEUP",
                    "detail": lineup.lineup_failure_reason, "n_players": len(team.roster),
                    "budget_remaining": team.budget_remaining,
                })
        for u in unsold:
            u["seed"] = seed
    return team_rows, sale_rows, failure_rows


def main() -> None:
    confirmed_keeper_names = set(pd.read_csv(BASE_DIR / "data" / "keepers_2026_confirmed.csv")["player_name"])

    print(f"Running {N_SEEDS} seeds with position caps ENABLED (primary capped scenario, "
          f"QB max={DEFAULT_POSITION_MAX['QB']}, TE max={DEFAULT_POSITION_MAX['TE']})...")
    team_rows, sale_rows, failure_rows = _run(range(N_SEEDS), True, confirmed_keeper_names)

    print(f"Running {N_SEEDS} seeds with position caps DISABLED (comparison scenario)...")
    team_rows_nocap, sale_rows_nocap, failure_rows_nocap = _run(range(N_SEEDS), False, confirmed_keeper_names)

    results_df = pd.DataFrame(team_rows + team_rows_nocap)
    sales_df = pd.DataFrame(sale_rows + sale_rows_nocap)
    failures_df = pd.DataFrame(failure_rows + failure_rows_nocap)

    results_path = OUT_DIR / "seed_team_results.csv"
    failures_path = OUT_DIR / "failures.csv"
    results_df.to_csv(results_path, index=False)
    if len(failures_df):
        failures_df.to_csv(failures_path, index=False)
    else:
        failures_path.write_text("seed,team,reason,detail,n_players,budget_remaining\n")

    capped = results_df[results_df["enable_position_max"]]
    uncapped = results_df[~results_df["enable_position_max"]]

    n_total = len(capped)
    n_legal_roster = int(capped["legal_roster"].sum())
    n_legal_lineup = int(capped["lineup_is_legal"].sum())
    n_negative_budget = int((capped["budget_remaining"] < 0).sum())
    n_duplicate = int(capped["has_duplicate_player"].sum())
    n_keeper_sold = int(capped["keeper_sold_in_auction"].sum())
    n_forced_final_slot = int(sales_df["forced_final_slot"].sum()) if len(sales_df) else 0
    max_qb_capped = int(capped["n_qb"].max())
    max_qb_uncapped = int(uncapped["n_qb"].max())
    n_third_plus_qb_capped = int((capped["n_qb"] >= 3).sum())

    summary = {
        "n_seeds": N_SEEDS,
        "n_teams": 12,
        "n_team_runs_total_capped_scenario": n_total,
        "legal_roster_count": n_legal_roster,
        "legal_roster_rate": n_legal_roster / n_total,
        "legal_lineup_count": n_legal_lineup,
        "legal_lineup_rate": n_legal_lineup / n_total,
        "negative_budget_count": n_negative_budget,
        "duplicate_sale_count": n_duplicate,
        "keeper_sold_count": n_keeper_sold,
        "forced_final_slot_count": n_forced_final_slot,
        "max_qb_count_position_caps_enabled": max_qb_capped,
        "configured_qb_cap": DEFAULT_POSITION_MAX["QB"],
        "qb_cap_held": max_qb_capped <= DEFAULT_POSITION_MAX["QB"],
        "n_team_runs_with_3plus_qb_capped_scenario": n_third_plus_qb_capped,
        "max_qb_count_position_caps_disabled_comparison": max_qb_uncapped,
        "outputs": {"seed_team_results": str(results_path), "failures": str(failures_path)},
    }
    summary_path = OUT_DIR / "phase2b_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    print(f"\nWrote {results_path} ({len(results_df)} rows)")
    print(f"Wrote {failures_path} ({len(failures_df)} rows)")
    print(f"Wrote {summary_path}")
    print("\n--- 200-SEED RESULTS (position caps enabled, primary scenario) ---")
    for k, v in summary.items():
        if k != "outputs":
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
