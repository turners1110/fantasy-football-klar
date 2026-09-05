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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main():
    parser = argparse.ArgumentParser(description="Launch the Sunday Live Auction Tool website.")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 to also allow phone access on the same local network.")
    args = parser.parse_args()

    import uvicorn
    print(f"Starting SUNDAY LIVE AUCTION TOOL at http://{args.host}:{args.port}")
    print("(This is NOT draft_ui/ -- that is the old, stale, pre-Phase-3E website.)")
    uvicorn.run("live_web.server:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
