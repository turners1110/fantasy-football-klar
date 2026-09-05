"""V3 Parts 9-10 -- the backend-authoritative nominee-panel verdict
endpoint (GET /api/verdict/{player}), verified through the real FastAPI
app (TestClient) using the exact required taxonomy: BID;
BID_BUT_RUN_EXACT_SOON; HOLD; ONE_MORE_DOLLAR; PASS; ILLEGAL;
CRITICAL_REVIEW_REQUIRED."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def client(tmp_path, monkeypatch):
    import importlib
    import live_web.server as server_module
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


VALID_VERDICTS = {"BID", "BID_BUT_RUN_EXACT_SOON", "HOLD", "ONE_MORE_DOLLAR", "PASS", "ILLEGAL", "CRITICAL_REVIEW_REQUIRED"}


def test_verdict_endpoint_returns_valid_taxonomy_value(client):
    rb = _first_available(client, "RB")
    r = client.get(f"/api/verdict/{rb}")
    assert r.status_code == 200
    data = r.json()
    assert data["verdict"] in VALID_VERDICTS


def test_no_bid_entered_returns_hold(client):
    rb = _first_available(client, "RB")
    r = client.get(f"/api/verdict/{rb}")
    data = r.json()
    assert data["verdict"] == "HOLD"
    assert data["current_bid_dollars"] is None


def test_bid_above_stop_returns_pass(client):
    rb = _first_available(client, "RB")
    stop = client.get(f"/api/check/{rb}").json()["recommended_stop"]
    r = client.get(f"/api/verdict/{rb}", params={"current_bid": stop + 20})
    assert r.json()["verdict"] == "PASS"


def test_bid_within_one_dollar_of_stop_returns_one_more_dollar(client):
    rb = _first_available(client, "RB")
    stop = client.get(f"/api/check/{rb}").json()["recommended_stop"]
    r = client.get(f"/api/verdict/{rb}", params={"current_bid": max(1, int(stop) - 1)})
    assert r.json()["verdict"] == "ONE_MORE_DOLLAR"


def test_bid_exceeding_legal_max_returns_illegal(client):
    status = client.get("/api/status").json()
    rb = _first_available(client, "RB")
    illegal_bid = int(status["legal_max_bid"]) + 10
    r = client.get(f"/api/verdict/{rb}", params={"current_bid": illegal_bid})
    assert r.json()["verdict"] == "ILLEGAL"


def test_small_safe_bid_returns_bid(client):
    rb = _first_available(client, "RB")
    r = client.get(f"/api/verdict/{rb}", params={"current_bid": 1})
    data = r.json()
    assert data["verdict"] in ("BID", "ONE_MORE_DOLLAR")  # depends on that player's stop, but never PASS/ILLEGAL at $1


def test_large_bid_without_exact_returns_bid_but_run_exact_soon(client):
    # Find a player whose stop is comfortably above $21 so a $21 bid isn't PASS/ONE_MORE_DOLLAR.
    board = client.get("/api/board").json()["players"]
    candidate = None
    for p in board:
        if p["position"] == "RB" and p["recommended_stop"] > 30:
            candidate = p["player"]
            break
    if candidate is None:
        pytest.skip("no RB with a stop > $30 in this pool snapshot")
    r = client.get(f"/api/verdict/{candidate}", params={"current_bid": 21})
    assert r.json()["verdict"] == "BID_BUT_RUN_EXACT_SOON"


def test_verdict_never_shows_dollar_sign_field_name_for_points():
    # Static/schema check: the points field is explicitly named
    # marginal_lineup_points (no _dollars suffix) and the dollar fields
    # are explicitly suffixed _dollars -- this is the unit-safety
    # contract Part 6/9 require.
    import live_auction_cli
    import inspect
    src = inspect.getsource(live_auction_cli.AuctionCLI.api_verdict)
    assert '"marginal_lineup_points"' in src
    assert '"team_specific_value_dollars"' in src
    assert '"recommended_stop_dollars"' in src


def test_verdict_endpoint_unknown_player_404(client):
    r = client.get("/api/verdict/Not_A_Real_Player")
    assert r.status_code == 404


def test_critical_review_verdict_reflects_check_endpoint(client):
    # Find any player whose /api/check already shows critical_review_required.
    board = client.get("/api/board").json()["players"]
    critical_player = next((p["player"] for p in board if p.get("critical_review_required")), None)
    if critical_player is None:
        pytest.skip("no critical-review player in this pool snapshot")
    r = client.get(f"/api/verdict/{critical_player}", params={"current_bid": 5})
    assert r.json()["verdict"] == "CRITICAL_REVIEW_REQUIRED"


def test_verdict_updates_live_after_a_sale_changes_state(client):
    rb = _first_available(client, "RB")
    r1 = client.get(f"/api/verdict/{rb}", params={"current_bid": 5})
    v1 = r1.json()["recommended_stop_dollars"]
    # Buy several RBs for Sam to trigger the RB-overload dynamic-value decline (V3 Part 7).
    board = client.get("/api/board").json()["players"]
    other_rbs = [p["player"] for p in board if p["position"] == "RB" and p["player"] != rb][:4]
    for other in other_rbs:
        client.post("/api/sale", json={"player": other, "team": "Sam", "price": 5, "confirm": True})
    r2 = client.get(f"/api/verdict/{rb}", params={"current_bid": 5})
    v2 = r2.json()["recommended_stop_dollars"]
    assert v2 <= v1  # stop must never rise after Sam gets MORE saturated at the position
