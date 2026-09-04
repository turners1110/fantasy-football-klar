#!/usr/bin/env python3
"""Phase 3B item 5: recompute historical 2025 salary concentration
directly from source files (data/historical_salaries_2025_raw.csv +
outputs/auction_rebuild/phase3a/salary_origin_audit.csv), rather than
reusing the earlier ~25.4% figure from memory. Produces 6 clearly-labeled
versions, from most-inclusive/least-certain to most-restrictive/most-
certain, per the instruction not to reject every historical measure just
because most salary origins are unconfirmed -- report the raw figure as
descriptive evidence, the filtered figures as sensitivity checks.

Writes outputs/auction_rebuild/phase3b/historical_concentration_benchmarks.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from auction_model import data_pipeline
from auction_model.confirmed_keeper_pipeline import normalize_name

RAW_PATH = BASE_DIR / "data" / "historical_salaries_2025_raw.csv"
AUDIT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "salary_origin_audit.csv"
OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3b" / "historical_concentration_benchmarks.csv"


def _concentration(salaries: pd.Series) -> dict:
    vals = sorted(salaries.dropna().astype(float).tolist(), reverse=True)
    total = sum(vals)
    top12 = sum(vals[:12])
    top24 = sum(vals[:24])
    return {
        "included_records": len(vals),
        "total_salary": round(total, 2),
        "top_12_salary": round(top12, 2),
        "top_12_share": round(top12 / total, 4) if total else None,
        "top_24_salary": round(top24, 2),
        "top_24_share": round(top24 / total, 4) if total else None,
        "highest_salary": vals[0] if vals else None,
        "median_salary": float(np.median(vals)) if vals else None,
        "one_dollar_count": sum(1 for v in vals if v == 1.0),
    }


def main() -> None:
    # Full roster (every rostered player row, salary or not).
    full_roster_raw = pd.read_csv(RAW_PATH)
    n_full_roster = len(full_roster_raw)

    # Deduplicated, salary-bearing rows (the authoritative loader).
    salaries, dedupe_log = data_pipeline.load_historical_salaries(RAW_PATH)
    salaries_with_value = salaries.dropna(subset=["salary_2025"]).copy()

    audit = pd.read_csv(AUDIT_PATH)
    audit["_key"] = audit["player"].map(normalize_name)
    salaries_with_value["_key"] = salaries_with_value["player"].map(normalize_name)
    merged = salaries_with_value.merge(
        audit[["_key", "origin", "reliability", "included_in_market_calibration"]],
        on="_key", how="left",
    )

    versions = {}

    versions["RAW_OBSERVED"] = {
        **_concentration(salaries_with_value["salary_2025"]),
        "excluded_records": n_full_roster - len(salaries_with_value),
        "limitations": "Every deduplicated 2025 salary with a recorded value, regardless of acquisition origin "
                       "(keeper, tag, $1 admin, or unknown). No draft-day transaction log exists to confirm any "
                       "row as a competitive-auction result -- this is DESCRIPTIVE EVIDENCE of final-roster "
                       "salary concentration, not a validated competitive-auction benchmark.",
    }

    non_dollar_one = salaries_with_value[salaries_with_value["salary_2025"] != 1.0]
    versions["NON_DOLLAR_ONE"] = {
        **_concentration(non_dollar_one["salary_2025"]),
        "excluded_records": len(salaries_with_value) - len(non_dollar_one),
        "limitations": "Excludes all $1 salary rows (a mix of genuine uncontested-nomination results and "
                       "administrative placeholders this repo cannot distinguish). Still includes unconfirmed "
                       "keeper/tag escalations.",
    }

    reliable = merged[merged["reliability"].fillna(0) > 0]
    versions["RELIABILITY_WEIGHTED"] = {
        **_concentration(reliable["salary_2025"]),
        "excluded_records": len(salaries_with_value) - len(reliable),
        "limitations": "Restricted to rows with reliability > 0 per salary_origin_audit.csv (excludes "
                       "ADMINISTRATIVE_DOLLAR_ONE rows, reliability 0.0, by the same rule item 11 used for "
                       "market calibration). 'Weighted' here means filtered-by-reliability, not a continuously "
                       "weighted sum -- documented explicitly since reliability is 0/0.35/0.4/0.5 categorical "
                       "in this dataset, not a smooth distribution.",
    }

    unknown_nondollar = merged[(merged["origin"] == "UNKNOWN") & (merged["salary_2025"] > 1)]
    versions["UNKNOWN_NON_DOLLAR_ONE_SUBSET"] = {
        **_concentration(unknown_nondollar["salary_2025"]),
        "excluded_records": len(salaries_with_value) - len(unknown_nondollar),
        "limitations": "Only rows classified UNKNOWN origin with salary > $1 -- the closest this data gets to "
                       "'plausibly a competitive auction result,' per phase 3A's own QB-audit language, but "
                       "explicitly NOT confirmed as such.",
    }

    confirmed_auction = merged[merged["origin"] == "COMPETITIVE_AUCTION"]
    versions["CONFIRMED_COMPETITIVE_AUCTION_SUBSET"] = {
        **_concentration(confirmed_auction["salary_2025"]),
        "excluded_records": len(salaries_with_value) - len(confirmed_auction),
        "limitations": "Zero rows in this repo carry a CONFIRMED_COMPETITIVE_AUCTION origin label -- no "
                       "draft-day transaction log exists anywhere in this repo to confirm any single salary "
                       "as a competitive-auction win (see phase 3A's historical_qb_roster_audit.csv and "
                       "salary_origin_audit.csv). This version is reported as empty/undefined, not fabricated.",
    }

    versions["FULL_ROSTER_ALL_PLAYERS"] = {
        **_concentration(full_roster_raw["salary_2025"].fillna(0)),
        "excluded_records": 0,
        "limitations": "Every rostered player row in the raw source file (192 rows before deduplication), "
                       "with missing salary treated as $0 -- includes the duplicate row load_historical_salaries "
                       "removes elsewhere, and college-rights holds / IR players with no real auction salary. "
                       "Provided only for completeness; RAW_OBSERVED (deduplicated, salary-bearing rows only) "
                       "is the more meaningful 'raw' figure for concentration purposes.",
    }

    fieldnames = ["version", "included_records", "excluded_records", "total_salary", "top_12_salary",
                  "top_12_share", "top_24_salary", "top_24_share", "highest_salary", "median_salary",
                  "one_dollar_count", "limitations"]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for name, v in versions.items():
            w.writerow({"version": name, **{k: v.get(k) for k in fieldnames if k != "version"}})

    print(f"Wrote {OUT_PATH}\n")
    for name, v in versions.items():
        share = v.get("top_12_share")
        share_str = f"{share:.2%}" if share is not None else "N/A (no records)"
        print(f"{name}: n={v['included_records']}, top12_share={share_str}, "
              f"top24_share={v.get('top_24_share')}, highest=${v.get('highest_salary')}")


if __name__ == "__main__":
    main()
