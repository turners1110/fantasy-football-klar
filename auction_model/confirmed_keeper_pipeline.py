"""Single authoritative keeper + budget computation, shared by
scripts/build_team_states.py (writes the audit-trail CSVs) and
run_valuation.py's --keeper-mode confirmed (feeds the real price sheet).
Both call `compute_team_states` on the same two tracked input files so
there is exactly one place this arithmetic lives -- per the rebuild
spec's "establish one authoritative keeper and budget pipeline" goal.
"""

from __future__ import annotations

import re

import pandas as pd

BUDGET_PER_TEAM = 400

# The sheet's own "2026 Budget" column value (its own remaining-budget
# calc, which appears to already bake in trades this session has no
# itemized record of for most teams). Recorded here for provenance/
# conflict-detection only -- NOT blindly trusted over an explicit
# higher-priority source (Sam's user-confirmed values win for Sam).
SHEET_REPORTED_BUDGET = {
    "Brandon": 184, "Coby": 274, "Brad": 281, "Reid": 260, "Evan": 184,
    "Sam": 225, "James": 297, "Ryan J": 257, "Jason": 209, "Travis": 264,
    "CJ": 264, "Shane": 324,
}

# Explicit, user-stated overrides -- highest priority per the source
# hierarchy (explicit manual override > commissioner sheet). Only Sam's
# values are directly user-confirmed; every other team relies on the
# sheet alone.
USER_CONFIRMED_BUDGET = {
    "Sam": {"primary": 223, "conversions": 221},
}


