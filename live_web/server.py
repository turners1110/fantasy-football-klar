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
import threading
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import os

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from live_auction_cli import AuctionCLI, DEFAULT_LOG_PATH
from auction_engine.practice_scenarios import build_practice_cli, SCENARIOS

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Sunday Live Auction Tool")


def require_lan_token(x_auth_token: str | None = Header(default=None)):
    """V3 Part 14: when SUNDAY_AUTH_TOKEN is set (run_live_web.py sets it
    only when binding to 0.0.0.0 for LAN access), every MUTATION endpoint
    requires this header to match. Read-only endpoints never call this
    dependency and stay open on the LAN, per the spec's explicit
    distinction. When the token is unset (the default 127.0.0.1-only
    launch), this is a no-op -- no token is required, matching current
    single-laptop-only behavior exactly."""
    required = os.environ.get("SUNDAY_AUTH_TOKEN")
    if required and x_auth_token != required:
        raise HTTPException(status_code=401, detail="Missing or incorrect X-Auth-Token header (LAN mode requires it for mutations).")

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

# V3 Gate D (Part 4 concurrency): a single process-wide lock serializes
# every STATE-MUTATING endpoint (sale, undo, correct, mode switch). This
# is a real, minimal fix for the "laptop and phone submit nearly
# simultaneously" race the spec describes -- Python's GIL already makes
# individual operations atomic, but WITHOUT this lock two concurrent
# requests could both read the same pre-mutation state, both decide
# their action is legal, and both apply it (e.g. two near-simultaneous
# sales of the same player racing past each other's "already sold"
# check). This does NOT hold the lock during long exact solves (those
# already snapshot state and solve outside any shared mutable object),
# only around the fast, in-memory mutation itself.
_mutation_lock = threading.Lock()

# Idempotency cache for /api/sale: {idempotency_key: response_dict}. A
# retried request with the SAME key (e.g. a phone that timed out waiting
# for a response and retries automatically) replays the cached result
# instead of risking a duplicate sale. Deliberately unbounded-but-tiny
# for a single Sunday session; not intended to survive a restart.
_sale_idempotency_cache: dict = {}

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
    # V3 Gate D (Part 4 concurrency): optional optimistic-concurrency
    # check -- if the caller knows what sequence they last saw, they can
    # assert it here so a stale client (e.g. a phone that hasn't
    # refreshed since a laptop recorded a sale) gets a clear rejection
    # instead of silently acting on outdated information.
    expected_sequence: int | None = None
    # Optional idempotency key: a retried request with the SAME key
    # (e.g. a phone that times out waiting for a response and retries)
    # replays the cached result instead of risking a duplicate sale.
    idempotency_key: str | None = None


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


@app.get("/api/draft-score")
def get_draft_score():
    return _active().api_draft_score()


@app.get("/api/coach")
def get_coach():
    """Stage-of-draft coaching (headline + up to two focus points); read-only."""
    return _active().api_coach()


@app.get("/api/operational-status")
def get_operational_status():
    """V3 Part 14: operational status area -- mode, sequence, active log
    path, last persisted event, exact-solve freshness, sim-prior
    freshness. Connection status is implicit in the fact this request
    succeeded at all; the client also reports its own perceived
    connection state (e.g. after a failed fetch) separately in the UI."""
    result = _active().api_operational_status()
    result["mode"] = _RUNTIME["mode"]
    result["scenario"] = _RUNTIME["scenario"]
    return result


@app.get("/api/teams")
def get_teams():
    """V3 Part 14: the 12 official commissioner team names, for a
    validated dropdown/autocomplete -- replaces free-text team entry."""
    from live_auction_cli import YAHOO_TEAM_NAMES, team_display_label
    ids = sorted(_active().store.state.teams.keys())
    return {
        "teams": ids,
        "labels": {t: team_display_label(t) for t in ids},
        "yahoo_names": {t: YAHOO_TEAM_NAMES.get(t) for t in ids},
    }


@app.get("/api/board")
def get_board():
    return {"players": _active().api_board(), "nominated": _nominated_by_mode[_RUNTIME["mode"]]}


@app.get("/api/check/{player}")
def get_check(player: str):
    result = _active().api_check(player)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown or unavailable player: {player}")
    return result


@app.get("/api/verdict/{player}")
def get_verdict(player: str, current_bid: float | None = None, leading_team: str | None = None):
    """V3 Parts 9-10: the single backend-authoritative nominee-panel
    verdict -- BID / BID_BUT_RUN_EXACT_SOON / HOLD / ONE_MORE_DOLLAR /
    PASS / ILLEGAL / CRITICAL_REVIEW_REQUIRED, per AuctionCLI.api_verdict."""
    result = _active().api_verdict(player, current_bid=current_bid, leading_team=leading_team)
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


@app.post("/api/nominate", dependencies=[Depends(require_lan_token)])
def post_nominate(req: NominateRequest):
    _nominated_by_mode[_RUNTIME["mode"]] = req.player
    return {"nominated": _nominated_by_mode[_RUNTIME["mode"]]}


