"""V3 Gate D (Part 4, concurrency half) -- server-side locking,
optimistic sequence validation, and idempotency keys for /api/sale,
verified through the real FastAPI app with genuine concurrent threads
(not just sequential calls dressed up as a concurrency test)."""
from __future__ import annotations

import sys
import threading
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


def test_two_near_simultaneous_sales_of_the_same_player_only_one_succeeds(client):
    """The genuine race the spec describes: laptop and phone submit
    competing mutations for the SAME player at nearly the same instant.
    Real threads, real HTTP calls through the real app -- not a
    sequential stand-in."""
    rb = _first_available(client, "RB")
    results = {}

    def sell(team, key):
        r = client.post("/api/sale", json={"player": rb, "team": team, "price": 5, "confirm": True})
        results[key] = r

    t1 = threading.Thread(target=sell, args=("Sam", "a"))
    t2 = threading.Thread(target=sell, args=("Brandon", "b"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    statuses = sorted(r.status_code for r in results.values())
    assert statuses == [200, 400], f"expected exactly one success and one clean rejection, got {statuses}"
    # Exactly one team ended up with the player -- never both, never neither.
    status = client.get("/api/status").json()
    sam_has_it = any(p["display_name"] == rb for p in status["roster"])
    league = client.get("/api/league").json()["teams"]
    brandon = next(t for t in league if t["team"] == "Brandon")
    brandon_has_it = brandon["latest_purchase"] == rb
    assert sam_has_it != brandon_has_it  # exactly one, via real XOR


def test_stale_expected_sequence_is_rejected(client):
    rb1 = _first_available(client, "RB")
    status0 = client.get("/api/status").json()
    seq0 = status0["sequence_number"]

    # Advance state past seq0 with an unrelated sale.
    client.post("/api/sale", json={"player": rb1, "team": "Sam", "price": 3, "confirm": True})

    rb2 = _first_available(client, "RB")
    r = client.post("/api/sale", json={"player": rb2, "team": "Sam", "price": 3, "confirm": True, "expected_sequence": seq0})
    assert r.status_code == 409
    assert "STALE_STATE" in r.json()["detail"]
    # The stale-rejected sale must NOT have gone through.
    status = client.get("/api/status").json()
    assert not any(p["display_name"] == rb2 for p in status["roster"])


def test_matching_expected_sequence_succeeds(client):
    status0 = client.get("/api/status").json()
    rb = _first_available(client, "RB")
    r = client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 3, "confirm": True,
                                        "expected_sequence": status0["sequence_number"]})
    assert r.status_code == 200


def test_idempotency_key_prevents_duplicate_sale_on_retry(client):
    rb = _first_available(client, "RB")
    key = "phone-retry-abc123"
    r1 = client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 5, "confirm": True, "idempotency_key": key})
    assert r1.status_code == 200

    # Simulate a client retry with the SAME idempotency key (e.g. the
    # phone never saw r1's response and resent the identical request).
    r2 = client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 5, "confirm": True, "idempotency_key": key})
    assert r2.status_code == 200
    assert r2.json() == r1.json()

    # Exactly one purchase must exist -- the retry must NOT have sold it twice.
    status = client.get("/api/status").json()
    matches = [p for p in status["roster"] if p["display_name"] == rb]
    assert len(matches) == 1


def test_different_idempotency_keys_are_independent(client):
    rb1 = _first_available(client, "RB")
    r1 = client.post("/api/sale", json={"player": rb1, "team": "Sam", "price": 5, "confirm": True, "idempotency_key": "key-1"})
    rb2 = _first_available(client, "RB")
    r2 = client.post("/api/sale", json={"player": rb2, "team": "Sam", "price": 5, "confirm": True, "idempotency_key": "key-2"})
    assert r1.status_code == r2.status_code == 200
    assert r1.json() != r2.json()
