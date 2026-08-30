"""Keeper pricing and the pre-auction pool-reduction / inflation estimate.

This is the piece of a generic auction calculator that literally cannot
exist off the shelf: it depends entirely on this league's roster history and
its specific keeper math (prior salary + $10, +$5 with the franchise tag,
or the Paul Rule for anyone who played fewer than 4 games last season).

Because keeper *decisions* are made by twelve different humans and haven't
happened yet, this module ships a transparent, overridable heuristic for
"who probably gets kept" rather than pretending to know. Supply
``keeper_overrides.csv`` (team, player, will_keep, tag_used) with real
decisions as they're announced and they take precedence row-by-row.
"""

from __future__ import annotations

import pandas as pd

from . import config

OVERRIDE_COLUMNS = ["team", "player", "will_keep", "tag_used"]


def keeper_price(prior_salary: float, tag_used: bool, paul_rule: bool) -> float:
    """Price to keep a player, per league rules."""
    if pd.isna(prior_salary):
        raise ValueError("Cannot price a keeper with unknown prior salary.")
    if paul_rule:
        return prior_salary
    bump = config.KEEPER_BUMP_TAGGED if tag_used else config.KEEPER_BUMP_STANDARD
    return prior_salary + bump


def heuristic_keep_flag(df: pd.DataFrame) -> pd.Series:
    """ASSUMPTION-heavy default: flag likely-kept players when we have no
    real keeper decisions yet.

    Logic (transparent, meant to be overridden):
      - Default candidate band = salary between config.KEEPER_HEURISTIC_MIN_SALARY
        and config.KEEPER_HEURISTIC_MAX_SALARY: expensive enough to signal a
        real starter, cheap enough to be clear surplus at +$10 next year.
      - Already-tagged players (strong signal of team intent) and Paul Rule
        / IR cases (keep at same price, no bump -- nearly free surplus) are
        candidates regardless of price.
      - Capped at league max of 6 keepers per team, ranked by cheapest
        salary first (most surplus value) within the qualifying set.
    """
    priced = df[df["has_confirmed_salary"]].copy()
    in_band = priced["salary_2025"].between(
        config.KEEPER_HEURISTIC_MIN_SALARY, config.KEEPER_HEURISTIC_MAX_SALARY
    )
    qualifies = in_band | priced["on_ir"] | priced["is_tagged_2025"]
    flags = pd.Series(False, index=df.index)
    flags.loc[priced.index[qualifies]] = True

    result = pd.Series(False, index=df.index)
    for team, idx in df.groupby("team").groups.items():
        team_flags = flags.loc[idx]
        candidates = df.loc[idx][team_flags].sort_values("salary_2025")
        keep_idx = candidates.index[: config.MAX_KEEPERS_PER_TEAM]
        result.loc[keep_idx] = True
    return result


def neutral_alpha_keep_flag(df: pd.DataFrame, neutral_value: pd.Series) -> pd.Series:
    """v4 Part 3: the DEFAULT keeper forecast, replacing the $15-45
    salary-band heuristic. Selects up to config.MAX_KEEPERS_PER_TEAM
    players per team by highest NEUTRAL ALPHA (neutral_value - standard
    keeper cost), requiring positive alpha unless
    config.KEEPER_COUNT_IS_EXACT forces exactly six.

    ``neutral_value`` must be a Series aligned to df's index -- talent-VBD-
    based hypothetical open-market value, computed independent of who ends
    up kept (see valuation.price_live_and_hypothetical's "hypothetical"
    pass). Standard (untagged, non-Paul-Rule) keeper cost is used for this
    initial ranking pass; tag optimization across a team's own candidates
    happens separately (see optimize_tag_placement) rather than assumed
    from salary alone.

    NOTE (reduced scope): this is a single pass, not the full iterative
    keeper-market convergence process (build live auction from this set,
    recompute depleted-market alpha, re-select, repeat to convergence)
    described in the v4 spec. That loop is NOT implemented here -- flagged
    explicitly rather than silently approximated. This single pass still
    replaces the salary-band heuristic as the default forecast.
    """
    cost = df.apply(
        lambda row: keeper_price(row["salary_2025"], False, bool(row.get("paul_rule_eligible", False)))
        if pd.notna(row["salary_2025"]) else pd.NA,
        axis=1,
    )
    alpha = neutral_value - cost

    result = pd.Series(False, index=df.index)
    for team, idx in df.groupby("team").groups.items():
        team_alpha = alpha.loc[idx].dropna()
        if config.KEEPER_COUNT_IS_EXACT:
            candidates = team_alpha.sort_values(ascending=False).index[: config.MAX_KEEPERS_PER_TEAM]
        else:
            positive = team_alpha[team_alpha > config.KEEPER_ALPHA_SELECTION_THRESHOLD]
            candidates = positive.sort_values(ascending=False).index[: config.MAX_KEEPERS_PER_TEAM]
        result.loc[candidates] = True
    return result


