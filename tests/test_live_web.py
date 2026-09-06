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
    # UPDATED (official commissioner data repair): Sam's official
    # remaining budget is $225 with 8 open slots (16-player roster), not
    # the old $223/9 assumption.
    assert data["budget_remaining"] == 225.0
    assert data["open_slots"] == 8


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
    assert status["budget_remaining"] == 225.0  # Sam's $20 fully refunded (official $225 budget)
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


# ---------------------------------------------------------------------------
# V2 Part 3/4: search and League Room endpoints
# ---------------------------------------------------------------------------

def test_league_endpoint_returns_all_12_teams(client):
    r = client.get("/api/league")
    assert r.status_code == 200
    assert len(r.json()["teams"]) == 12


def test_league_endpoint_includes_sam(client):
    data = client.get("/api/league").json()["teams"]
    sam = [t for t in data if t["team"] == "Sam"][0]
    assert sam["is_sam"] is True
    assert sam["keeper_count"] == 6


# ---------------------------------------------------------------------------
# V2.1 Part 8: League Room field re-verification
# ---------------------------------------------------------------------------

def test_league_endpoint_includes_v21_required_fields(client):
    data = client.get("/api/league").json()["teams"]
    row = data[0]
    for field in ("flex_capacity", "latest_purchase", "current_nominee_demand"):
        assert field in row


def test_league_endpoint_current_nominee_demand_uses_spec_label_set(client):
    board = client.get("/api/board").json()["players"]
    player = next(p["player"] for p in board if p.get("player"))
    client.post("/api/nominate", json={"player": player})
    data = client.get("/api/league").json()["teams"]
    valid_labels = {"HIGH_REQUIRED_NEED", "MEDIUM_FLEX_OR_DEPTH", "LOW_POSITION_FILLED", "NO_LEGAL_BID", "UNKNOWN"}
    for row in data:
        assert row["current_nominee_demand"] in valid_labels
    client.post("/api/nominate", json={"player": None})


def test_league_endpoint_demand_is_none_when_nothing_nominated(client):
    client.post("/api/nominate", json={"player": None})
    data = client.get("/api/league").json()["teams"]
    assert all(row["current_nominee_demand"] is None for row in data)


def test_team_detail_endpoint(client):
    r = client.get("/api/league/Sam")
    assert r.status_code == 200
    data = r.json()
    assert len(data["roster"]) == 6  # 6 keepers, no purchases yet


# ---------------------------------------------------------------------------
# V2.2 Request 3: full player-by-player rosters for all 12 teams
# ---------------------------------------------------------------------------

def test_team_detail_includes_starter_bench_roles(client):
    data = client.get("/api/league/Sam").json()
    assert data["roster_count"] == 6
    for p in data["roster"]:
        assert "lineup_role" in p and "slot_type" in p
        assert p["slot_type"] in ("STARTER", "BENCH")
    starters = [p for p in data["roster"] if p["slot_type"] == "STARTER"]
    assert len(starters) > 0


def test_college_rights_holdings_never_appear_inside_roster_list(client):
    data = client.get("/api/league/Sam").json()
    roster_names = {p["display_name"] for p in data["roster"]}
    assert "Fernando Mendoza" not in roster_names
    assert "Isaiah Bond" not in roster_names
    # Sam also holds Bryce Young's college-draft rights (owner "Sam" in
    # data/college_draft_completed_picks.csv, closed via
    # data/protected_player_overrides.csv alongside the Mendoza/Bond
    # hardcoded pair) -- college_rights_holdings is sorted, so this is
    # a 3-name list, not just the original 2.
    assert "Bryce Young" not in roster_names
    assert data["college_rights_holdings"] == ["Bryce Young", "Fernando Mendoza", "Isaiah Bond"]


def test_non_sam_team_has_no_college_rights_holdings(client):
    data = client.get("/api/league/Brandon").json()
    assert data["college_rights_holdings"] == []


def test_all_rosters_endpoint_returns_all_12_teams(client):
    r = client.get("/api/rosters")
    assert r.status_code == 200
    data = r.json()
    assert len(data["teams"]) == 12
    for t in data["teams"]:
        assert t["roster_count"] == len(t["roster"]) == 6  # pre-auction: 6 keepers each
        assert all("Fernando Mendoza" != p["display_name"] and "Isaiah Bond" != p["display_name"] for p in t["roster"])


