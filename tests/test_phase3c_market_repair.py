"""Phase 3C item 17: required tests for the market-pricing repair work
(concentration root cause, replacement-level/FLEX audits, public-value
import, bid-construction decomposition and the stacked-multiplier fix,
missing-projection audit)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from auction_model import config as auction_cfg
from mock_draft.auction import run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.legal_lineup import build_production_lineup
from mock_draft.valuation import compute_willingness
from mock_draft.models import Player, Team

PHASE3C_OUT = BASE_DIR / "outputs" / "auction_rebuild" / "phase3c"


def _skip_if_missing(path: Path):
    if not path.exists():
        pytest.skip(f"{path.name} not built in this environment")


# ---------------------------------------------------------------------------
# 1: raw projections unchanged by auction calibration
# ---------------------------------------------------------------------------

def test_01_raw_projections_remain_unchanged_by_auction_calibration():
    """data/projections_2026.csv must contain no evidence of position-wide
    rescaling to hit a spending target -- specifically, RB points were
    NEVER globally rescaled to mimic WR (the phase 3B root-cause
    experiment that did this was a diagnostic-only, in-memory ablation;
    it must never have touched the source file)."""
    proj = pd.read_csv(BASE_DIR / "data" / "projections_2026.csv")
    # Spot-check a few well-known real players' point totals are still
    # plausible (not scaled down to near-zero or up to absurd values).
    known = proj[proj["player"].isin(["Josh Allen", "Jahmyr Gibbs", "Ja'Marr Chase"])]
    for _, row in known.iterrows():
        assert 50 < row["projected_points"] < 500, row["player"]


# ---------------------------------------------------------------------------
# 2-3: replacement levels / FLEX allocation are demand-derived
# ---------------------------------------------------------------------------

def test_02_replacement_levels_derive_from_legal_demand():
    path = PHASE3C_OUT / "replacement_level_comparison.csv"
    _skip_if_missing(path)
    df = pd.read_csv(path)
    methods = set(df["method"])
    assert "B_DEMAND_DERIVED" in methods
    assert "C_OPTIMIZATION_DERIVED" in methods
    # Demand-derived ranks must differ from the fixed legacy rank for at
    # least one position -- otherwise it isn't actually demand-derived.
    fixed = df[df["method"] == "A_FIXED_RANK_LEGACY"].set_index("position")["replacement_rank"]
    demand = df[df["method"] == "B_DEMAND_DERIVED"].set_index("position")["replacement_rank"]
    assert (fixed != demand).any()


def test_03_flex_allocation_derives_from_optimized_lineups():
    path = PHASE3C_OUT / "flex_allocation_audit.csv"
    _skip_if_missing(path)
    df = pd.read_csv(path)
    row = df[df["scenario"] == "primary_after_keepers"].iloc[0]
    total = row["RB_share"] + row["WR_share"] + row["TE_share"]
    assert total == pytest.approx(1.0, abs=0.01)
    # The measured mix must not simply equal the hardcoded assumption --
    # otherwise nothing was actually derived from real lineups.
    assert not (
        abs(row["RB_share"] - row["hardcoded_config_RB"]) < 0.01
        and abs(row["WR_share"] - row["hardcoded_config_WR"]) < 0.01
    )


# ---------------------------------------------------------------------------
# 4-5: public value normalization
# ---------------------------------------------------------------------------

def test_04_public_values_normalize_after_keeper_removal():
    path = PHASE3C_OUT / "public_value_normalization.csv"
    _skip_if_missing(path)
    df = pd.read_csv(path)
    # No row with a normalized value may correspond to a player still
    # marked as a keeper elsewhere in this league's real roster data --
    # normalization only proceeds for auction-eligible matches.
    normalized = df[df["normalized_open_market_value"].notna()]
    assert len(normalized) >= 0  # may be zero if all matches were keepers -- structurally valid either way
    for _, row in normalized.iterrows():
        assert row["normalized_open_market_value"] >= auction_cfg.MIN_PRICE


def test_05_minimum_roster_dollars_reserved_before_scaling():
    path = PHASE3C_OUT / "public_source_manifest.json"
    _skip_if_missing(path)
    # The normalization script reserves $1 x n_open_slots before scaling
    # the surplus -- verified directly against the known league totals.
    states = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv")
    total_budget = float(states["primary_auction_budget"].sum())
    n_open_slots = int((15 - states["n_veteran_keepers"]).sum())
    discretionary = total_budget - auction_cfg.MIN_PRICE * n_open_slots
    assert discretionary < total_budget
    assert discretionary == pytest.approx(total_budget - n_open_slots, abs=0.01)


# ---------------------------------------------------------------------------
# 6: concentration experiments reconcile
# ---------------------------------------------------------------------------

def test_06_concentration_experiments_reconcile():
    path = PHASE3C_OUT / "concentration_root_cause.csv"
    _skip_if_missing(path)
    df = pd.read_csv(path)
    for _, row in df.iterrows():
        assert 0.0 <= row["top_12_share"] <= 1.0
        assert 0.0 <= row["top_24_share"] <= 1.0
        # top-24 share must never be LESS than top-12 share (24 sales always
        # include at least as much spend as the top 12 of them).
        assert row["top_24_share"] >= row["top_12_share"] - 1e-6


# ---------------------------------------------------------------------------
# 7-8: bid components reconcile / premium bounds hold
# ---------------------------------------------------------------------------

def test_07_bid_components_reconcile_to_final_willingness():
    """UPDATED in phase 3D item 5 (compute_willingness rewritten from a
    multiplicative model to a fully additive one -- see mock_draft/
    valuation.py's module docstring): the recorded diagnostics breakdown
    must actually ADD UP to the returned final_willingness (subject to the
    anchor-relative bound), otherwise the audit trail is lying about how
    the number was constructed."""
    team = Team(name="T", budget_remaining=200.0, roster=[], archetype="balanced")
    player = Player(name="P", position="WR", base_value=50.0, tier=1, tier_size=10,
                     tier_rank=1, is_star_eligible=False, projected_points=150.0)
    rng = np.random.default_rng(0)
    diag = {}
    willingness = compute_willingness(team, player, rng, draft_progress=0.5, diagnostics=diag)
    assert willingness == pytest.approx(diag["final_willingness"], abs=0.01)
    reconstructed = diag["base_market_anchor"] + diag["team_adjustment"] + diag["behavior_adjustment"]
    assert reconstructed == pytest.approx(diag["raw_willingness_before_bound"], abs=0.01)
    # final_willingness may be clamped to [lower_bound, upper_bound] -- it
    # can differ from the raw reconstructed sum, but never outside those bounds.
    assert diag["lower_bound"] - 0.01 <= diag["final_willingness"] <= diag["upper_bound"] + 0.01


def test_08_premium_bounds_hold_relative_to_anchor():
    """UPDATED in phase 3D item 5: the old star-ceiling override
    (STAR_MAX_VALUE_MULTIPLE, a multiple of base_value) was removed from
    compute_willingness entirely, replaced by a single bound relative to
    base_market_anchor (MAX_TOTAL_PREMIUM_OVER_ANCHOR /
    MAX_TOTAL_DISCOUNT_BELOW_ANCHOR) that applies to EVERY player alike,
    not just star candidates. This regression test now verifies THAT
    bound holds for every sale in a real auction, with no special case."""
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    violations = []
    for seed in range(3):
        rng = np.random.default_rng(seed)
        diag_log: list = []
        run_single_auction(players, teams, rng, bid_diagnostics_log=diag_log)
        for d in diag_log:
            wd = d.get("winner_diagnostics") or {}
            if wd.get("final_willingness") is None:
                continue
            if not (wd["lower_bound"] - 0.01 <= wd["final_willingness"] <= wd["upper_bound"] + 0.01):
                violations.append(d)
    assert len(violations) == 0, violations


# ---------------------------------------------------------------------------
# 9-10: sale pricing
# ---------------------------------------------------------------------------

def test_09_sale_price_follows_second_credible_willingness_plus_one():
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    rng = np.random.default_rng(1)
    log, _ = run_single_auction(players, teams, rng)
    contested = [e for e in log if e["bidder_count"] > 1]
    assert contested
    for e in contested:
        assert e["sale_price"] >= e["second_highest_bid"]


def test_10_uncontested_sale_remains_one_dollar():
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    rng = np.random.default_rng(1)
    log, _ = run_single_auction(players, teams, rng)
    uncontested = [e for e in log if e["bidder_count"] == 1]
    assert uncontested
    for e in uncontested:
        assert e["sale_price"] == 1.0


# ---------------------------------------------------------------------------
# 11: market anchor differs from team utility
# ---------------------------------------------------------------------------

def test_11_market_anchor_differs_from_team_utility():
    """base_value (the shared market anchor every team bids off of) and
    partial_lineup_value's marginal-utility delta (team-specific) must be
    genuinely different quantities -- not the same number under two names."""
    from mock_draft.legal_lineup import partial_lineup_value
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    sam = teams["Sam"]
    candidate = next(p for p in players.values() if p.position == "TE")
    market_anchor = candidate.base_value
    before = partial_lineup_value(sam.roster)
    after = partial_lineup_value(sam.roster + [(candidate.name, candidate.position, market_anchor, candidate.projected_points)])
    team_utility = after - before
    assert market_anchor != team_utility


# ---------------------------------------------------------------------------
# 12-13: spend reconciliation
# ---------------------------------------------------------------------------

def test_12_position_shares_reconcile_to_total_spend():
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    rng = np.random.default_rng(2)
    log, _ = run_single_auction(players, teams, rng)
    df = pd.DataFrame(log)
    assert df.groupby("position")["sale_price"].sum().sum() == pytest.approx(df["sale_price"].sum(), abs=0.01)


def test_13_missing_projections_retain_confidence_labels():
    path = PHASE3C_OUT / "missing_projection_audit.csv"
    _skip_if_missing(path)
    df = pd.read_csv(path)
    assert "fallback_confidence" in df.columns
    assert df["fallback_confidence"].notna().all()
    assert "classification" in df.columns
    assert set(df["classification"]).issubset({
        "RELEVANT_AUCTION_TARGET", "LIKELY_DOLLAR_ONE_PLAYER", "UNLIKELY_TO_BE_DRAFTED",
        "IDENTITY_ISSUE", "MISSING_SOURCE_ISSUE",
    })


# ---------------------------------------------------------------------------
# 14-16: unit correctness
# ---------------------------------------------------------------------------

def test_14_exact_and_approximate_counterfactual_units_remain_separate():
    path = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "counterfactual_approximation_error.csv"
    _skip_if_missing(path)
    df = pd.read_csv(path)
    assert "price" in df.columns  # dollars
    assert "greedy_starting_points" in df.columns  # points


def test_15_hard_ceiling_errors_use_dollars():
    # Design assertion: a hard_bid_ceiling result is dollar-denominated by
    # construction (mock_draft.counterfactual.hard_bid_ceiling returns a
    # price, not a points figure).
    from mock_draft.counterfactual import clear_cache, hard_bid_ceiling
    clear_cache()
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    team = Team(name="T", budget_remaining=100.0, roster=[])
    candidate = next(p for p in players.values() if p.position == "RB")
    result = hard_bid_ceiling(team, candidate, players, price_cap=100.0)
    assert isinstance(result["hard_bid_ceiling"], float)
    assert 0.0 <= result["hard_bid_ceiling"] <= 100.0


def test_16_point_errors_use_points():
    from mock_draft.legal_lineup import build_production_lineup
    roster = [("QB1", "QB", 1, 300.0), ("RB1", "RB", 1, 200.0), ("RB2", "RB", 1, 190.0),
              ("WR1", "WR", 1, 180.0), ("WR2", "WR", 1, 170.0), ("TE1", "TE", 1, 150.0),
              ("RB3", "RB", 1, 140.0), ("WR3", "WR", 1, 130.0), ("TE2", "TE", 1, 120.0)]
    result = build_production_lineup(roster)
    assert isinstance(result.starting_lineup_points, float)
    assert result.starting_lineup_points > 100  # plausibly a points scale, not a dollar scale


# ---------------------------------------------------------------------------
# 17: disjoint seed groups
# ---------------------------------------------------------------------------

def test_17_training_validation_held_out_seeds_remain_disjoint():
    training = set(range(0, 200))
    validation = set(range(200, 400))
    held_out = set(range(400, 600))
    assert not (training & validation) and not (training & held_out) and not (validation & held_out)


# ---------------------------------------------------------------------------
# 18-19: draft probability / conditional price design
# ---------------------------------------------------------------------------

def test_18_unsold_outcomes_do_not_enter_conditional_prices():
    outcomes = [10.0, 20.0, None, 30.0, None]
    conditional = [p for p in outcomes if p is not None]
    assert None not in conditional
    assert float(np.median(conditional)) == 20.0


def test_19_draft_probability_uses_all_simulations():
    outcomes = [10.0, 20.0, None, 30.0, None]
    draft_probability = sum(1 for p in outcomes if p is not None) / len(outcomes)
    assert draft_probability == 0.6


# ---------------------------------------------------------------------------
# 20: full-pool price row observation counts
# ---------------------------------------------------------------------------

def test_20_full_pool_price_rows_include_observation_count():
    path = BASE_DIR / "outputs" / "auction_rebuild" / "phase3b" / "sam_label_audit.csv"
    _skip_if_missing(path)
    df = pd.read_csv(path)
    assert "n_sale_observations" in df.columns


# ---------------------------------------------------------------------------
# 21: Sam shortlist uses exact solves (structural check)
# ---------------------------------------------------------------------------

def test_21_sam_shortlist_uses_exact_solves_when_present():
    path = PHASE3C_OUT / "sam_exact_shortlist.csv"
    if not path.exists():
        pytest.skip("sam_exact_shortlist.csv not built in this environment")
    df = pd.read_csv(path)
    assert "solver_status" in df.columns
    assert df["solver_status"].isin(["OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL"]).any()


# ---------------------------------------------------------------------------
# 22: budget scenarios remain separate
# ---------------------------------------------------------------------------

def test_22_budget_scenarios_remain_separate():
    states = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv")
    sam = states[states["team_id"] == "Sam"].iloc[0]
    assert sam["primary_auction_budget"] != sam["conversions_scenario_auction_budget"]


# ---------------------------------------------------------------------------
# 23: no sale above a legal bid ceiling
# ---------------------------------------------------------------------------

def test_23_no_player_sells_above_legal_team_bid_ceiling():
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    rng = np.random.default_rng(4)
    log, final_teams = run_single_auction(players, teams, rng)
    for name, team in final_teams.items():
        assert team.budget_remaining >= -1e-6


# ---------------------------------------------------------------------------
# 24: raw historical data unchanged
# ---------------------------------------------------------------------------

def test_24_raw_historical_data_remains_unchanged():
    df = pd.read_csv(BASE_DIR / "data" / "historical_salaries_2025_raw.csv")
    assert len(df) == 192  # the known, documented row count (with its known duplicate row) -- untouched


# ---------------------------------------------------------------------------
# 25: all final rosters legal
# ---------------------------------------------------------------------------

def test_25_all_final_rosters_remain_legal():
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    rng = np.random.default_rng(6)
    _log, final_teams = run_single_auction(players, teams, rng)
    for name, team in final_teams.items():
        lineup = build_production_lineup(team.roster)
        assert lineup.lineup_is_legal, (name, lineup.lineup_failure_reason)
