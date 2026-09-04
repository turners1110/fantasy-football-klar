#!/usr/bin/env python3
"""Phase 3A item 11: classify every 2025 historical salary by origin
before using ANY of them for economic calibration. This repo has no
draft-day transaction log (checked in build_historical_qb_roster_audit.py
-- none found), so most rows cannot be confirmed as COMPETITIVE_AUCTION;
they are labeled UNKNOWN with a reliability weight, not asserted as
auction prices.

Writes outputs/auction_rebuild/phase3a/salary_origin_audit.csv and
salary_origin_summary.json.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from auction_model import data_pipeline

OUT_CSV = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "salary_origin_audit.csv"
OUT_JSON = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "salary_origin_summary.json"


def classify(row) -> tuple[str, str, float, bool]:
    """Returns (origin, evidence, reliability[0-1], included_in_market_calibration)."""
    if bool(row.get("is_tagged_2025")):
        return (
            "FRANCHISE_TAG", "is_tagged_2025=True (confirmed +$5 franchise-tag escalation delta)",
            0.4, False,  # a tag price is a keeper-rule escalation of a PRIOR price, not a fresh auction result
        )
    if row["salary_origin"] == "KEEPER_ESCALATION_CONFIRMED":
        return (
            "KEEPER_ESCALATION", "salary_origin=KEEPER_ESCALATION_CONFIRMED (+$10 standard escalation delta confirmed)",
            0.4, False,  # same reasoning -- this is last year's price plus a fixed rule bump, not new market info
        )
    if bool(row.get("paul_rule_eligible")):
        return (
            "PAUL_RULE", "paul_rule_eligible=True (kept at prior salary under the Paul Rule, unescalated)",
            0.3, False,
        )
    if "no salary on record" in str(row.get("notes", "")):
        return ("UNKNOWN", "no salary_2025 value recorded at all", 0.0, False)
    if pd.isna(row["salary_2025"]):
        return ("UNKNOWN", "missing salary value", 0.0, False)
    if row["salary_2025"] == 1.0:
        return (
            "ADMINISTRATIVE_DOLLAR_ONE", (
                "$1 salary with no keeper/tag marker -- ambiguous (a genuine uncontested competitive-auction "
                "winner also settles at $1 in this league's rules) but NOT confirmed as a real competitive "
                "result either; treated as unreliable for calibration by default per the phase-3A instruction "
                "'unknown $1 prices receive zero weight by default.'"
            ),
            0.0, False,
        )
    return (
        "UNKNOWN", (
            f"${row['salary_2025']:.0f}, no keeper/tag/Paul-Rule marker -- plausibly a competitive-auction "
            f"result (this is what an unadorned salary in a keeper league usually represents) but NOT "
            f"independently confirmed by any acquisition-method record in this repo."
        ),
        0.5, True,  # "Unknown non-$1 prices receive limited weight" -- included, but at half weight
    )


def main() -> None:
    salaries, _ = data_pipeline.load_historical_salaries(BASE_DIR / "data" / "historical_salaries_2025_raw.csv")

    rows = []
    for _, row in salaries.iterrows():
        origin, evidence, reliability, included = classify(row)
        rows.append({
            "player": row["player"], "team": row["team"], "position": row["position"],
            "salary": row["salary_2025"], "origin": origin, "origin_evidence": evidence,
            "reliability": reliability, "included_in_market_calibration": included,
            "notes": row.get("notes", ""),
        })

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    origin_counts = {}
    for r in rows:
        origin_counts[r["origin"]] = origin_counts.get(r["origin"], 0) + 1
    n_included = sum(1 for r in rows if r["included_in_market_calibration"])

    summary = {
        "total_rows": len(rows),
        "origin_counts": origin_counts,
        "n_included_in_market_calibration": n_included,
        "n_excluded_from_market_calibration": len(rows) - n_included,
        "note": (
            "No draft-day transaction log exists in this repo, so no row is labeled "
            "COMPETITIVE_AUCTION with full confidence. Only unadorned, non-$1, non-keeper/tag/Paul-Rule "
            "salaries are included in market calibration, at reliability 0.5 (limited weight) -- "
            "consistent with 'only confirmed competitive-auction prices receive full weight; unknown "
            "non-$1 prices receive limited weight; unknown $1 prices receive zero weight by default.'"
        ),
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2))

    print(f"Wrote {OUT_CSV} ({len(rows)} rows)")
    print(f"Wrote {OUT_JSON}")
    print(f"\nOrigin breakdown: {origin_counts}")
    print(f"Included in market calibration (reliability-weighted): {n_included} / {len(rows)}")


if __name__ == "__main__":
    main()
