#!/usr/bin/env python3
"""One-off cleaner for fantasy_data.xlsx (an NFL.com-style export where each
player is a 4-row block: stat row, name row, video-forecast/team-pos row,
schedule row, with stat columns repeated under a single shared header row).

Writes:
  data/fantasy_data_last_year_clean.csv  (sheet "Last Year")
  data/fantasy_data_projections_clean.csv (sheet "Projections")

Both use one row per player with real column names, split TEAM/POSITION out
of the buried "Video Forecast...Team - Pos" text, and a `projected_points`
column scored under this league's own rules (auction_model.config.SCORING),
not whatever scoring format the source used internally (its own "Fan Pts"
column is kept alongside, renamed `source_fan_pts`, for comparison only).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from auction_model import config

BASE_DIR = Path(__file__).parent.parent
STAT_COL_LABELS = [
    "pass_yd", "pass_td", "interception",
    "rush_att", "rush_yd", "rush_td",
    "targets", "reception", "rec_yd", "rec_td",
    "total_td_misc", "two_pt", "fumble_lost",
]
SCORING_STAT_COLS = [c for c in STAT_COL_LABELS if c != "targets" and c != "rush_att" and c != "total_td_misc"]

# The team code always immediately follows "...Note" or "...Notes" in this
# source's mashed-together forecast text (e.g. "...NotesDet - RB",
# "...NoteLAR - WR") -- anchor on that rather than grabbing the last 2-4
# letters of whatever word precedes it.
TEAM_POS_RE = re.compile(r"Notes?([A-Za-z]{2,3})\s*-\s*([A-Z]{1,3})\s*$")


def _num(v):
    if pd.isna(v):
        return pd.NA
    if isinstance(v, str):
        v = v.strip()
        if v in ("", "-"):
            return pd.NA
        try:
            return float(v)
        except ValueError:
            return pd.NA
    return v


def parse_sheet(path: Path, sheet_name: str) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name=sheet_name, header=None)

    header_row = None
    for i in range(min(10, len(raw))):
        if str(raw.iat[i, 4]).strip() == "Roster Status":
            header_row = i
            break
    if header_row is None:
        raise ValueError(f"Could not find the 'Roster Status' header row in sheet {sheet_name!r}.")

    rows = []
    i = header_row + 1
    n = len(raw)
    while i + 3 < n:
        stat_row = raw.iloc[i]
        name_row = raw.iloc[i + 1]
        team_pos_row = raw.iloc[i + 2]
        i += 4

        player = name_row[3]
        if pd.isna(player) or not str(player).strip():
            continue
        player = str(player).strip()

        team, position = "", ""
        m = TEAM_POS_RE.search(str(team_pos_row[3]))
        if m:
            team, position = m.group(1).upper(), m.group(2).upper()

        stats = {label: _num(stat_row[14 + j]) for j, label in enumerate(STAT_COL_LABELS)}

        row = {
            "player": player,
            "position": position,
            "nfl_team": team,
            "roster_status": stat_row[4],
            "games_played": _num(stat_row[5]),
            "bye_week": _num(stat_row[6]),
            "ecr_position_rank": stat_row[7],
            "ecr_value": _num(stat_row[8]),
            "source_fan_pts": _num(stat_row[10]),
            "preseason_rank": _num(stat_row[11]),
            "actual_rank": _num(stat_row[12]),
            "pct_rostered": _num(stat_row[13]),
            **stats,
        }
        rows.append(row)

    df = pd.DataFrame(rows)

    def score(row):
        stat_row = {k: row[k] for k in SCORING_STAT_COLS}
        return config.score_from_stats(stat_row)

    df["projected_points"] = df.apply(score, axis=1)

    ordered = [
        "player", "position", "nfl_team", "projected_points",
        "pass_yd", "pass_td", "interception",
        "rush_att", "rush_yd", "rush_td",
        "targets", "reception", "rec_yd", "rec_td",
        "total_td_misc", "two_pt", "fumble_lost",
        "roster_status", "games_played", "bye_week",
        "ecr_position_rank", "ecr_value", "source_fan_pts",
        "preseason_rank", "actual_rank", "pct_rostered",
    ]
    return df[ordered]


def main() -> None:
    src = BASE_DIR / "fantasy_data.xlsx"
    out_last_year = BASE_DIR / "data" / "fantasy_data_last_year_clean.csv"
    out_projections = BASE_DIR / "data" / "fantasy_data_projections_clean.csv"

    last_year = parse_sheet(src, "Last Year")
    last_year.to_csv(out_last_year, index=False)
    print(f"Wrote {out_last_year} ({len(last_year)} players)")

    projections = parse_sheet(src, "Projections")
    projections.to_csv(out_projections, index=False)
    print(f"Wrote {out_projections} ({len(projections)} players)")


if __name__ == "__main__":
    main()
