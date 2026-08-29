"""Parse FantasyPros-style Excel exports (multi-row player blocks)."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from auction_model import config
from auction_model.data_pipeline import EXCLUDED_POSITIONS, _normalize_name

# Column indices in the export's stat row (header at col 4+).
COL_ECR = 7
COL_WEEKLY_PROJ = 8
COL_SEASON_FAN_PTS = 10
COL_GP = 5
COL_PASS_YDS = 14
COL_PASS_TD = 15
COL_INT = 16
COL_RUSH_ATT = 17
COL_RUSH_YDS = 18
COL_RUSH_TD = 19
COL_TARGETS = 20
COL_REC = 21
COL_REC_YDS = 22
COL_REC_TD = 23
COL_TWO_PT = 25
COL_FUM_LOST = 26
COL_PLAYER_NAME = 3

TEAM_POS_PATTERN = re.compile(
    r"(?:Note|Notes)([A-Z]{2,3}|[A-Z][a-z]{1,3})\s*-\s*(QB|RB|WR|TE|K|DST)"
)
ECR_POSITION_PATTERN = re.compile(r"^(QB|RB|WR|TE|K|DST)")

TEAM_ALIASES = {
    "JAC": "JAX",
    "WSH": "WAS",
}


def _find_header_row(raw: pd.DataFrame) -> int:
    for idx in range(min(10, len(raw))):
        row = raw.iloc[idx].astype(str).tolist()
        if "Proj" in row and "Fan Pts" in row:
            return idx
    raise ValueError("Could not find header row in fantasy_data.xlsx sheet")


def _to_number(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_nfl_team(team: str) -> str:
    normalized = team.upper()
    return TEAM_ALIASES.get(normalized, normalized)


def _is_stat_row(raw: pd.DataFrame, row_idx: int) -> bool:
    ecr = raw.iloc[row_idx, COL_ECR]
    if pd.isna(ecr):
        return False
    return bool(ECR_POSITION_PATTERN.match(str(ecr)))


def _looks_like_player_name(value) -> bool:
    if pd.isna(value):
        return False
    text = str(value).strip()
    if not text or text.startswith(("Video Forecast", "Sun ", "Mon ", "Thu ", "Sat ")):
        return False
    if TEAM_POS_PATTERN.search(text):
        return False
    return True


def parse_fantasy_data_sheet(raw: pd.DataFrame, sheet_name: str) -> tuple[pd.DataFrame, list[str]]:
    """Parse one sheet from ``fantasy_data.xlsx`` into a flat player table."""
    _find_header_row(raw)  # validates format
    log: list[str] = []
    rows: list[dict] = []

    row_idx = 0
    while row_idx < len(raw) - 2:
        if not _is_stat_row(raw, row_idx):
            row_idx += 1
            continue

        stat_row = raw.iloc[row_idx]
        name_row = raw.iloc[row_idx + 1, COL_PLAYER_NAME]
        notes_row = raw.iloc[row_idx + 2, COL_PLAYER_NAME]

        if not _looks_like_player_name(name_row):
            log.append(f"{sheet_name} row {row_idx + 1}: stat row without player name -> skipped.")
            row_idx += 1
            continue

        notes_text = "" if pd.isna(notes_row) else str(notes_row)
        team_pos = TEAM_POS_PATTERN.search(notes_text)
        if not team_pos:
            log.append(f"{sheet_name} row {row_idx + 1}: could not parse team/position for {name_row!r}.")
            row_idx += 1
            continue

        nfl_team = _normalize_nfl_team(team_pos.group(1))
        position = team_pos.group(2)
        if position in EXCLUDED_POSITIONS:
            row_idx += 4
            continue

        stat_values = {
            "pass_yd": _to_number(stat_row[COL_PASS_YDS]),
            "pass_td": _to_number(stat_row[COL_PASS_TD]),
            "interception": _to_number(stat_row[COL_INT]),
            "rush_yd": _to_number(stat_row[COL_RUSH_YDS]),
            "rush_td": _to_number(stat_row[COL_RUSH_TD]),
            "reception": _to_number(stat_row[COL_REC]),
            "rec_yd": _to_number(stat_row[COL_REC_YDS]),
            "rec_td": _to_number(stat_row[COL_REC_TD]),
            "fumble_lost": _to_number(stat_row[COL_FUM_LOST]),
            "two_pt": _to_number(stat_row[COL_TWO_PT]),
        }

        scored_points = config.score_from_stats(stat_values)
        vendor_season_points = _to_number(stat_row[COL_SEASON_FAN_PTS])
        vendor_weekly_proj = _to_number(stat_row[COL_WEEKLY_PROJ])

        rows.append(
            {
                "player": str(name_row).strip(),
                "position": position,
                "nfl_team": nfl_team,
                "ecr": str(stat_row[COL_ECR]).strip(),
                "games": _to_number(stat_row[COL_GP]),
                "vendor_weekly_proj": vendor_weekly_proj,
                "vendor_season_points": vendor_season_points,
                "projected_points": scored_points if scored_points > 0 else vendor_season_points,
                **stat_values,
                "source_sheet": sheet_name,
            }
        )
        row_idx += 4

    df = pd.DataFrame(rows)
    if not df.empty:
        df["_key"] = df["player"].map(_normalize_name)
        dupes = df[df.duplicated("_key", keep=False)]
        if not dupes.empty:
            for key in dupes["_key"].unique():
                names = dupes.loc[dupes["_key"] == key, "player"].tolist()
                log.append(f"Duplicate normalized name {key!r}: {names} -> kept first row.")
            df = df.drop_duplicates("_key", keep="first")
        df = df.drop(columns=["_key"]).reset_index(drop=True)

    return df, log


def load_fantasy_data_xlsx(path: str | Path) -> dict[str, tuple[pd.DataFrame, list[str]]]:
    """Load and parse both sheets from the workbook."""
    path = Path(path)
    workbook = pd.ExcelFile(path)
    parsed: dict[str, tuple[pd.DataFrame, list[str]]] = {}

    sheet_map = {
        "Last Year": "actuals_2025",
        "Projections": "projections_2026",
    }
    for sheet_name, label in sheet_map.items():
        if sheet_name not in workbook.sheet_names:
            raise ValueError(f"Expected sheet {sheet_name!r} in {path}")
        raw = pd.read_excel(path, sheet_name=sheet_name, header=None)
        parsed[label] = parse_fantasy_data_sheet(raw, sheet_name)

    return parsed


def to_projections_csv(df: pd.DataFrame) -> pd.DataFrame:
    """Shape parsed projection rows for ``run_valuation.py``."""
    columns = [
        "player", "position", "nfl_team", "projected_points",
        "pass_yd", "pass_td", "interception",
        "rush_yd", "rush_td",
        "reception", "rec_yd", "rec_td",
        "fumble_lost", "two_pt",
    ]
    return df[columns].copy()


def to_actuals_csv(df: pd.DataFrame) -> pd.DataFrame:
    """Keep extra context columns for the prior-season sheet."""
    columns = [
        "player", "position", "nfl_team", "ecr", "games",
        "vendor_weekly_proj", "vendor_season_points", "projected_points",
        "pass_yd", "pass_td", "interception",
        "rush_yd", "rush_td",
        "reception", "rec_yd", "rec_td",
        "fumble_lost", "two_pt",
    ]
    return df[columns].rename(columns={"projected_points": "scored_points_league_rules"}).copy()
