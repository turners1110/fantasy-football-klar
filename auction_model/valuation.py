"""Value-Based Drafting engine, blended with historical-salary anchoring.

Two signals feed every price:

1. **VBD dollars** -- projected points above this league's actual
   replacement level (see config.replacement_rank, which bakes in the
   2RB/2WR/TE/3FLEX-no-K/DEF roster math), converted to dollars. Only
   available for players with a projection supplied.
2. **Anchor dollars** -- last year's real league salary, carried forward
   and scaled by the keeper-driven inflation multiplier. Always available
   for anyone with a confirmed 2025 salary.

``blend_weight`` controls how much the final price trusts (1) vs (2). With
no projections file supplied, blend_weight is forced to 0 (pure historical
anchor) rather than fabricating point projections for real players.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


def compute_replacement_baseline(pool: pd.DataFrame, points_col: str = "projected_points") -> dict:
    """Replacement-level projected points per position, using this league's
    actual replacement rank (config.replacement_rank), not a generic one."""
    baseline = {}
    for position in ("QB", "RB", "WR", "TE"):
        rank = config.replacement_rank(position)
        pos_players = pool[pool["position"] == position].dropna(subset=[points_col])
        pos_players = pos_players.sort_values(points_col, ascending=False)
        if len(pos_players) >= rank and rank > 0:
            baseline[position] = float(pos_players.iloc[rank - 1][points_col])
        elif len(pos_players) > 0:
            baseline[position] = float(pos_players[points_col].min())
        else:
            baseline[position] = np.nan
    return baseline


def add_vbd_scores(pool: pd.DataFrame, points_col: str = "projected_points") -> pd.DataFrame:
    pool = pool.copy()
    if points_col not in pool.columns or pool[points_col].dropna().empty:
        pool["VBD_score"] = np.nan
        return pool
    baseline = compute_replacement_baseline(pool, points_col)
    pool["replacement_points"] = pool["position"].map(baseline)
    pool["VBD_score"] = pool[points_col] - pool["replacement_points"]
    pool.loc[pool["VBD_score"] < 0, "VBD_score"] = 0.0
    return pool


def _proportional_dollars(values: pd.Series, budget: float) -> pd.Series:
    """Distribute `budget` proportionally to positive values; zero/NaN -> 0."""
    positive = values.clip(lower=0).fillna(0)
    total = positive.sum()
    if total <= 0:
        return pd.Series(0.0, index=values.index)
    return positive / total * budget


def price_pool(
    pool: pd.DataFrame,
    remaining_budget: float,
    inflation_multiplier: float,
    blend_weight: float,
    points_col: str = "projected_points",
) -> pd.DataFrame:
    """Compute suggested_auction_price for every non-keeper player.

    blend_weight: 0.0 = pure historical-salary anchor (no projections
    trusted), 1.0 = pure VBD-from-projections. Forced to 0 automatically
    for any player missing a projection, and forced to 0 league-wide if no
    projections were supplied at all.
    """
    pool = add_vbd_scores(pool, points_col)

    has_projection = pool[points_col].notna() if points_col in pool.columns else pd.Series(False, index=pool.index)
    effective_weight = pd.Series(blend_weight, index=pool.index)
    effective_weight[~has_projection] = 0.0

    vbd_dollars = _proportional_dollars(pool["VBD_score"], remaining_budget)

    anchor_raw = pool["salary_2025"] * inflation_multiplier
    anchor_dollars = _proportional_dollars(
        anchor_raw.where(pool["has_confirmed_salary"], other=np.nan), remaining_budget
    )

    blended = effective_weight * vbd_dollars + (1 - effective_weight) * anchor_dollars

    # Players with neither a confirmed historical salary nor a projection
    # cannot be priced responsibly -- leave null rather than guessing.
    unpriceable = (~pool["has_confirmed_salary"]) & (~has_projection)
    blended[unpriceable] = np.nan

    pool["suggested_auction_price_raw"] = blended
    pool["blend_weight_used"] = effective_weight

    # Rescale priced players so they sum to the actual remaining budget,
    # then apply league price floor/ceiling.
    priceable = pool["suggested_auction_price_raw"].notna()
    current_sum = pool.loc[priceable, "suggested_auction_price_raw"].sum()
    if current_sum > 0:
        scale = remaining_budget / current_sum
    else:
        scale = 1.0
    pool["suggested_auction_price"] = np.nan
    pool.loc[priceable, "suggested_auction_price"] = (
        pool.loc[priceable, "suggested_auction_price_raw"] * scale
    ).clip(lower=config.MIN_PRICE, upper=config.MAX_PRICE).round(0)

    return pool.drop(columns=["suggested_auction_price_raw"])


def run_sanity_checks(pool: pd.DataFrame, remaining_budget: float) -> dict:
    """Return the sanity-check report described in the spec."""
    priced = pool[pool["suggested_auction_price"].notna()]
    total_priced = float(priced["suggested_auction_price"].sum())
    tolerance = remaining_budget * config.BUDGET_TOLERANCE
    budget_ok = abs(total_priced - remaining_budget) <= tolerance

    out_of_range = pool[
        (pool["suggested_auction_price"] < config.MIN_PRICE)
        | (pool["suggested_auction_price"] > config.MAX_PRICE)
    ]

    comparable = pool[pool["has_confirmed_salary"] & pool["suggested_auction_price"].notna()].copy()
    comparable = comparable[comparable["salary_2025"] > 0]
    ratio = comparable["suggested_auction_price"] / comparable["salary_2025"]
    large_moves = comparable[
        (ratio >= config.LARGE_MOVE_MULTIPLE) | (ratio <= 1 / config.LARGE_MOVE_MULTIPLE)
    ][["team", "player", "position", "salary_2025", "suggested_auction_price"]]

    unpriced = pool[pool["suggested_auction_price"].isna()][["team", "player", "position", "notes"]]

    return {
        "remaining_budget": round(remaining_budget, 2),
        "total_priced": round(total_priced, 2),
        "budget_within_tolerance": bool(budget_ok),
        "n_out_of_range": int(len(out_of_range)),
        "n_large_moves_vs_2025_salary": int(len(large_moves)),
        "large_moves": large_moves,
        "n_unpriced_no_data": int(len(unpriced)),
        "unpriced_no_data": unpriced,
    }
