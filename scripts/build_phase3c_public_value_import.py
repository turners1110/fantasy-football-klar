#!/usr/bin/env python3
"""Phase 3C item 4/5: import + normalize public auction values.

Automated retrieval was attempted for FantasyPros, Yahoo, ESPN, RotoWire,
RotoAlpha, FantasyNerds, DraftExpertPro, PropsBot, and Footballguys via
WebFetch. EVERY attempt returned EGRESS_BLOCKED -- confirmed as a total
network-egress-proxy restriction of this execution environment (tested
against 4 unrelated domains including espn.com, all blocked identically),
not a failure specific to any one source. WebSearch (a different tool,
not subject to the same block) DOES work and returned real, attributable
consensus dollar figures for 33 players plus a real position-spend-share
rule of thumb, saved verbatim in
data/external/auction_values_2026/websearch_consensus_2026.json. This is
disclosed as a LOWER-CONFIDENCE source than a verbatim single-page
scrape (WebSearch's answer is itself an AI synthesis across several
cited sites, not one verified table) -- treated accordingly (confidence
capped, never presented as an authoritative single source).

A manual-import CSV template
(data/external/auction_values_2026/manual_import_template.csv) is
provided for a human to complete with direct account/browser access to
any of the blocked sources, if higher-confidence coverage is wanted
later.

NORMALIZATION (item 5's 12 steps): the WebSearch figures are for a
generic "12 teams, $200 budget" format; this league is 12 teams / $400
gross format (this session's own $3,021 total REPORTED remaining
auction cash after keepers, from team_starting_states.csv) -- normalized
by (1) doubling the raw $200-budget dollar figures to a $400-equivalent
scale (a legitimate, documented step for THIS specific case since the
source's own budget is exactly half of this league's, not an arbitrary
multiplier), (2) removing this league's actual keepers/college-rights
holds from the priced pool, (3) reserving $1 per open roster slot
league-wide (108 slots), (4) rescaling the remaining (post-doubling)
surplus proportionally onto this league's actual $2,913 discretionary
cash pool so it sums exactly, preserving relative tier structure.

Writes:
  outputs/auction_rebuild/phase3c/public_source_manifest.json
  outputs/auction_rebuild/phase3c/public_value_normalization.csv
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from auction_model import config as auction_cfg
from auction_model.confirmed_keeper_pipeline import normalize_name
from mock_draft.data import load_confirmed_pool_and_teams

RAW_PATH = BASE_DIR / "data" / "external" / "auction_values_2026" / "websearch_consensus_2026.json"
MANIFEST_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3c" / "public_source_manifest.json"
NORM_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3c" / "public_value_normalization.csv"


def main() -> None:
    raw = json.loads(RAW_PATH.read_text())
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    eligible_keys = {normalize_name(p.name): p for p in players.values()}
    keeper_keys = {normalize_name(n) for t in teams.values() for n, _p, _pr, _pts in t.roster}

    states = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv")
    total_budget = float(states["primary_auction_budget"].sum())
    n_open_slots = int((15 - states["n_veteran_keepers"]).sum())
    discretionary_cash = total_budget - auction_cfg.MIN_PRICE * n_open_slots

    manifest = {
        "retrieval_date": raw["retrieval_date"],
        "attempted_sources": [
            {"source": s["title"], "url": s["url"], "status": s["fetch_status"],
             "reason": "EGRESS_BLOCKED by this execution environment's network proxy -- confirmed as a "
                       "total restriction (tested against espn.com, an unrelated domain, with identical result), "
                       "not a per-source failure." if s["fetch_status"] == "EGRESS_BLOCKED" else None}
            for s in raw["cited_sources"]
        ],
        "successful_retrieval_method": "WebSearch tool (result synthesis across cited sources, not a verbatim "
                                        "single-page scrape) -- see websearch_consensus_2026.json for the raw "
                                        "saved snapshot and full disclosure of this method's limitations.",
        "coverage": {
            "players_with_public_value": len(raw["players"]),
            "position_breakdown": pd.Series([p["position"] for p in raw["players"]]).value_counts().to_dict(),
            "label": "PARTIAL -- top-tier players only (33 of ~320 auction-eligible players), consensus-level "
                     "confidence, not a full-pool authoritative source.",
        },
        "manual_import_template": "data/external/auction_values_2026/manual_import_template.csv",
        "position_spend_share_consensus_cited": raw["position_spend_share_consensus_cited"],
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(f"Wrote {MANIFEST_PATH}")

    rows = []
    matched, unmatched, keeper_removed = 0, 0, 0
    doubled_total = 0.0
    matched_players = []
    for p in raw["players"]:
        key = normalize_name(p["player"])
        doubled = p["auction_value_200_budget"] * 2.0  # this league's $400 gross format is exactly 2x the source's $200
        if key in keeper_keys:
            keeper_removed += 1
            continue
        if key not in eligible_keys:
            unmatched += 1
            rows.append({
                "player": p["player"], "position": p["position"], "source": "WebSearch_consensus_2026",
                "raw_public_value": p["auction_value_200_budget"], "normalized_open_market_value": None,
                "keeper_adjusted_value": None, "source_settings": "12 teams, $200 budget (stated)",
                "normalization_factor": None, "scarcity_adjustment": None,
                "confidence": "UNMATCHED -- not found in this league's auction-eligible pool (may be a keeper, "
                              "college-rights hold, or name-matching gap)",
            })
            continue
        matched += 1
        doubled_total += doubled
        matched_players.append((p["player"], p["position"], doubled))

    scale = discretionary_cash / doubled_total if doubled_total else 0.0
    for player, position, doubled in matched_players:
        normalized = round(auction_cfg.MIN_PRICE + doubled * scale, 2)
        rows.append({
            "player": player, "position": position, "source": "WebSearch_consensus_2026",
            "raw_public_value": doubled / 2.0, "normalized_open_market_value": normalized,
            "keeper_adjusted_value": normalized,  # keepers already removed from this pool entirely
            "source_settings": "12 teams, $200 budget (stated) -> doubled to this league's $400 gross format",
            "normalization_factor": round(scale, 4),
            "scarcity_adjustment": "not separately applied -- see keeper_adjusted_position_benchmark.csv (phase 3B) "
                                    "for position-level keeper-driven scarcity",
            "confidence": "MODERATE -- WebSearch-synthesized consensus, not a verified single-source scrape; "
                          "top-tier players only",
        })

    fieldnames = ["player", "position", "source", "raw_public_value", "normalized_open_market_value",
                  "keeper_adjusted_value", "source_settings", "normalization_factor", "scarcity_adjustment",
                  "confidence"]
    NORM_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NORM_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {NORM_PATH}")
    print(f"Matched: {matched}, keeper-removed: {keeper_removed}, unmatched: {unmatched}")
    print(f"Discretionary cash: ${discretionary_cash:.2f}, doubled raw total (matched players): ${doubled_total:.2f}, "
          f"normalization_factor: {scale:.4f}")


if __name__ == "__main__":
    main()
