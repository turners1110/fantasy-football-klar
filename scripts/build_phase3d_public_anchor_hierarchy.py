#!/usr/bin/env python3
"""Phase 3D item 7: build the public-anchor hierarchy source-record table
for every auction-eligible player.

Writes:
  outputs/auction_rebuild/phase3d/public_anchor_hierarchy.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from auction_model.public_anchor import build_public_anchor_hierarchy
from mock_draft.data import load_confirmed_pool_and_teams

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3d"


def main() -> None:
    players, _teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    df = build_public_anchor_hierarchy(players)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "public_anchor_hierarchy.csv"
    df.to_csv(out_path, index=False)

    counts = df["source"].value_counts()
    print(f"Wrote {out_path} ({len(df)} players)")
    for source, n in counts.items():
        print(f"  {source}: {n} players ({100 * n / len(df):.1f}%)")


if __name__ == "__main__":
    main()
