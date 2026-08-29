"""Load and clean the league's historical salary data."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = ["team", "player", "position", "salary_2025", "notes"]


def load_historical_salaries(path: str | Path) -> tuple[pd.DataFrame, list[str]]:
    """Parse the raw historical-salary CSV into a clean dataframe.

    Returns (dataframe, data_quality_log). Blank salaries become a real
    ``pandas.NA`` (never imputed) so downstream code can tell "unknown" from
    "zero". Exact duplicate (team, player) rows are collapsed, preferring the
    row with a non-null salary and keeping the union of notes -- this is
    reported in the log rather than silently dropped.
    """
    df = pd.read_csv(path, dtype=str)
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"historical salary CSV missing columns: {missing_cols}")

    log: list[str] = []

    df["player"] = df["player"].str.strip()
    df["team"] = df["team"].str.strip()
    df["position"] = df["position"].str.strip().str.upper()
    df["notes"] = df["notes"].fillna("").str.strip()

    df["salary_2025"] = pd.to_numeric(df["salary_2025"], errors="coerce")
    n_null_salary = int(df["salary_2025"].isna().sum())
    if n_null_salary:
        log.append(f"{n_null_salary} rows have no confirmed 2025 salary -> left null.")

    df["has_confirmed_salary"] = df["salary_2025"].notna()

    dupe_mask = df.duplicated(subset=["team", "player"], keep=False)
    if dupe_mask.any():
        dupes = df[dupe_mask]
        for (team, player), group in dupes.groupby(["team", "player"]):
            log.append(
                f"Duplicate roster row for {player} ({team}): "
                f"{len(group)} entries, salaries={group['salary_2025'].tolist()}, "
                f"notes={group['notes'].tolist()} -> collapsed to one row."
            )

        def collapse(group: pd.DataFrame) -> pd.Series:
            row = group.sort_values("has_confirmed_salary", ascending=False).iloc[0].copy()
            combined_notes = "; ".join(n for n in group["notes"] if n)
            row["notes"] = combined_notes
            return row

        df = (
            df.groupby(["team", "player"], as_index=False, sort=False)
            .apply(collapse, include_groups=False)
            .reset_index(drop=True)
        )
        df = df[REQUIRED_COLUMNS + ["has_confirmed_salary"]]

    df["is_tagged_2025"] = df["notes"].str.contains("tagged", case=False)
    df["on_ir"] = df["notes"].str.contains(r"\bIR\b", case=False, regex=True)
    df["games_played_note"] = df["notes"]

    df = df.reset_index(drop=True)
    return df, log


def load_optional_csv(path: str | Path) -> pd.DataFrame | None:
    """Load a user-supplied CSV if it exists and has data rows, else None."""
    p = Path(path)
    if not p.exists():
        return None
    df = pd.read_csv(p)
    if df.empty:
        return None
    return df


def _normalize_name(name: str) -> str:
    """Normalize a player name for cross-source matching: lowercase, strip
    punctuation and suffixes (Jr/Sr/II/III/IV) that vary between sources."""
    s = str(name).lower().replace(".", "").replace("'", "")
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    s = re.sub(r"[^a-z ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Positions this league doesn't roster at all -- excluded from the
# draftable universe outright, never priced or listed as unpriced.
EXCLUDED_POSITIONS = {"K", "DST"}


def load_fantasypros_rankings(path: str | Path) -> pd.DataFrame | None:
    """Load a FantasyPros 'ALL Rankings' export into the draftable-universe
    shape: player, position, nfl_team, fp_overall_rank, fp_position_rank,
    fp_tier, bye_week. K/DST are dropped -- this league doesn't roster them.

    This is rank/tier data, not point projections or stat lines -- it can
    never populate ``projected_points`` (that would mean fabricating a
    projection from a rank, which this pipeline refuses to do). Its only
    job is to define the full set of players draftable this year, beyond
    whoever happened to be on a 2025 league roster.
    """
    p = Path(path)
    if not p.exists():
        return None
    df = pd.read_csv(p)
    if df.empty:
        return None
    df.columns = [c.strip() for c in df.columns]

    out = pd.DataFrame()
    out["player"] = df["PLAYER NAME"].str.strip()
    out["position"] = df["POS"].str.extract(r"^([A-Z]+)")[0]
    out["fp_position_rank"] = df["POS"].str.extract(r"([0-9]+)$")[0]
    out["nfl_team"] = df["TEAM"].str.strip()
    out["fp_overall_rank"] = pd.to_numeric(df["RK"], errors="coerce")
    out["fp_tier"] = df.get("TIERS")
    out["bye_week"] = df.get("BYE WEEK")
    out["_key"] = out["player"].map(_normalize_name)

    out = out[~out["position"].isin(EXCLUDED_POSITIONS)].reset_index(drop=True)
    return out


def expand_pool_with_full_universe(pool: pd.DataFrame, fp_rankings: pd.DataFrame | None) -> pd.DataFrame:
    """Add rows for any FantasyPros-ranked player not already in ``pool``
    (i.e. not on a 2025 league roster). They get no historical salary --
    they're draftable this year but unpriceable without a projection, and
    will correctly surface in the "no data to price" sanity check rather
    than being silently absent from the auction sheet entirely.
    """
    if fp_rankings is None or fp_rankings.empty:
        return pool

    pool = pool.copy()
    pool["_key"] = pool["player"].map(_normalize_name)
    existing_keys = set(pool["_key"])

    new_rows = fp_rankings[~fp_rankings["_key"].isin(existing_keys)].drop_duplicates("_key")

    added = pd.DataFrame({
        "team": pd.NA,
        "player": new_rows["player"].values,
        "position": new_rows["position"].values,
        "nfl_team": new_rows["nfl_team"].values,
        "salary_2025": pd.NA,
        "notes": [
            f"not on a 2025 league roster -- FantasyPros 2026 rank #{r}"
            + (f", tier {t}" if pd.notna(t) else "")
            for r, t in zip(new_rows["fp_overall_rank"], new_rows["fp_tier"])
        ],
        "has_confirmed_salary": False,
        "is_tagged_2025": False,
        "on_ir": False,
        "will_keep": False,
        "tag_used": False,
        "keep_source": "not_prev_rostered",
        "keeper_price_2026": pd.NA,
    })

    result = pd.concat([pool.drop(columns=["_key"]), added], ignore_index=True)
    result["salary_2025"] = pd.to_numeric(result["salary_2025"], errors="coerce")
    return result
