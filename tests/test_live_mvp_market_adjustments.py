"""Live MVP Part 3 market-adjustment tests."""
from __future__ import annotations

import pytest

from auction_engine.market_adjustments import (
    MarketAdjustmentState, demand_signal, live_expected_price,
    LEAGUE_PRIOR_WEIGHT, POSITION_PRIOR_WEIGHT, TIER_PRIOR_WEIGHT,
)


def test_one_overpriced_rb_sale_moves_price_only_slightly():
    state = MarketAdjustmentState()
    state.add_observation("RB", "tier1", actual_price=60.0, expected_price=40.0)  # 1.5x overpriced
    ratio, n = state.position_ratio("RB")
    # heavily shrunk toward 1.0 (league prior) with n=1
    assert 1.0 < ratio < 1.15


def test_five_overpriced_rb_sales_move_price_more_than_one():
    state_one = MarketAdjustmentState()
    state_one.add_observation("RB", "tier1", actual_price=60.0, expected_price=40.0)
    ratio_one, _ = state_one.position_ratio("RB")

    state_five = MarketAdjustmentState()
    for _ in range(5):
        state_five.add_observation("RB", "tier1", actual_price=60.0, expected_price=40.0)
    ratio_five, _ = state_five.position_ratio("RB")

    assert ratio_five > ratio_one


def test_rb_overpricing_does_not_move_qb_prices():
    state = MarketAdjustmentState()
    for _ in range(5):
        state.add_observation("RB", "tier1", actual_price=60.0, expected_price=40.0)
    rb_ratio, _ = state.position_ratio("RB")
    qb_ratio, qb_n = state.position_ratio("QB")
    assert qb_n == 0
    assert qb_ratio < rb_ratio  # QB falls back to the much-smaller league-wide drift, not RB's own spike


def test_qb_prices_fall_when_most_teams_fill_qb():
    # demand signal: few teams still need a starting QB, ample remaining supply
    low_demand = demand_signal("QB", teams_open_starter=1, teams_open_flex=0, teams_with_cash=10, remaining_supply=8)
    high_demand = demand_signal("QB", teams_open_starter=8, teams_open_flex=0, teams_with_cash=10, remaining_supply=8)
    assert low_demand < high_demand
    assert low_demand < 1.0


def test_te_prices_rise_with_scarce_supply_and_high_demand():
    adj = demand_signal("TE", teams_open_starter=5, teams_open_flex=2, teams_with_cash=6, remaining_supply=2)
    assert adj > 1.0


def test_rival_losing_spending_power_lowers_premium_price_pressure():
    # fewer teams_with_cash directly lowers the pressure term
    with_cash = demand_signal("WR", teams_open_starter=4, teams_open_flex=2, teams_with_cash=6, remaining_supply=4)
    less_cash = demand_signal("WR", teams_open_starter=4, teams_open_flex=2, teams_with_cash=2, remaining_supply=4)
    assert less_cash <= with_cash


def test_undo_reverses_market_adjustments_via_full_rebuild():
    sales_with_one_more = [
        {"position": "RB", "tier": "t1", "actual_price": 60.0, "expected_price": 40.0},
        {"position": "RB", "tier": "t1", "actual_price": 55.0, "expected_price": 40.0},
    ]
    sales_after_undo = sales_with_one_more[:1]
    state_full = MarketAdjustmentState.rebuild_from_sales(sales_with_one_more)
    state_undone = MarketAdjustmentState.rebuild_from_sales(sales_after_undo)
    assert state_full.position_ratio("RB")[0] != state_undone.position_ratio("RB")[0]
    # rebuilding from the SAME (shorter) sales list must exactly match a fresh build
    state_reference = MarketAdjustmentState.rebuild_from_sales(sales_after_undo)
    assert state_undone.position_ratio("RB") == state_reference.position_ratio("RB")


def test_correcting_sale_price_rebuilds_from_event_log():
    original = [{"position": "WR", "tier": "t1", "actual_price": 40.0, "expected_price": 40.0}]
    corrected = [{"position": "WR", "tier": "t1", "actual_price": 80.0, "expected_price": 40.0}]
    state_before = MarketAdjustmentState.rebuild_from_sales(original)
    state_after = MarketAdjustmentState.rebuild_from_sales(corrected)
    assert state_after.position_ratio("WR")[0] > state_before.position_ratio("WR")[0]


def test_total_multiplier_capped_at_bounds():
    state = MarketAdjustmentState()
    for _ in range(50):
        state.add_observation("TE", "t1", actual_price=200.0, expected_price=20.0)  # wildly overpriced
    result = live_expected_price(
        pre_draft_price=20.0, position="TE", tier="t1", market_state=state,
        teams_open_starter=10, teams_open_flex=5, teams_with_cash=10, remaining_supply=1,
    )
    assert result["combined_multiplier_capped"] <= 1.40 + 1e-9
    assert result["combined_multiplier_capped"] >= 0.70 - 1e-9


def test_whole_dollar_live_price():
    state = MarketAdjustmentState()
    result = live_expected_price(
        pre_draft_price=40.0, position="WR", tier="t1", market_state=state,
        teams_open_starter=3, teams_open_flex=2, teams_with_cash=8, remaining_supply=6,
    )
    assert result["live_expected_price"] == int(result["live_expected_price"])


def test_tier_signal_shrinks_toward_position_signal_not_raw_ratio():
    state = MarketAdjustmentState()
    state.add_observation("WR", "t1", actual_price=100.0, expected_price=50.0)  # one wild tier1 sale, 2x
    state.add_observation("WR", "t2", actual_price=45.0, expected_price=40.0)
    state.add_observation("WR", "t2", actual_price=44.0, expected_price=40.0)
    tier1_ratio, n = state.tier_ratio("WR", "t1")
    position_ratio, _ = state.position_ratio("WR")
    # tier1's single 2.0x sale must be heavily pulled back toward the position signal, not sit near 2.0
    assert tier1_ratio < 1.5
    assert n == 1


def test_position_signal_does_not_leak_fully_across_positions():
    state = MarketAdjustmentState()
    for _ in range(5):
        state.add_observation("RB", "t1", actual_price=80.0, expected_price=40.0)  # 2x RB spending spree
    rb_ratio, _ = state.position_ratio("RB")
    wr_ratio, wr_n = state.position_ratio("WR")
    assert wr_n == 0
    assert wr_ratio < rb_ratio - 0.2  # WR reflects only the diluted league-wide signal, not RB's own spike
