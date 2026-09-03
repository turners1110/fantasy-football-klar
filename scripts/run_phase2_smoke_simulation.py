#!/usr/bin/env python3
"""Phase 2 smoke simulation: confirms the confirmed-keeper pipeline, the
legal-lineup utility, and the no-forced-final-slot auction engine all work
together across several full drafts. NOT a strategy or pricing exercise --
no evolution, no genome optimization, only the existing named hand-built
archetypes (random assignment per run, same as any ordinary mock draft).

Writes:
  outputs/auction_rebuild/phase2/smoke_rosters.csv
  outputs/auction_rebuild/phase2/smoke_sales.csv
  outputs/auction_rebuild/phase2/smoke_team_results.csv
  outputs/auction_rebuild/phase2/smoke_failures.csv
  outputs/auction_rebuild/phase2/smoke_summary.json

Do NOT interpret these results as draft advice -- see smoke_summary.json's
"safe_for_draft_use" field (always False in phase 2: no strategy has been
re-validated under the corrected fitness function, and several teams'
keeper state is only PARTIALLY_CONFIRMED).
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from mock_draft.auction import run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.legal_lineup import build_production_lineup

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase2"
OUT_DIR.mkdir(parents=True, exist_ok=True)

N_SEEDS = 20  # >= 12 required auction seeds


def main() -> None:
    players, teams_template, meta = load_confirmed_pool_and_teams(budget_scenario="primary")
    team_states = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv")
    keeper_state_by_team = dict(zip(team_states["team_id"], team_states["keeper_state_status"]))
    confirmed_keeper_names = set(
        pd.read_csv(BASE_DIR / "data" / "keepers_2026_confirmed.csv")["player_name"]
    )

    roster_rows, sale_rows, team_result_rows, failure_rows = [], [], [], []

    for seed in range(N_SEEDS):
        rng = np.random.default_rng(seed)
        log, final_teams = run_single_auction(players, teams_template, rng, verbose=False)

        for entry in log:
            row = dict(entry)
            row["seed"] = seed
            sale_rows.append(row)

        for team_name, team in final_teams.items():
            names = [r[0] for r in team.roster]
            has_dupes = len(names) != len(set(names))
            legal_roster = (len(team.roster) == 15) and not has_dupes and team.budget_remaining >= -1e-6

            lineup = build_production_lineup(team.roster)
            n_qb = sum(1 for _n, p, _pr, _pts in team.roster if p == "QB")

            for name, pos, price, pts in team.roster:
                roster_rows.append({
                    "seed": seed, "team": team_name, "player": name, "position": pos,
                    "price": price, "projected_points": pts,
                    "is_starting_qb": name == lineup.starting_qb,
                    "is_starting_rb": name in lineup.starting_rbs,
                    "is_starting_wr": name in lineup.starting_wrs,
                    "is_starting_te": name == lineup.starting_te,
                    "is_starting_flex": name in lineup.starting_flex,
                    "is_starter": name in (
                        {lineup.starting_qb, lineup.starting_te} | set(lineup.starting_rbs)
                        | set(lineup.starting_wrs) | set(lineup.starting_flex)
                    ),
                })

            team_result_rows.append({
                "seed": seed, "team": team_name,
                "keeper_state_status": keeper_state_by_team.get(team_name, "UNKNOWN"),
                "n_players": len(team.roster), "n_qb_rostered": n_qb,
                "legal_roster": legal_roster, "has_duplicate_player": has_dupes,
                "lineup_is_legal": lineup.lineup_is_legal, "lineup_failure_reason": lineup.lineup_failure_reason,
                "starting_lineup_points": lineup.starting_lineup_points,
                "bench_option_value": lineup.bench_option_value,
                "total_roster_utility": lineup.total_roster_utility,
                "raw_all_rostered_points": lineup.raw_all_rostered_points,
                "budget_remaining_unspent_cash": team.budget_remaining,
            })

            if not legal_roster:
                reason = "DUPLICATE_PLAYER" if has_dupes else (
                    "INCOMPLETE_ROSTER" if len(team.roster) != 15 else "NEGATIVE_BUDGET"
                )
                failure_rows.append({
                    "seed": seed, "team": team_name, "failure_type": "ILLEGAL_ROSTER",
                    "reason": reason, "n_players": len(team.roster), "budget_remaining": team.budget_remaining,
                })
            if not lineup.lineup_is_legal:
                failure_rows.append({
                    "seed": seed, "team": team_name, "failure_type": "ILLEGAL_STARTING_LINEUP",
                    "reason": lineup.lineup_failure_reason, "n_players": len(team.roster), "budget_remaining": team.budget_remaining,
                })

    results_df = pd.DataFrame(team_result_rows)
    sales_df = pd.DataFrame(sale_rows)

    n_one_dollar_sales = int((sales_df["sale_price"] == 1).sum())
    n_forced_final_slot_sales = int(sales_df["forced_final_slot"].sum())
    # Duplicate sale = the same player name sold more than once within one seed's log
    # (would indicate the pool wasn't correctly removing sold players).
    n_duplicate_sales = int(sales_df.groupby("seed")["player"].apply(lambda s: s.duplicated().sum()).sum())
    # Keeper sale = a confirmed keeper/college-rights-hold name appearing as a SOLD
    # player in the live auction log (NOT a team's own pre-loaded keeper roster --
    # every team's roster legitimately contains its own keepers already).
    keeper_sale_mask = sales_df["player"].isin(confirmed_keeper_names)
    n_keeper_sales = int(keeper_sale_mask.sum())
    for _, row in sales_df[keeper_sale_mask].iterrows():
        failure_rows.append({
            "seed": row["seed"], "team": row["winning_team"], "failure_type": "KEEPER_SOLD_IN_AUCTION",
            "reason": row["player"], "n_players": "", "budget_remaining": "",
        })

    rosters_path = OUT_DIR / "smoke_rosters.csv"
    sales_path = OUT_DIR / "smoke_sales.csv"
    results_path = OUT_DIR / "smoke_team_results.csv"
    failures_path = OUT_DIR / "smoke_failures.csv"

    pd.DataFrame(roster_rows).to_csv(rosters_path, index=False)
    sales_df.to_csv(sales_path, index=False)
    results_df.to_csv(results_path, index=False)
    if failure_rows:
        pd.DataFrame(failure_rows).to_csv(failures_path, index=False)
    else:
        failures_path.write_text("seed,team,failure_type,reason,n_players,budget_remaining\n")

    sam_results = results_df[results_df["team"] == "Sam"]

    summary = {
        "safe_for_draft_use": False,
        "safe_for_draft_use_reason": (
            "Phase 2 explicitly does not publish player prices, draft targets, or strategy advice. "
            "No strategy has been re-validated under the corrected legal-lineup fitness function "
            "(evolution was not run in phase 2), and several teams' keeper state is only "
            "PARTIALLY_CONFIRMED (see team_starting_states.csv keeper_state_status). "
            "This smoke test proves the pipeline runs and produces legal outcomes -- nothing more."
        ),
        "n_seeds": N_SEEDS,
        "n_teams": len(teams_template),
        "legal_roster_rate": float(results_df["legal_roster"].mean()),
        "legal_lineup_rate": float(results_df["lineup_is_legal"].mean()),
        "avg_qbs_per_team": float(results_df["n_qb_rostered"].mean()),
        "max_qbs_on_one_team": int(results_df["n_qb_rostered"].max()),
        "avg_unspent_cash": float(results_df["budget_remaining_unspent_cash"].mean()),
        "max_unspent_cash": float(results_df["budget_remaining_unspent_cash"].max()),
        "n_one_dollar_sales": n_one_dollar_sales,
        "n_duplicate_sales": n_duplicate_sales,
        "n_keeper_sales": n_keeper_sales,
        "n_forced_final_slot_sales": n_forced_final_slot_sales,
        "n_total_sales": len(sales_df),
        "sam_avg_legal_lineup_points": float(sam_results["starting_lineup_points"].mean()),
        "sam_avg_bench_option_value": float(sam_results["bench_option_value"].mean()),
        "sam_avg_total_utility": float(sam_results["total_roster_utility"].mean()),
        "teams_by_keeper_state_status": (
            results_df.drop_duplicates("team")[["team", "keeper_state_status"]]
            .set_index("team")["keeper_state_status"].to_dict()
        ),
        "outputs": {
            "smoke_rosters": str(rosters_path), "smoke_sales": str(sales_path),
            "smoke_team_results": str(results_path), "smoke_failures": str(failures_path),
        },
    }

    summary_path = OUT_DIR / "smoke_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    print(f"Wrote {rosters_path} ({len(roster_rows)} rows)")
    print(f"Wrote {sales_path} ({len(sale_rows)} rows)")
    print(f"Wrote {results_path} ({len(team_result_rows)} rows)")
    print(f"Wrote {failures_path} ({len(failure_rows)} rows)")
    print(f"Wrote {summary_path}")
    print("\n--- SMOKE SIMULATION SUMMARY (NOT draft advice) ---")
    for k, v in summary.items():
        if k not in ("outputs", "teams_by_keeper_state_status"):
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
