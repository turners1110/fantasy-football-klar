"""V3 Gate G -- official-team dropdown data, operational status area,
viewport meta tag, and LAN mutation-token protection. The mobile-layout
CSS itself and a real browser rehearsal are NOT unit-testable and are
addressed separately (see browser_rehearsal.md)."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def client(tmp_path, monkeypatch):
    import live_web.server as server_module
    monkeypatch.setattr("live_auction_cli.DEFAULT_LOG_PATH", tmp_path / "session.jsonl", raising=False)
    monkeypatch.delenv("SUNDAY_AUTH_TOKEN", raising=False)
    importlib.reload(server_module)
    server_module.cli.log_path = tmp_path / "session.jsonl"
    return TestClient(server_module.app)


def test_teams_endpoint_returns_all_12_official_names(client):
    r = client.get("/api/teams")
    assert r.status_code == 200
    teams = r.json()["teams"]
    assert len(teams) == 12
    assert "Sam" in teams


def test_operational_status_endpoint_shape(client):
    r = client.get("/api/operational-status")
    assert r.status_code == 200
    data = r.json()
    for field in ("mode", "sequence_number", "active_log_path", "last_persisted_event",
                  "exact_freshness", "market_prior_freshness"):
        assert field in data
    assert data["mode"] == "production"
    assert data["last_persisted_event"] is None  # nothing sold yet


def test_operational_status_reflects_a_real_sale(client):
    board = client.get("/api/board").json()["players"]
    rb = next(p["player"] for p in board if p["position"] == "RB")
    client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 5, "confirm": True})
    data = client.get("/api/operational-status").json()
    assert data["sequence_number"] == 1
    assert data["last_persisted_event"]["event_type"] == "PLAYER_SOLD"
    assert data["market_prior_freshness"] != "STATIC_PRE_DRAFT_MARKET_PRIOR (no live observations yet)"


def test_index_html_has_viewport_meta_tag():
    html = (Path(__file__).parent.parent / "live_web" / "static" / "index.html").read_text()
    assert 'name="viewport"' in html
    assert "width=device-width" in html


def test_index_html_replaces_free_text_team_inputs_with_selects():
    html = (Path(__file__).parent.parent / "live_web" / "static" / "index.html").read_text()
    assert '<select id="modal-team">' in html
    assert '<select id="correct-team">' in html
    assert '<input id="modal-team">' not in html
    assert '<input id="correct-team"' not in html


# ---------------------------------------------------------------------------
# LAN mutation-token protection
# ---------------------------------------------------------------------------

def test_no_token_required_when_sunday_auth_token_unset(client):
    board = client.get("/api/board").json()["players"]
    rb = next(p["player"] for p in board if p["position"] == "RB")
    r = client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 5, "confirm": True})
    assert r.status_code == 200


def test_mutation_rejected_without_token_when_lan_mode_enabled(client, monkeypatch):
    monkeypatch.setenv("SUNDAY_AUTH_TOKEN", "secret123")
    board = client.get("/api/board").json()["players"]
    rb = next(p["player"] for p in board if p["position"] == "RB")
    r = client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 5, "confirm": True})
    assert r.status_code == 401


def test_mutation_succeeds_with_correct_token_when_lan_mode_enabled(client, monkeypatch):
    monkeypatch.setenv("SUNDAY_AUTH_TOKEN", "secret123")
    board = client.get("/api/board").json()["players"]
    rb = next(p["player"] for p in board if p["position"] == "RB")
    r = client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 5, "confirm": True},
                     headers={"X-Auth-Token": "secret123"})
    assert r.status_code == 200


def test_read_only_endpoint_never_requires_token_even_in_lan_mode(client, monkeypatch):
    monkeypatch.setenv("SUNDAY_AUTH_TOKEN", "secret123")
    r = client.get("/api/board")
    assert r.status_code == 200
    r2 = client.get("/api/status")
    assert r2.status_code == 200


def test_wrong_token_rejected(client, monkeypatch):
    monkeypatch.setenv("SUNDAY_AUTH_TOKEN", "secret123")
    board = client.get("/api/board").json()["players"]
    rb = next(p["player"] for p in board if p["position"] == "RB")
    r = client.post("/api/sale", json={"player": rb, "team": "Sam", "price": 5, "confirm": True},
                     headers={"X-Auth-Token": "wrong-token"})
    assert r.status_code == 401
