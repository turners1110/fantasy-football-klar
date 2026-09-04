"""Phase 3D item 18: required tests for the production market repair,
calibration, full-player price distributions, and Sam's exact preliminary
bid board.

Built incrementally alongside each Phase 3D item; grows across this file
as later items land (calibration harness, willingness rebuild, full-pool
distributions, Sam's board)."""

from __future__ import annotations

import sys
from pathlib import Path

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
