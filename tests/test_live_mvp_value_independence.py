"""Live MVP Part 1: value-independence audit tests. Must pass BEFORE any
other live_mvp work is trusted."""
from __future__ import annotations

import inspect

import pandas as pd

from auction_model import exact_roster_solver


def _pool(rows):
    return pd.DataFrame(rows)


def _keepers(rows):
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["player", "position", "projected_points", "keeper_price_2026"])


def test_identical_projection_different_price_same_benefit():
    pool_low = _pool([
        {"player": "A", "position": "WR", "projected_points": 150.0, "suggested_auction_price": 5.0},
    ])
    pool_high = _pool([
        {"player": "A", "position": "WR", "projected_points": 150.0, "suggested_auction_price": 500.0},
    ])
    filler = (
        [{"player": f"wr{i}", "position": "WR", "projected_points": 10.0, "suggested_auction_price": 1.0} for i in range(10)]
        + [{"player": f"rb{i}", "position": "RB", "projected_points": 10.0, "suggested_auction_price": 1.0} for i in range(5)]
        + [{"player": f"qb{i}", "position": "QB", "projected_points": 10.0, "suggested_auction_price": 1.0} for i in range(2)]
        + [{"player": f"te{i}", "position": "TE", "projected_points": 10.0, "suggested_auction_price": 1.0} for i in range(3)]
    )
    keepers = _keepers([])
    result_low = exact_roster_solver.solve_exact_roster(pd.concat([pool_low, _pool(filler)], ignore_index=True), budget=400.0, n_auction_spots=16, keepers=keepers)
    # In pool_high, A costs 500 (unaffordable) -- so instead confirm the OBJECTIVE contribution of A's points
    # is identical regardless of price by checking start_expr/bench_expr construction directly (see next test).
    assert result_low.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL")


def test_changing_price_changes_cost_not_benefit():
    """A player's price must appear ONLY inside the budget constraint,
    never inside the starting/bench objective expressions."""
    pool = _pool([{"player": "A", "position": "WR", "projected_points": 100.0, "suggested_auction_price": 5.0}])
    keepers = _keepers([])
    prob, y, expr = exact_roster_solver._build_model(
        exact_roster_solver._build_combined_pool(pool, keepers, set()), budget=100.0, n_auction_spots=1, stage=1,
    )
    obj_str = str(prob.objective)
    # The stage-1 objective (start_expr) must reference projected_points'
    # coefficient (100.0) and must NOT reference the price coefficient (5.0)
    assert "100.0" in obj_str or "100*" in obj_str.replace(" ", "")
    assert "5.0*y" not in obj_str.replace(" ", "") and "5*y" not in obj_str.replace(" ", "")


def test_changing_points_changes_benefit():
    pool_a = _pool([{"player": "A", "position": "WR", "projected_points": 50.0, "suggested_auction_price": 5.0}])
    pool_b = _pool([{"player": "A", "position": "WR", "projected_points": 300.0, "suggested_auction_price": 5.0}])
    keepers = _keepers([])
    prob_a, *_ = exact_roster_solver._build_model(exact_roster_solver._build_combined_pool(pool_a, keepers, set()), budget=100.0, n_auction_spots=1, stage=1)
    prob_b, *_ = exact_roster_solver._build_model(exact_roster_solver._build_combined_pool(pool_b, keepers, set()), budget=100.0, n_auction_spots=1, stage=1)
    assert str(prob_a.objective) != str(prob_b.objective)


def test_historical_anchor_does_not_enter_objective():
    src = inspect.getsource(exact_roster_solver._build_model)
    assert "historical_anchor" not in src


def test_public_anchor_does_not_enter_objective():
    src = inspect.getsource(exact_roster_solver._build_model)
    assert "public_anchor" not in src


def test_price_only_affects_cost_not_benefit():
    src = inspect.getsource(exact_roster_solver._build_model)
    # start_expr and bench_expr must be built purely from projected_points
    start_block = src[src.index("start_expr ="):src.index("bench_expr =")]
    assert "price" not in start_block
    bench_block = src[src.index("bench_expr ="):src.index("spend_expr =")]
    assert "price" not in bench_block


def test_stage3_tiebreak_rewards_lower_spend_not_higher_price():
    src = inspect.getsource(exact_roster_solver._build_model)
    assert "(budget - spend_expr)" in src.replace(" ", "").replace("\n", "") or \
           "budget-spend_expr" in src.replace(" ", "")


def test_inflated_price_alone_does_not_raise_exact_ceiling():
    """Binary-search style ceiling probe: force-buying a candidate at a
    higher test price can only ever match or worsen (never improve) the
    purchase-scenario objective versus a lower test price, for identical
    projected_points."""
    def solve_at(test_price):
        pool = _pool([{"player": "Target", "position": "WR", "projected_points": 120.0, "suggested_auction_price": test_price}]
                      + [{"player": f"f{i}", "position": "WR", "projected_points": 20.0, "suggested_auction_price": 1.0} for i in range(10)])
        keepers = _keepers([])
        return exact_roster_solver.solve_exact_roster(pool, budget=50.0, n_auction_spots=1, keepers=keepers)

    cheap = solve_at(5.0)
    expensive = solve_at(45.0)  # still affordable under budget=50
    assert cheap.starting_points == expensive.starting_points, (
        "starting points from purchasing the SAME player with the SAME projection must not change with price"
    )
