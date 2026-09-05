"""Tests for the Live Auction Website's FastAPI endpoints (live_web/server.py).

Uses FastAPI's TestClient (in-process, no real server needed) but exercises
the REAL app object and the REAL AuctionCLI singleton it wraps -- these are
the exact same code paths a live curl/browser request would hit. Each test
reloads the live_web.server module to get a fresh AuctionCLI instance (a
fresh, isolated auction) so tests don't interfere with each other.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import live_web.server as server_module
    # Force a fresh AuctionCLI per test by pointing its session log at a
    # scratch path and reloading the module (re-runs `cli = AuctionCLI(...)`).
    monkeypatch.setattr("live_auction_cli.DEFAULT_LOG_PATH", tmp_path / "session.jsonl", raising=False)
    importlib.reload(server_module)
    server_module.cli.log_path = tmp_path / "session.jsonl"
    return TestClient(server_module.app)


def _first_available(client, position):
    board = client.get("/api/board").json()["players"]
    for p in board:
        if p["position"] == position:
            return p["player"]
    raise AssertionError(f"no available {position}")


def test_status_endpoint(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["budget_remaining"] == 223.0
    assert data["open_slots"] == 9


def test_board_endpoint_returns_players(client):
    r = client.get("/api/board")
    assert r.status_code == 200
    data = r.json()
    assert len(data["players"]) > 100
    assert "recommendation" in data["players"][0]


def test_sale_via_api_updates_state_identically_to_cli_event(client):
    """A sale via the API must produce the same event-sourced state change
    as calling AuctionCLI.cmd_sale directly (same underlying function)."""
    import live_web.server as server_module
    rb = _first_available(client, "RB")
    budget_before = server_module.cli.api_status()["budget_remaining"]

    r = client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 20, "confirm": True})
    assert r.status_code == 200
    data = r.json()
    assert data["needs_confirmation"] is False

    status_after = server_module.cli.api_status()
    assert status_after["budget_remaining"] == budget_before - 20
    assert any(p["display_name"] == rb for p in status_after["roster"])


def test_keeper_sale_via_api_rejected_with_clear_error(client):
    import live_web.server as server_module
    keeper_name = server_module.cli.api_status()["roster"][0]["display_name"]
    r = client.post("/api/sale", json={"player": keeper_name, "team": "Brad", "price": 10, "confirm": True})
    assert r.status_code == 400
    assert "keeper" in r.json()["detail"].lower()


def test_college_rights_sale_via_api_rejected(client):
    r = client.post("/api/sale", json={"player": "Fernando Mendoza", "team": "Sam", "price": 1, "confirm": True})
    assert r.status_code == 400
    assert "college-rights" in r.json()["detail"].lower()


def test_large_sale_requires_confirmation_via_api(client):
    rb = _first_available(client, "RB")
    r = client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 20, "confirm": False})
    assert r.status_code == 200
    data = r.json()
    assert data["needs_confirmation"] is True
    # not yet recorded
    board_names = [p["player"] for p in client.get("/api/board").json()["players"]]
    assert rb in board_names


def test_undo_via_api_reverses_sale(client):
    import live_web.server as server_module
    rb = _first_available(client, "RB")
    budget_before = server_module.cli.api_status()["budget_remaining"]
    client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 20, "confirm": True})
    r = client.post("/api/undo")
    assert r.status_code == 200
    assert r.json()["status"]["budget_remaining"] == budget_before


def test_board_reflects_sold_players_as_unavailable(client):
    rb = _first_available(client, "RB")
    client.post("/api/sale", json={"player": rb, "team": "Brad", "price": 20, "confirm": True})
    board_names = [p["player"] for p in client.get("/api/board").json()["players"]]
    assert rb not in board_names


def test_correct_via_api(client):
    import live_web.server as server_module
    rb = _first_available(client, "RB")
    client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 20, "confirm": True})
    r = client.post("/api/correct", json={"player": rb, "team": "Brad", "price": 15})
    assert r.status_code == 200
    status = server_module.cli.api_status()
    assert status["budget_remaining"] == 223.0  # Sam's $20 fully refunded
    assert not any(p["display_name"] == rb for p in status["roster"])


def test_check_endpoint(client):
    rb = _first_available(client, "RB")
    r = client.get(f"/api/check/{rb}")
    assert r.status_code == 200
    data = r.json()
    assert "recommended_stop" in data


def test_check_unknown_player_404(client):
    r = client.get("/api/check/Not_A_Real_Player_Xyz")
    assert r.status_code == 404


def test_targets_endpoint(client):
    r = client.get("/api/targets")
    assert r.status_code == 200
    data = r.json()
    assert len(data["targets"]) > 0
    assert "recommendation_class" in data["targets"][0]


def test_market_endpoint(client):
    r = client.get("/api/market")
    assert r.status_code == 200
    assert r.json()["active_prior"] == "STATIC_PRE_DRAFT_MARKET_PRIOR"


def test_nominate_endpoint(client):
    rb = _first_available(client, "RB")
    r = client.post("/api/nominate", json={"player": rb})
    assert r.status_code == 200
    assert r.json()["nominated"] == rb
    board = client.get("/api/board").json()
    assert board["nominated"] == rb


def test_save_and_load_endpoints(client):
    rb = _first_available(client, "RB")
    client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 20, "confirm": True})
    r_save = client.post("/api/save", json={"name": "webtest_snapshot"})
    assert r_save.status_code == 200
    client.post("/api/undo")
    r_load = client.post("/api/load", json={"name": "webtest_snapshot"})
    assert r_load.status_code == 200
    assert any(p["display_name"] == rb for p in r_load.json()["status"]["roster"])


def test_log_endpoint(client):
    rb = _first_available(client, "RB")
    client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 20, "confirm": True})
    r = client.get("/api/log")
    assert r.status_code == 200
    assert any(e["player"] == rb for e in r.json()["events"])


def test_emergency_endpoint(client):
    r = client.get("/api/emergency")
    assert r.status_code == 200
    assert len(r.text) > 100


def test_index_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "SUNDAY LIVE AUCTION TOOL" in r.text
