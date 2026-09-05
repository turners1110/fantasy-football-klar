"""V2.1 Part 6 -- Practice Mode isolation and RB-overload proof tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

from live_web.server import app, cli as production_cli
from auction_engine.practice_scenarios import build_practice_cli, SCENARIOS


client = TestClient(app)


def teardown_function(_fn):
    # Always leave the module-level server back in production mode so
    # tests don't bleed practice state into each other.
    client.post("/api/mode/production")


def test_scenario_builder_produces_isolated_cli_objects():
    cli_a, _ = build_practice_cli("normal")
    cli_b, _ = build_practice_cli("normal")
    assert cli_a is not cli_b
    assert cli_a.store is not cli_b.store
    assert cli_a is not production_cli


def test_rb_overload_scenario_proves_rb_value_decline_and_wr_te_rise():
    _, proof = build_practice_cli("sam_rb_overload")
    assert proof["rb_value_declined"] is True
    assert proof["wr_te_relative_priority_rose"] is True
    assert proof["rb_marginal_value_after"] < proof["rb_marginal_value_before"]
    assert len(proof["added_rbs"]) == 3


def test_all_scenarios_buildable():
    for scenario in SCENARIOS:
        cli_obj, _ = build_practice_cli(scenario)
        assert cli_obj.store.state.sequence_number == 0


def test_mode_endpoint_defaults_to_production():
    r = client.get("/api/mode")
    assert r.status_code == 200
    assert r.json()["mode"] == "production"


def test_switching_to_practice_does_not_mutate_production_state():
    prod_status_before = client.get("/api/status").json()

    r = client.post("/api/mode/practice", json={"scenario": "sam_rb_overload"})
    assert r.status_code == 200
    assert r.json()["mode"] == "practice"
    assert r.json()["proof"]["rb_value_declined"] is True

    practice_status = client.get("/api/status").json()
    # Practice Sam should show 3 extra RBs / reduced budget vs production Sam.
    assert practice_status["budget_remaining"] != prod_status_before["budget_remaining"]

    # Switch back -- production must be byte-for-byte the same as before.
    client.post("/api/mode/production")
    prod_status_after = client.get("/api/status").json()
    assert prod_status_after == prod_status_before


def test_practice_sale_never_touches_production_event_log():
    prod_log_before = client.get("/api/log").json()["events"]

    client.post("/api/mode/practice", json={"scenario": "normal"})
    board = client.get("/api/board").json()["players"]
    some_player = next(p["player"] for p in board if p.get("player"))
    client.post("/api/sale", json={"player": some_player, "team": "Sam", "price": 1, "confirm": True})

    client.post("/api/mode/production")
    prod_log_after = client.get("/api/log").json()["events"]
    assert prod_log_after == prod_log_before


def test_practice_nomination_isolated_from_production_nomination():
    client.post("/api/mode/production")
    client.post("/api/nominate", json={"player": None})

    client.post("/api/mode/practice", json={"scenario": "normal"})
    board = client.get("/api/board").json()["players"]
    some_player = next(p["player"] for p in board if p.get("player"))
    client.post("/api/nominate", json={"player": some_player})
    practice_board = client.get("/api/board").json()
    assert practice_board["nominated"] == some_player

    client.post("/api/mode/production")
    prod_board = client.get("/api/board").json()
    assert prod_board["nominated"] is None


def test_practice_exact_cache_isolated_from_production():
    client.post("/api/mode/production")
    board = client.get("/api/board").json()["players"]
    some_player = next(p["player"] for p in board if p.get("player"))

    client.post("/api/mode/practice", json={"scenario": "normal"})
    status = client.get("/api/exact-status/" + some_player).json()
    assert status["has_current_exact"] is False
    client.post("/api/mode/production")


def test_invalid_scenario_rejected():
    r = client.post("/api/mode/practice", json={"scenario": "not_a_real_scenario"})
    assert r.status_code == 400
    client.post("/api/mode/production")
