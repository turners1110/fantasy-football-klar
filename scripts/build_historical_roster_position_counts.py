#!/usr/bin/env python3
"""Study real 2025 final-roster position counts (data/historical_salaries_2025_raw.csv,
192 rows across 12 teams -- 2025's actual, played-out rosters, not a mock draft) to
ground the phase-2B position-cap defaults in league history rather than an
arbitrary guess.

Writes outputs/auction_rebuild/audit/historical_roster_position_counts.csv.

NOTE on "separate veteran-auction purchases from college assignments where
evidence exists": historical_salaries_2025_raw.csv is a final-roster snapshot
with no acquisition-method column (auction vs. rookie-draft vs. waiver) --
that distinction does not exist in this source. Labeled as a data-quality
limitation below rather than fabricated.

PHASE 3A CORRECTION: this originally read the raw CSV directly via
pd.read_csv, which double-counted a duplicate Kyler Murray row for Coby
(the raw file lists him twice, once with notes="", once with notes="on
IR", same $16 salary -- a data-entry artifact, not two players). That
inflated Coby's QB count to 4 and became phase 2B's now-retracted
DEFAULT_POSITION_MAX["QB"]=4. Now loads via
auction_model.data_pipeline.load_historical_salaries, which already
detects and collapses this exact duplicate (see its own "Duplicate roster
row for Kyler Murray" log line) -- the corrected real 2025 max is 3 QBs
(Coby and Sam both), not 4. See
outputs/auction_rebuild/phase3a/historical_qb_roster_audit.csv for the
full per-QB acquisition-origin audit this correction is based on.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from auction_model import data_pipeline

OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "audit" / "historical_roster_position_counts.csv"


def main() -> None:
    df, _ = data_pipeline.load_historical_salaries(BASE_DIR / "data" / "historical_salaries_2025_raw.csv")
    by_team_pos = df.groupby(["team", "position"]).size().unstack(fill_value=0)
    for pos in ("QB", "RB", "WR", "TE"):
        if pos not in by_team_pos.columns:
            by_team_pos[pos] = 0

    rows = []
    for pos in ("QB", "RB", "WR", "TE"):
        counts = by_team_pos[pos]
        rows.append({
            "position": pos,
            "minimum": int(counts.min()),
            "median": float(counts.median()),
            "p75": float(counts.quantile(0.75)),
            "p90": float(counts.quantile(0.90)),
            "maximum": int(counts.max()),
            "n_teams": int(len(counts)),
            "source": "data/historical_salaries_2025_raw.csv (2025 final rosters, all 12 teams)",
            "acquisition_method_available": False,
            "notes": (
                "Source has no acquisition-method column (auction vs. rookie draft vs. "
                "waiver) -- veteran-auction purchases and college/rookie assignments "
                "cannot be separated from this file. Reported as one combined "
                "final-roster count per team, not an auction-only count."
            ),
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {OUT_PATH}")
    print(by_team_pos.to_string())
    print()
    for r in rows:
        print(f"{r['position']}: min={r['minimum']} median={r['median']} p75={r['p75']} "
              f"p90={r['p90']} max={r['maximum']}")


if __name__ == "__main__":
    main()
