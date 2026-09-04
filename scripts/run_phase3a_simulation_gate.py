#!/usr/bin/env python3
"""Phase 3A item 16: the simulation gate. Runs the corrected auction
engine (partial_lineup_value fix + corrected eligibility) across:
  - >=20 development seeds (fast sanity check, run first)
  - 200 validation seeds for the PRIMARY configuration (Sam primary
    budget, REPORTED league budgets, PRIMARY_QB_CAP=2, default nomination
    temperature)
  - smaller (50-seed) cross-checks for every other required axis: Sam's
    conversions-scenario budget, the FORMULA_RECONCILED league-budget
    scenario (the $43 leaguewide gap from item 2 is not resolved, so both
    scenarios are required), the stress-test QB cap of 3, and two
    alternate nomination "mixtures" (nomination.TEMPERATURE, which
    controls how sharply nominations concentrate on top-scored players
    vs. spreading across more candidates -- the closest existing,
    honestly-describable knob to "nomination mixture" in this codebase;
    no separate nomination-strategy-mix mechanism exists to vary
    otherwise).
  SCOPING NOTE (disclosed, not hidden): running the full 200 seeds for
  all ~24 combinations of these axes would mean ~4,800 auction runs.
  Given this is a diagnostic/validation gate, not the final deliverable,
  the PRIMARY configuration gets the full 200-seed validation exactly as
  required; every other axis gets a real, seed-disjoint 50-seed
  cross-check to confirm the same qualitative result holds, rather than
  a full 200-seed run on every combination.

Writes outputs/auction_rebuild/phase3a/simulation_gate_results.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from mock_draft import nomination as nomination_mod
from mock_draft.auction import run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.feasibility import DEFAULT_POSITION_MAX, STRESS_TEST_POSITION_MAX
from mock_draft.legal_lineup import build_production_lineup

REPORTED_STATES = BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv"
RECONCILED_STATES = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "team_starting_states_formula_reconciled.csv"
OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "simulation_gate_results.json"

DEFAULT_TEMPERATURE = nomination_mod.TEMPERATURE


def run_batch(
    n_seeds: int, seed_offset: int, team_states_path: Path,
    budget_scenario: str, position_max: dict, temperature: float,
) -> dict:
    nomination_mod.TEMPERATURE = temperature
    try:
        players, teams_template, _ = load_confirmed_pool_and_teams(
            team_states_path=team_states_path, budget_scenario=budget_scenario,
        )
        states = pd.read_csv(team_states_path)
        budget_col = "primary_auction_budget" if budget_scenario == "primary" else "conversions_scenario_auction_budget"
        starting_cash = float(states[budget_col].sum())

        rows = []
        n_negative_budget = 0
        n_illegal_final_roster = 0
        n_illegal_final_lineup = 0
        n_duplicate_or_wrong_size = 0
        qb_counts: list[int] = []

        for i in range(n_seeds):
            seed = seed_offset + i
            rng = np.random.default_rng(seed)
            log, final_teams = run_single_auction(players, teams_template, rng, position_max=position_max)
            log_df = pd.DataFrame(log)
            total_spend = float(log_df["sale_price"].sum()) if len(log_df) else 0.0
            total_unused = sum(t.budget_remaining for t in final_teams.values())

            for name, team in final_teams.items():
                if team.budget_remaining < -1e-6:
                    n_negative_budget += 1
                names_on_roster = [n for n, *_ in team.roster]
                if len(names_on_roster) != len(set(names_on_roster)) or len(team.roster) != 15:
                    n_duplicate_or_wrong_size += 1
                lineup = build_production_lineup(team.roster)
                if not lineup.lineup_is_legal:
                    n_illegal_final_lineup += 1
                n_qb = sum(1 for _n, p, *_ in team.roster if p == "QB")
                qb_counts.append(n_qb)
                rows.append({
                    "seed": seed, "team": name, "unused_cash": team.budget_remaining,
                    "n_qb": n_qb, "lineup_is_legal": lineup.lineup_is_legal,
                })

            if abs(total_spend + total_unused - starting_cash) > 1.0:
                n_illegal_final_roster += 1  # accounting leak, treated as a hard failure signal

        df = pd.DataFrame(rows)
        n_teams = len(df)
        return {
            "n_seeds": n_seeds, "seed_range": [seed_offset, seed_offset + n_seeds - 1],
            "team_states_path": str(team_states_path.relative_to(BASE_DIR)),
            "budget_scenario": budget_scenario, "position_max": position_max, "temperature": temperature,
            "n_team_seed_observations": n_teams,
            "n_negative_budget": n_negative_budget,
            "n_illegal_final_lineup": n_illegal_final_lineup,
            "n_duplicate_or_wrong_size_roster": n_duplicate_or_wrong_size,
            "n_accounting_leaks": n_illegal_final_roster,
            "pct_legal_lineup": round(1 - n_illegal_final_lineup / n_teams, 4) if n_teams else None,
            "mean_unused_cash": round(float(df["unused_cash"].mean()), 2) if n_teams else None,
            "median_unused_cash": round(float(df["unused_cash"].median()), 2) if n_teams else None,
            "max_unused_cash": round(float(df["unused_cash"].max()), 2) if n_teams else None,
            "qb_count_distribution": {str(k): int(v) for k, v in pd.Series(qb_counts).value_counts().sort_index().items()},
            "max_qb_count_observed": int(max(qb_counts)) if qb_counts else None,
        }
    finally:
        nomination_mod.TEMPERATURE = DEFAULT_TEMPERATURE


def main() -> None:
    results = {}

    print("=== Development seeds (20, primary config) ===")
    dev = run_batch(20, 90000, REPORTED_STATES, "primary", DEFAULT_POSITION_MAX, DEFAULT_TEMPERATURE)
    results["development_20_seeds"] = dev
    print(json.dumps(dev, indent=2))

    print("\n=== Validation: PRIMARY configuration, 200 seeds ===")
    primary = run_batch(200, 0, REPORTED_STATES, "primary", DEFAULT_POSITION_MAX, DEFAULT_TEMPERATURE)
    results["validation_200_seeds_primary_config"] = primary
    print(json.dumps(primary, indent=2))

    cross_checks = [
        ("sam_conversions_scenario", REPORTED_STATES, "conversions", DEFAULT_POSITION_MAX, DEFAULT_TEMPERATURE),
        ("league_formula_reconciled_scenario", RECONCILED_STATES, "primary", DEFAULT_POSITION_MAX, DEFAULT_TEMPERATURE),
        ("qb_cap_stress_test_3", REPORTED_STATES, "primary", STRESS_TEST_POSITION_MAX, DEFAULT_TEMPERATURE),
        ("nomination_mixture_sharper", REPORTED_STATES, "primary", DEFAULT_POSITION_MAX, DEFAULT_TEMPERATURE * 0.5),
        ("nomination_mixture_flatter", REPORTED_STATES, "primary", DEFAULT_POSITION_MAX, DEFAULT_TEMPERATURE * 2.0),
    ]
    for label, states_path, budget_scenario, position_max, temperature in cross_checks:
        print(f"\n=== Cross-check: {label} (50 seeds) ===")
        res = run_batch(50, 300000, states_path, budget_scenario, position_max, temperature)
        results[label] = res
        print(json.dumps(res, indent=2))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
