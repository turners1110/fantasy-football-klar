#!/usr/bin/env python3
"""Merge the raw FantasyPros per-stat projection exports
(data/fantasypros_projections/*.csv) into data/projections_2026.csv.

Why raw stats, not FantasyPros' own FPTS column: FPTS is scored under
FantasyPros' generic default scoring, not this league's actual 0.5 PPR /
6pt rush+rec TD / 4pt pass TD rules. Feeding raw per-stat lines into the
existing projections_2026.csv template lets run_valuation.py re-score
everyone itself (auction_model.config.score_from_stats), consistent with
every other player in the pool.

Additive only: never overwrites or removes an existing row in
projections_2026.csv (which may already be populated from another
pipeline) -- only appends players not already present by normalized name.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent.parent
SRC_DIR = BASE_DIR / "data" / "fantasypros_projections"
OUT_PATH = BASE_DIR / "data" / "projections_2026.csv"

TEMPLATE_COLUMNS = [
    "player", "position", "nfl_team", "projected_points",
    "pass_yd", "pass_td", "interception",
    "rush_yd", "rush_td",
    "reception", "rec_yd", "rec_td",
    "fumble_lost", "two_pt",
]


def _normalize_name(name: str) -> str:
    name = re.sub(r"[.'’]", "", str(name))
    name = re.sub(r"\s+(Jr|Sr|II|III|IV)\.?$", "", name, flags=re.IGNORECASE)
    return name.strip().lower()


def _clean_raw(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df[df["Player"].astype(str).str.strip().ne("")]
    df = df.dropna(subset=["Player"])
    return df.reset_index(drop=True)


def load_flx() -> pd.DataFrame:
    """RB+WR combined file; POS column is like 'RB1', 'WR12' -- strip the
    rank digits to get the real position."""
    df = _clean_raw(SRC_DIR / "FantasyPros_Projections_FLX.csv")
    cols = list(df.columns)
    # Expected: Player, Team, POS, ATT, YDS, TDS, REC, YDS, TDS, FL, FPTS
    rush_yd_col, rush_td_col = cols[4], cols[5]
    rec_col, rec_yd_col, rec_td_col = cols[6], cols[7], cols[8]
    fl_col = cols[9]
    out = pd.DataFrame({
        "player": df["Player"],
        "position": df["POS"].astype(str).str.extract(r"([A-Za-z]+)")[0],
        "nfl_team": df["Team"],
        "rush_yd": pd.to_numeric(df[rush_yd_col], errors="coerce"),
        "rush_td": pd.to_numeric(df[rush_td_col], errors="coerce"),
        "reception": pd.to_numeric(df[rec_col], errors="coerce"),
        "rec_yd": pd.to_numeric(df[rec_yd_col], errors="coerce"),
        "rec_td": pd.to_numeric(df[rec_td_col], errors="coerce"),
        "fumble_lost": pd.to_numeric(df[fl_col], errors="coerce"),
    })
    return out


def load_qb() -> pd.DataFrame:
    df = _clean_raw(SRC_DIR / "FantasyPros_Projections_QB.csv")
    cols = list(df.columns)
    # Expected: Player, Team, ATT, CMP, YDS, TDS, INTS, ATT, YDS, TDS, FL, FPTS
    pass_yd_col, pass_td_col, int_col = cols[4], cols[5], cols[6]
    rush_yd_col, rush_td_col, fl_col = cols[8], cols[9], cols[10]
    out = pd.DataFrame({
        "player": df["Player"],
        "position": "QB",
        "nfl_team": df["Team"],
        "pass_yd": pd.to_numeric(df[pass_yd_col], errors="coerce"),
        "pass_td": pd.to_numeric(df[pass_td_col], errors="coerce"),
        "interception": pd.to_numeric(df[int_col], errors="coerce"),
        "rush_yd": pd.to_numeric(df[rush_yd_col], errors="coerce"),
        "rush_td": pd.to_numeric(df[rush_td_col], errors="coerce"),
        "fumble_lost": pd.to_numeric(df[fl_col], errors="coerce"),
    })
    return out


def load_te() -> pd.DataFrame:
    df = _clean_raw(SRC_DIR / "FantasyPros_Projections_TE.csv")
    cols = list(df.columns)
    # Expected: Player, Team, REC, YDS, TDS, FL, FPTS
    rec_col, rec_yd_col, rec_td_col, fl_col = cols[2], cols[3], cols[4], cols[5]
    out = pd.DataFrame({
        "player": df["Player"],
        "position": "TE",
        "nfl_team": df["Team"],
        "reception": pd.to_numeric(df[rec_col], errors="coerce"),
        "rec_yd": pd.to_numeric(df[rec_yd_col], errors="coerce"),
        "rec_td": pd.to_numeric(df[rec_td_col], errors="coerce"),
        "fumble_lost": pd.to_numeric(df[fl_col], errors="coerce"),
    })
    return out


def main() -> None:
    combined = pd.concat([load_flx(), load_qb(), load_te()], ignore_index=True, sort=False)
    for col in TEMPLATE_COLUMNS:
        if col not in combined.columns:
            combined[col] = pd.NA
    combined["projected_points"] = pd.NA  # let run_valuation.py score raw stats itself
    combined = combined[TEMPLATE_COLUMNS]
    combined["_key"] = combined["player"].map(_normalize_name)
    combined = combined.drop_duplicates(subset="_key", keep="first")

    existing = pd.DataFrame(columns=TEMPLATE_COLUMNS)
    if OUT_PATH.exists():
        existing = pd.read_csv(OUT_PATH)
        for col in TEMPLATE_COLUMNS:
            if col not in existing.columns:
                existing[col] = pd.NA
        existing = existing[TEMPLATE_COLUMNS]
    existing_keys = set(existing["player"].map(_normalize_name)) if len(existing) else set()

    new_rows = combined[~combined["_key"].isin(existing_keys)].drop(columns=["_key"])
    merged = pd.concat([existing, new_rows], ignore_index=True)
    merged.to_csv(OUT_PATH, index=False)

    print(f"FantasyPros raw rows parsed: {len(combined)} (FLX+QB+TE, deduped)")
    print(f"Already present in {OUT_PATH.name}: {len(combined) - len(new_rows)}")
    print(f"New players appended: {len(new_rows)}")
    print(f"{OUT_PATH.name} now has {len(merged)} total rows.")


if __name__ == "__main__":
    main()
