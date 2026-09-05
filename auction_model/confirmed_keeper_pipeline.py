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
    "Sam": 225, "James": 297, "Ryan J": 257, "Jason": 209, "Travis": 307,
    "CJ": 264, "Shane": 324,
}

# GATE A CLOSURE (V3 repair): the official commissioner table's per-team
# starting budget and TOTAL protected-player count, transcribed directly
# from the commissioner's own table (see
# outputs/auction_rebuild/official_repair_v1/commissioner_data_transcription.csv).
# starting_budget is independently reconciled -- it always equals
# keeper_spend + the winning remaining-budget value above, for every
# team, confirmed by the assertion in compute_team_states below.
# official_protected_count is the authoritative total (6-8 per team);
# n_veteran_keepers + n_college_rights_holds from keepers_2026_confirmed.csv
# only sums to 6 for EVERY team in that file (it has no college-rights
# rows for Brad or Reid), which is 1 short of the official 7 for each of
# those two teams -- the identity of that 7th protected player is
# UNKNOWN (the commissioner has not supplied per-team protected-player
# NAMES, only counts). unidentified_protected_count below tracks that
# gap explicitly and honestly rather than silently under-counting it.
OFFICIAL_STARTING_BUDGET = {
    "Brandon": 405, "Brad": 400, "Travis": 420, "Coby": 400, "Shane": 400,
    "James": 400, "CJ": 400, "Ryan J": 393, "Jason": 395, "Evan": 390,
    "Sam": 387, "Reid": 410,
}
OFFICIAL_PROTECTED_COUNT = {
    "Brandon": 6, "Brad": 7, "Travis": 8, "Coby": 6, "Shane": 7,
    "James": 6, "CJ": 6, "Ryan J": 6, "Jason": 6, "Evan": 6,
    "Sam": 8, "Reid": 7,
}

# Explicit, user-stated overrides -- highest priority per the source
# hierarchy (explicit manual override > commissioner sheet). Only Sam's
# values are directly user-confirmed; every other team relies on the
# sheet alone.
#
# OFFICIAL COMMISSIONER DATA REPAIR (see
# outputs/auction_rebuild/official_repair_v1/): Sam's real remaining
# auction budget is $225 per the commissioner's own table (Woody
# Johnson's D...efence: $387 start / 8 protected / $162 keeper / $225
# remaining). The old $223/$221 primary/conversions split is RETIRED --
# both scenario columns now hold the single official $225 figure.
# Travis's SHEET_REPORTED_BUDGET above was also corrected from the old,
# simply-wrong $264 to the official $307 (Bishop Sycamore) -- his
# keeper_spend ($113) already matched the commissioner table exactly,
# which is how this owner was identified as Bishop Sycamore in the
# owner-to-team mapping audit.
USER_CONFIRMED_BUDGET = {
    "Sam": {"primary": 225, "conversions": 225},
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
        official_starting_budget = OFFICIAL_STARTING_BUDGET.get(team)
        official_protected = OFFICIAL_PROTECTED_COUNT.get(team)
        unidentified_protected = max(0, (official_protected or 0) - n_keepers - n_holds)
        # Gate A hard assertion (Part 4 of the spec): each team's official
        # starting budget must equal keeper_spend + the winning remaining
        # budget value, exactly -- this is the "starting budget - keeper
        # salaries = remaining budget" reconciliation the spec requires,
        # checked per-team at data-build time rather than only in a test.
        if official_starting_budget is not None:
            assert abs(official_starting_budget - (keeper_spend + primary_budget)) < 0.01, (
                f"{team}: official starting budget {official_starting_budget} != "
                f"keeper_spend {keeper_spend} + remaining {primary_budget}"
            )
        state_rows.append({
            "season": 2026, "team_id": team, "team_name": team,
            "n_veteran_keepers": n_keepers, "n_college_rights_holds": n_holds,
            "keeper_spend": keeper_spend, "cash_adjustments": cash_adj,
            "sheet_reported_remaining_budget": sheet_reported if sheet_reported is not None else "",
            "primary_auction_budget": primary_budget,
            "conversions_scenario_auction_budget": conversions_budget,
            "budget_source": winning_source,
            "keeper_state_status": keeper_status,
            "official_starting_budget": official_starting_budget,
            "official_protected_count": official_protected,
            "unidentified_protected_count": unidentified_protected,
            "notes": "College-rights conversions scenario only defined for Sam (2 x $1)" if team != "Sam" else "",
        })

    return state_rows, conflict_rows
