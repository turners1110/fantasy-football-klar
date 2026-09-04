"""Phase 3D item 18: required tests for the production market repair,
calibration, full-player price distributions, and Sam's exact preliminary
bid board.

Built incrementally alongside each Phase 3D item; grows across this file
as later items land (calibration harness, willingness rebuild, full-pool
distributions, Sam's board)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from auction_model import config as auction_cfg
from auction_model import labels
from auction_model import replacement_methods
from auction_model.exact_leaguewide_allocation import (
    MAX_QB_PER_TEAM,
    REQUIRED_STARTERS as EXACT_REQUIRED_STARTERS,
    ROSTER_SIZE,
    solve_exact_leaguewide_allocation,
)
from mock_draft.data import load_confirmed_pool_and_teams

PHASE3D_OUT = BASE_DIR / "outputs" / "auction_rebuild" / "phase3d"


def _skip_if_missing(path: Path):
    if not path.exists():
        pytest.skip(f"{path.name} not built in this environment")


# ---------------------------------------------------------------------------
# item 1: label taxonomy
# ---------------------------------------------------------------------------

def test_01_label_taxonomy_has_all_seven_required_labels():
    required = {
        "UNCALIBRATED_SIMULATED_PRICE",
        "CALIBRATED_EXPECTED_MARKET_PRICE",
        "PUBLIC_AUCTION_ANCHOR",
        "HISTORICAL_LEAGUE_PRICE",
        "TEAM_SPECIFIC_VALUE",
        "EXACT_TEAM_BID_CEILING",
        "APPROXIMATE_TEAM_BID_CEILING",
    }
    assert required == set(labels.ALL_LABELS)
    # Every label's constant name must equal its own string value (so a
    # typo can never silently produce a label absent from ALL_LABELS).
    for name in required:
        assert getattr(labels, name) == name


def test_02_historical_reports_carry_a_label_correction_notice():
    for phase in ("phase3b", "phase3c"):
        path = BASE_DIR / "outputs" / "auction_rebuild" / phase / "final_report.md"
        _skip_if_missing(path)
        text = path.read_text()
        assert "LABEL CORRECTION NOTICE" in text
        assert "UNCALIBRATED_SIMULATED_PRICE" in text


# ---------------------------------------------------------------------------
# items 2-3: EXACT_LEAGUEWIDE_ALLOCATION (real MIP) + production adoption
# ---------------------------------------------------------------------------

def test_03_exact_allocation_produces_legal_rosters():
    """Every team must end with exactly ROSTER_SIZE players, the required
    starter/FLEX counts, and no more than MAX_QB_PER_TEAM quarterbacks."""
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    team_keepers = {}
    for name, t in teams.items():
        team_keepers[name] = [(n, p, pts) for n, p, _pr, pts in t.roster]
    pool_points = {p.name: (p.position, p.projected_points) for p in players.values()}

    result = solve_exact_leaguewide_allocation(pool_points, team_keepers)
    assert result.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL")
    # result.assignments already includes BOTH keepers and pool fills (one
    # row per on-roster player per team) -- see the module's own row-build
    # loop, which iterates all_players_by_team (keepers + pool).
    by_team = result.assignments.groupby("team")
    for team_name, keepers in team_keepers.items():
        team_rows = by_team.get_group(team_name) if team_name in by_team.groups else pd.DataFrame(columns=result.assignments.columns)
        assert len(team_rows) == ROSTER_SIZE, team_name
        qb_count = (team_rows["position"] == "QB").sum()
        assert qb_count <= MAX_QB_PER_TEAM, team_name
    for role, need in EXACT_REQUIRED_STARTERS.items():
        counts = result.assignments[result.assignments["role"] == role].groupby("team").size()
        assert (counts.reindex(team_keepers.keys(), fill_value=0) == need).all(), role


def test_04_greedy_method_is_labeled_a_heuristic_not_an_optimization():
    """The renamed greedy method's own docstring must disclose it is a
    heuristic -- the exact "not an exact optimization" wording the old
    'C_OPTIMIZATION_DERIVED' label was retracted for lacking."""
    doc = replacement_methods.greedy_leaguewide_selection.__doc__ or ""
    assert "not an exact optimization" in doc.lower() or "heuristic" in doc.lower()
    assert auction_cfg.GREEDY_LEAGUEWIDE_ALLOCATION == "GREEDY_LEAGUEWIDE_ALLOCATION"
    assert auction_cfg.EXACT_LEAGUEWIDE_ALLOCATION == "EXACT_LEAGUEWIDE_ALLOCATION"


def test_05_production_default_is_exact_leaguewide_allocation():
    assert auction_cfg.REPLACEMENT_METHOD == auction_cfg.EXACT_LEAGUEWIDE_ALLOCATION


def test_06_exact_and_greedy_replacement_outputs_exist_and_agree_mostly():
    path = PHASE3D_OUT / "greedy_exact_replacement_comparison.csv"
    _skip_if_missing(path)
    df = pd.read_csv(path)
    summary = df[df["method"] == "COMPARISON_SUMMARY"].iloc[0]
    total_compared = summary["players_only_in_greedy"] + summary["players_only_in_exact"] + summary["players_in_both"]
    # The two methods must agree on the large majority of pool-fill slots --
    # if they disagreed on most players, something would be structurally
    # broken (they both fill the same roster demand from the same pool).
    assert summary["players_in_both"] / total_compared > 0.5


def test_07_exact_allocation_does_not_use_auction_price_in_objective():
    """Item 2 explicitly requires the exact solve to ignore auction price
    entirely -- verified by checking the solver's inputs never carry a
    price/dollar field, only (position, points)."""
    import inspect
    src = inspect.getsource(solve_exact_leaguewide_allocation)
    assert "price" not in src.lower()


# ---------------------------------------------------------------------------
# item 4: FLEX demand from the exact allocation + player-specific sensitivity
# ---------------------------------------------------------------------------

def test_08_flex_sensitivity_output_exists_and_sums_to_100pct():
    path = PHASE3D_OUT / "flex_allocation_sensitivity.csv"
    _skip_if_missing(path)
    df = pd.read_csv(path)
    assert set(df["position"]) == {"RB", "WR", "TE"}
    assert df["baseline_flex_share_pct"].sum() == pytest.approx(100.0, abs=0.5)
    for _, row in df.iterrows():
        assert row["sensitivity_p10_pct"] <= row["sensitivity_p50_pct"] <= row["sensitivity_p90_pct"] + 1e-6


def test_09_flex_sensitivity_uses_position_specific_not_uniform_bounds():
    from auction_model.flex_sensitivity import PROJECTION_UNCERTAINTY
    ranges = set(PROJECTION_UNCERTAINTY.values())
    # NOT a uniform scalar: at least two positions must have DIFFERENT
    # [low, high] bounds, or this would just be one global scalar in disguise.
    assert len(ranges) > 1


# ---------------------------------------------------------------------------
# items 7-8: public anchor hierarchy + keeper-removal normalization
# ---------------------------------------------------------------------------

def test_10_anchor_hierarchy_covers_every_player_with_a_disclosed_source():
    path = PHASE3D_OUT / "public_anchor_hierarchy.csv"
    _skip_if_missing(path)
    df = pd.read_csv(path)
    valid_sources = {"PARTIAL_WEBSEARCH_VALUES", "FANTASYPROS_RANK_TIER_CONVERSION",
                      "NO_PUBLIC_ANCHOR_INTERNAL_NEUTRAL_VALUE"}
    assert set(df["source"]).issubset(valid_sources)
    assert df["normalized_value"].notna().all()


def test_11_anchor_normalization_matches_reported_budget_within_clip_tolerance():
    path = PHASE3D_OUT / "anchor_normalization.csv"
    _skip_if_missing(path)
    df = pd.read_csv(path)
    primary_total = df["keeper_removed_anchor_primary"].sum()
    reported_total = pd.read_csv(
        BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv"
    )["primary_auction_budget"].sum()
    # The post-clip total can drift slightly above the target (the $1
    # floor can only push values up), never wildly off.
    assert primary_total >= reported_total - 1.0
    assert primary_total <= reported_total * 1.05


def test_12_websearch_anchor_bug_is_fixed_not_absurd():
    """Regression test for the phase-3D-discovered bug: phase 3C's
    normalized_open_market_value column rescaled its 6-7-player WebSearch
    sample as if it alone should absorb the entire pool's discretionary
    cash (Rashee Rice: $711.83, >3x this league's own per-team budget).
    auction_model.public_anchor must not reproduce that inflation."""
    from auction_model.config import BUDGET_PER_TEAM
    from auction_model.public_anchor import build_public_anchor_hierarchy
    players, _teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    df = build_public_anchor_hierarchy(players)
    websearch = df[df["source"] == "PARTIAL_WEBSEARCH_VALUES"]
    if len(websearch):
        assert (websearch["normalized_value"] < BUDGET_PER_TEAM).all()


# ---------------------------------------------------------------------------
# item 5: bounded additive willingness (star-ceiling override removed)
# ---------------------------------------------------------------------------

def test_13_star_max_value_multiple_not_used_in_compute_willingness():
    import inspect
    from mock_draft import valuation
    src = inspect.getsource(valuation.compute_willingness)
    assert "STAR_MAX_VALUE_MULTIPLE" not in src
    assert "is_star_candidate" not in src


def test_14_all_required_bounded_adjustment_config_fields_exist():
    required = [
        "MAX_ROSTER_FIT_ADJUSTMENT", "MAX_SCARCITY_ADJUSTMENT", "MAX_TIER_ADJUSTMENT",
        "MAX_ARCHETYPE_ADJUSTMENT", "MAX_NOISE_ADJUSTMENT",
        "MAX_TOTAL_PREMIUM_OVER_ANCHOR", "MAX_TOTAL_DISCOUNT_BELOW_ANCHOR",
    ]
    for field_name in required:
        assert hasattr(auction_cfg, field_name), field_name
        assert isinstance(getattr(auction_cfg, field_name), (int, float))


def test_15_willingness_is_additive_and_bounded_relative_to_anchor():
    from mock_draft.models import Player, Team
    from mock_draft.valuation import compute_willingness
    team = Team(name="T", budget_remaining=200.0, roster=[], archetype="stars_and_scrubs")
    player = Player(name="P", position="RB", base_value=80.0, tier=1, tier_size=4,
                     tier_rank=4, is_star_eligible=True, projected_points=200.0)
    rng = np.random.default_rng(2)
    diag = {}
    w = compute_willingness(team, player, rng, draft_progress=0.0, diagnostics=diag)
    assert diag["lower_bound"] == pytest.approx(diag["base_market_anchor"] - auction_cfg.MAX_TOTAL_DISCOUNT_BELOW_ANCHOR)
    assert diag["upper_bound"] == pytest.approx(diag["base_market_anchor"] + auction_cfg.MAX_TOTAL_PREMIUM_OVER_ANCHOR)
    assert diag["lower_bound"] - 0.01 <= w <= diag["upper_bound"] + 0.01


def test_16_value_purist_never_exceeds_own_anchor():
    from mock_draft.models import Player, Team
    from mock_draft.valuation import compute_willingness
    team = Team(name="T", budget_remaining=200.0, roster=[], archetype="value_purist")
    player = Player(name="P", position="RB", base_value=40.0, tier=1, tier_size=4,
                     tier_rank=1, is_star_eligible=True, projected_points=200.0)
    for seed in range(5):
        rng = np.random.default_rng(seed)
        diag = {}
        w = compute_willingness(team, player, rng, draft_progress=0.0, diagnostics=diag)
        assert w <= diag["base_market_anchor"] + 0.01


def test_17_base_market_anchor_falls_back_gracefully_with_no_external_coverage():
    from mock_draft.valuation import compute_base_market_anchor
    from mock_draft.models import Player
    player = Player(name="Nobody", position="WR", base_value=25.0, tier=2, tier_size=4, tier_rank=1)
    assert player.public_anchor_value is None
    assert player.historical_anchor_value is None
    assert compute_base_market_anchor(player) == pytest.approx(25.0)
