"""Live Auction Website (2026) -- a thin FastAPI layer over the exact same
AuctionCLI backend used by live_auction_cli.py.

DELIBERATELY SEPARATE from draft_ui/ (the stale, pre-Phase-3E website):
different package, different port (8010 by default, vs draft_ui's own
default), different page title ("SUNDAY LIVE AUCTION TOOL"), different
launch script (run_live_web.py, not run_draft_ui.py). draft_ui/ is left
completely untouched -- it uses its own unrelated state.py/engine.py, not
auction_engine/, and reusing it would have meant either duplicating state
logic or risking breaking a website Sam might still open by habit. See
outputs/auction_rebuild/sunday_final/final_report.md for the full
architecture decision writeup.

Every endpoint below calls an AuctionCLI method directly -- sale
recording, undo, correction, save/load, targets, roster paths, market
summary, and the emergency sheet are all the SAME functions
live_auction_cli.py's REPL calls. No duplicate state or accounting logic
exists in this file.
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from live_auction_cli import AuctionCLI, DEFAULT_LOG_PATH
from auction_engine.practice_scenarios import build_practice_cli, SCENARIOS

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Sunday Live Auction Tool")

# Single process-wide PRODUCTION AuctionCLI instance -- the SAME
# event-sourced auction_engine state the CLI would use if launched
# instead. This is the one and only source of truth for a real Sunday
# draft. It is NEVER mutated, replaced, or reset by practice-mode code
# below -- switching modes only changes which instance _RUNTIME["cli"]
# points at.
#
# V3 REPAIR (Part 4): this used to point at its own separate
# "web_session.jsonl" file while live_auction_cli.py's terminal REPL
# defaulted to "cli_session.jsonl" -- two DIFFERENT production logs for
# the same live draft. If Sam ever fell back from the website to the
# terminal CLI mid-draft (the documented emergency path), the two
# interfaces would silently diverge onto separate histories instead of
# sharing one. Fixed by importing the SAME DEFAULT_LOG_PATH constant
# live_auction_cli.py itself uses, so both interfaces always default to
# the one production event log, with no path to configure them
# differently by accident.
cli = AuctionCLI(log_path=DEFAULT_LOG_PATH)

# V2.1 Part 6: Practice Mode. _RUNTIME tracks which AuctionCLI instance is
# "active" for every endpoint below (see _active()). A practice instance
# is a brand-new AuctionCLI object with its own store/event-log/
# market-state/exact-cache/current-nomination -- full namespace isolation
# by construction, since nothing here ever copies state between the two
# objects. Switching back to production always returns the same
# untouched `cli` object above; production's own state is never at risk
# from anything that happens in practice mode.
_RUNTIME: dict = {"mode": "production", "cli": cli, "scenario": None, "proof": {}}

# UI-only "currently nominated" tracker -- explicitly NOT auction state
# (nominating a player never touches auction_engine; only a sale does).
# Kept per-mode so a practice nomination can never leak into production.
_nominated_by_mode: dict = {"production": None, "practice": None}


def _active() -> AuctionCLI:
    return _RUNTIME["cli"]


def _nominated() -> dict:
    return _nominated_by_mode


class SaleRequest(BaseModel):
    player: str
    team: str
    price: float
    confirm: bool = False


class CorrectRequest(BaseModel):
    player: str
    team: str
    price: float


class SnapshotRequest(BaseModel):
    name: str


class NominateRequest(BaseModel):
    player: str | None = None


class ExactRequest(BaseModel):
    player: str
    test_price: float | None = None
    expected_sequence: int | None = None


class LadderRequest(BaseModel):
    player: str


@app.get("/api/status")
def get_status():
    return _active().api_status()


@app.get("/api/board")
def get_board():
    return {"players": _active().api_board(), "nominated": _nominated_by_mode[_RUNTIME["mode"]]}


@app.get("/api/check/{player}")
def get_check(player: str):
    result = _active().api_check(player)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown or unavailable player: {player}")
    return result


@app.get("/api/targets")
def get_targets():
    return {"targets": _active().api_targets(25)}


@app.get("/api/paths")
def get_paths():
    return _active().api_paths()


@app.get("/api/market")
def get_market():
    return _active().api_market()


@app.get("/api/log")
def get_log():
    return {"events": _active().api_log()}


@app.get("/api/league")
def get_league():
    # V2.1 Part 8: automatically include current-nominee demand per team
    # when something is nominated, reusing the same per-mode nomination
    # tracker the bid panel uses (no separate practice/production leak).
    nominee = _nominated_by_mode[_RUNTIME["mode"]]
    return {"teams": _active().api_league(nominee=nominee)}


@app.get("/api/league/{team_id}")
def get_team_detail(team_id: str):
    result = _active().api_team_detail(team_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown team: {team_id}")
    return result


@app.get("/api/rosters")
def get_all_rosters():
    """V2.2 Request 3: every team's complete player-by-player roster
    (starters + bench + keepers + auction purchases) in one response, so
    the whole league can be scanned in one place instead of clicking
    through 12 separate team pages."""
    return {"teams": _active().api_all_rosters()}


@app.get("/api/demand/{player}")
def get_nominee_demand(player: str):
    result = _active().api_nominee_demand(player)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown or unavailable player: {player}")
    return result


@app.get("/api/search")
def get_search(q: str, include_protected: bool = False):
    return {"results": _active().api_search(q, include_protected=include_protected)}


_MC_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "live_web_v2" / "player_price_distributions.csv"
_mc_cache: dict = {}


def _load_mc():
    if not _mc_cache and _MC_PATH.exists():
        import csv as _csv
        with _MC_PATH.open() as f:
            for row in _csv.DictReader(f):
                _mc_cache[row["player"]] = row
    return _mc_cache


@app.get("/api/distributions/{player}")
def get_distribution(player: str):
    data = _load_mc()
    row = data.get(player)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No Monte Carlo distribution for {player}")
    return row


@app.get("/api/distributions")
def get_all_distributions():
    return {"players": list(_load_mc().values()), "source": str(_MC_PATH.name)}


@app.get("/api/emergency", response_class=PlainTextResponse)
def get_emergency():
    return _active().cmd_emergency()


@app.post("/api/exact")
def post_exact(req: ExactRequest):
    result = _active().api_exact(req.player, req.test_price, req.expected_sequence)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/ladder")
def post_ladder(req: LadderRequest):
    result = _active().api_ladder(req.player)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/api/exact-status/{player}")
def get_exact_status(player: str):
    cache_key_prefix = (_active().store.state.sequence_number, player)
    cached = [v for k, v in _active()._exact_cache.items() if k[0] == cache_key_prefix[0] and k[1] == cache_key_prefix[1]]
    if not cached:
        return {"player": player, "has_current_exact": False, "state_sequence": _active().store.state.sequence_number}
    return {"player": player, "has_current_exact": True, "state_sequence": _active().store.state.sequence_number,
           "cached_prices": [k[2] for k in _active()._exact_cache if k[0] == cache_key_prefix[0] and k[1] == cache_key_prefix[1]]}


@app.post("/api/nominate")
def post_nominate(req: NominateRequest):
    _nominated_by_mode[_RUNTIME["mode"]] = req.player
    return {"nominated": _nominated_by_mode[_RUNTIME["mode"]]}


@app.post("/api/sale")
def post_sale(req: SaleRequest):
    # Same underlying call the CLI's `sale` command makes -- goes through
    # the real auction_engine event log, real keeper/college-rights
    # rejection, and the real large-sale confirmation gate.
    message = _active().cmd_sale(req.player, req.team, str(req.price), confirmed=req.confirm)
    if message.startswith("REFUSED") or message.startswith("ERROR"):
        raise HTTPException(status_code=400, detail=message)
    if message.startswith("CONFIRM:"):
        return {"needs_confirmation": True, "message": message}
    if _nominated_by_mode[_RUNTIME["mode"]] == req.player:
        _nominated_by_mode[_RUNTIME["mode"]] = None
    return {"needs_confirmation": False, "message": message, "status": _active().api_status()}


@app.post("/api/undo")
def post_undo():
    message = _active().cmd_undo()
    return {"message": message, "status": _active().api_status()}


@app.post("/api/correct")
def post_correct(req: CorrectRequest):
    message = _active().cmd_correct(req.player, req.team, str(req.price))
    if message.startswith("REFUSED") or message.startswith("ERROR"):
        raise HTTPException(status_code=400, detail=message)
    return {"message": message, "status": _active().api_status()}


@app.post("/api/save")
def post_save(req: SnapshotRequest):
    return {"message": _active().cmd_save(req.name)}


@app.post("/api/load")
def post_load(req: SnapshotRequest):
    message = _active().cmd_load(req.name)
    if message.startswith("ERROR"):
        raise HTTPException(status_code=400, detail=message)
    return {"message": message, "status": _active().api_status()}


class ModeRequest(BaseModel):
    scenario: str = "normal"


@app.get("/api/mode")
def get_mode():
    return {
        "mode": _RUNTIME["mode"],
        "scenario": _RUNTIME["scenario"],
        "proof": _RUNTIME["proof"],
        "available_scenarios": list(SCENARIOS),
    }


@app.post("/api/mode/practice")
def post_mode_practice(req: ModeRequest):
    """Switches the active instance to a brand-new, fully isolated
    practice AuctionCLI seeded with the requested scenario. The
    production `cli` object above is never touched by this call -- it
    keeps running in the background exactly as it was, so switching back
    is always safe and lossless."""
    if req.scenario not in SCENARIOS:
        raise HTTPException(status_code=400, detail=f"Unknown scenario {req.scenario!r}; must be one of {SCENARIOS}")
    practice_cli, proof = build_practice_cli(req.scenario)
    _RUNTIME["mode"] = "practice"
    _RUNTIME["cli"] = practice_cli
    _RUNTIME["scenario"] = req.scenario
    _RUNTIME["proof"] = proof
    _nominated_by_mode["practice"] = None
    return {"mode": "practice", "scenario": req.scenario, "proof": proof}


@app.post("/api/mode/production")
def post_mode_production():
    """Switches back to the single persistent production AuctionCLI
    instance. Nothing about production state is rebuilt or reset here --
    it is the same object that has been running the whole time."""
    _RUNTIME["mode"] = "production"
    _RUNTIME["cli"] = cli
    _RUNTIME["scenario"] = None
    _RUNTIME["proof"] = {}
    return {"mode": "production"}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
