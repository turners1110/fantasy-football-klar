"""Sunday Final Build Stage 1: target-ranking tests."""
from __future__ import annotations

from auction_engine.live_target_scoring import compute_target_score, RECOMMENDATION_CLASSES


def test_weak_te_does_not_outrank_strong_cheap_player_when_replacement_te_exists():
    weak_te = compute_target_score(
        player="Weak TE", position="TE", team_specific_value_dollars=60.0, expected_role="required starter",
        expected_market_price_dollars=20.0, exact_or_approximate_ceiling_dollars=22.0, hard_max=20.0,
        remaining_alternatives_count=3, is_last_legal_alternative=False,
        price_confidence=0.4, position_need_score=1.0, portfolio_paths_broken_if_missed=0,
    )
    strong_wr = compute_target_score(
        player="Strong WR", position="WR", team_specific_value_dollars=150.0, expected_role="FLEX starter",
        expected_market_price_dollars=30.0, exact_or_approximate_ceiling_dollars=70.0, hard_max=65.0,
        remaining_alternatives_count=3, is_last_legal_alternative=False,
        price_confidence=0.8, position_need_score=0.2, portfolio_paths_broken_if_missed=0,
    )
    assert strong_wr.total_score > weak_te.total_score


def test_final_viable_te_value_rises_when_no_alternative_remains():
    with_alt = compute_target_score(
        player="Last TE", position="TE", team_specific_value_dollars=90.0, expected_role="required starter",
        expected_market_price_dollars=15.0, exact_or_approximate_ceiling_dollars=25.0, hard_max=22.0,
        remaining_alternatives_count=2, is_last_legal_alternative=False,
        price_confidence=0.5, position_need_score=1.0, portfolio_paths_broken_if_missed=0,
    )
    no_alt = compute_target_score(
        player="Last TE", position="TE", team_specific_value_dollars=90.0, expected_role="required starter",
        expected_market_price_dollars=15.0, exact_or_approximate_ceiling_dollars=25.0, hard_max=22.0,
        remaining_alternatives_count=0, is_last_legal_alternative=True,
        price_confidence=0.5, position_need_score=1.0, portfolio_paths_broken_if_missed=0,
    )
    assert no_alt.total_score > with_alt.total_score
    assert no_alt.recommendation_class == "TIER_CLIFF"


def test_cheaper_of_two_equal_gain_players_ranks_higher():
    common = dict(team_specific_value_dollars=100.0, expected_role="FLEX starter", exact_or_approximate_ceiling_dollars=60.0,
                  hard_max=55.0, remaining_alternatives_count=3, is_last_legal_alternative=False,
                  price_confidence=0.7, position_need_score=0.3, portfolio_paths_broken_if_missed=0)
    cheap = compute_target_score(player="Cheap", position="WR", expected_market_price_dollars=20.0, **common)
    expensive = compute_target_score(player="Expensive", position="WR", expected_market_price_dollars=45.0, **common)
    assert cheap.total_score > expensive.total_score


def test_qb_falls_sharply_to_bench_after_allen_reflected_in_score():
    before_allen = compute_target_score(
        player="Backup QB", position="QB", team_specific_value_dollars=180.0, expected_role="required starter",
        expected_market_price_dollars=15.0, exact_or_approximate_ceiling_dollars=30.0, hard_max=28.0,
        remaining_alternatives_count=4, is_last_legal_alternative=False,
        price_confidence=0.6, position_need_score=0.8, portfolio_paths_broken_if_missed=0,
    )
    after_allen = compute_target_score(
        player="Backup QB", position="QB", team_specific_value_dollars=27.0, expected_role="bench depth",
        expected_market_price_dollars=15.0, exact_or_approximate_ceiling_dollars=30.0, hard_max=28.0,
        remaining_alternatives_count=4, is_last_legal_alternative=False,
        price_confidence=0.6, position_need_score=0.0, portfolio_paths_broken_if_missed=0,
    )
    assert after_allen.total_score < before_allen.total_score
    assert after_allen.recommendation_class == "BENCH_DEPTH_ONLY"


def test_strong_rb_after_five_rbs_gets_bench_depth_treatment():
    score = compute_target_score(
        player="Strong RB", position="RB", team_specific_value_dollars=10.0, expected_role="bench depth",
        expected_market_price_dollars=40.0, exact_or_approximate_ceiling_dollars=50.0, hard_max=45.0,
        remaining_alternatives_count=5, is_last_legal_alternative=False,
        price_confidence=0.6, position_need_score=0.0, portfolio_paths_broken_if_missed=0,
    )
    assert score.recommendation_class in ("BENCH_DEPTH_ONLY", "WAIT_FOR_ALTERNATIVE", "PASS_ABOVE_LIMIT")
    assert score.bench_probability > 0.5


def test_wr_displacing_weakest_flex_gets_starting_lineup_gain_credit():
    score = compute_target_score(
        player="Flex WR", position="WR", team_specific_value_dollars=45.0, expected_role="FLEX starter",
        expected_market_price_dollars=20.0, exact_or_approximate_ceiling_dollars=35.0, hard_max=32.0,
        remaining_alternatives_count=2, is_last_legal_alternative=False,
        price_confidence=0.7, position_need_score=0.5, portfolio_paths_broken_if_missed=0,
    )
    assert score.starting_lineup_gain == 45.0


def test_position_need_never_exceeds_its_capped_share_of_total():
    from auction_engine.live_target_scoring import MAX_POSITION_NEED_SHARE
    score = compute_target_score(
        player="X", position="TE", team_specific_value_dollars=5.0, expected_role="required starter",
        expected_market_price_dollars=50.0, exact_or_approximate_ceiling_dollars=10.0, hard_max=5.0,
        remaining_alternatives_count=3, is_last_legal_alternative=False,
        price_confidence=0.1, position_need_score=1.0, portfolio_paths_broken_if_missed=0,
    )
    assert score.position_need_score <= MAX_POSITION_NEED_SHARE + 1e-9


def test_all_recommendation_classes_from_allowed_set():
    score = compute_target_score(
        player="X", position="WR", team_specific_value_dollars=50.0, expected_role="FLEX starter",
        expected_market_price_dollars=20.0, exact_or_approximate_ceiling_dollars=40.0, hard_max=36.0,
        remaining_alternatives_count=3, is_last_legal_alternative=False,
        price_confidence=0.6, position_need_score=0.3, portfolio_paths_broken_if_missed=0,
    )
    assert score.recommendation_class in RECOMMENDATION_CLASSES


def test_pass_above_limit_when_price_exceeds_hard_max():
    score = compute_target_score(
        player="X", position="WR", team_specific_value_dollars=50.0, expected_role="FLEX starter",
        expected_market_price_dollars=60.0, exact_or_approximate_ceiling_dollars=40.0, hard_max=36.0,
        remaining_alternatives_count=3, is_last_legal_alternative=False,
        price_confidence=0.6, position_need_score=0.3, portfolio_paths_broken_if_missed=0,
    )
    assert score.recommendation_class == "PASS_ABOVE_LIMIT"
