"""Keeper market iteration tests."""

import pandas as pd
import pytest

from auction_model import config, keeper_market, keepers


def _minimal_fixtures():
    salaries = pd.DataFrame([
        {"team": "Sam", "player": "Player A", "position": "RB", "salary_2025": 20,
         "notes": "", "has_confirmed_salary": True, "is_tagged_2025": False,
         "on_ir": False, "paul_rule_eligible": False, "paul_rule_verified": False,
         "paul_rule_source": "", "salary_origin": "UNKNOWN", "origin_confidence": 0.1},
        {"team": "Sam", "player": "Player B", "position": "WR", "salary_2025": 15,
         "notes": "", "has_confirmed_salary": True, "is_tagged_2025": False,
         "on_ir": False, "paul_rule_eligible": False, "paul_rule_verified": False,
         "paul_rule_source": "", "salary_origin": "UNKNOWN", "origin_confidence": 0.1},
    ])
    pool = pd.DataFrame([
        {"team": "Sam", "player": "Player A", "position": "RB", "salary_2025": 20,
         "projected_points": 150, "has_confirmed_salary": True, "fp_tier": 3},
        {"team": "Sam", "player": "Player B", "position": "WR", "salary_2025": 15,
         "projected_points": 140, "has_confirmed_salary": True, "fp_tier": 3},
        {"team": pd.NA, "player": "Free Agent", "position": "RB", "salary_2025": 5,
         "projected_points": 100, "has_confirmed_salary": True, "fp_tier": 5},
    ])
    neutral = pd.Series([50.0, 40.0], index=salaries.index)
    return salaries, pool, neutral


def test_at_least_one_iteration_runs():
    salaries, pool, neutral = _minimal_fixtures()
    result = keeper_market.iterate_keeper_market(
        salaries, pool, neutral, blend_weight=0.0, max_iterations=5,
    )
    assert result.iterations >= 1
    assert not result.iteration_log.empty


def test_iteration_log_has_required_columns():
    salaries, pool, neutral = _minimal_fixtures()
    result = keeper_market.iterate_keeper_market(
        salaries, pool, neutral, blend_weight=0.0, max_iterations=3,
    )
    for col in ("iteration", "team", "player", "keeper_status", "market_state_hash"):
        assert col in result.iteration_log.columns
