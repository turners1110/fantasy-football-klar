"""Exact roster solver synthetic tests."""

import pandas as pd
import pytest

from auction_model import config, exact_roster_solver, roster_optimizer


@pytest.fixture(autouse=True)
def _clear():
    roster_optimizer.clear_caches()
    yield
    roster_optimizer.clear_caches()


def _candidates(rows):
    return pd.DataFrame(rows)


def test_exact_fifteen_players():
    rows = []
    for i, (pos, pts, price) in enumerate([
        ("QB", 280, 30), ("RB", 220, 40), ("RB", 200, 35), ("RB", 180, 20),
        ("WR", 210, 38), ("WR", 190, 32), ("WR", 170, 15), ("WR", 160, 12),
        ("TE", 140, 25), ("TE", 120, 10), ("RB", 150, 8), ("WR", 130, 5),
        ("RB", 100, 3), ("WR", 90, 2), ("TE", 80, 1), ("QB", 60, 1),
    ]):
        rows.append({"player": f"P{i}", "position": pos, "projected_points": pts, "suggested_auction_price": price})
    result = exact_roster_solver.solve_exact_auction_roster(_candidates(rows), 400, 15)
    assert result.status == "OPTIMAL"
    assert len(result.selected) == 15
    failures = exact_roster_solver.post_solve_assertions(result, 400)
    assert failures == []


def test_qb_not_in_flex():
    rows = [
        {"player": "QB1", "position": "QB", "projected_points": 300, "suggested_auction_price": 10},
        {"player": "QB2", "position": "QB", "projected_points": 250, "suggested_auction_price": 5},
    ]
    for i in range(20):
        rows.append({"player": f"RB{i}", "position": "RB", "projected_points": 150 - i, "suggested_auction_price": 3})
    for i in range(20):
        rows.append({"player": f"WR{i}", "position": "WR", "projected_points": 140 - i, "suggested_auction_price": 3})
    for i in range(10):
        rows.append({"player": f"TE{i}", "position": "TE", "projected_points": 100 - i, "suggested_auction_price": 2})
    result = exact_roster_solver.solve_exact_auction_roster(_candidates(rows), 400, 15)
    assert result.status == "OPTIMAL"
    for player, role in result.role_assignments.items():
        if role.startswith("FLEX"):
            pos = next(r["position"] for r in rows if r["player"] == player)
            assert pos in {"RB", "WR", "TE"}


def test_budget_not_exceeded():
    rows = [{"player": f"P{i}", "position": "RB", "projected_points": 100, "suggested_auction_price": 30} for i in range(20)]
    rows += [{"player": "QB1", "position": "QB", "projected_points": 200, "suggested_auction_price": 10}]
    rows += [{"player": f"WR{i}", "position": "WR", "projected_points": 90, "suggested_auction_price": 5} for i in range(10)]
    rows += [{"player": "TE1", "position": "TE", "projected_points": 80, "suggested_auction_price": 5}]
    result = exact_roster_solver.solve_exact_auction_roster(_candidates(rows), 400, 15)
    assert result.spent <= 400


def test_infeasible_budget():
    rows = [{"player": "QB1", "position": "QB", "projected_points": 300, "suggested_auction_price": 500}]
    result = exact_roster_solver.solve_exact_auction_roster(_candidates(rows), 400, 15)
    assert result.status == "INFEASIBLE"


def test_exact_beats_or_matches_greedy():
    rows = []
    for i in range(15):
        rows.append({"player": f"STAR{i}", "position": "RB", "projected_points": 200 - i, "suggested_auction_price": 20})
    for i in range(30):
        rows.append({"player": f"F{i}", "position": "WR", "projected_points": 80, "suggested_auction_price": 1})
    rows += [
        {"player": "QB1", "position": "QB", "projected_points": 250, "suggested_auction_price": 10},
        {"player": "TE1", "position": "TE", "projected_points": 120, "suggested_auction_price": 8},
    ]
    pool = _candidates(rows)
    exact = exact_roster_solver.solve_exact_auction_roster(pool, 400, 15)
    greedy, _, _ = roster_optimizer.solve_auction_roster_greedy(pool, 400, 15)
    greedy_owned = greedy.copy()
    greedy_lu = roster_optimizer.assign_lineup(greedy_owned)
    assert exact.starting_points >= greedy_lu.starting_points


def test_deterministic():
    rows = [
        {"player": "QB1", "position": "QB", "projected_points": 250, "suggested_auction_price": 10},
        {"player": "RB1", "position": "RB", "projected_points": 200, "suggested_auction_price": 20},
    ] * 10
    pool = _candidates(rows)
    a = exact_roster_solver.solve_exact_auction_roster(pool, 400, 15)
    b = exact_roster_solver.solve_exact_auction_roster(pool, 400, 15)
    assert a.starting_points == b.starting_points
    assert set(a.role_assignments.keys()) == set(b.role_assignments.keys())
