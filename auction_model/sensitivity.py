"""Sensitivity scenarios for keeper decision confidence."""

from __future__ import annotations

import itertools

import pandas as pd

from . import config, keeper_market


def sensitivity_grid(full_mode: bool = False) -> list[dict]:
    blends = [(0.6, 0.4), (0.75, 0.25), (1.0, 0.0)]
    tiers = [0.25, 0.5, 0.75]
    vbds = [1.2, 1.4, 1.7]
    keeper_rules = [False, True]  # max vs exact
    flex_modes = ["marginal", "fixed_share_fallback"]
    tag_modes = ["C", "D"]

    if not full_mode:
        return [{
            "blend_weight": 0.6,
            "tier_shrinkage": config.TIER_SHRINKAGE_PCT,
            "vbd_power": config.VBD_DOLLAR_POWER,
            "keeper_count_exact": config.KEEPER_COUNT_IS_EXACT,
            "flex_mode": config.FLEX_ALLOCATION_MODE,
            "tag_scenario": config.SCENARIO_TAG,
        }]

    scenarios = []
    for (bw, _), tier, vbd, exact, flex, tag in itertools.product(
        blends, tiers, vbds, keeper_rules, flex_modes, tag_modes
    ):
        scenarios.append({
            "blend_weight": bw,
            "tier_shrinkage": tier,
            "vbd_power": vbd,
            "keeper_count_exact": exact,
            "flex_mode": flex,
            "tag_scenario": tag,
        })
    return scenarios


def run_sensitivity(
    roster: pd.DataFrame,
    full_pool: pd.DataFrame,
    neutral_value: pd.Series,
    overrides: pd.DataFrame | None,
    full_mode: bool = False,
) -> pd.DataFrame:
    """Return per-player selection rates across scenarios."""
    scenarios = sensitivity_grid(full_mode)
    selection_counts: dict[tuple[str, str], int] = {}
    tag_counts: dict[tuple[str, str], int] = {}
    alpha_ranges: dict[tuple[str, str], list[float]] = {}

    for sc in scenarios:
        orig_exact = config.KEEPER_COUNT_IS_EXACT
        orig_flex = config.FLEX_ALLOCATION_MODE
        orig_tag = config.SCENARIO_TAG
        orig_vbd = config.VBD_DOLLAR_POWER
        orig_tier = config.TIER_SHRINKAGE_PCT
        try:
            config.KEEPER_COUNT_IS_EXACT = sc["keeper_count_exact"]
            config.FLEX_ALLOCATION_MODE = sc["flex_mode"]
            config.SCENARIO_TAG = sc["tag_scenario"]
            config.VBD_DOLLAR_POWER = sc["vbd_power"]
            config.TIER_SHRINKAGE_PCT = sc["tier_shrinkage"]
            result = keeper_market.iterate_keeper_market(
                roster, full_pool, neutral_value, sc["blend_weight"], overrides, max_iterations=3
            )
        finally:
            config.KEEPER_COUNT_IS_EXACT = orig_exact
            config.FLEX_ALLOCATION_MODE = orig_flex
            config.SCENARIO_TAG = orig_tag
            config.VBD_DOLLAR_POWER = orig_vbd
            config.TIER_SHRINKAGE_PCT = orig_tier

        for _, row in result.roster.iterrows():
            key = (row["team"], row["player"])
            if row["will_keep"]:
                selection_counts[key] = selection_counts.get(key, 0) + 1
            if row.get("tag_used"):
                tag_counts[key] = tag_counts.get(key, 0) + 1
            alpha_ranges.setdefault(key, []).append(float(row["depleted_market_alpha"]))

    n_scenarios = len(scenarios)
    rows = []
    for _, row in roster.iterrows():
        key = (row["team"], row["player"])
        sel_rate = selection_counts.get(key, 0) / n_scenarios
        tag_rate = tag_counts.get(key, 0) / n_scenarios
        alphas = alpha_ranges.get(key, [0.0])
        rows.append({
            "team": row["team"],
            "player": row["player"],
            "keeper_selection_rate": round(sel_rate, 3),
            "tag_selection_rate": round(tag_rate, 3),
            "minimum_depleted_alpha": round(min(alphas), 2),
            "base_depleted_alpha": round(sum(alphas) / len(alphas), 2),
            "maximum_depleted_alpha": round(max(alphas), 2),
        })
    out = pd.DataFrame(rows)
    out["decision_stability"] = out["keeper_selection_rate"].apply(
        lambda r: "stable" if r in {0.0, 1.0} else "mixed"
    )
    out["decision_confidence"] = out.apply(_confidence_label, axis=1)
    return out


def _confidence_label(row: pd.Series) -> str:
    r = row["keeper_selection_rate"]
    if r >= config.CONFIDENCE_LOCK_SELECTION_RATE and row["base_depleted_alpha"] > 0:
        return "LOCK"
    if r >= config.CONFIDENCE_STRONG_KEEP_RATE:
        return "STRONG_KEEP"
    if config.CONFIDENCE_BORDERLINE_LOW <= r < config.CONFIDENCE_STRONG_KEEP_RATE:
        return "BORDERLINE_KEEP"
    if r < config.CONFIDENCE_BORDERLINE_LOW and row["base_depleted_alpha"] < 0:
        return "STRONG_RELEASE"
    return "BORDERLINE_KEEP"
