"""V2 Part 2: Josh Jacobs anomaly fix + recommendation guardrail tests."""
from __future__ import annotations

from auction_engine.recommendation_guardrails import compute_governed_dollar_ceiling
from live_auction_cli import AuctionCLI


def test_jacobs_style_case_no_longer_uses_raw_points_as_dollars():
    """The exact bug: 189.35 fantasy points must never become a $189 stop."""
    g = compute_governed_dollar_ceiling(
        player="Josh Jacobs", position="RB", live_expected_price=60.0, legal_max_bid=215.0,
        static_hard_max=None, exact_ceiling=None, exact_status=None, exact_is_current=False,
        expected_role="required starter", sam_position_count=3, sam_budget_remaining=223.0, open_slots=9,
    )
    assert g.dollar_ceiling != 189.35
    assert g.dollar_ceiling < 100  # should land near live_expected_price * 1.3 = 78


def test_conservative_fallback_is_multiple_of_live_price_not_points():
    g = compute_governed_dollar_ceiling(
        player="X", position="WR", live_expected_price=40.0, legal_max_bid=200.0,
        static_hard_max=None, exact_ceiling=None, exact_status=None, exact_is_current=False,
        expected_role="FLEX starter", sam_position_count=1, sam_budget_remaining=200.0, open_slots=9,
    )
    assert g.dollar_ceiling == 52.0  # 40 * 1.3


def test_static_hard_max_caps_the_ceiling_when_lower():
    g = compute_governed_dollar_ceiling(
        player="Josh Allen", position="QB", live_expected_price=22.0, legal_max_bid=215.0,
        static_hard_max=37.0, exact_ceiling=None, exact_status=None, exact_is_current=False,
        expected_role="required starter", sam_position_count=1, sam_budget_remaining=223.0, open_slots=9,
    )
    assert g.dollar_ceiling <= 37.0


def test_exact_ceiling_used_when_current_and_optimal():
    g = compute_governed_dollar_ceiling(
        player="X", position="RB", live_expected_price=30.0, legal_max_bid=200.0,
        static_hard_max=None, exact_ceiling=45.0, exact_status="OPTIMAL", exact_is_current=True,
        expected_role="required starter", sam_position_count=1, sam_budget_remaining=200.0, open_slots=9,
    )
    assert g.dollar_ceiling == 45.0


def test_stale_exact_result_not_used_as_ceiling():
    g = compute_governed_dollar_ceiling(
        player="X", position="RB", live_expected_price=30.0, legal_max_bid=200.0,
        static_hard_max=None, exact_ceiling=200.0, exact_status="OPTIMAL", exact_is_current=False,  # STALE
        expected_role="required starter", sam_position_count=1, sam_budget_remaining=200.0, open_slots=9,
    )
    assert g.dollar_ceiling != 200.0
    assert g.dollar_ceiling < 100


def test_ceiling_never_exceeds_legal_max_bid():
    g = compute_governed_dollar_ceiling(
        player="X", position="RB", live_expected_price=1000.0, legal_max_bid=50.0,
        static_hard_max=None, exact_ceiling=None, exact_status=None, exact_is_current=False,
        expected_role="required starter", sam_position_count=1, sam_budget_remaining=200.0, open_slots=9,
    )
    assert g.dollar_ceiling <= 50.0


def test_critical_review_when_stop_exceeds_40pct_budget():
    g = compute_governed_dollar_ceiling(
        player="X", position="RB", live_expected_price=100.0, legal_max_bid=215.0,
        static_hard_max=None, exact_ceiling=None, exact_status=None, exact_is_current=False,
        expected_role="required starter", sam_position_count=1, sam_budget_remaining=223.0, open_slots=9,
    )
    assert g.critical_review_required
    assert "STOP_EXCEEDS_40PCT_BUDGET" in g.critical_reasons


def test_critical_review_bench_depth_over_25():
    g = compute_governed_dollar_ceiling(
        player="X", position="RB", live_expected_price=30.0, legal_max_bid=215.0,
        static_hard_max=None, exact_ceiling=None, exact_status=None, exact_is_current=False,
        expected_role="bench depth", sam_position_count=1, sam_budget_remaining=223.0, open_slots=9,
    )
    assert g.critical_review_required
    assert "BENCH_DEPTH_STOP_OVER_25" in g.critical_reasons


def test_critical_review_overloaded_position_starter_value():
    g = compute_governed_dollar_ceiling(
        player="X", position="RB", live_expected_price=20.0, legal_max_bid=215.0,
        static_hard_max=None, exact_ceiling=None, exact_status=None, exact_is_current=False,
        expected_role="required starter", sam_position_count=5, sam_budget_remaining=223.0, open_slots=9,
    )
    assert g.critical_review_required
    assert "OVERLOADED_POSITION_STARTER_VALUE" in g.critical_reasons


def test_no_critical_review_for_reasonable_case():
    g = compute_governed_dollar_ceiling(
        player="X", position="WR", live_expected_price=25.0, legal_max_bid=200.0,
        static_hard_max=None, exact_ceiling=None, exact_status=None, exact_is_current=False,
        expected_role="FLEX starter", sam_position_count=1, sam_budget_remaining=200.0, open_slots=9,
    )
    assert not g.critical_review_required


def test_live_check_endpoint_reflects_the_fix_for_a_real_high_point_rb():
    """End-to-end (not just the guardrail unit): whichever RB in the real
    pool has the highest projected points must not get a stop equal to
    his raw point total via the CLI's real api_check path."""
    cli = AuctionCLI(log_path=None)
    rbs = [(n, v) for n, v in cli.store.state.available_pool.items() if v["position"] == "RB"]
    top_rb = max(rbs, key=lambda t: t[1]["projected_points"])
    name, info = top_rb
    result = cli.api_check(name)
    assert abs(result["recommended_stop"] - info["projected_points"]) > 5, (
        f"{name}'s stop (${result['recommended_stop']}) must not equal his raw projected points "
        f"({info['projected_points']}) -- this is exactly the Josh Jacobs bug class"
    )


def test_governed_ceiling_field_present_in_api_board():
    cli = AuctionCLI(log_path=None)
    board = cli.api_board()
    assert all("critical_review_required" in row for row in board[:5])


def test_governed_ceiling_field_present_in_api_targets():
    cli = AuctionCLI(log_path=None)
    targets = cli.api_targets(5)
    assert all("critical_review_required" in row for row in targets)
