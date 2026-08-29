"""In-memory draft state + JSON snapshot persistence so the server can be
killed/restarted mid-draft without losing anything."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from draft_ui import engine

SNAPSHOT_PATH = Path(__file__).parent / "draft_state.json"

_lock = Lock()
_state: dict | None = None


def _strip_for_json(state: dict) -> dict:
    """draft_log entries carry a live `_entry` dict reference for undo --
    drop it before serializing (it's redundant with baseline_price/player)."""
    clean = dict(state)
    clean["draft_log"] = [
        {k: v for k, v in entry.items() if k != "_entry"} for entry in state["draft_log"]
    ]
    return clean


def get_state() -> dict:
    global _state
    with _lock:
        if _state is None:
            _state = _load_or_init()
        return _state


def _load_or_init() -> dict:
    if SNAPSHOT_PATH.exists():
        with open(SNAPSHOT_PATH) as f:
            saved = json.load(f)
        # Baseline pool/prices are re-derived fresh (cheap, and picks up any
        # data changes), but draft progress (picks, roster, budget) is restored.
        fresh = engine.load_baseline()
        for key in ("my_remaining_budget", "my_roster", "remaining_league_budget", "draft_log"):
            fresh[key] = saved.get(key, fresh[key])
        for entry in saved.get("draft_log", []):
            fresh["available"].pop(entry["_key"], None)
        for entry in fresh["draft_log"]:
            entry["_entry"] = {
                "player": entry["player"],
                "position": entry["position"],
                "nfl_team": entry.get("nfl_team", ""),
                "baseline_price": entry["baseline_price"],
            }
        engine.recompute(fresh)
        return fresh
    return engine.recompute(engine.load_baseline())


def mutate(fn, *args, **kwargs) -> dict:
    """Run a state-mutating engine function under the lock, then persist."""
    global _state
    with _lock:
        if _state is None:
            _state = _load_or_init()
        _state = fn(_state, *args, **kwargs)
    save()
    return _state


def save() -> None:
    state = get_state()
    tmp = SNAPSHOT_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(_strip_for_json(state), f, indent=2)
    tmp.replace(SNAPSHOT_PATH)


def reset() -> dict:
    """Drop all draft-day progress and reload a clean baseline."""
    global _state
    with _lock:
        _state = engine.recompute(engine.load_baseline())
        if SNAPSHOT_PATH.exists():
            SNAPSHOT_PATH.unlink()
        return _state
