"""Load and clean the league's historical salary data."""

from __future__ import annotations

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
