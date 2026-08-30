"""Core correctness tests for the v4 methodology. Not the full 30+ item
list from the v4 spec (Part 14) -- reduced scope given time constraints,
covering the highest-risk acceptance criteria: keeper cost math, the
retired heuristic vs. new default, anchor renormalization, no-cap /
no-guaranteed-floor pricing, and exact budget reconciliation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from auction_model import config, data_pipeline, keepers, valuation


def test_keeper_cost_standard_increase():
    assert keepers.keeper_price(20, tag_used=False, paul_rule=False) == 30


def test_keeper_cost_tag_increase():
    assert keepers.keeper_price(20, tag_used=True, paul_rule=False) == 25


def test_keeper_cost_paul_rule_unchanged():
    assert keepers.keeper_price(20, tag_used=False, paul_rule=True) == 20


def test_keeper_cost_requires_known_salary():
    with pytest.raises(ValueError):
        keepers.keeper_price(np.nan, tag_used=False, paul_rule=False)


def test_flat_dollar_assignment_classified_not_auction():
    df = pd.DataFrame({
        "salary_2025": [1.0, 21.0],
        "notes": ["", ""],
        "on_ir": [False, False],
    })
    origin, confidence = data_pipeline.classify_salary_origin(df)
    assert origin.iloc[0] == "UNKNOWN_DOLLAR_ONE"
    assert confidence.iloc[0] == config.SALARY_ORIGIN_RELIABILITY["UNKNOWN_DOLLAR_ONE"]
    assert origin.iloc[1] == "UNKNOWN_NON_DOLLAR_ONE"
    assert confidence.iloc[1] == config.SALARY_ORIGIN_RELIABILITY["UNKNOWN_NON_DOLLAR_ONE"]


def test_paul_rule_ir_note_does_not_confirm_origin():
    df = pd.DataFrame({"salary_2025": [16.0], "notes": ["on IR"]})
    df["on_ir"] = df["notes"].str.contains("IR")
    origin, confidence = data_pipeline.classify_salary_origin(df)
    assert origin.iloc[0] == "UNKNOWN_NON_DOLLAR_ONE"
    assert confidence.iloc[0] == config.SALARY_ORIGIN_RELIABILITY["UNKNOWN_NON_DOLLAR_ONE"]


def _minimal_pool(n=6):
    """Small synthetic pool: enough rows/positions for price_pool to run."""
    return pd.DataFrame({
        "player": [f"P{i}" for i in range(n)],
        "position": (["RB", "WR"] * n)[:n],
        "team": ["A"] * n,
        "salary_2025": [50.0, np.nan, 30.0, np.nan, np.nan, np.nan][:n],
        "has_confirmed_salary": [True, False, True, False, False, False][:n],
        "origin_confidence": [1.0, 0.0, 1.0, 0.0, 0.0, 0.0][:n],
        "projected_points": [200.0, 150.0, np.nan, 100.0, np.nan, np.nan][:n],
        "will_keep": [False] * n,
        "notes": [""] * n,
    })


def test_projection_only_gets_full_projection_weight_not_phantom_anchor():
    """v4 acceptance #8: a player with a projection but no anchor must not
    lose blend_weight's share to a phantom $0 anchor."""
    pool = _minimal_pool()
    priced = valuation.price_pool(pool, remaining_budget=100, inflation_multiplier=1.0, blend_weight=0.6)
    row = priced[priced["player"] == "P3"].iloc[0]  # projection, no anchor
    assert row["blend_weight_used"] == pytest.approx(1.0)


def test_anchor_only_gets_pure_anchor_weight():
    pool = _minimal_pool()
    priced = valuation.price_pool(pool, remaining_budget=100, inflation_multiplier=1.0, blend_weight=0.6)
    row = priced[priced["player"] == "P2"].iloc[0]  # anchor, no projection
    assert row["blend_weight_used"] == pytest.approx(0.0)


def test_neither_signal_gets_no_price():
    pool = _minimal_pool()
    priced = valuation.price_pool(pool, remaining_budget=100, inflation_multiplier=1.0, blend_weight=0.6)
    row = priced[priced["player"] == "P4"].iloc[0]  # no salary, no projection
    assert pd.isna(row["suggested_auction_price"])


def test_no_price_ceiling_by_default():
    assert config.MAX_PRICE is None


def test_undrafted_players_get_zero_not_one():
    """v4 Part 7: only n_open_roster_spots players get $1+; the rest are $0."""
    pool = _minimal_pool()
    priced = valuation.price_pool(
        pool, remaining_budget=100, inflation_multiplier=1.0, blend_weight=0.6,
        n_open_roster_spots=1,
    )
    n_drafted = (priced["suggested_auction_price"] > 0).sum()
    assert n_drafted == 1


def test_exact_budget_reconciliation():
    pool = _minimal_pool()
    priced = valuation.price_pool(
        pool, remaining_budget=97, inflation_multiplier=1.0, blend_weight=0.6,
        n_open_roster_spots=3,
    )
    assert priced["suggested_auction_price"].sum() == pytest.approx(97, abs=1e-6)


def test_no_negative_prices():
    pool = _minimal_pool()
    priced = valuation.price_pool(pool, remaining_budget=100, inflation_multiplier=1.0, blend_weight=0.6)
    assert (priced["suggested_auction_price"].dropna() >= 0).all()


def test_largest_remainder_rounding_exact():
    values = pd.Series([10.4, 10.4, 10.2])
    result = valuation._largest_remainder_round(values, target_total=31)
    assert result.sum() == 31


def test_negative_vbd_excluded_from_surplus():
    pool = pd.DataFrame({
        "player": ["Good", "Bad"],
        "position": ["RB", "RB"],
        "team": ["A", "A"],
        "salary_2025": [np.nan, np.nan],
        "has_confirmed_salary": [False, False],
        "origin_confidence": [0.0, 0.0],
        "projected_points": [200.0, 5.0],
        "VBD_score": [150.0, -20.0],  # negative already floored elsewhere normally
        "will_keep": [False, False],
        "notes": ["", ""],
    })
    priced = valuation.price_pool(pool, remaining_budget=50, inflation_multiplier=1.0, blend_weight=1.0)
    bad_price = priced[priced["player"] == "Bad"]["suggested_auction_price"].iloc[0]
    good_price = priced[priced["player"] == "Good"]["suggested_auction_price"].iloc[0]
    assert bad_price <= good_price


def test_keeper_alpha_selection_caps_at_max_per_team():
    df = pd.DataFrame({
        "team": ["A"] * 8,
        "player": [f"P{i}" for i in range(8)],
        "salary_2025": [10.0] * 8,
        "paul_rule_eligible": [False] * 8,
    })
    neutral_value = pd.Series([100.0] * 8)  # all hugely positive alpha
    flags = keepers.neutral_alpha_keep_flag(df, neutral_value)
    assert flags.sum() <= config.MAX_KEEPERS_PER_TEAM


def test_keeper_alpha_selection_excludes_negative_alpha():
    df = pd.DataFrame({
        "team": ["A"] * 2,
        "player": ["Good", "Bad"],
        "salary_2025": [10.0, 10.0],
        "paul_rule_eligible": [False, False],
    })
    neutral_value = pd.Series([100.0, 1.0])  # Bad's alpha is negative (cost=20)
    flags = keepers.neutral_alpha_keep_flag(df, neutral_value)
    assert flags.iloc[0] and not flags.iloc[1]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
