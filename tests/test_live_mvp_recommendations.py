"""Live MVP Part 4 recommendation tests."""
from __future__ import annotations

import pytest

from auction_engine.live_recommendations import compute_recommended_bid, RECOMMENDATION_TYPES


def test_recommended_stop_never_exceeds_legal_max_bid():
    rec = compute_recommended_bid(
        player="X", safety_adjusted_ceiling=100.0, legal_max_bid=30.0,
        portfolio_feasibility_limit=None, confidence=9, live_expected_price=25.0,
    )
    assert rec.recommended_final_bid <= 30.0


def test_recommended_stop_never_exceeds_team_value_ceiling():
    rec = compute_recommended_bid(
        player="X", safety_adjusted_ceiling=20.0, legal_max_bid=200.0,
        portfolio_feasibility_limit=None, confidence=9, live_expected_price=15.0,
    )
    assert rec.recommended_final_bid <= 20.0


def test_recommended_stop_never_exceeds_portfolio_feasibility_limit():
    rec = compute_recommended_bid(
        player="X", safety_adjusted_ceiling=100.0, legal_max_bid=100.0,
        portfolio_feasibility_limit=12.0, confidence=9, live_expected_price=10.0,
    )
    assert rec.recommended_final_bid <= 12.0


def test_expected_market_price_is_never_the_hard_cap():
    # even if expected price is very high, the stop is bounded by ceiling/legal-max, not price
    rec = compute_recommended_bid(
        player="X", safety_adjusted_ceiling=20.0, legal_max_bid=200.0,
        portfolio_feasibility_limit=None, confidence=9, live_expected_price=500.0,
    )
    assert rec.recommended_final_bid == 20.0


def test_insufficient_evidence_when_no_ceiling():
    rec = compute_recommended_bid(
        player="X", safety_adjusted_ceiling=None, legal_max_bid=100.0,
        portfolio_feasibility_limit=None, confidence=9, live_expected_price=10.0,
    )
    assert rec.recommendation_type == "INSUFFICIENT_EVIDENCE"


def test_pass_above_limit_when_current_bid_exceeds_stop():
    rec = compute_recommended_bid(
        player="X", safety_adjusted_ceiling=20.0, legal_max_bid=100.0,
        portfolio_feasibility_limit=None, confidence=9, live_expected_price=15.0, current_bid=25.0,
    )
    assert rec.recommendation_type == "PASS_ABOVE_LIMIT"


def test_all_recommendation_types_are_from_the_allowed_set():
    rec = compute_recommended_bid(
        player="X", safety_adjusted_ceiling=20.0, legal_max_bid=100.0,
        portfolio_feasibility_limit=None, confidence=9, live_expected_price=15.0,
    )
    assert rec.recommendation_type in RECOMMENDATION_TYPES


def test_whole_dollar_recommendation():
    rec = compute_recommended_bid(
        player="X", safety_adjusted_ceiling=20.7, legal_max_bid=100.0,
        portfolio_feasibility_limit=None, confidence=9, live_expected_price=15.0,
    )
    assert rec.recommended_final_bid == int(rec.recommended_final_bid)
