#!/usr/bin/env python3
"""Phase 3A item 2 / item 16: build the FORMULA_RECONCILED_BUDGETS
scenario's team_starting_states.csv (item 2 requires two explicit
scenarios -- REPORTED_TEAM_BUDGETS and FORMULA_RECONCILED_BUDGETS --
rather than distributing the unexplained $43 leaguewide gap across
players or hiding it in inflation).

REPORTED_TEAM_BUDGETS is the existing
outputs/auction_rebuild/data/team_starting_states.csv (sheet-reported
budgets, authoritative_budget in team_budget_reconciliation.csv).
FORMULA_RECONCILED_BUDGETS instead uses the naive
(400 - keeper_spend - cash_sent + cash_received) formula for every team
that has no sheet-budget override, i.e.
formula_reconciled_budgets_scenario from
outputs/auction_rebuild/phase3a/team_budget_reconciliation.csv. Sam's
row is identical in both scenarios (her case is EXPLAINED_OVERRIDE, not
an unresolved gap) -- only the 6 UNRESOLVED_GAP teams differ.

Writes outputs/auction_rebuild/phase3a/team_starting_states_formula_reconciled.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

REPORTED_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv"
RECONCILIATION_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "team_budget_reconciliation.csv"
OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "team_starting_states_formula_reconciled.csv"


def main() -> None:
    states = pd.read_csv(REPORTED_PATH)
    recon = pd.read_csv(RECONCILIATION_PATH).set_index("team")

    out = states.copy()
    for i, row in out.iterrows():
        team = row["team_id"]
        formula_budget = float(recon.loc[team, "formula_reconciled_budgets_scenario"])
        reported_budget = float(row["primary_auction_budget"])
        delta = formula_budget - reported_budget  # 0 for RESOLVED/EXPLAINED_OVERRIDE teams
        out.loc[i, "primary_auction_budget"] = round(formula_budget, 2)
        out.loc[i, "conversions_scenario_auction_budget"] = round(
            float(row["conversions_scenario_auction_budget"]) + delta, 2
        )
        out.loc[i, "budget_source"] = (
            row["budget_source"] if delta == 0 else "formula_reconciled_budgets_scenario_phase3a"
        )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_PATH, index=False)
    print(f"Wrote {OUT_PATH}")
    changed = out[out["primary_auction_budget"] != states["primary_auction_budget"]]
    print(f"{len(changed)} of {len(out)} teams differ from the REPORTED scenario:")
    for _, row in changed.iterrows():
        old = states.loc[states['team_id'] == row['team_id'], 'primary_auction_budget'].iloc[0]
        print(f"  {row['team_id']}: reported=${old} -> formula_reconciled=${row['primary_auction_budget']}")


if __name__ == "__main__":
    main()
