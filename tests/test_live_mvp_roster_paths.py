"""Live MVP Part 5 roster-path tests."""
from __future__ import annotations

from auction_engine.auction_state import TeamState
from auction_engine.live_roster_paths import compute_live_roster_paths, PATH_STYLES


def _make_team(open_slots=9, budget=223.0):
    n_keepers = 15 - open_slots
    roster = [{"player_id": f"keeper{i}", "position": "RB", "price": 10.0, "projected_points": 100.0} for i in range(n_keepers)]
    return TeamState(team_id="Sam", budget_remaining=budget, roster=roster)


def _pool(n=30):
    positions = ["QB", "RB", "WR", "TE"]
    pool = {}
    for i in range(n):
        pool[f"p{i}"] = {"display_name": f"P{i}", "position": positions[i % 4],
                          "projected_points": 100.0 + i, "expected_price": max(1, 30 - i), "conservative_price": max(1, 35 - i)}
    return pool


def test_all_five_styles_present():
    team = _make_team(open_slots=9)
    result = compute_live_roster_paths(team, _pool())
    assert set(result.keys()) == set(PATH_STYLES)


def test_paths_stay_within_budget():
    team = _make_team(open_slots=9, budget=50.0)
    result = compute_live_roster_paths(team, _pool())
    for style, r in result.items():
        if r["status"] == "OPTIMAL":
            assert r["spend"] <= 50.0


def test_no_open_slots_returns_no_open_slots_status():
    team = _make_team(open_slots=0)
    result = compute_live_roster_paths(team, _pool())
    for style, r in result.items():
        assert r["status"] == "NO_OPEN_SLOTS"


def test_hard_maxes_are_enforced_structurally():
    team = _make_team(open_slots=9, budget=223.0)
    pool = _pool()
    hard_maxes = {pid: 5 for pid in pool}  # cap every candidate at $5
    result = compute_live_roster_paths(team, pool, hard_maxes=hard_maxes)
    for style, r in result.items():
        if r["status"] == "OPTIMAL":
            for p in r["players"]:
                assert p["price"] <= 5