def test_all_rosters_reflects_sale_immediately(client):
    board = client.get("/api/board").json()["players"]
    player = next(p["player"] for p in board if p.get("player"))
    client.post("/api/sale", json={"player": player, "team": "Brandon", "price": 3, "confirm": True})
    data = client.get("/api/rosters").json()
    brandon = next(t for t in data["teams"] if t["team"] == "Brandon")
    assert any(p["display_name"] == player for p in brandon["roster"])
    assert brandon["roster_count"] == 7
    client.post("/api/undo")


def test_team_detail_unknown_team_404(client):
    r = client.get("/api/league/NotARealTeam")
    assert r.status_code == 404


def test_search_endpoint_finds_partial_match(client):
    board = client.get("/api/board").json()["players"]
    target = board[0]["player"]
    partial = target.split()[0]
    r = client.get(f"/api/search?q={partial}")
    assert r.status_code == 200
    names = [x["player"] for x in r.json()["results"]]
    assert target in names


def test_search_excludes_protected_by_default(client):
    r = client.get("/api/search?q=Garrett Wilson")
    names = [x["player"] for x in r.json()["results"]]
    assert "Garrett Wilson" not in names  # keeper, excluded by default


def test_search_includes_protected_when_requested(client):
    r = client.get("/api/search?q=Garrett Wilson&include_protected=true")
    results = [x for x in r.json()["results"] if x["player"] == "Garrett Wilson"]
    assert len(results) == 1
    assert results[0]["status"] == "KEEPER"
    assert results[0]["owner"] == "Sam"


def test_nominee_demand_endpoint(client):
    rb = _first_available(client, "RB")
    r = client.get(f"/api/demand/{rb}")
    assert r.status_code == 200
    data = r.json()
    assert len(data["demand_by_team"]) == 12
    assert data["credible_bidder_count"] >= 0


def test_board_critical_review_field_present(client):
    board = client.get("/api/board").json()["players"]
    assert all("critical_review_required" in p for p in board[:5])


def test_no_player_gets_stop_equal_to_raw_points_via_website(client):
    """End-to-end regression for the Josh Jacobs fix, through the real
    website API (not just the CLI)."""
    board = client.get("/api/board").json()["players"]
    for p in board:
        if p["position"] == "RB" and p["expected_role"] == "required starter":
            assert abs(p["recommended_stop"] - p.get("marginal_value", 0)) > 1 or p["marginal_value"] < 5


# ---------------------------------------------------------------------------
# V2 Part 5: Monte Carlo distribution endpoints
# ---------------------------------------------------------------------------

def test_distributions_endpoint_returns_data(client):
    r = client.get("/api/distributions")
    assert r.status_code == 200
    assert len(r.json()["players"]) > 0


def test_distribution_unknown_player_404(client):
    r = client.get("/api/distributions/Not_A_Real_Player_Xyz")
    assert r.status_code == 404


def test_distribution_insufficient_sales_labeled(client):
    data = client.get("/api/distributions").json()["players"]
    insufficient = [p for p in data if p["status"] == "INSUFFICIENT_SIMULATED_SALES"]
    assert len(insufficient) > 0
    assert all(p["p50"] in ("", None) for p in insufficient)


# ---------------------------------------------------------------------------
# V2.1 Part 4: exact/ladder website endpoints
# ---------------------------------------------------------------------------

def test_exact_endpoint_matches_cli(client):
    import live_web.server as server_module
    rb = _first_available(client, "RB")
    r = client.post("/api/exact", json={"player": rb})
    assert r.status_code == 200
    data = r.json()
    cli_result = server_module.cli.api_exact(rb)
    # both should reflect the same underlying solve for a fresh call
    assert data["exact_ceiling"] == cli_result["exact_ceiling"] or abs(data["exact_ceiling"] - cli_result["exact_ceiling"]) <= 1


def test_exact_endpoint_candidate_in_purchase_absent_from_pass(client):
    rb = _first_available(client, "RB")
    r = client.post("/api/exact", json={"player": rb})
    data = r.json()
    assert rb in data["purchase_roster"]
    assert rb not in data["pass_roster"]


def test_exact_endpoint_requires_optimal(client):
    r = client.post("/api/exact", json={"player": "Not_A_Real_Player_Xyz"})
    assert r.status_code == 400


def test_exact_endpoint_stale_sequence_rejected(client):
    rb = _first_available(client, "RB")
    r = client.post("/api/exact", json={"player": rb, "expected_sequence": 999})
    assert r.status_code == 400
    assert "STALE" in r.json()["detail"]


def test_ladder_endpoint_matches_cli_shape(client):
    rb = _first_available(client, "RB")
    r = client.post("/api/ladder", json={"player": rb})
    assert r.status_code == 200
    data = r.json()
    assert len(data["ladder"]) >= 3
    assert all("recommended_action" in row for row in data["ladder"])


