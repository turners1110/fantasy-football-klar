"""Authoritative player-contract table and data-quality reporting."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import config, data_pipeline, keepers

PRIORITY_ORDER = {
    "duplicate_player_ownership": 1,
    "missing_prior_salary": 2,
    "missing_canonical_match": 3,
    "paul_rule_unverified": 4,
    "conflicting_position": 5,
    "conflicting_salary": 6,
    "unresolved_tag_status": 7,
    "missing_projection": 8,
    "unknown_salary_origin": 9,
    "duplicate_row": 10,
}


def _load_nflverse_games(path: Path | None) -> pd.DataFrame | None:
    if path is None or not path.exists():
        return None
    stats = pd.read_csv(path, usecols=["player_display_name", "position", "games"])
    stats = stats[stats["position"].isin({"QB", "RB", "WR", "TE"})]
    stats["_key"] = stats["player_display_name"].map(data_pipeline._normalize_name)
    return stats.groupby("_key", as_index=False)["games"].max()


def build_player_contracts(
    salaries: pd.DataFrame,
    projections: pd.DataFrame | None,
    fp_rankings: pd.DataFrame | None,
    overrides: pd.DataFrame | None = None,
    nflverse_stats_path: Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (contracts, data_quality_issues)."""
    df = salaries.copy()
    df["canonical_player_id"] = df["player"].map(data_pipeline._normalize_name)
    games_df = _load_nflverse_games(nflverse_stats_path)

    proj_lookup = {}
    if projections is not None and not projections.empty:
        p = projections.copy()
        p["_key"] = p["player"].map(data_pipeline._normalize_name)
        proj_lookup = p.set_index("_key")["projected_points"].to_dict()

    fp_lookup = {}
    if fp_rankings is not None and not fp_rankings.empty:
        fp_lookup = set(fp_rankings["_key"].tolist())

    nflverse_keys = set()
    if games_df is not None:
        nflverse_keys = set(games_df["_key"].tolist())
        games_map = games_df.set_index("_key")["games"].to_dict()
        df["games_played"] = df["canonical_player_id"].map(games_map)
        df["games_played_source"] = np.where(
            df["canonical_player_id"].isin(nflverse_keys), "nflverse_2025_reg", pd.NA
        )
        verified_paul = df["games_played"].notna() & (df["games_played"] < config.PAUL_RULE_MIN_GAMES)
        df.loc[verified_paul, "paul_rule_eligible"] = True
        df.loc[verified_paul, "paul_rule_verified"] = True
        df.loc[verified_paul, "paul_rule_source"] = "nflverse_games_verified"
    else:
        df["games_played"] = pd.NA
        df["games_played_source"] = pd.NA

    if overrides is not None and not overrides.empty:
        df = _apply_contract_overrides(df, overrides)

    def _keeper_costs(row: pd.Series) -> tuple[float | None, float | None]:
        if pd.isna(row["salary_2025"]):
            return None, None
        std = keepers.keeper_price(row["salary_2025"], False, bool(row["paul_rule_eligible"]))
        tagged = keepers.keeper_price(row["salary_2025"], True, bool(row["paul_rule_eligible"]))
        return std, tagged

    costs = df.apply(_keeper_costs, axis=1, result_type="expand")
    df["standard_keeper_cost"] = costs[0]
    df["tagged_keeper_cost"] = costs[1]
    df["selected_keeper_cost"] = df["standard_keeper_cost"]
    df["prior_salary"] = df["salary_2025"]
    df["salary_origin"] = df.get("salary_origin", "UNKNOWN")
    df["salary_origin_confidence"] = df.get("origin_confidence", 0.0)
    df["contract_source"] = "historical_salaries_2025_raw.csv"
    df["tagged_last_year"] = df.get("is_tagged_2025", False)
    df["tagged_this_year"] = False
    df["projection_available"] = df["canonical_player_id"].isin(proj_lookup.keys())
    df["projection_source"] = np.where(
        df["projection_available"], "projections_2026.csv", pd.NA
    )
    df["fantasypros_match"] = df["canonical_player_id"].isin(fp_lookup)
    df["nflverse_match"] = df["canonical_player_id"].isin(nflverse_keys)
    df["historical_salary_available"] = df["has_confirmed_salary"]
    df["manual_override"] = False

    issues: list[dict] = []
    for idx, row in df.iterrows():
        row_issues = _contract_issues(row)
        if row_issues:
            df.at[idx, "data_quality_status"] = row_issues[0]["issue_type"]
            df.at[idx, "data_quality_notes"] = "; ".join(i["detail"] for i in row_issues)
            issues.extend(row_issues)
        else:
            df.at[idx, "data_quality_status"] = "OK"
            df.at[idx, "data_quality_notes"] = ""

    dup_keys = df[df.duplicated("canonical_player_id", keep=False)]
    for key, group in dup_keys.groupby("canonical_player_id"):
        if key:
            issues.append({
                "priority": PRIORITY_ORDER["duplicate_player_ownership"],
                "issue_type": "duplicate_player_ownership",
                "team": ", ".join(group["team"].astype(str)),
                "player": group["player"].iloc[0],
                "detail": f"Player appears on {len(group)} teams: {group['team'].tolist()}",
            })

    issues_df = pd.DataFrame(issues)
    if not issues_df.empty:
        issues_df = issues_df.sort_values(["priority", "player"]).reset_index(drop=True)

    contract_cols = [
        "team", "player", "canonical_player_id", "position", "prior_salary",
        "salary_origin", "salary_origin_confidence", "contract_source",
        "standard_keeper_cost", "tagged_keeper_cost", "selected_keeper_cost",
        "tagged_last_year", "tagged_this_year", "paul_rule_eligible", "paul_rule_verified",
        "games_played", "games_played_source", "projection_available", "projection_source",
        "fantasypros_match", "nflverse_match", "historical_salary_available",
        "manual_override", "data_quality_status", "data_quality_notes", "notes",
    ]
    return df[contract_cols], issues_df


