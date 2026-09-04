#!/usr/bin/env python3
"""Phase 3C item 12: audit every row in data/projections_2026.csv with no
projected_points value (158 rows, per phase 3B's projection_position_audit.csv),
classifying each by real relevance rather than spending equal engineering
effort on every one of them.

Writes outputs/auction_rebuild/phase3c/missing_projection_audit.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from auction_model.confirmed_keeper_pipeline import normalize_name
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.points import build_points_lookup, compute_fallback_ratio

OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3c" / "missing_projection_audit.csv"
FANTASYPROS_PATH = BASE_DIR / "FantasyPros_2026_Draft_ALL_Rankings.csv"


def classify(row) -> str:
    if row["identity_issue"]:
        return "IDENTITY_ISSUE"
    if not row["auction_eligible"]:
        return "UNLIKELY_TO_BE_DRAFTED"
    if row["public_rank"] is not None and row["public_rank"] <= 150:
        return "RELEVANT_AUCTION_TARGET"
    if row["auction_eligible"] and row["base_value"] and row["base_value"] > 5:
        return "RELEVANT_AUCTION_TARGET"
    if row["auction_eligible"]:
        return "LIKELY_DOLLAR_ONE_PLAYER"
    return "MISSING_SOURCE_ISSUE"


def main() -> None:
    proj = pd.read_csv(BASE_DIR / "data" / "projections_2026.csv")
    missing = proj[proj["projected_points"].isna()].copy()
    print(f"{len(missing)} of {len(proj)} projection rows have no projected_points value.")

    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    base_value_lookup = {normalize_name(p.name): p.base_value for p in players.values()}
    eligible_keys = set(base_value_lookup.keys())

    fp = pd.read_csv(FANTASYPROS_PATH)
    fp["_key"] = fp["PLAYER NAME"].map(normalize_name)
    fp = fp.sort_values("RK").drop_duplicates("_key")
    fp_lookup = fp.set_index("_key")[["RK", "TIERS"]].to_dict("index")

    points_lookup = build_points_lookup()
    prices = pd.read_csv(BASE_DIR / "output_mock_draft_snapshot" / "veteran_auction_price_sheet.csv")
    prices = prices[prices["suggested_auction_price"].notna()][["player", "position", "suggested_auction_price"]].rename(
        columns={"suggested_auction_price": "base_value"})
    fallback_ratio = compute_fallback_ratio(prices, points_lookup)

    # Identity issue: same normalized name appears more than once anywhere
    # in the FULL projections file (missing-row or not) -- a real signal
    # something is duplicated/misparsed, not a coincidence.
    all_keys = proj["player"].map(normalize_name)
    dupe_keys = set(all_keys[all_keys.duplicated(keep=False)])

    rows = []
    for _, r in missing.iterrows():
        key = normalize_name(r["player"])
        fp_row = fp_lookup.get(key)
        eligible = key in eligible_keys
        base_value = base_value_lookup.get(key)
        fallback_pts, is_real = None, False
        if eligible and base_value is not None:
            ratio_cap = fallback_ratio.get(r["position"], fallback_ratio.get("_global", (1.0, float("inf"))))
            fallback_pts = round(max(0.0, base_value) * ratio_cap[0], 1)
            fallback_pts = min(fallback_pts, ratio_cap[1])

        row = {
            "player": r["player"], "position": r["position"], "nfl_team": r.get("nfl_team", ""),
            "auction_eligible": eligible, "identity_issue": key in dupe_keys,
            "public_rank": int(fp_row["RK"]) if fp_row is not None else None,
            "public_tier": int(fp_row["TIERS"]) if fp_row is not None else None,
            "base_value": base_value,
            "fallback_source": "mock_draft.points.points_for (position-specific ratio, capped)" if eligible else None,
            "fallback_points": fallback_pts,
            "fallback_confidence": "LOW (imputed, no real projection)" if eligible else "N/A (not auction-eligible)",
        }
        row["classification"] = classify(row)
        # HEURISTIC PROXY, not a measured simulation figure (that would
        # require a dedicated simulation batch per missing player, out of
        # scope given how many of these are irrelevant by classification):
        # coarse draft-probability estimate by classification bucket only.
        row["expected_draft_probability_HEURISTIC"] = {
            "RELEVANT_AUCTION_TARGET": 0.9, "LIKELY_DOLLAR_ONE_PLAYER": 0.35,
            "UNLIKELY_TO_BE_DRAFTED": 0.02, "IDENTITY_ISSUE": 0.05, "MISSING_SOURCE_ISSUE": 0.1,
        }[row["classification"]]
        rows.append(row)

    fieldnames = ["player", "position", "nfl_team", "auction_eligible", "public_rank", "public_tier",
                  "base_value", "fallback_source", "fallback_points", "fallback_confidence",
                  "classification", "identity_issue", "expected_draft_probability_HEURISTIC"]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in fieldnames})

    print(f"Wrote {OUT_PATH}")
    counts = pd.Series([r["classification"] for r in rows]).value_counts()
    print(counts)


if __name__ == "__main__":
    main()
