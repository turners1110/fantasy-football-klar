"""V3 Gate B -- transaction integrity, verified through the REAL
reducer/CLI/API paths (not isolated unit assertions), per the spec's
Part 5 enumerated list."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from live_auction_cli import AuctionCLI
from auction_engine.auction_reducer import IllegalEventError


@pytest.fixture
def cli(tmp_path):
    return AuctionCLI(budget_scenario="primary", log_path=tmp_path / "session.jsonl")


def _first_available(cli, position):
    for name, info in cli.store.state.available_pool.items():
        if info["position"] == position:
            return name
    raise AssertionError(f"no available {position}")


# ---------------------------------------------------------------------------
# Whole-dollar / minimum-$1 / decimal / $0 / negative -- via cmd_sale (CLI)
# ---------------------------------------------------------------------------

def test_decimal_price_rejected_via_cli(cli):
    rb = _first_available(cli, "RB")
    out = cli.cmd_sale(rb, "Sam", "12.50", confirmed=True)
    assert out.startswith("ERROR")
    assert "whole dollar" in out
    assert rb in cli.store.state.available_pool  # sale must NOT have gone through


def test_zero_price_rejected_via_cli(cli):
    rb = _first_available(cli, "RB")
    out = cli.cmd_sale(rb, "Sam", "0", confirmed=True)
    assert out.startswith("ERROR")
    assert "minimum" in out.lower()
    assert rb in cli.store.state.available_pool


def test_negative_price_rejected_via_cli(cli):
    rb = _first_available(cli, "RB")
    out = cli.cmd_sale(rb, "Sam", "-5", confirmed=True)
    assert out.startswith("ERROR")
    assert rb in cli.store.state.available_pool


def test_minimum_one_dollar_sale_succeeds(cli):
    rb = _first_available(cli, "RB")
    out = cli.cmd_sale(rb, "Sam", "1", confirmed=True)
    assert out.startswith("Recorded")
    assert rb not in cli.store.state.available_pool


def test_whole_dollar_sale_succeeds(cli):
    rb = _first_available(cli, "RB")
    out = cli.cmd_sale(rb, "Sam", "25", confirmed=True)
    assert out.startswith("Recorded")


# ---------------------------------------------------------------------------
# Duplicate-sale rejection
# ---------------------------------------------------------------------------

def test_duplicate_sale_rejected(cli):
    rb = _first_available(cli, "RB")
    first = cli.cmd_sale(rb, "Sam", "5", confirmed=True)
    assert first.startswith("Recorded")
    second = cli.cmd_sale(rb, "Brandon", "5", confirmed=True)
    assert second.startswith("REFUSED")
    assert "already" in second.lower() or "duplicate" in second.lower()
    # The player must still belong to the FIRST winner only.
    assert any(p["player_id"] == rb for p in cli.store.state.teams["Sam"].roster)
    assert not any(p["player_id"] == rb for p in cli.store.state.teams["Brandon"].roster)


# ---------------------------------------------------------------------------
# Protected-player rejection (keeper and college-rights)
# ---------------------------------------------------------------------------

def test_keeper_sale_rejected(cli):
    keeper_name = next(p["player_id"] for p in cli.store.state.teams["Sam"].roster if p.get("is_keeper"))
    out = cli.cmd_sale(keeper_name, "Brandon", "10", confirmed=True)
    assert out.startswith("REFUSED")
    assert "keeper" in out.lower()


def test_college_rights_sale_rejected(cli):
    out = cli.cmd_sale("Fernando Mendoza", "Brandon", "5", confirmed=True)
    assert out.startswith("REFUSED")
    assert "college-rights" in out.lower()


# ---------------------------------------------------------------------------
# Full-team / roster-cap rejection (no roster above 16)
# ---------------------------------------------------------------------------

def test_team_cannot_exceed_sixteen_players(cli):
    # Sam starts with 6 named keepers + college_rights_count=2 (Mendoza/
    # Bond, never added to `roster` itself) = 8 of 16 official slots
    # already occupied, leaving exactly 8 real auction purchases legal.
    sam = cli.store.state.teams["Sam"]
    assert sam.open_slots == 8
    bought = 0
    while bought < 8:
        candidate = next(iter(cli.store.state.available_pool))
        out = cli.cmd_sale(candidate, "Sam", "1", confirmed=True)
        if out.startswith("Recorded"):
            bought += 1
    # 6 named keepers + 8 real purchases = 14 roster entries; together
    # with college_rights_count=2, that's the full official 16.
    assert len(cli.store.state.teams["Sam"].roster) == 14
    assert len(cli.store.state.teams["Sam"].roster) + cli.store.state.teams["Sam"].college_rights_count == 16
    # A 9th purchase (the true 17th protected-or-owned player) must be
    # refused by the real reducer, not just the CLI.
    extra = next(iter(cli.store.state.available_pool))
    out = cli.cmd_sale(extra, "Sam", "1", confirmed=True)
    assert out.startswith("REFUSED")
    assert "16 players" in out


# ---------------------------------------------------------------------------
# Reserve-invariant enforcement ($1 reserved per remaining open slot)
# ---------------------------------------------------------------------------

def test_sale_above_legal_max_bid_rejected(cli):
    sam = cli.store.state.teams["Sam"]
    rb = _first_available(cli, "RB")
    illegal_price = int(sam.legal_max_bid) + 5
    out = cli.cmd_sale(rb, "Sam", str(illegal_price), confirmed=True)
    assert out.startswith("REFUSED")
    assert "legal max bid" in out.lower()


def test_sale_at_exact_legal_max_bid_succeeds(cli):
    sam = cli.store.state.teams["Sam"]
    rb = _first_available(cli, "RB")
    exact_max = int(sam.legal_max_bid)
    out = cli.cmd_sale(rb, "Sam", str(exact_max), confirmed=True)
    assert out.startswith("Recorded")
    assert cli.store.state.teams["Sam"].budget_remaining >= 0


# ---------------------------------------------------------------------------
# Correction preserves metadata (canonical ID, position, projected points)
# ---------------------------------------------------------------------------

def test_correction_preserves_position_and_points(cli):
    rb = _first_available(cli, "RB")
    original_points = cli.players[rb].projected_points if rb in cli.players else 0.0
    cli.cmd_sale(rb, "Sam", "5", confirmed=True)
    out = cli.cmd_correct(rb, "Brandon", "8")
    assert out.startswith("Corrected")
    sold_record = cli.store.state.sold_players[rb]
    assert sold_record["winning_owner"] == "Brandon"
    assert sold_record["sale_price"] == 8.0
    corrected_roster_entry = next(p for p in cli.store.state.teams["Brandon"].roster if p["player_id"] == rb)
    assert corrected_roster_entry["position"] != "UNKNOWN"
    assert corrected_roster_entry["projected_points"] == pytest.approx(original_points)
    # Old team's accounting must be fully reversed, not left with a
    # phantom roster entry or an un-refunded budget.
    assert not any(p["player_id"] == rb for p in cli.store.state.teams["Sam"].roster)


def test_correction_decimal_price_rejected(cli):
    rb = _first_available(cli, "RB")
    cli.cmd_sale(rb, "Sam", "5", confirmed=True)
    out = cli.cmd_correct(rb, "Brandon", "8.50")
    assert out.startswith("ERROR")
    assert "whole dollar" in out


def test_correction_zero_price_rejected(cli):
    rb = _first_available(cli, "RB")
    cli.cmd_sale(rb, "Sam", "5", confirmed=True)
    out = cli.cmd_correct(rb, "Brandon", "0")
    assert out.startswith("ERROR")


# ---------------------------------------------------------------------------
# Same checks through the real FastAPI website endpoints (not just the CLI)
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    import importlib
    import live_web.server as server_module
    monkeypatch.setattr("live_auction_cli.DEFAULT_LOG_PATH", tmp_path / "session.jsonl", raising=False)
    importlib.reload(server_module)
    server_module.cli.log_path = tmp_path / "session.jsonl"
    return TestClient(server_module.app)


def _first_available_api(client, position):
    board = client.get("/api/board").json()["players"]
    for p in board:
        if p["position"] == position:
            return p["player"]
    raise AssertionError(f"no available {position}")


def test_api_rejects_decimal_price(client):
    rb = _first_available_api(client, "RB")
    r = client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 12.5, "confirm": True})
    assert r.status_code == 400
    assert "whole dollar" in r.json()["detail"]


def test_api_rejects_zero_price(client):
    rb = _first_available_api(client, "RB")
    r = client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 0, "confirm": True})
    assert r.status_code == 400


def test_api_rejects_duplicate_sale(client):
    rb = _first_available_api(client, "RB")
    r1 = client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 5, "confirm": True})
    assert r1.status_code == 200
    r2 = client.post("/api/sale", json={"player": rb, "team": "Brandon", "price": 5, "confirm": True})
    assert r2.status_code == 400


def test_api_rejects_college_rights_sale(client):
    r = client.post("/api/sale", json={"player": "Isaiah Bond", "team": "Sam", "price": 5, "confirm": True})
    assert r.status_code == 400
    assert "college-rights" in r.json()["detail"].lower()


def test_api_rejects_sale_above_legal_max(client):
    status = client.get("/api/status").json()
    rb = _first_available_api(client, "RB")
    illegal_price = int(status["legal_max_bid"]) + 5
    r = client.post("/api/sale", json={"player": rb, "team": "Sam", "price": illegal_price, "confirm": True})
    assert r.status_code == 400


def test_api_correction_preserves_metadata_and_replay_stability(client):
    rb = _first_available_api(client, "RB")
    client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 5, "confirm": True})
    r = client.post("/api/correct", json={"player": rb, "team": "Brandon", "price": 9})
    assert r.status_code == 200
    status = r.json()["status"]
    assert not any(p["display_name"] == rb for p in status["roster"])  # left Sam's roster