def _contract_issues(row: pd.Series) -> list[dict]:
    issues = []
    player = row["player"]
    team = row["team"]

    if not row["historical_salary_available"]:
        issues.append({
            "priority": PRIORITY_ORDER["missing_prior_salary"],
            "issue_type": "missing_prior_salary",
            "team": team, "player": player,
            "detail": "No confirmed prior salary on roster record.",
        })
    if not row["fantasypros_match"] and not row["nflverse_match"]:
        issues.append({
            "priority": PRIORITY_ORDER["missing_canonical_match"],
            "issue_type": "missing_canonical_match",
            "team": team, "player": player,
            "detail": "No FantasyPros or nflverse identity match.",
        })
    if str(row.get("notes", "")).find("IR") >= 0 and not row["paul_rule_verified"]:
        issues.append({
            "priority": PRIORITY_ORDER["paul_rule_unverified"],
            "issue_type": "paul_rule_unverified",
            "team": team, "player": player,
            "detail": "IR note present but Paul Rule not verified via games played.",
        })
    if not row["projection_available"]:
        issues.append({
            "priority": PRIORITY_ORDER["missing_projection"],
            "issue_type": "missing_projection",
            "team": team, "player": player,
            "detail": "No 2026 projection available.",
        })
    if str(row["salary_origin"]).startswith("UNKNOWN"):
        issues.append({
            "priority": PRIORITY_ORDER["unknown_salary_origin"],
            "issue_type": "unknown_salary_origin",
            "team": team, "player": player,
            "detail": f"Salary origin {row['salary_origin']} — not confirmed from league records.",
        })
    return issues


def _apply_contract_overrides(df: pd.DataFrame, overrides: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    ov = overrides.copy()
    ov["team"] = ov["team"].str.strip()
    ov["player"] = ov["player"].str.strip()
    df = df.set_index(["team", "player"])
    for key, row in ov.set_index(["team", "player"]).iterrows():
        if key not in df.index:
            continue
        df.loc[key, "manual_override"] = True
        if "confirmed_salary_origin" in row and pd.notna(row["confirmed_salary_origin"]):
            origin = str(row["confirmed_salary_origin"]).strip()
            df.loc[key, "salary_origin"] = origin
            df.loc[key, "salary_origin_confidence"] = config.SALARY_ORIGIN_RELIABILITY.get(origin, 0.0)
        if "confirmed_prior_salary" in row and pd.notna(row["confirmed_prior_salary"]):
            df.loc[key, "salary_2025"] = float(row["confirmed_prior_salary"])
            df.loc[key, "has_confirmed_salary"] = True
        if "confirmed_paul_rule" in row and str(row["confirmed_paul_rule"]).lower() in {"true", "1", "yes", "y"}:
            df.loc[key, "paul_rule_eligible"] = True
            df.loc[key, "paul_rule_verified"] = True
            df.loc[key, "paul_rule_source"] = "manual_override"
        if "confirmed_games_played" in row and pd.notna(row["confirmed_games_played"]):
            gp = int(row["confirmed_games_played"])
            df.loc[key, "games_played"] = gp
            df.loc[key, "games_played_source"] = "manual_override"
            if gp < config.PAUL_RULE_MIN_GAMES:
                df.loc[key, "paul_rule_eligible"] = True
                df.loc[key, "paul_rule_verified"] = True
    return df.reset_index()
