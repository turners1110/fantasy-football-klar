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


def main():
    parser = argparse.ArgumentParser(description="Launch the Sunday Live Auction Tool website.")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 to also allow phone access on the same local network.")
    parser.add_argument("--no-auth", action="store_true", help="Skip the LAN mutation token even when binding to 0.0.0.0. "
                         "Only use this on a trusted private network -- anyone on the same WiFi could then record/undo/correct sales.")
    args = parser.parse_args()

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