def apply_keeper_overrides(
    df: pd.DataFrame,
    overrides: pd.DataFrame | None,
    neutral_value: pd.Series | None = None,
    skip_default_forecast: bool = False,
) -> pd.DataFrame:
    """Merge confirmed keeper decisions on top of the default forecast.

    ``overrides`` columns: team, player, will_keep (bool-like), tag_used
    (bool-like). Rows not present in overrides keep the default forecast.

    v4: default forecast is neutral-alpha-based (neutral_alpha_keep_flag),
    NOT the retired $15-45 salary-band heuristic -- pass ``neutral_value``
    (talent-VBD hypothetical value per player) to use it. Omitting
    neutral_value falls back to the legacy heuristic, kept only as a
    comparison field (df["legacy_heuristic_keeper"]), per the instruction
    to preserve it for existing outputs rather than delete it outright.
    """
    df = df.copy()
    df["legacy_heuristic_keeper"] = heuristic_keep_flag(df)
    if skip_default_forecast:
        if "will_keep" not in df.columns:
            df["will_keep"] = False
        if "keep_source" not in df.columns:
            df["keep_source"] = "authoritative_file"
    elif neutral_value is not None:
        df["will_keep"] = neutral_alpha_keep_flag(df, neutral_value)
        df["keep_source"] = "neutral_alpha"
    else:
        df["will_keep"] = df["legacy_heuristic_keeper"]
        df["keep_source"] = "legacy_heuristic_fallback"
    if "tag_used" not in df.columns:
        df["tag_used"] = False

    if overrides is None or overrides.empty:
        return df

    overrides = overrides.copy()
    overrides["team"] = overrides["team"].str.strip()
    overrides["player"] = overrides["player"].str.strip()
    overrides["will_keep"] = overrides["will_keep"].astype(str).str.lower().isin(
        ["true", "1", "yes", "y"]
    )
    overrides["tag_used"] = overrides["tag_used"].astype(str).str.lower().isin(
        ["true", "1", "yes", "y"]
    )

    df = df.set_index(["team", "player"])
    overrides = overrides.set_index(["team", "player"])
    for key in overrides.index.intersection(df.index):
        df.loc[key, "will_keep"] = overrides.loc[key, "will_keep"]
        df.loc[key, "tag_used"] = overrides.loc[key, "tag_used"]
        df.loc[key, "keep_source"] = "confirmed"

    # Enforce the hard league caps even after overrides are applied.
    df = df.reset_index()
    over_cap = []
    for team, group in df[df["will_keep"]].groupby("team"):
        if len(group) > config.MAX_KEEPERS_PER_TEAM:
            over_cap.append((team, len(group)))
    if over_cap:
        raise ValueError(
            f"Teams exceed the {config.MAX_KEEPERS_PER_TEAM}-keeper cap after "
            f"overrides: {over_cap}. Fix keeper_overrides.csv."
        )

    tag_over_cap = []
    for team, group in df[df["tag_used"]].groupby("team"):
        if len(group) > config.FRANCHISE_TAGS_PER_TEAM:
            tag_over_cap.append((team, len(group)))
    if tag_over_cap:
        raise ValueError(
            f"Teams use more than {config.FRANCHISE_TAGS_PER_TEAM} franchise tag(s) "
            f"after overrides: {tag_over_cap}. Fix keeper_overrides.csv."
        )

    return df


def price_keepers(df: pd.DataFrame) -> pd.DataFrame:
    """Compute keeper_price_2026 for every player flagged will_keep."""
    df = df.copy()
    df["keeper_price_2026"] = pd.NA

    def _price(row):
        if not row["will_keep"]:
            return pd.NA
        if pd.isna(row["salary_2025"]):
            return pd.NA  # can't price a keeper with no known prior salary
        return keeper_price(row["salary_2025"], row["tag_used"], bool(row.get("paul_rule_eligible", False)))

    df["keeper_price_2026"] = df.apply(_price, axis=1)
    return df


def inflation_summary(df: pd.DataFrame) -> dict:
    """Estimate the auction-inflation effect: dollars and players removed
    from the live pool via keepers, and the resulting per-dollar inflation
    multiplier for everyone left in the pool.
    """
    kept = df[df["will_keep"]]
    pool = df[~df["will_keep"]]

    total_keeper_spend = float(kept["keeper_price_2026"].dropna().sum())
    n_keepers = int(kept["will_keep"].sum())

    remaining_budget = config.TOTAL_LEAGUE_BUDGET - total_keeper_spend
    # Historical dollars that keepers are removing from what would have been
    # open-market competition -- the numerator of the inflation effect.
    historical_value_removed = float(kept["salary_2025"].dropna().sum())
    historical_value_remaining = float(pool["salary_2025"].dropna().sum())

    inflation_multiplier = (
        remaining_budget / (config.TOTAL_LEAGUE_BUDGET - historical_value_removed)
        if (config.TOTAL_LEAGUE_BUDGET - historical_value_removed) > 0
        else 1.0
    )

    return {
        "n_keepers": n_keepers,
        "total_keeper_spend": round(total_keeper_spend, 2),
        "remaining_budget": round(remaining_budget, 2),
        "historical_value_removed": round(historical_value_removed, 2),
        "historical_value_remaining_in_pool": round(historical_value_remaining, 2),
        "inflation_multiplier": round(inflation_multiplier, 4),
    }
