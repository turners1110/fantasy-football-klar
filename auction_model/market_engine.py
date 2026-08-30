"""Unified market pricing: neutral + depleted (low/expected/high) redraft values."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import config, keepers, valuation
from . import price_tiers

FLEX_ELIGIBLE = {"RB", "WR", "TE"}


@dataclass(frozen=True)
class MarketScenario:
    name: str
    vbd_power: float
    tier_shrinkage: float
    inflation_scale: float = 1.0


SCENARIO_NEUTRAL = MarketScenario("neutral", config.VBD_DOLLAR_POWER, config.TIER_SHRINKAGE_PCT, 1.0)
SCENARIO_DEPLETED_LOW = MarketScenario("depleted_low", 1.2, 0.25, 0.92)
SCENARIO_DEPLETED_EXPECTED = MarketScenario("depleted_expected", config.VBD_DOLLAR_POWER, config.TIER_SHRINKAGE_PCT, 1.0)
SCENARIO_DEPLETED_HIGH = MarketScenario("depleted_high", 1.7, 0.75, 1.08)


def _apply_scenario(scenario: MarketScenario):
    orig_vbd = config.VBD_DOLLAR_POWER
    orig_tier = config.TIER_SHRINKAGE_PCT
    config.VBD_DOLLAR_POWER = scenario.vbd_power
    config.TIER_SHRINKAGE_PCT = scenario.tier_shrinkage
    return orig_vbd, orig_tier


def _restore_scenario(orig_vbd: float, orig_tier: float):
    config.VBD_DOLLAR_POWER = orig_vbd
    config.TIER_SHRINKAGE_PCT = orig_tier


def price_neutral_market(
    full_pool: pd.DataFrame,
    blend_weight: float,
) -> pd.DataFrame:
    orig_vbd, orig_tier = _apply_scenario(SCENARIO_NEUTRAL)
    try:
        priced = valuation.price_neutral_value(full_pool, blend_weight)
        priced = priced.rename(columns={"hypothetical_open_market_value": "neutral_redraft_value"})
        return priced
    finally:
        _restore_scenario(orig_vbd, orig_tier)


def price_depleted_market(
    full_pool: pd.DataFrame,
    roster: pd.DataFrame,
    blend_weight: float,
    scenario: MarketScenario,
) -> tuple[dict, pd.DataFrame]:
    """Return inflation summary and live auction prices under a scenario."""
    orig_vbd, orig_tier = _apply_scenario(scenario)
    try:
        inflation = keepers.inflation_summary(roster)
        inflation = inflation.copy()
        inflation["remaining_budget"] = round(
            inflation["remaining_budget"] * scenario.inflation_scale, 2
        )
        inflation["inflation_multiplier"] = round(
            inflation["inflation_multiplier"] * scenario.inflation_scale, 4
        )
        keeper_cols = roster[["team", "player", "will_keep", "tag_used", "keeper_price_2026"]].copy()
        pool = full_pool.drop(
            columns=[c for c in ("will_keep", "tag_used", "keeper_price_2026") if c in full_pool.columns]
        )
        pool = pool.merge(keeper_cols, on=["team", "player"], how="left")
        pool["will_keep"] = pool["will_keep"].fillna(False).astype(bool)
        pool["tag_used"] = pool["tag_used"].fillna(False).astype(bool)
        priced_live, _ = valuation.price_live_and_hypothetical(pool, inflation, blend_weight)
        return inflation, priced_live
    finally:
        _restore_scenario(orig_vbd, orig_tier)


def counterfactual_release_prices(
    row_idx: int,
    full_pool: pd.DataFrame,
    roster: pd.DataFrame,
    blend_weight: float,
    scenarios: tuple[MarketScenario, ...] = (
        SCENARIO_DEPLETED_LOW,
        SCENARIO_DEPLETED_EXPECTED,
        SCENARIO_DEPLETED_HIGH,
    ),
) -> dict[str, float | str]:
    """Exact player-level release counterfactual across price tiers."""
    row = roster.loc[row_idx]
    alt = roster.copy()
    alt.loc[row_idx, "will_keep"] = False
    alt.loc[row_idx, "tag_used"] = False
    alt = keepers.price_keepers(alt)

    base_infl = keepers.inflation_summary(roster)
    release_infl = keepers.inflation_summary(alt)
    results: dict[str, float | str] = {
        "calculation_method": "EXACT_PLAYER_COUNTERFACTUAL",
        "base_auction_budget": config.BUDGET_PER_TEAM,
        "release_auction_budget": config.BUDGET_PER_TEAM,
        "base_open_slots": config.TOTAL_ROSTER_SPOTS_PER_TEAM,
        "release_open_slots": config.TOTAL_ROSTER_SPOTS_PER_TEAM,
    }

    tier_prices: dict[str, float] = {}
    for scenario in scenarios:
        _, priced = price_depleted_market(full_pool, alt, blend_weight, scenario)
        match = priced[priced["player"] == row["player"]]
        price = float(match.iloc[0]["suggested_auction_price"]) if len(match) else 0.0
        tier_prices[scenario.name] = price

    ordered = price_tiers.collect_scenario_prices(tier_prices, expected_scenario="depleted_expected")
    results["released_low_price"] = ordered["depleted_redraft_low"]
    results["released_expected_price"] = ordered["depleted_redraft_expected"]
    results["released_high_price"] = ordered["depleted_redraft_high"]
    kp = row.get("keeper_price_2026")
    results["keeper_cost_returned"] = float(kp) if pd.notna(kp) else 0.0
    return results


def build_release_counterfactual_audit(
    roster: pd.DataFrame,
    full_pool: pd.DataFrame,
    neutral_values: pd.Series,
    blend_weight: float,
    exact_teams: set[str] | None = None,
) -> pd.DataFrame:
    """Counterfactual audit for rostered players."""
    rows = []
    for idx, row in roster.iterrows():
        if pd.isna(row.get("salary_2025")):
            continue
        team = row["team"]
        use_exact = exact_teams is None or team in exact_teams
        std_cost = keepers.keeper_price(
            row["salary_2025"], False, bool(row.get("paul_rule_eligible", False))
        )
        neutral = float(neutral_values.loc[idx]) if idx in neutral_values.index else 0.0

        if use_exact:
            cf = counterfactual_release_prices(idx, full_pool, roster, blend_weight)
            method = "EXACT_PLAYER_COUNTERFACTUAL"
            low = float(cf["released_low_price"])
            exp = float(cf["released_expected_price"])
            high = float(cf["released_high_price"])
            price_tiers.assert_price_order(pd.Series({
                "player": row["player"],
                "depleted_redraft_low": low,
                "depleted_redraft_expected": exp,
                "depleted_redraft_high": high,
            }))
        else:
            method = "APPROXIMATE_POSITION_RATIO"
            exp = neutral * 0.9
            low = min(neutral * 0.75, exp)
            high = max(neutral * 1.1, exp)

        rows.append({
            "team": team,
            "player": row["player"],
            "base_keeper_status": bool(row.get("will_keep", False)),
            "keeper_cost": std_cost,
            "neutral_value": round(neutral, 2),
            "released_low_price": round(low, 2),
            "released_expected_price": round(exp, 2),
            "released_high_price": round(high, 2),
            "neutral_alpha": round(neutral - std_cost, 2),
            "depleted_alpha_low": round(low - std_cost, 2),
            "depleted_alpha_expected": round(exp - std_cost, 2),
            "depleted_alpha_high": round(high - std_cost, 2),
            "calculation_method": method,
            "warnings": "" if use_exact else "approximate_only",
        })
    return pd.DataFrame(rows)
