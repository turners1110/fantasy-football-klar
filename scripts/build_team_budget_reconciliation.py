#!/usr/bin/env python3
"""Team-level budget reconciliation -- phase 2B item 7/8. Traces every
team's budget arithmetic individually rather than reporting a single
aggregate leaguewide gap, per the explicit instruction not to hide a
discrepancy inside one inflation-style number.

Required equation (per team):
    team_final_budget = 400 - keeper_spend + cash_received - cash_sent - confirmed_conversion_charges
Required equation (leaguewide):
    league_final_auction_cash = 4800 - league_keeper_spend - external_league_charges
Internal cash transfers must net to zero across all twelve teams -- they
move cash between teams, they never destroy or create it leaguewide.

Writes outputs/auction_rebuild/audit/team_budget_reconciliation.csv
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


def main() -> None:
    keepers = pd.read_csv(BASE_DIR / "data" / "keepers_2026_confirmed.csv")
    adjustments = pd.read_csv(BASE_DIR / "data" / "team_budget_adjustments_2026.csv")

    # Item 5 in the trade-tracing checklist: "identify both sides of each
    # cash trade." The only recorded trade is Sam's -$15 for Cam Skattebo
    # (user-confirmed directly). The historical roster data
    # (data/historical_salaries_2025_raw.csv) shows Brandon held Cam
    # Skattebo at $18 in 2025 -- the only team with any Skattebo record at
    # all -- making Brandon the highest-probability counterparty by direct
    # evidence. This is a HIGH-CONFIDENCE INFERENCE, not a confirmed
    # source: nothing in the tracked inputs states Brandon received cash,
    # and applying a +$15 credit to Brandon does NOT fully close his own
    # $5 sheet-vs-formula gap (it makes a different, larger gap: see the
    # 'brandon_with_inferred_trade_credit' scenario column below). Per the
    # explicit instruction not to invent a recipient, this is recorded as
    # an inferred hypothesis for the audit trail, NOT applied to the
    # primary reconciliation numbers.
    INFERRED_TRADE_RECIPIENT = "Brandon"
    INFERRED_TRADE_AMOUNT = 15
    INFERRED_TRADE_EVIDENCE = (
        "data/historical_salaries_2025_raw.csv row 25: 'Brandon,Cam Skattebo,RB,18' -- Brandon is the "
        "only team with any historical record of Cam Skattebo. Sam's confirmed keeper file prices "
        "Skattebo at $28 ($18 + $10 standard bump), consistent with this being the same player/salary "
        "chain. UNCONFIRMED: no tracked source states Brandon received the $15."
    )

    rows = []
    league_keeper_spend = 0.0
    league_cash_sent = 0.0
    league_cash_received = 0.0

    for team, group in keepers.groupby("team_name"):
        keeper_count = int(group["counts_as_keeper"].sum())
        keeper_spend = float(group.loc[group["counts_as_keeper"], "keeper_cost"].astype(float).sum())
        franchise_tag_count = int(group["franchise_tag"].fillna(False).astype(bool).sum())
        n_holds = int((~group["counts_as_keeper"]).sum())

        team_adj = adjustments[adjustments["team_name"] == team]
        cash_sent = float(-team_adj.loc[team_adj["amount"] < 0, "amount"].sum())
        cash_received = float(team_adj.loc[team_adj["amount"] > 0, "amount"].sum())
        net_cash = cash_received - cash_sent

        formula_budget_before_trades = BUDGET_PER_TEAM - keeper_spend
        calculated_final_budget = formula_budget_before_trades + net_cash
        sheet_reported = SHEET_REPORTED_BUDGET.get(team)
        difference = (sheet_reported - calculated_final_budget) if sheet_reported is not None else None

        source = "sheet+user_direct_statement" if team == "Sam" else "sheet_only"
        if difference is None:
            resolution_status = "NO_SHEET_VALUE"
        elif abs(difference) < 0.01:
            resolution_status = "RESOLVED"
        else:
            resolution_status = "UNRESOLVED_GAP"

        notes = ""
        if team == INFERRED_TRADE_RECIPIENT:
            scenario_budget = calculated_final_budget + INFERRED_TRADE_AMOUNT
            scenario_diff = sheet_reported - scenario_budget if sheet_reported is not None else None
            notes = (
                f"INFERRED (unconfirmed) trade-recipient hypothesis: if this team received the "
                f"+${INFERRED_TRADE_AMOUNT} from Sam's Skattebo trade, calculated_final_budget would be "
                f"${scenario_budget:.0f} (diff vs sheet: {scenario_diff:+.0f}) instead of "
                f"${calculated_final_budget:.0f} (diff: {difference:+.0f}) -- the hypothesis does NOT "
                f"close this team's gap, it changes which direction it's off. {INFERRED_TRADE_EVIDENCE}"
            )
        if team == "Sam":
            notes = (notes + " " if notes else "") + (
                "Sam's primary budget is set by explicit user statement (223), NOT derived from this "
                "formula -- shown here for comparison only. Required formula for Sam: "
                "400 - $162 keeper_spend - $15 cash_sent = $223 (matches user-required value exactly)."
            )

        rows.append({
            "team": team,
            "starting_budget": BUDGET_PER_TEAM,
            "keeper_count": keeper_count,
            "n_college_rights_holds": n_holds,
            "franchise_tag_count": franchise_tag_count,
            "keeper_spend": keeper_spend,
            "formula_budget_before_trades": formula_budget_before_trades,
            "sheet_reported_budget": sheet_reported if sheet_reported is not None else "",
            "cash_sent": cash_sent,
            "cash_received": cash_received,
            "net_cash_adjustment": net_cash,
            "calculated_final_budget": calculated_final_budget,
            "difference": difference if difference is not None else "",
            "source": source,
            "resolution_status": resolution_status,
            "notes": notes,
        })

        league_keeper_spend += keeper_spend
        league_cash_sent += cash_sent
        league_cash_received += cash_received

    out_path = BASE_DIR / "outputs" / "auction_rebuild" / "audit" / "team_budget_reconciliation.csv"
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # Leaguewide checks (items 8-10 + "internal transfers net to zero").
    league_final_auction_cash = 4800 - league_keeper_spend
    net_internal_transfer = league_cash_received - league_cash_sent
    sheet_total = sum(v for v in SHEET_REPORTED_BUDGET.values()) - SHEET_REPORTED_BUDGET["Sam"] + USER_CONFIRMED_BUDGET["Sam"]["primary"]

    print(f"Wrote {out_path} ({len(rows)} teams)")
    print(f"\nLeaguewide keeper spend: ${league_keeper_spend:.0f}")
    print(f"Leaguewide naive auction cash (4800 - keeper spend): ${league_final_auction_cash:.0f}")
    print(f"Sum of all 12 teams' actual (sheet/user) reported budgets: ${sheet_total:.0f}")
    print(f"Leaguewide gap (naive - actual): ${league_final_auction_cash - sheet_total:+.0f}")
    print(f"Total cash sent across all recorded adjustments: ${league_cash_sent:.0f}")
    print(f"Total cash received across all recorded adjustments: ${league_cash_received:.0f}")
    print(f"Net internal transfer (should be $0 if all trades are two-sided and recorded): ${net_internal_transfer:+.0f}")
    print(
        f"\n*** UNRESOLVED: Sam's recorded -$15 has NO matching +$15 recipient adjustment in "
        f"data/team_budget_adjustments_2026.csv. Net internal transfer is ${net_internal_transfer:+.0f}, "
        f"not $0 -- this $15 is the single, exact, named source of the leaguewide gap traced above from "
        f"the ADJUSTMENTS side. A separate, only-partially-overlapping ~$45 gap also exists between the "
        f"naive per-team formula and the sheet's own reported numbers for OTHER teams "
        f"(Evan, Reid, Jason -- see per-team rows with resolution_status=UNRESOLVED_GAP), independent of "
        f"the Sam/Brandon trade question. Brandon is the highest-confidence candidate recipient for "
        f"Sam's $15 by direct historical-roster evidence (see notes column), but applying it does not "
        f"cleanly close Brandon's own gap either -- so it is recorded as an inferred hypothesis, not "
        f"applied to primary budgets. PHASE 2 STATUS: budget gap NOT fully resolved. ***"
    )


if __name__ == "__main__":
    main()