@app.post("/api/sale", dependencies=[Depends(require_lan_token)])
def post_sale(req: SaleRequest):
    with _mutation_lock:
        # Idempotency replay: a retried request with the same key gets
        # the SAME cached result rather than being re-applied.
        if req.idempotency_key is not None and req.idempotency_key in _sale_idempotency_cache:
            return _sale_idempotency_cache[req.idempotency_key]

        # Optimistic concurrency: if the caller told us what sequence
        # they expected, reject a stale request cleanly rather than
        # letting it act on state that has since moved on (e.g. a phone
        # that hasn't refreshed since a laptop already recorded a sale).
        current_seq = _active().store.state.sequence_number
        if req.expected_sequence is not None and req.expected_sequence != current_seq:
            raise HTTPException(
                status_code=409,
                detail=f"STALE_STATE: client expected sequence {req.expected_sequence}, "
                       f"but current state is at sequence {current_seq} -- refresh and retry.",
            )

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
        result = {"needs_confirmation": False, "message": message, "status": _active().api_status()}
        if req.idempotency_key is not None:
            _sale_idempotency_cache[req.idempotency_key] = result
        return result


@app.post("/api/undo", dependencies=[Depends(require_lan_token)])
def post_undo():
    with _mutation_lock:
        message = _active().cmd_undo()
        return {"message": message, "status": _active().api_status()}


@app.post("/api/correct", dependencies=[Depends(require_lan_token)])
def post_correct(req: CorrectRequest):
    with _mutation_lock:
        message = _active().cmd_correct(req.player, req.team, str(req.price))
        if message.startswith("REFUSED") or message.startswith("ERROR"):
            raise HTTPException(status_code=400, detail=message)
        return {"message": message, "status": _active().api_status()}


@app.post("/api/save", dependencies=[Depends(require_lan_token)])
def post_save(req: SnapshotRequest):
    return {"message": _active().cmd_save(req.name)}


@app.post("/api/load", dependencies=[Depends(require_lan_token)])
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


@app.post("/api/mode/practice", dependencies=[Depends(require_lan_token)])
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


@app.post("/api/mode/production", dependencies=[Depends(require_lan_token)])
def post_mode_production():
    """Switches back to the single persistent production AuctionCLI
    instance. Nothing about production state is rebuilt or reset here --
    it is the same object that has been running the whole time."""
    _RUNTIME["mode"] = "production"
    _RUNTIME["cli"] = cli
    _RUNTIME["scenario"] = None
    _RUNTIME["proof"] = {}
    return {"mode": "production"}


# ---------------------------------------------------------------------------
# V3 Gate F (Part 13): true, interactive Practice Mode.
#
# Completely separate from BOTH the production `cli` above and the
# existing scenario-sandbox `_RUNTIME["cli"]` practice mechanism --
# sessions live in their own registry, keyed by a session_id the client
# generates and keeps in its own browser session (localStorage), so one
# device starting a practice draft can never switch any other connected
# client into practice. Each session has its own AuctionCLI instance
# with its own isolated log path (auction_engine.practice_draft_session's
# PRACTICE_DRAFT_LOG_DIR) -- never the production log, never the
# scenario-sandbox log.
# ---------------------------------------------------------------------------
_practice_draft_sessions: dict = {}


class PracticeDraftStartRequest(BaseModel):
    session_id: str
    seed: int = 909001


class PracticeDraftBidRequest(BaseModel):
    amount: float


def _get_practice_draft_session(session_id: str):
    sess = _practice_draft_sessions.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"No practice draft session {session_id!r} -- start one first.")
    return sess


@app.post("/api/practice-draft/start", dependencies=[Depends(require_lan_token)])
def post_practice_draft_start(req: PracticeDraftStartRequest):
    from auction_engine.practice_draft_session import PracticeDraftSession
    sess = PracticeDraftSession(session_id=req.session_id, seed=req.seed)
    _practice_draft_sessions[req.session_id] = sess
    return {"session_id": req.session_id, "status": sess.status, "pending": sess.pending_nomination()}


@app.get("/api/practice-draft/{session_id}/pending")
def get_practice_draft_pending(session_id: str):
    sess = _get_practice_draft_session(session_id)
    return {"status": sess.status, "pending": sess.pending_nomination()}


@app.get("/api/practice-draft/{session_id}/status")
def get_practice_draft_status(session_id: str):
    sess = _get_practice_draft_session(session_id)
    return sess.cli.api_status()


@app.post("/api/practice-draft/{session_id}/pass", dependencies=[Depends(require_lan_token)])
def post_practice_draft_pass(session_id: str):
    sess = _get_practice_draft_session(session_id)
    result = sess.sam_pass()
    result["pending"] = sess.pending_nomination()
    return result


@app.post("/api/practice-draft/{session_id}/bid", dependencies=[Depends(require_lan_token)])
def post_practice_draft_bid(session_id: str, req: PracticeDraftBidRequest):
    sess = _get_practice_draft_session(session_id)
    result = sess.sam_bid(req.amount)
    result["pending"] = sess.pending_nomination()
    return result


@app.post("/api/practice-draft/{session_id}/undo", dependencies=[Depends(require_lan_token)])
def post_practice_draft_undo(session_id: str):
    sess = _get_practice_draft_session(session_id)
    msg = sess.undo()
    return {"message": msg, "status": sess.status, "pending": sess.pending_nomination()}


@app.get("/api/practice-draft/{session_id}/review")
def get_practice_draft_review(session_id: str):
    sess = _get_practice_draft_session(session_id)
    return sess.post_draft_review()


@app.delete("/api/practice-draft/{session_id}", dependencies=[Depends(require_lan_token)])
def delete_practice_draft_session(session_id: str):
    _practice_draft_sessions.pop(session_id, None)
    return {"deleted": session_id}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
