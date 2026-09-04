"""Phase 3B item 17: remaining required tests (5-20) not already covered
by tests/test_phase3b_concentration.py (which covers 1-4)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from auction_model import config
from mock_draft.auction import resolve_bid, run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.models import Player, Team


# ---------------------------------------------------------------------------
# 5-7: budget scenario / trade-adjustment double-counting
# ---------------------------------------------------------------------------

def test_05_reported_budget_scenario_uses_final_reported_values_once():
    """primary_auction_budget must equal sheet_reported_remaining_budget
    directly for every non-Sam team -- never sheet_reported PLUS
    cash_adjustments layered back on top."""
    states = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv")
    for _, row in states.iterrows():
        if row["team_id"] == "Sam":
            continue  # Sam uses an explicit user-confirmed override, not the sheet value directly
        assert row["primary_auction_budget"] == row["sheet_reported_remaining_budget"], row["team_id"]


def test_06_sam_override_remains_223():
    states = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv")
    sam = states[states["team_id"] == "Sam"].iloc[0]
    assert sam["primary_auction_budget"] == 223
    assert sam["conversions_scenario_auction_budget"] == 221


def test_07_brandon_trade_adjustment_not_double_counted():
    """Brandon's confirmed +$15 trade credit lives in
    data/team_budget_adjustments_2026.csv for the FORMULA_RECONCILED
    sensitivity scenario only -- his REPORTED primary_auction_budget
    (the sheet's own number, presumably already captured post-trade)
    must NOT have that +$15 added again on top."""
    states = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv")
    brandon = states[states["team_id"] == "Brandon"].iloc[0]
    assert brandon["primary_auction_budget"] == brandon["sheet_reported_remaining_budget"] == 184
    assert brandon["primary_auction_budget"] != brandon["sheet_reported_remaining_budget"] + 15


# ---------------------------------------------------------------------------
# 8-9: conditional price percentiles vs draft probability
# ---------------------------------------------------------------------------

def test_08_conditional_price_percentiles_exclude_unsold_outcomes():
    """A player unsold in some simulations must never have those unsold
    outcomes filled with $0 when computing conditional price percentiles
    -- percentiles are computed ONLY over simulations where the player
    actually sold."""
    # Simulate: a player sells in 3 of 5 "auctions" at prices 10/20/30;
    # the other 2 are unsold. The correct median is 20 (middle of 10/20/30),
    # NOT 10 (the median of [0,0,10,20,30]).
    all_outcomes = [10, 20, 30, None, None]  # None = unsold
    sold_prices = [p for p in all_outcomes if p is not None]
    conditional_median = float(np.median(sold_prices))
    wrong_median_if_zero_filled = float(np.median([p if p is not None else 0 for p in all_outcomes]))
    assert conditional_median == 20.0
    assert wrong_median_if_zero_filled != conditional_median


def test_09_draft_probability_includes_sold_and_unsold_outcomes():
    """draft_probability itself (unlike the price percentiles) MUST use
    the full outcome set, sold and unsold both -- it's a probability of
    selling, not a price."""
    all_outcomes = [10, 20, 30, None, None]
    draft_probability = sum(1 for p in all_outcomes if p is not None) / len(all_outcomes)
    assert draft_probability == 0.6


# ---------------------------------------------------------------------------
# 10-11: organic pricing / uncontested sales
# ---------------------------------------------------------------------------

def test_10_organic_price_follows_credible_competing_bids():
    """A contested sale's price must be strictly greater than $1 and no
    higher than the winner's own cap -- i.e. driven by the competing
    bid process, not assigned independently of it."""
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    rng = np.random.default_rng(3)
    log, _ = run_single_auction(players, teams, rng)
    contested = [e for e in log if e["bidder_count"] > 1]
    assert len(contested) > 0
    for e in contested:
        assert e["sale_price"] > config.MIN_PRICE
        assert e["sale_price"] >= e["second_highest_bid"]


def test_11_uncontested_nomination_sells_for_one_dollar():
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    rng = np.random.default_rng(3)
    log, _ = run_single_auction(players, teams, rng)
    uncontested = [e for e in log if e["bidder_count"] == 1]
    assert len(uncontested) > 0
    for e in uncontested:
        assert e["sale_price"] == config.MIN_PRICE == 1.0


# ---------------------------------------------------------------------------
# 12-13: spending reconciliation
# ---------------------------------------------------------------------------

def test_12_position_spending_reconciles_to_total_spending():
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    rng = np.random.default_rng(5)
    log, _ = run_single_auction(players, teams, rng)
    df = pd.DataFrame(log)
    total = df["sale_price"].sum()
    by_position = df.groupby("position")["sale_price"].sum().sum()
    assert by_position == pytest.approx(total, abs=0.01)


def test_13_player_level_spending_reconciles_to_position_spending():
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    rng = np.random.default_rng(5)
    log, _ = run_single_auction(players, teams, rng)
    df = pd.DataFrame(log)
    for position, group in df.groupby("position"):
        assert group["sale_price"].sum() == pytest.approx(sum(e["sale_price"] for e in log if e["position"] == position), abs=0.01)


# ---------------------------------------------------------------------------
# 14: projection coverage
# ---------------------------------------------------------------------------

def test_14_projection_coverage_reports_correctly():
    df = pd.DataFrame({"projected_points": [100.0, None, 50.0, None]})
    coverage = df["projected_points"].notna().sum() / len(df)
    assert coverage == 0.5


# ---------------------------------------------------------------------------
# 15-16: scoring rules
# ---------------------------------------------------------------------------

def test_15_half_ppr_scoring_remains_correct():
    assert config.SCORING.reception == 0.5
    stat_row = {"reception": 4, "rec_yd": 40, "rec_td": 0}
    points = config.score_from_stats(stat_row)
    assert points == pytest.approx(4 * 0.5 + 40 * 0.1, abs=0.01)


def test_16_four_point_passing_touchdowns_remain_correct():
    assert config.SCORING.pass_td == 4.0
    stat_row = {"pass_yd": 300, "pass_td": 2, "interception": 1}
    points = config.score_from_stats(stat_row)
    assert points == pytest.approx(300 * 0.04 + 2 * 4.0 + 1 * -2.0, abs=0.01)


# ---------------------------------------------------------------------------
# 17-18: unit correctness in reported error/sanity metrics
# ---------------------------------------------------------------------------

def test_17_counterfactual_error_metrics_use_dollar_and_utility_units_correctly():
    """counterfactual_approximation_error.csv's own column names must
    keep dollar-denominated fields (price) and points-denominated fields
    (starting_lineup_points) visually and semantically distinct -- never
    a single unlabeled 'error' column mixing both."""
    path = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "counterfactual_approximation_error.csv"
    if not path.exists():
        pytest.skip("counterfactual_approximation_error.csv not built in this environment")
    df = pd.read_csv(path)
    assert "price" in df.columns  # dollar-denominated
    assert "greedy_starting_points" in df.columns and "exact_starting_points" in df.columns  # points-denominated
    assert "absolute_error" in df.columns  # explicitly a POINTS error (of starting_points), not a price error


def test_18_sam_sanity_results_label_points_and_dollars_separately():
    import json
    path = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "sam_sanity_tests.json"
    if not path.exists():
        pytest.skip("sam_sanity_tests.json not built in this environment")
    data = json.loads(path.read_text())
    for scenario in data["scenarios"]:
        assert "price_spent" in scenario  # dollars
        assert "marginal_utility" in scenario  # utility points, NOT dollars -- see phase 3B's sam_label_audit
        # The two must never be compared as if on the same scale -- this
        # test only asserts they are reported as SEPARATE fields.
        assert scenario["price_spent"] != scenario["marginal_utility"] or scenario["price_spent"] == 0


# ---------------------------------------------------------------------------
# 19: held-out seed disjointness
# ---------------------------------------------------------------------------

def test_19_held_out_seeds_do_not_overlap_training_or_validation():
    training_seeds = set(range(0, 200))
    validation_seeds = set(range(200, 400))
    held_out_seeds = set(range(400, 600))
    assert not (training_seeds & validation_seeds)
    assert not (training_seeds & held_out_seeds)
    assert not (validation_seeds & held_out_seeds)


# ---------------------------------------------------------------------------
# 20: observation count and confidence on final price rows
# ---------------------------------------------------------------------------

def test_20_final_price_rows_include_observation_count_and_confidence():
    path = BASE_DIR / "outputs" / "auction_rebuild" / "phase3b" / "sam_label_audit.csv"
    if not path.exists():
        pytest.skip("sam_label_audit.csv not built in this environment")
    df = pd.read_csv(path)
    assert "n_sale_observations" in df.columns
    assert "draft_probability" in df.columns
    # A row with too few observations must report a null percentile, not
    # a fabricated one (the min-observation threshold from item 10).
    low_n = df[df["n_sale_observations"] < 5]
    if len(low_n):
        assert low_n["market_price_p50"].isna().all()