def test_exact_status_endpoint_reflects_cache(client):
    rb = _first_available(client, "RB")
    r0 = client.get(f"/api/exact-status/{rb}")
    assert r0.json()["has_current_exact"] is False
    client.post("/api/exact", json={"player": rb})
    r1 = client.get(f"/api/exact-status/{rb}")
    assert r1.json()["has_current_exact"] is True


def test_exact_cache_invalidates_after_sale_via_website(client):
    rb = _first_available(client, "RB")
    client.post("/api/exact", json={"player": rb})
    other_wr = _first_available(client, "WR")
    client.post("/api/sale", json={"player": other_wr, "team": "Brad", "price": 10, "confirm": True})
    r = client.get(f"/api/exact-status/{rb}")
    assert r.json()["has_current_exact"] is False


def test_jacobs_role_not_misclassified_as_bench_depth_via_exact(client):
    """Regression for the expected_role_guess bug found while wiring this
    endpoint: Josh Jacobs (a real required starter) must not get a false
    BENCH_DEPTH_STOP_OVER_25 critical warning from the exact endpoint."""
    r = client.post("/api/exact", json={"player": "Josh Jacobs"})
    if r.status_code == 200:
        data = r.json()
        assert "BENCH_DEPTH_STOP_OVER_25" not in data.get("critical_reasons", [])


# ---------------------------------------------------------------------------
# Real Sunday-week usability bug: in LAN mode (--host 0.0.0.0), mutation
# endpoints 401 without X-Auth-Token, but every mutation click handler in
# live_web/static/app.js used to call `await api(...)` with no try/catch,
# so a failed request threw an unhandled promise rejection with zero
# visible feedback -- Sam hit this exactly, clicking "Start New Practice
# Draft" and seeing nothing happen. Fixed by wrapping every such handler
# in try/catch and showing a clear toast() (a specific "enter your LAN
# token" message on 401, the real e.message otherwise).
# ---------------------------------------------------------------------------

def test_lan_mode_mutation_401_has_actionable_detail_message(client, monkeypatch):
    """The backend's 401 detail is what the client's toastError() falls
    back to displaying for any non-401-specific path, and is what a
    developer reads when debugging -- it must actually say what's wrong
    (LAN mode + missing/incorrect token), not a bare 'Unauthorized'."""
    monkeypatch.setenv("SUNDAY_AUTH_TOKEN", "test-token-abc123")
    r = client.post("/api/undo")  # no X-Auth-Token header at all
    assert r.status_code == 401
    detail = r.json()["detail"]
    assert "X-Auth-Token" in detail
    assert "LAN mode" in detail

    # Wrong token also 401s...
    r2 = client.post("/api/undo", headers={"X-Auth-Token": "wrong"})
    assert r2.status_code == 401

    # ...and the correct token clears the auth gate (may still 200 or
    # fail for an unrelated business reason -- e.g. nothing to undo --
    # but it must not be a 401 once the token matches).
    r3 = client.post("/api/undo", headers={"X-Auth-Token": "test-token-abc123"})
    assert r3.status_code != 401


def _app_js_source():
    from pathlib import Path
    return (Path(__file__).parent.parent / "live_web" / "static" / "app.js").read_text()


def test_app_js_defines_shared_401_toast_helper():
    src = _app_js_source()
    assert "function toastError(" in src
    assert "Authentication required" in src
    assert "LAN token" in src


def _click_handler_blocks(src: str):
    """Yields (line_number, block_body) for every
    `addEventListener("click", async () => { ... })` block in app.js,
    using simple brace-counting (matches how this same check was used
    to verify the fix live, before writing this test)."""
    import re
    for m in re.finditer(r'addEventListener\("click", async[^{]*\{', src):
        start = m.end()
        depth = 1
        i = start
        while depth > 0 and i < len(src):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
            i += 1
        line_no = src[: m.start()].count("\n") + 1
        yield line_no, src[start:i]


def test_no_unguarded_mutation_click_handlers_in_app_js():
    """Structural regression guard for the exact silent-failure bug class:
    any click handler that calls api(...) must wrap it in a try/catch (or
    at minimum reference `try` somewhere in the block) so a rejected
    promise always produces visible feedback instead of an unhandled
    rejection. This is a coarse static check, not a JS test runner, but
    it directly encodes the bug that shipped and was fixed."""
    src = _app_js_source()
    offenders = [
        line_no for line_no, block in _click_handler_blocks(src)
        if "api(" in block and "try" not in block
    ]
    assert offenders == [], f"click handler(s) call api() without a try/catch at line(s): {offenders}"
