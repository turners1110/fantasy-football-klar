#!/usr/bin/env python3
"""Live draft-day auction UI.

    python run_draft_ui.py
    python run_draft_ui.py --port 8080

Opens a local web server with your roster, remaining budget, the full
available-player pool, a live league-wide recommended auction value, and a
personal "my_team target price" that tapers as you fill positional need.
Log every closed pick as it happens; everything recomputes in real time.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import uvicorn


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    print(f"Draft UI: open http://{args.host}:{args.port} in your browser.")
    uvicorn.run("draft_ui.server:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
