#!/usr/bin/env python3
"""Download nflverse data for the auction model.

Examples:

    python pull_nflverse_data.py
    python pull_nflverse_data.py --seasons 2024 2025 --build-projections
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from auction_model import nflverse_pull

BASE_DIR = Path(__file__).parent
DEFAULT_DATA_DIR = BASE_DIR / "data" / "nflverse"
DEFAULT_PROJECTIONS = BASE_DIR / "data" / "projections_2026.csv"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--seasons",
        type=int,
        nargs="+",
        default=[2024, 2025],
        help="Seasons for player stats and rosters (default: 2024 2025).",
    )
    p.add_argument(
        "--draft-seasons",
        type=int,
        nargs="+",
        default=[2024, 2025],
        help="Draft classes to download (default: 2024 2025).",
    )
    p.add_argument(
        "--output-dir",
        default=DEFAULT_DATA_DIR,
        help="Directory for raw nflverse CSV exports.",
    )
    p.add_argument(
        "--build-projections",
        action="store_true",
        help="Also write a projections CSV from the latest requested season's stats.",
    )
    p.add_argument(
        "--projection-season",
        type=int,
        default=None,
        help="Season to use for projections (default: max --seasons value).",
    )
    p.add_argument(
        "--projections-out",
        default=DEFAULT_PROJECTIONS,
        help="Output path when --build-projections is set.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    seasons = sorted(set(args.seasons))
    draft_seasons = sorted(set(args.draft_seasons))
    output_dir = Path(args.output_dir)

    print(f"Pulling nflverse data for seasons {seasons}...")
    written = nflverse_pull.pull_all(seasons, draft_seasons, output_dir)
    for name, path in sorted(written.items()):
        print(f"  wrote {name}: {path}")

    if args.build_projections:
        projection_season = args.projection_season or max(seasons)
        stats = nflverse_pull.pull_player_stats(seasons)
        projections = nflverse_pull.build_projections_from_stats(stats, projection_season)
        out_path = Path(args.projections_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        projections.to_csv(out_path, index=False)
        print(
            f"\nBuilt projections from {projection_season} nflverse stats: "
            f"{out_path} ({len(projections)} players)"
        )
        print(
            "Re-run valuation with:\n"
            f"  python run_valuation.py --projections {out_path}"
        )


if __name__ == "__main__":
    main()
