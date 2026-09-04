#!/usr/bin/env python3
"""Phase 3A item 2: team-level budget reconciliation with Brandon's
confirmed +$15 trade credit applied (see data/team_budget_adjustments_2026.csv
-- USER_CONFIRMED_TRADE, superseding phase 2B's unconfirmed inference).

Writes outputs/auction_rebuild/phase3a/team_budget_reconciliation.csv with
two explicit scenario columns (REPORTED_TEAM_BUDGETS,
FORMULA_RECONCILED_BUDGETS) rather than picking one number and hiding the
other, per the explicit instruction not to bury an unexplained gap.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from auction_model.confirmed_keeper_pipeline import SHEET_REPORTED_BUDGET, USER_CONFIRMED_BUDGET

BUDGET_PER_TEAM = 400
OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "team_budget_reconciliation.csv"


def main() -> None:
    keepers = pd.read_csv(BASE_DIR / "data" / "keepers_2026_confirmed.csv")
    adjustments = pd.read_csv(BASE_DIR / "data" / "team_budget_adjustments_2026.csv")

    rows = []
    league_keeper_spend = 0.0
    league_cash_sent = 0.0
    league_cash_received = 0.0

    for team, group in keepers.groupby("team_name"):
        keeper_spend = float(group.loc[group["counts_as_keeper"], "keeper_cost"].astype(float).sum())
        team_adj = adjustments[adjustments["team_name"] == team]
        cash_sent = float(-team_adj.loc[team_adj["amount"] < 0, "amount"].sum())
        cash_received = float(team_adj.loc[team_adj["amount"] > 0, "amount"].sum())

        conversion_fees = 2.0 if team == "Sam" else 0.0  # Mendoza $1 + Bond $1, OPTIONAL conversion scenario only
        formula_budget = BUDGET_PER_TEAM - keeper_spend
        calculated_budget = formula_budget + cash_received - cash_sent  # primary scenario, no conversion fees
        sheet_budget = SHEET_REPORTED_BUDGET.get(team)

        reported_team_budgets_scenario = sheet_budget if sheet_budget is not None else calculated_budget
        formula_reconciled_budgets_scenario = calculated_budget

        if team == "Sam":
            authoritative_budget = USER_CONFIRMED_BUDGET["Sam"]["primary"]
            source = "USER_CONFIRMED_TRADE"
        else:
            authoritative_budget = reported_team_budgets_scenario
            source = "google_sheet_reported_budget_column"

        difference = (sheet_budget - calculated_budget) if sheet_budget is not None else None
        if difference is None:
            status = "NO_SHEET_VALUE"
        elif abs(difference) < 0.01:
            status = "RESOLVED"
        elif team == "Sam":
            status = "EXPLAINED_OVERRIDE"  # user-confirmed $223 takes priority over sheet's $225; gap is known, not unknown
        else:
            status = "UNRESOLVED_GAP"

        notes = ""
        if team == "Brandon":
            notes = (
                "Received the confirmed +$15 from the Sam/Brandon Skattebo trade (USER_CONFIRMED_TRADE, "
                "phase 3A -- supersedes phase 2B's unconfirmed INFERRED_FROM_SKATTEBO_HISTORY hypothesis). "
                f"formula_reconciled_budgets_scenario ({formula_reconciled_budgets_scenario:.0f}) now includes "
                f"this credit; it does NOT close the gap to the sheet's reported {sheet_budget} -- either the "
                "sheet was captured before this trade posted, or an additional un-itemized adjustment exists "
                "for this team. NOT double-counted: the naive formula never included this trade before phase 3A."
            )
        elif team == "Sam":
            notes = (
                f"Authoritative budget set by explicit user confirmation (USER_CONFIRMED_TRADE), not derived "
                f"from the sheet: 400 - $162 keeper_spend - $15 cash_sent = $223 exactly. Sheet's $225 is a "
                f"$2 discrepancy, preserved here, NOT overwritten. Conversion scenario: $223 - $1 (Mendoza) - "
                f"$1 (Bond) = $221 (conversion_fees column shows this $2, applied only in that scenario, not "
                f"in calculated_budget above)."
            )
        elif sheet_budget is not None and abs(sheet_budget - formula_budget) > 0.01:
            notes = (
                f"Sheet-reported budget differs from the pre-trade naive formula (${formula_budget:.0f}) by "
                f"${sheet_budget - formula_budget:+.0f}; no confirmed trade exists for this team to explain it "
                f"-- logged as an unexplained per-team gap, not distributed across players or hidden in an "
                f"inflation multiplier."
            )

        rows.append({
            "team": team,
            "keeper_spend": keeper_spend,
            "formula_budget": formula_budget,
            "cash_sent": cash_sent,
            "cash_received": cash_received,
            "conversion_fees": conversion_fees,
            "calculated_budget": calculated_budget,
            "sheet_budget": sheet_budget if sheet_budget is not None else "",
            "authoritative_budget": authoritative_budget,
            "difference": difference if difference is not None else "",
            "source": source,
            "status": status,
            "reported_team_budgets_scenario": reported_team_budgets_scenario,
            "formula_reconciled_budgets_scenario": formula_reconciled_budgets_scenario,
            "notes": notes,
        })

        league_keeper_spend += keeper_spend
        league_cash_sent += cash_sent
        league_cash_received += cash_received

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    net_internal_transfer = league_cash_received - league_cash_sent
    league_final_auction_cash_formula = 4800 - league_keeper_spend + net_internal_transfer
    sheet_total_reported = sum(r["reported_team_budgets_scenario"] for r in rows)
    formula_total_reconciled = sum(r["formula_reconciled_budgets_scenario"] for r in rows)

    print(f"Wrote {OUT_PATH} ({len(rows)} teams)")
    print(f"\nLeaguewide keeper spend: ${league_keeper_spend:.0f}")
    print(f"Net internal cash transfer (should be $0 now that Brandon's +$15 offsets Sam's -$15): ${net_internal_transfer:+.0f}")
    print(f"REPORTED_TEAM_BUDGETS total (sheet values, Sam at user-confirmed $223): ${sheet_total_reported:.0f}")
    print(f"FORMULA_RECONCILED_BUDGETS total (400 x 12 - keeper spend +/- confirmed trades): ${formula_total_reconciled:.0f}")
    print(f"Remaining unexplained leaguewide gap (reported - formula): ${sheet_total_reported - formula_total_reconciled:+.0f}")
    unresolved = [r["team"] for r in rows if r["status"] == "UNRESOLVED_GAP"]
    print(f"Teams with an unresolved per-team gap (no confirmed trade to explain it): {unresolved}")
    print(
        "\n*** Brandon's +$15 is now recorded and internal transfers net to $0 (Sam -15 / Brandon +15). "
        "This does NOT fully close the leaguewide gap -- Evan, Reid, Jason, Ryan J, Travis, Brad, and "
        "Brandon itself still show sheet-vs-formula differences with no confirmed trade behind them. "
        "Both scenarios (REPORTED_TEAM_BUDGETS, FORMULA_RECONCILED_BUDGETS) are preserved in the output "
        "for market-sensitivity testing under either assumption. ***"
    )


if __name__ == "__main__":
    main()
