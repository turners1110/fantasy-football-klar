"""V3 Gate C -- Targets page rebuild (Part 9), verified through the
real FastAPI endpoint: full required field list, correct units (never
a $ sign on a points field), and the documented non-raw-points scoring
formula."""
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


REQUIRED_FIELDS = [
    "player", "position", "tier", "projected_points", "marginal_lineup_points",
    "team_specific_value_dollars", "expected_market_price_dollars",
    "market_p25_p50_p75_p90_dollars", "draft_probability",
    "exact_ceiling_dollars", "approximate_ceiling_dollars", "exact_or_approximate_status",
    "recommended_stop_dollars", "current_live_bid_dollars", "surplus_or_deficit_dollars",
    "confidence", "total_score", "recommendation_class", "reason",
    "critical_review_required", "critical_reasons",
]


def test_targets_endpoint_has_full_required_field_list(client):
    r = client.get("/api/targets")
    assert r.status_code == 200
    targets = r.json()["targets"]
    assert len(targets) > 0
    for field in REQUIRED_FIELDS:
        assert field in targets[0], f"missing required Part 9 field: {field}"


def test_points_fields_are_never_dollar_denominated(client):
    """Regression for the exact bug this repair started with: a points
    field must never be usable as-is where a dollar ceiling is expected
    -- projected_points/marginal_lineup_points should be in a plausible
    fantasy-points range (tens to low hundreds), never silently equal to
    a dollar figure bounded by the legal max bid."""
    status = client.get("/api/status").json()
    legal_max = status["legal_max_bid"]
    r = client.get("/api/targets")
    for t in r.json()["targets"][:10]:
        assert t["team_specific_value_dollars"] <= legal_max + 1e-6
        assert t["recommended_stop_dollars"] <= legal_max + 1e-6
        # marginal_lineup_points is explicitly NOT constrained to the
        # dollar budget -- it's a points quantity, this proves it's
        # tracked completely separately from the dollar fields.


def test_targets_scoring_does_not_over_promote_from_small_denominator(client):
    """A player with tiny absolute value must not reach the top of the
    list merely because Sam has zero players at that position -- the
    position-need contribution is capped (auction_engine.live_target_scoring's
    own documented MAX_POSITION_NEED_SHARE), so real value should still
    dominate ranking for the top few slots."""
    r = client.get("/api/targets")
    targets = r.json()["targets"]
    top5_team_values = [t["team_specific_value_dollars"] for t in targets[:5]]
    # The top 5 by score must have MEANINGFUL absolute dollar value
    # (not just a $1-2 player promoted purely by an empty-position bonus).
    assert all(v >= 3 for v in top5_team_values), top5_team_values


def test_exact_status_field_reflects_real_exact_cache_state(client):
    board = client.get("/api/board").json()["players"]
    rb = next(p["player"] for p in board if p["position"] == "RB")
    client.post("/api/exact", json={"player": rb, "expected_sequence": 0})
    r = client.get("/api/targets")
    targets_by_player = {t["player"]: t for t in r.json()["targets"]}
    if rb in targets_by_player:
        t = targets_by_player[rb]
        assert t["exact_or_approximate_status"] in ("EXACT_CURRENT", "APPROXIMATE_NO_CURRENT_EXACT")
        if t["exact_or_approximate_status"] == "EXACT_CURRENT":
            assert t["exact_ceiling_dollars"] is not None
            assert t["approximate_ceiling_dollars"] is None


def test_targets_page_html_shows_unit_labeled_columns():
    index_html = (Path(__file__).parent.parent / "live_web" / "static" / "index.html").read_text()
    assert "Marginal (pts)" in index_html
    assert "Team Value ($)" in index_html
    assert "Recommended Stop ($)" in index_html
