"""Tests for joint roster optimizer."""

import pandas as pd
import pytest

from auction_model import config, roster_optimizer


@pytest.fixture(autouse=True)
def _clear():
    roster_optimizer.clear_caches()
    yield
    roster_optimizer.clear_caches()


def _pool(rows):
    return pd.DataFrame(rows)


def test_one_qb_starter():
    players = _pool([
        {"player": "QB1", "position": "QB", "projected_points": 300},
        {"player": "RB1", "position": "RB", "projected_points": 200},
        {"player": "RB2", "position": "RB", "projected_points": 180},
        {"player": "WR1", "position": "WR", "projected_points": 190},
        {"player": "WR2", "position": "WR", "projected_points": 170},
        {"player": "TE1", "position": "TE", "projected_points": 140},
    ])
    lu = roster_optimizer.assign_lineup(players)
    assert len(lu.starting_lineup) >= 6
    qbs = [p for p, r in lu.roles.items() if r.startswith("QB")]
    assert len(qbs) == 1


def test_qb_not_in_flex():
    players = _pool([
        {"player": "QB1", "position": "QB", "projected_points": 300},
        {"player": "QB2", "position": "QB", "projected_points": 250},
        {"player": "RB1", "position": "RB", "projected_points": 200},
        {"player": "RB2", "position": "RB", "projected_points": 180},
        {"player": "WR1", "position": "WR", "projected_points": 190},
        {"player": "WR2", "position": "WR", "projected_points": 170},
        {"player": "TE1", "position": "TE", "projected_points": 140},
        {"player": "RB3", "position": "RB", "projected_points": 160},
        {"player": "WR3", "position": "WR", "projected_points": 150},
    ])
    lu = roster_optimizer.assign_lineup(players)
    flex_players = [p for p, r in lu.roles.items() if r.startswith("FLEX")]
    for p in flex_players:
        assert players[players["player"] == p]["position"].iloc[0] in {"RB", "WR", "TE"}


def test_distinct_cache_keys_keep_vs_release():
    roster = pd.DataFrame([
        {"team": "Sam", "player": "A", "position": "RB", "salary_2025": 10,
         "will_keep": False, "tag_used": False, "keeper_price_2026": 0, "projected_points": 150},
        {"team": "Sam", "player": "B", "position": "WR", "salary_2025": 20,
         "will_keep": False, "tag_used": False, "keeper_price_2026": 0, "projected_points": 140},
    ])
    pool = _pool([
        {"player": f"P{i}", "position": "RB" if i % 2 else "WR",
         "projected_points": 100 - i, "suggested_auction_price": 5}
        for i in range(30)
    ])
    pool.loc[pool["position"] == "WR", "position"] = "WR"
    # add required positions
    for pos, n in [("QB", 3), ("TE", 3)]:
        for j in range(n):
            pool = pd.concat([pool, pd.DataFrame([{
                "player": f"{pos}{j}", "position": pos,
                "projected_points": 80, "suggested_auction_price": 3,
            }])], ignore_index=True)

    k = roster_optimizer.evaluate_portfolio("Sam", ["A"], None, roster, pool, "t", "keep_A")
    r = roster_optimizer.evaluate_portfolio("Sam", [], None, roster, pool, "t", "release_A")
    assert k.cache_key != r.cache_key


def test_deterministic():
    roster = pd.DataFrame([
        {"team": "Sam", "player": "A", "position": "RB", "salary_2025": 10,
         "will_keep": False, "tag_used": False, "keeper_price_2026": 0, "projected_points": 150},
    ])
    rows = []
    for i in range(20):
        pos = ["QB", "RB", "WR", "TE"][i % 4]
        rows.append({
            "player": f"P{i}", "position": pos,
            "projected_points": 200 - i, "suggested_auction_price": max(1, 5 + i % 10),
        })
    pool = _pool(rows)
    a = roster_optimizer.evaluate_portfolio("Sam", [], None, roster, pool)
    roster_optimizer.clear_caches()
    b = roster_optimizer.evaluate_portfolio("Sam", [], None, roster, pool)
    assert a.objective_value == b.objective_value
    assert a.auction_players == b.auction_players


def test_active_roster_size_is_16():
    # UPDATED (official commissioner data repair): 16-player roster
    # (9 starters + 7 bench), not 15/6.
    assert config.AUCTION_PURCHASE_REQUIREMENT == 16
    assert config.STARTING_ROSTER_SIZE == 9
    assert config.BENCH_SIZE == 7
    assert config.IR_CAPACITY == 2
