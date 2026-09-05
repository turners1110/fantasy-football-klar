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

from live_auction_cli import AuctionCLI

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Sunday Live Auction Tool")

# Single process-wide AuctionCLI instance -- the SAME event-sourced
# auction_engine state the CLI would use if launched instead. This is the
# one and only source of truth for the running server.
cli = AuctionCLI(log_path=BASE_DIR / "outputs" / "auction_rebuild" / "live_mvp" / "web_session.jsonl")

# UI-only "currently nominated" tracker -- explicitly NOT auction state
# (nominating a player never touches auction_engine; only a sale does).
_nominated: dict = {"player": None}


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


@app.get("/api/status")
def get_status():
    return cli.api_status()


@app.get("/api/board")
def get_board():
    return {"players": cli.api_board(), "nominated": _nominated["player"]}


@app.get("/api/check/{player}")
def get_check(player: str):
    result = cli.api_check(player)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown or unavailable player: {player}")
    return result


@app.get("/api/targets")
def get_targets():
    return {"targets": cli.api_targets(25)}


@app.get("/api/paths")
def get_paths():
    return cli.api_paths()


@app.get("/api/market")
def get_market():
    return cli.api_market()


@app.get("/api/log")
def get_log():
    return {"events": cli.api_log()}


@app.get("/api/emergency", response_class=PlainTextResponse)
def get_emergency():
    return cli.cmd_emergency()


@app.post("/api/nominate")
def post_nominate(req: NominateRequest):
    _nominated["player"] = req.player
    return {"nominated": _nominated["player"]}


@app.post("/api/sale")
def post_sale(req: SaleRequest):
    # Same underlying call the CLI's `sale` command makes -- goes through
    # the real auction_engine event log, real keeper/college-rights
    # rejection, and the real large-sale confirmation gate.
    message = cli.cmd_sale(req.player, req.team, str(req.price), confirmed=req.confirm)
    if message.startswith("REFUSED") or message.startswith("ERROR"):
        raise HTTPException(status_code=400, detail=message)
    if message.startswith("CONFIRM:"):
        return {"needs_confirmation": True, "message": message}
    if _nominated["player"] == req.player:
        _nominated["player"] = None
    return {"needs_confirmation": False, "message": message, "status": cli.api_status()}


@app.post("/api/undo")
def post_undo():
    message = cli.cmd_undo()
    return {"message": message, "status": cli.api_status()}


@app.post("/api/correct")
def post_correct(req: CorrectRequest):
    message = cli.cmd_correct(req.player, req.team, str(req.price))
    if message.startswith("REFUSED") or message.startswith("ERROR"):
        raise HTTPException(status_code=400, detail=message)
    return {"message": message, "status": cli.api_status()}


@app.post("/api/save")
def post_save(req: SnapshotRequest):
    return {"message": cli.cmd_save(req.name)}


@app.post("/api/load")
def post_load(req: SnapshotRequest):
    message = cli.cmd_load(req.name)
    if message.startswith("ERROR"):
        raise HTTPException(status_code=400, detail=message)
    return {"message": message, "status": cli.api_status()}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
