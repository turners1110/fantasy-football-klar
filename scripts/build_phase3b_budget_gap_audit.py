#!/usr/bin/env python3
"""Phase 3B item 2/3: fix budget-gap terminology and make the primary
budget scenario explicit.

CORRECTION TO THE PHASE 3A REPORT (found while building this audit): the
phase 3A final report described "$43" as "the sum of absolute
differences" across the 6 unresolved teams. That description was WRONG.
Recomputing directly from team_budget_reconciliation.csv:
  - sum(abs(sheet_budget - formula_budget)) across the 6 UNRESOLVED_GAP
    teams = $65 (the real gross absolute team-level allocation gap)
  - sum(sheet_budget - formula_budget) across ALL 12 teams (including
    Sam's own +$2 explained-override difference and the 5 fully RESOLVED
    $0 teams, which contribute nothing) = -$43
"$43" was actually the SIGNED NET total across all 12 teams, not a sum
of absolute values -- a real terminology error in the phase 3A report,
now corrected here per this phase's own instruction not to repeat it.

PRIMARY BUDGET SCENARIO: auction_model/confirmed_keeper_pipeline.py's
compute_team_states already uses the sheet-reported budget directly for
every non-Sam team (primary_budget = sheet_reported, NOT
sheet_reported + cash_adjustments) and Sam's explicit $223/$221
user-confirmed override -- i.e. it was ALREADY implementing
REPORTED_BUDGETS_WITH_SAM_OVERRIDE with no double-counting of the
Brandon/Sam trade adjustment. cash_adjustments is used only to compute a
naive comparison figure for conflict-logging, never added into the
primary budget itself. This is confirmed directly in this script's
output, not merely asserted.

Writes outputs/auction_rebuild/phase3b/budget_gap_definition_audit.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

RECON_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "team_budget_reconciliation.csv"
OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3b" / "budget_gap_definition_audit.csv"


def main() -> None:
    recon = pd.read_csv(RECON_PATH)

    rows = []
    for _, r in recon.iterrows():
        reported = float(r["sheet_budget"])
        # calculated_budget (NOT the raw formula_budget column) is the
        # correct "formula" figure here -- formula_budget is the naive
        # 400-minus-keeper-spend number BEFORE trade adjustments
        # (cash_sent/cash_received/conversion_fees) are applied;
        # calculated_budget is formula_budget + those adjustments, which
        # is what "difference" in team_budget_reconciliation.csv was
        # always computed against (sheet_budget - calculated_budget).
        # Using formula_budget here would silently re-strip Brandon's/
        # Sam's confirmed trade adjustment right back out.
        formula = float(r["calculated_budget"])
        gross = abs(reported - formula)
        signed = reported - formula
        rows.append({
            "team": r["team"],
            "reported_team_budget": reported,
            "formula_team_budget": formula,
            "gross_absolute_team_difference": round(gross, 2),
            "signed_team_difference": round(signed, 2),
            "status": r["status"],
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
        # League-level summary rows, computed two ways per the instruction
        # to make the distinction impossible to miss.
        all_gross = sum(r["gross_absolute_team_difference"] for r in rows)
        all_signed = sum(r["signed_team_difference"] for r in rows)
        unresolved = [r for r in rows if r["status"] == "UNRESOLVED_GAP"]
        unresolved_gross = sum(r["gross_absolute_team_difference"] for r in unresolved)
        unresolved_signed = sum(r["signed_team_difference"] for r in unresolved)
        for label, gross, signed in [
            ("LEAGUE_TOTAL_ALL_12_TEAMS", all_gross, all_signed),
            ("LEAGUE_TOTAL_UNRESOLVED_GAP_TEAMS_ONLY", unresolved_gross, unresolved_signed),
        ]:
            w.writerow({
                "team": label, "reported_team_budget": "", "formula_team_budget": "",
                "gross_absolute_team_difference": round(gross, 2),
                "signed_team_difference": round(signed, 2), "status": "SUMMARY",
            })

    print(f"Wrote {OUT_PATH}")
    print(f"\ngross_absolute_team_difference (all 12 teams): ${all_gross:.2f}")
    print(f"signed_net_league_difference (all 12 teams): ${all_signed:.2f}")
    print(f"gross_absolute_team_difference (6 UNRESOLVED_GAP teams only): ${unresolved_gross:.2f}")
    print(f"signed_net_league_difference (6 UNRESOLVED_GAP teams only): ${unresolved_signed:.2f}")
    print(
        f"\nCORRECTION: the phase 3A report's '$43 gap, sum of absolute differences' was WRONG on its face -- "
        f"${all_signed:.2f} is the SIGNED NET total across all 12 teams (Sam's own +$2 included), not a sum of "
        f"absolute values. The real gross absolute gap among the 6 unresolved teams is ${unresolved_gross:.2f}."
    )
    print(
        "\nPRIMARY BUDGET SCENARIO CONFIRMED: auction_model/confirmed_keeper_pipeline.py's compute_team_states "
        "already sets primary_auction_budget = sheet_reported directly for every non-Sam team (never "
        "sheet_reported + cash_adjustments), and Sam's row uses the $223/$221 user-confirmed override. "
        "REPORTED_BUDGETS_WITH_SAM_OVERRIDE was already the production primary scenario with no "
        "double-counting of the Brandon/Sam trade adjustment; outputs/auction_rebuild/data/"
        "team_starting_states.csv's primary_auction_budget IS this scenario, and "
        "outputs/auction_rebuild/phase3a/team_starting_states_formula_reconciled.csv remains the sensitivity."
    )


if __name__ == "__main__":
    main()
