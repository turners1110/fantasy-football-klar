"""Load and clean the league's historical salary data."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import config

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
    # v4 Part 2: Paul Rule needs VERIFIED games played, not an IR note --
    # this dataset has no games-played column at all, so eligibility here
    # is inferred from the IR note and explicitly labeled unverified.
    df["paul_rule_eligible"] = False
    df["paul_rule_verified"] = False
    df["paul_rule_source"] = "unverified_no_games_data"

    df["salary_origin"], df["origin_confidence"] = classify_salary_origin(df)

    df = df.reset_index(drop=True)
    return df, log


def classify_salary_origin(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Infer salary-origin labels. Nothing is promoted to *_CONFIRMED without
    explicit manual override or league-record evidence in the source data."""
    origin = pd.Series("UNKNOWN", index=df.index, dtype=object)
    confidence = pd.Series(
        config.SALARY_ORIGIN_RELIABILITY["UNKNOWN"], index=df.index, dtype=float
    )

    no_salary = df["salary_2025"].isna()
    origin[no_salary] = "UNKNOWN"
    confidence[no_salary] = 0.0

    is_one_dollar_no_notes = (df["salary_2025"] == 1) & (df["notes"].fillna("") == "")
    origin[is_one_dollar_no_notes] = "UNKNOWN_DOLLAR_ONE"
    confidence[is_one_dollar_no_notes] = config.SALARY_ORIGIN_RELIABILITY["UNKNOWN_DOLLAR_ONE"]

    has_real_salary = df["salary_2025"].notna() & ~is_one_dollar_no_notes
    origin[has_real_salary] = "UNKNOWN_NON_DOLLAR_ONE"
    confidence[has_real_salary] = config.SALARY_ORIGIN_RELIABILITY["UNKNOWN_NON_DOLLAR_ONE"]

    tagged = df.get("is_tagged_2025", pd.Series(False, index=df.index))
    keeper_escalation = tagged & df["salary_2025"].notna() & (df["salary_2025"] > 1)
    origin[keeper_escalation] = "KEEPER_ESCALATION_CONFIRMED"
    confidence[keeper_escalation] = config.SALARY_ORIGIN_RELIABILITY["KEEPER_ESCALATION_CONFIRMED"]

    return origin, confidence


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


def merge_fp_tiers(pool: pd.DataFrame, fp_rankings: pd.DataFrame | None) -> pd.DataFrame:
    """Attach FantasyPros' own analyst-drawn tier per player (fp_tier) to
    ``pool`` by normalized name. Real auctions price in tiers, not a smooth
    curve -- players within a tier go for near-identical prices, with a real
    cliff between tiers -- so this is the pricing model's tier signal.
    Players FantasyPros doesn't rank (or that don't match) get no tier and
    fall back to individual-player pricing.
    """
    pool = pool.copy()
    if fp_rankings is None or fp_rankings.empty:
        pool["fp_tier"] = pd.NA
        return pool

    pool["_key"] = pool["player"].map(_normalize_name)
    tier_lookup = fp_rankings.drop_duplicates("_key").set_index("_key")["fp_tier"]
    pool["fp_tier"] = pool["_key"].map(tier_lookup)
    pool["fp_tier"] = pd.to_numeric(pool["fp_tier"], errors="coerce")
    return pool.drop(columns=["_key"])


def fill_anchor_fallback(pool: pd.DataFrame) -> pd.DataFrame:
    """Priority 5 fallback chain for players with NO usable anchor and NO
    projection at all (e.g. deep waiver-tier names FantasyPros ranks but
    Yahoo didn't project) -- without this they'd stay silently unpriced
    even though they have real position/tier information to lean on.

    Order actually implemented (see README/changelog for the two skipped
    rungs and why):
      1. Yahoo projection -- not this function's job, already the primary
         signal upstream; this only runs for players who have neither.
      2. (SKIPPED) an alternate raw projection source -- none exists in
         this repo today. Flagged as a real gap, not silently faked.
      3. FantasyPros (position, tier) -> this league's own historical price
         curve: median `salary_2025` among real players confirmed in that
         same (position, tier) group.
      4. Position-only median salary_2025, if no tier-mate has a confirmed
         salary at all.
      5. Left null -- flagged in `notes` for manual review -- if neither
         3 nor 4 produces a number (no salaried comparable exists anywhere
         at that position).

    Filled rows get `has_confirmed_salary=True` so they flow through the
    normal anchor-dollars proportional split (and are therefore
    automatically rescaled to sum to the real remaining budget along with
    everyone else -- no separate rescale step needed), plus
    `anchor_source="tier_median_fallback"` so they're never confused with a
    real observed salary downstream.
    """
    pool = pool.copy()
    pool["anchor_source"] = np.where(pool["has_confirmed_salary"], "observed_2025_salary", pd.NA)

    needs_fallback = pool["salary_2025"].isna() & pool.get("projected_points", pd.Series(pd.NA, index=pool.index)).isna()
    if not needs_fallback.any():
        return pool

    observed = pool[pool["has_confirmed_salary"]]
    tier_median = observed.groupby(["position", "fp_tier"])["salary_2025"].median()
    position_median = observed.groupby("position")["salary_2025"].median()

    for idx in pool.index[needs_fallback]:
        position = pool.at[idx, "position"]
        tier = pool.at[idx, "fp_tier"] if "fp_tier" in pool.columns else pd.NA

        value = pd.NA
        source = None
        if pd.notna(tier) and (position, tier) in tier_median.index:
            value = tier_median.loc[(position, tier)]
            source = "tier_median_fallback"
        elif position in position_median.index:
            value = position_median.loc[position]
            source = "position_median_fallback"

        if pd.notna(value):
            pool.at[idx, "salary_2025"] = value
            pool.at[idx, "has_confirmed_salary"] = True
            pool.at[idx, "anchor_source"] = source
            existing_notes = pool.at[idx, "notes"] if pd.notna(pool.at[idx, "notes"]) else ""
            pool.at[idx, "notes"] = (existing_notes + f"; anchor imputed via {source}").strip("; ")
        else:
            existing_notes = pool.at[idx, "notes"] if pd.notna(pool.at[idx, "notes"]) else ""
            pool.at[idx, "notes"] = (existing_notes + "; FLAG: no anchor or projection available at all -- manual review").strip("; ")

    return pool


def expand_pool_with_full_universe(
    pool: pd.DataFrame,
    fp_rankings: pd.DataFrame | None,
    also_exclude: pd.Series | None = None,
) -> pd.DataFrame:
    """Add rows for any FantasyPros-ranked player not already in ``pool``
    and not already rostered anywhere else this year. They get no
    historical salary -- they're draftable this year but unpriceable
    without a projection, and will correctly surface in the "no data to
    price" sanity check rather than being silently absent from the auction
    sheet entirely.

    ``pool`` is normally the *keeper-filtered* live-draftable pool (kept
    players already removed) -- so on its own it can't tell a kept
    superstar from a real free agent, and would wrongly re-add the kept
    player as if they were undrafted. Pass ``also_exclude`` (a Series of
    player names covering the FULL roster, keepers included) so kept
    players never leak back into the live pool as phantom free agents.
    """
    if fp_rankings is None or fp_rankings.empty:
        return pool

    pool = pool.copy()
    pool["_key"] = pool["player"].map(_normalize_name)
    existing_keys = set(pool["_key"])
    if also_exclude is not None:
        existing_keys |= set(also_exclude.map(_normalize_name))

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
