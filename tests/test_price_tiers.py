"""Tests for price tier ordering."""

import math

import pandas as pd
import pytest

from auction_model import price_tiers
from auction_model.price_tiers import PriceTierError


def test_already_ordered():
    out = price_tiers.collect_scenario_prices({
        "depleted_low": 10.0, "depleted_expected": 15.0, "depleted_high": 20.0,
    })
    assert out["depleted_redraft_low"] == 10.0
    assert out["depleted_redraft_expected"] == 15.0
    assert out["depleted_redraft_high"] == 20.0


def test_reversed_inputs_corrected():
    out = price_tiers.collect_scenario_prices({
        "depleted_low": 33.0, "depleted_expected": 29.0, "depleted_high": 21.0,
    })
    assert out["depleted_redraft_low"] == 21.0
    assert out["depleted_redraft_expected"] == 29.0
    assert out["depleted_redraft_high"] == 33.0


def test_expected_outside_range_still_ordered():
    out = price_tiers.collect_scenario_prices({
        "a": 5.0, "b": 50.0, "depleted_expected": 100.0,
    })
    assert out["depleted_redraft_low"] <= out["depleted_redraft_expected"]
    assert out["depleted_redraft_expected"] <= out["depleted_redraft_high"]


def test_missing_scenario_uses_median():
    out = price_tiers.collect_scenario_prices({"a": 10.0, "b": 20.0})
    assert out["depleted_redraft_expected"] == 15.0


def test_nan_raises():
    with pytest.raises(PriceTierError):
        price_tiers.collect_scenario_prices({"a": float("nan")})


def test_negative_raises():
    with pytest.raises(PriceTierError):
        price_tiers.collect_scenario_prices({"a": -1.0})


def test_assert_order_passes():
    price_tiers.assert_price_order(pd.Series({
        "player": "X",
        "depleted_redraft_low": 5,
        "depleted_redraft_expected": 10,
        "depleted_redraft_high": 15,
    }))


def test_assert_order_fails():
    with pytest.raises(PriceTierError):
        price_tiers.assert_price_order(pd.Series({
            "player": "X",
            "depleted_redraft_low": 20,
            "depleted_redraft_expected": 10,
            "depleted_redraft_high": 5,
        }))
