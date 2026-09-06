#!/usr/bin/env python3
"""Launch the Sunday Live Auction Tool website.

    python3 run_live_web.py
    python3 run_live_web.py --port 8020

This is a SEPARATE tool from `python run_draft_ui.py` (the old, stale
pre-Phase-3E website in draft_ui/) -- different port by default (8010),
different page title ("SUNDAY LIVE AUCTION TOOL"), different backend
(live_web/server.py wraps live_auction_cli.AuctionCLI / auction_engine/,
not draft_ui's own state.py). Do not confuse the two.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _guard_existing_session_log(args) -> None:
    """Draft-night safety: AuctionCLI defaults to AUCTION_RESUME_MODE=clean,
    which DELETES an existing production session log on startup. That is
    fine for a first launch, but a crash-restart mid-draft typed as a bare
    `python3 run_live_web.py ...` would silently wipe every recorded sale.
    start_sunday_live_tool.sh already prompts for this; this makes the raw
    launcher equally safe: with a non-empty log and no explicit choice
    (--resume / --clean / AUCTION_RESUME_MODE), refuse to start."""
    from live_auction_cli import DEFAULT_LOG_PATH
    from datetime import datetime
    import shutil

    log_path = Path(DEFAULT_LOG_PATH)
    event_count = 0
    if log_path.exists():
        with log_path.open() as f:
            event_count = sum(1 for line in f if line.strip())

    if args.resume:
        os.environ["AUCTION_RESUME_MODE"] = "resume"
        print(f"Resuming existing production session ({event_count} recorded event(s)).", flush=True)
        return
    if args.clean:
        if event_count > 0:
            archive = log_path.with_name(f"cli_session_archived_{datetime.now():%Y%m%d_%H%M%S}.jsonl")
            shutil.copy2(log_path, archive)
            print(f"Archived old session log ({event_count} event(s)) to {archive}", flush=True)
        os.environ["AUCTION_RESUME_MODE"] = "clean"
        return
    if event_count == 0 or os.environ.get("AUCTION_RESUME_MODE") in ("resume", "clean"):
        return  # nothing at stake, or the launcher script already decided

    print("=" * 70, flush=True)
    print(" REFUSING TO START: an in-progress production session log exists:", flush=True)
    print(f"   {log_path}  ({event_count} recorded event(s))", flush=True)
    print(" Starting without a choice would DELETE it. Re-run with:", flush=True)
    print("   --resume   to continue that draft exactly where it left off", flush=True)
    print("   --clean    to archive it and start a fresh draft", flush=True)
    print("=" * 70, flush=True)
    sys.exit(2)


def main():
    parser = argparse.ArgumentParser(description="Launch the Sunday Live Auction Tool website.")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 to also allow phone access on the same local network.")
    parser.add_argument("--no-auth", action="store_true", help="Skip the LAN mutation token even when binding to 0.0.0.0. "
                         "Only use this on a trusted private network -- anyone on the same WiFi could then record/undo/correct sales.")
    parser.add_argument("--resume", action="store_true",
                        help="Replay an existing production session log and keep appending to it.")
    parser.add_argument("--clean", action="store_true",
                        help="Archive any existing production session log and start a fresh draft.")
    args = parser.parse_args()

    if args.resume and args.clean:
        parser.error("--resume and --clean are mutually exclusive")
    _guard_existing_session_log(args)

    if args.host == "0.0.0.0" and not args.no_auth:
        # V3 Part 14: binding to 0.0.0.0 (LAN access) requires a session
        # token for mutation endpoints -- read-only endpoints stay open
        # on the LAN, but sale/undo/correct/mode-switch require this
        # token so a stray device on the same WiFi cannot mutate the
        # real draft. Generated fresh per launch, printed once here.
        # Pass --no-auth to skip this entirely on a trusted network.
        token = os.environ.get("SUNDAY_AUTH_TOKEN") or secrets.token_urlsafe(16)
        os.environ["SUNDAY_AUTH_TOKEN"] = token
        print("=" * 70, flush=True)
        print(f" LAN MODE: mutation endpoints require this token: {token}", flush=True)
        print(f" Add header 'X-Auth-Token: {token}' (the website's own UI does", flush=True)
        print(" this automatically once you enter it in the top-right box).", flush=True)
        print("=" * 70, flush=True)
    elif args.host == "0.0.0.0" and args.no_auth:
        os.environ.pop("SUNDAY_AUTH_TOKEN", None)
        print("=" * 70, flush=True)
        print(" LAN MODE, --no-auth: mutation endpoints are OPEN, no token required.", flush=True)
        print(" Anyone on this WiFi network can record/undo/correct sales.", flush=True)
        print("=" * 70, flush=True)

    import uvicorn
    print(f"Starting SUNDAY LIVE AUCTION TOOL at http://{args.host}:{args.port}")
    print("(This is NOT draft_ui/ -- that is the old, stale, pre-Phase-3E website.)")
    uvicorn.run("live_web.server:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