def normalize_name(name: str) -> str:
    name = re.sub(r"[.'’]", "", str(name))
    name = re.sub(r"\s+(Jr|Sr|II|III|IV)\.?$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip().lower()
    return name


def compute_identity_issues(keepers: pd.DataFrame, salaries: pd.DataFrame | None = None) -> list[dict]:
    """salaries: optional data/historical_salaries_2025_raw.csv-shaped
    DataFrame. When given, also flags confirmed veteran keepers that
    don't match any row there by normalized name (UNMATCHED_TO_HISTORICAL_SALARY
    -- e.g. a nickname/full-name mismatch, or a player genuinely absent
    from that extraction). PHASE 2B FIX: this check used to live only
    inside run_valuation.py's confirmed-mode loader, duplicated rather
    than shared, so scripts/build_team_states.py's own identity audit
    silently missed it -- the two 'one authoritative pipeline' entry
    points were producing different identity audits depending on which
    one ran last. Now both call this one function with the same inputs."""
    keepers = keepers.copy()
    keepers["_norm"] = keepers["player_name"].map(normalize_name)
    identity_rows = []
    dupes = keepers[keepers.duplicated("_norm", keep=False)].sort_values("_norm")
    for _, row in dupes.iterrows():
        identity_rows.append({
            "issue_type": "DUPLICATE_NORMALIZED_NAME", "player_name": row["player_name"],
            "normalized": row["_norm"], "team": row["team_name"], "detail": "appears more than once after normalization",
        })
    required_checks = ["Kenneth Walker III", "Quentin Johnston", "Jaxson Dart", "Cam Skattebo"]
    for name in required_checks:
        match = keepers[keepers["_norm"] == normalize_name(name)]
        identity_rows.append({
            "issue_type": "REQUIRED_IDENTITY_CHECK", "player_name": name,
            "normalized": normalize_name(name),
            "team": match["team_name"].iloc[0] if len(match) else "NOT_FOUND",
            "detail": "resolved OK" if len(match) == 1 else f"resolved to {len(match)} rows (expected 1)",
        })
    two_team = keepers.groupby("_norm")["team_name"].nunique()
    for norm_name, n_teams in two_team[two_team > 1].items():
        identity_rows.append({
            "issue_type": "PLAYER_ON_MULTIPLE_TEAMS", "player_name": norm_name,
            "normalized": norm_name, "team": "MULTIPLE",
            "detail": f"appears on {n_teams} different teams",
        })
    if salaries is not None and "counts_as_keeper" in keepers.columns:
        salary_norm = set(salaries["player"].map(normalize_name))
        veteran_rows = keepers[keepers["counts_as_keeper"].astype(bool)]
        for _, row in veteran_rows.iterrows():
            if row["_norm"] not in salary_norm:
                identity_rows.append({
                    "issue_type": "UNMATCHED_TO_HISTORICAL_SALARY", "player_name": row["player_name"],
                    "normalized": row["_norm"], "team": row["team_name"],
                    "detail": (
                        f"No row in historical_salaries_2025_raw.csv matched by normalized name -- using "
                        f"confirmed file's own prior_salary (${row.get('prior_salary', '?')}) / "
                        f"keeper_cost (${row.get('keeper_cost', '?')}) directly instead of silently dropping."
                    ),
                })
    if not any(r["issue_type"] not in ("REQUIRED_IDENTITY_CHECK",) for r in identity_rows):
        identity_rows.append({
            "issue_type": "NONE_FOUND", "player_name": "", "normalized": "", "team": "",
            "detail": "No duplicate/ambiguous/multi-team identity issues found in keepers_2026_confirmed.csv",
        })
    return identity_rows


def unresolved_duplicate_identities(identity_rows: list[dict]) -> list[dict]:
    """Identity issues that MUST stop a confirmed-mode run (a duplicate or
    multi-team identity is unresolvable ambiguity, unlike a required
    spot-check note)."""
    return [r for r in identity_rows if r["issue_type"] in ("DUPLICATE_NORMALIZED_NAME", "PLAYER_ON_MULTIPLE_TEAMS")]


def compute_team_states(keepers: pd.DataFrame, adjustments: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    """Returns (state_rows, conflict_rows). Does not raise on its own --
    callers (the CLI script, or run_valuation.py's confirmed mode) decide
    what to do with conflicts/negative budgets."""
    conflict_rows = []
    state_rows = []
    for team, group in keepers.groupby("team_name"):
        keeper_spend = group.loc[group["counts_as_keeper"], "keeper_cost"].astype(float).sum()
        n_keepers = int(group["counts_as_keeper"].sum())
        n_holds = int((~group["counts_as_keeper"]).sum())

        cash_adj = adjustments.loc[adjustments["team_name"] == team, "amount"].sum()
        naive_primary = BUDGET_PER_TEAM - keeper_spend + cash_adj
        sheet_reported = SHEET_REPORTED_BUDGET.get(team)

        if team in USER_CONFIRMED_BUDGET:
            primary_budget = USER_CONFIRMED_BUDGET[team]["primary"]
            conversions_budget = USER_CONFIRMED_BUDGET[team]["conversions"]
            winning_source = "user_direct_statement"
            if sheet_reported is not None and sheet_reported != primary_budget:
                conflict_rows.append({
                    "team": team, "field": "primary_auction_budget",
                    "winning_source": winning_source, "winning_value": primary_budget,
                    "losing_source": "google_sheet_reported_budget_column", "losing_value": sheet_reported,
                    "detail": "User-stated value takes priority over the sheet's own remaining-budget "
                              "column per explicit-override > commissioner-sheet source priority. "
                              "Gap not reconciled to a specific cause (possibly a stale sheet cell "
                              "predating the $15 Skattebo cash trade, or an additional adjustment "
                              "not yet itemized).",
                })
            if naive_primary != primary_budget:
                conflict_rows.append({
                    "team": team, "field": "primary_auction_budget",
                    "winning_source": winning_source, "winning_value": primary_budget,
                    "losing_source": "computed_400_minus_keeper_spend_plus_adjustments", "losing_value": naive_primary,
                    "detail": "Naive formula check vs. user-confirmed value.",
                })
        else:
            primary_budget = sheet_reported if sheet_reported is not None else naive_primary
            conversions_budget = primary_budget
            winning_source = "google_sheet_reported_budget_column" if sheet_reported is not None else "computed_400_minus_keeper_spend"
            if sheet_reported is not None and sheet_reported != naive_primary:
                conflict_rows.append({
                    "team": team, "field": "primary_auction_budget",
                    "winning_source": winning_source, "winning_value": primary_budget,
                    "losing_source": "computed_400_minus_keeper_spend_plus_adjustments", "losing_value": naive_primary,
                    "detail": f"Sheet-reported budget differs from naive calc by ${sheet_reported - naive_primary:+.0f}; "
                              f"likely an un-itemized trade for this team. Sheet value used as it is the more "
                              f"complete real-world source, but the underlying trade is NOT itemized in "
                              f"team_budget_adjustments_2026.csv -- follow up if exact composition matters.",
                })

        keeper_status = "CONFIRMED" if team == "Sam" else "PARTIALLY_CONFIRMED"
        state_rows.append({
            "season": 2026, "team_id": team, "team_name": team,
            "n_veteran_keepers": n_keepers, "n_college_rights_holds": n_holds,
            "keeper_spend": keeper_spend, "cash_adjustments": cash_adj,
            "sheet_reported_remaining_budget": sheet_reported if sheet_reported is not None else "",
            "primary_auction_budget": primary_budget,
            "conversions_scenario_auction_budget": conversions_budget,
            "budget_source": winning_source,
            "keeper_state_status": keeper_status,
            "notes": "College-rights conversions scenario only defined for Sam (2 x $1)" if team != "Sam" else "",
        })

    return state_rows, conflict_rows
