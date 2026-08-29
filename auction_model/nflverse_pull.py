"""Download NFL data from nflverse and convert it for this league's pipeline."""

from __future__ import annotations

from pathlib import Path

import nflreadpy as nfl
import pandas as pd
import polars as pl

from auction_model import config

FANTASY_POSITIONS = {"QB", "RB", "WR", "TE"}

NFLVERSE_TO_LEAGUE_STATS = {
    "pass_yd": "passing_yards",
    "pass_td": "passing_tds",
    "interception": "passing_interceptions",
    "rush_yd": "rushing_yards",
    "rush_td": "rushing_tds",
    "reception": "receptions",
    "rec_yd": "receiving_yards",
    "rec_td": "receiving_tds",
    "fumble_lost": "fumbles_lost_total",
}


def _to_pandas(frame: pl.DataFrame) -> pd.DataFrame:
    return frame.to_pandas(use_pyarrow_extension_array=True)


def pull_player_stats(seasons: list[int]) -> pd.DataFrame:
    """Regular-season player stats for the requested seasons."""
    frame = nfl.load_player_stats(seasons=seasons, summary_level="reg")
    return _to_pandas(frame)


def pull_rosters(seasons: list[int]) -> pd.DataFrame:
    frame = nfl.load_rosters(seasons=seasons)
    return _to_pandas(frame)


def pull_draft_picks(seasons: list[int]) -> pd.DataFrame:
    frame = nfl.load_draft_picks(seasons=seasons)
    return _to_pandas(frame)


def save_dataset(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def pull_all(
    seasons: list[int],
    draft_seasons: list[int],
    output_dir: str | Path,
) -> dict[str, Path]:
    """Fetch core nflverse tables and write CSVs to ``output_dir``."""
    output_dir = Path(output_dir)
    written: dict[str, Path] = {}

    stats = pull_player_stats(seasons)
    stats_path = save_dataset(stats, output_dir / "player_stats_reg.csv")
    written["player_stats_reg"] = stats_path

    for season in seasons:
        season_stats = stats[stats["season"] == season]
        path = save_dataset(season_stats, output_dir / f"player_stats_reg_{season}.csv")
        written[f"player_stats_reg_{season}"] = path

    rosters = pull_rosters(seasons)
    written["rosters"] = save_dataset(rosters, output_dir / "rosters.csv")
    for season in seasons:
        season_rosters = rosters[rosters["season"] == season]
        path = save_dataset(season_rosters, output_dir / f"rosters_{season}.csv")
        written[f"rosters_{season}"] = path

    draft = pull_draft_picks(draft_seasons)
    written["draft_picks"] = save_dataset(draft, output_dir / "draft_picks.csv")
    for season in draft_seasons:
        season_draft = draft[draft["season"] == season]
        path = save_dataset(season_draft, output_dir / f"draft_picks_{season}.csv")
        written[f"draft_picks_{season}"] = path

    return written


def _two_point_total(row: pd.Series) -> float:
    cols = [
        "passing_2pt_conversions",
        "rushing_2pt_conversions",
        "receiving_2pt_conversions",
    ]
    total = 0.0
    for col in cols:
        value = row.get(col)
        if pd.notna(value):
            total += float(value)
    return total


def build_projections_from_stats(stats: pd.DataFrame, season: int) -> pd.DataFrame:
    """Convert nflverse seasonal stats into this project's projections CSV format."""
    season_df = stats[stats["season"] == season].copy()
    season_df = season_df[season_df["position"].isin(FANTASY_POSITIONS)].copy()

    rows: list[dict] = []
    for _, row in season_df.iterrows():
        stat_row: dict[str, float] = {}
        for league_col, nfl_col in NFLVERSE_TO_LEAGUE_STATS.items():
            value = row.get(nfl_col)
            stat_row[league_col] = 0.0 if pd.isna(value) else float(value)
        stat_row["two_pt"] = _two_point_total(row)

        projected_points = config.score_from_stats(stat_row)
        if projected_points <= 0:
            continue

        rows.append(
            {
                "player": row["player_display_name"],
                "position": row["position"],
                "nfl_team": row.get("recent_team", ""),
                "projected_points": projected_points,
                **stat_row,
            }
        )

    projections = pd.DataFrame(rows)
    projections = projections.sort_values("projected_points", ascending=False).reset_index(drop=True)
    return projections
