"""Stage-of-draft coach (auction_engine/coach.py + /api/coach)."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from auction_engine.coach import coach_message, draft_phase


def _msg(**over):
    base = dict(sales_so_far=0, open_slots=8, budget_remaining=225.0,
                position_counts={"QB": 1, "RB": 3, "WR": 2, "TE": 0},
                position_needs={"QB": 0, "RB": 0, "WR": 0, "TE": 1, "FLEX": 2},
                monitor_status="ON_PACE", projected_unused=7.0)
    base.update(over)
    return coach_message(**base)


def test_phase_thresholds():
    assert draft_phase(0) == "EARLY"
    assert draft_phase(28) == "EARLY"
    assert draft_phase(29) == "MIDDLE"
    assert draft_phase(73) == "MIDDLE"
    assert draft_phase(74) == "LATE"
    assert draft_phase(102) == "ENDGAME"


def test_opening_state_reports_counts_and_te_need():
    out = _msg(best_remaining={"TE": {"player": "Brock Bowers", "recommended_stop": 41.0}})
    assert out["phase"] == "EARLY"
    assert "0/113" in out["headline"] and "8 slots" in out["headline"] and "$225" in out["headline"]
    assert len(out["points"]) <= 2
    assert any("starting TE" in p and "Brock Bowers" in p and "$41" in p for p in out["points"])


def test_monitor_warning_takes_priority():
    out = _msg(sales_so_far=90, open_slots=2, budget_remaining=60.0,
               position_counts={"QB": 1, "RB": 4, "WR": 4, "TE": 2},
               position_needs={"QB": 0, "RB": 0, "WR": 0, "TE": 0, "FLEX": 0},
               monitor_status="FINAL_SLOTS_CASH_STRANDED", projected_unused=48.0)
    assert out["phase"] == "LATE"
    assert out["points"][0].startswith("CASH STRANDING")
    assert "$48" in out["points"][0]


def test_te_cap_and_full_roster():
    out = _msg(position_counts={"QB": 1, "RB": 2, "WR": 4, "TE": 2},
               position_needs={"QB": 0, "RB": 0, "WR": 0, "TE": 0, "FLEX": 0})
    assert any("do not buy a 3rd" in p for p in out["points"])
    # Opening state: TE hole and WR focus outrank the QB "don't".
    opening = _msg()
    assert "starting TE" in opening["points"][0] and "WR is where the money goes" in opening["points"][1]
    done = _msg(sales_so_far=113, open_slots=0, budget_remaining=3.0)
    assert done["points"] == [] and "Roster full" in done["headline"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    import live_web.server as server_module
    monkeypatch.setattr("live_auction_cli.DEFAULT_LOG_PATH", tmp_path / "session.jsonl", raising=False)
    importlib.reload(server_module)
    server_module.cli.log_path = tmp_path / "session.jsonl"
    return TestClient(server_module.app)


def test_coach_endpoint_is_read_only_and_tracks_sales(client):
    before = client.get("/api/status").json()["sequence_number"]
    r = client.get("/api/coach")
    assert r.status_code == 200
    c = r.json()
    assert c["phase"] == "EARLY" and c["sales_so_far"] == 0 and c["open_slots"] == 8
    assert c["headline"] and 1 <= len(c["points"]) <= 2
    assert client.get("/api/status").json()["sequence_number"] == before

    board = client.get("/api/board").json()["players"]
    wr = next(p for p in board if p["position"] == "WR")
    assert client.post("/api/sale", json={"player": wr["player"], "team": "Brad", "price": 20}).status_code == 200
    assert client.get("/api/coach").json()["sales_so_far"] == 1
