from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auction_model import data_pipeline
from draft_ui import engine, state

BASE_DIR = Path(__file__).parent.parent
STATIC_DIR = Path(__file__).parent / "static"
OUTPUT_DIR = BASE_DIR / "output"

app = FastAPI(title="Draft Day")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class PickRequest(BaseModel):
    player: str
    price: float
    is_me: bool = False


def _public_state(s: dict) -> dict:
    return {
        "my_team": s["my_team"],
        "my_remaining_budget": s["my_remaining_budget"],
        "my_roster": s["my_roster"],
        "available": sorted(s["available"].values(), key=lambda p: -p["my_target_price"]),
        "draft_log": list(reversed([
            {k: v for k, v in entry.items() if k not in ("_entry", "_key")}
            for entry in s["draft_log"]
        ])),
        "remaining_league_budget": s["remaining_league_budget"],
        "remaining_baseline_value": s["remaining_baseline_value"],
        "live_inflation_multiplier": s["live_inflation_multiplier"],
    }


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
def get_state():
    return _public_state(state.get_state())


@app.post("/api/pick")
def post_pick(pick: PickRequest):
    key = data_pipeline._normalize_name(pick.player)
    if key not in state.get_state()["available"]:
        raise HTTPException(status_code=404, detail=f"{pick.player!r} not found in available pool.")
    try:
        s = state.mutate(engine.apply_pick, key, pick.price, pick.is_me)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _public_state(s)


@app.post("/api/undo")
def post_undo():
    s = state.mutate(engine.undo_last)
    return _public_state(s)


@app.post("/api/reset")
def post_reset():
    s = state.reset()
    return _public_state(s)


@app.get("/api/export")
def export():
    s = state.get_state()
    log = s["draft_log"]
    if not log:
        raise HTTPException(status_code=400, detail="No picks logged yet.")
    rows = [
        {
            "order": i + 1,
            "player": entry["player"],
            "position": entry["position"],
            "nfl_team": entry.get("nfl_team", ""),
            "price": entry["price"],
            "is_me": entry["is_me"],
            "recommended_at_time": entry["recommended_at_time"],
            "baseline_price": entry["baseline_price"],
        }
        for i, entry in enumerate(log)
    ]
    df = pd.DataFrame(rows)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"draft_day_log_{ts}.csv"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    return {"written": str(out_path), "n_picks": len(rows)}
