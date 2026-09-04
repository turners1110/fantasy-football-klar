#!/usr/bin/env python3
"""Phase 3B item 6: current public-market benchmarks.

Searched this repo for existing 2026 public rankings/projections/tiers/
auction values before adding anything new. Found:
  - FantasyPros_2026_Draft_ALL_Rankings.csv -- real external public
    RANK + TIER + positional rank (e.g. "WR1"). No dollar auction values.
  - data/projections_2026.csv -- this repo's own projection set (used
    for the "existing projection-based neutral curve").
  - output_mock_draft_snapshot/veteran_auction_price_sheet.csv --
    this repo's own VBD-priced sheet (also "existing projection-based
    neutral curve" -- suggested_auction_price).
No genuine external PUBLIC AUCTION-VALUE list (real dollar figures from
a public source) exists anywhere in this repo (confirmed by search of
inputs/, data/, output*/ directories). Per the instruction ("if public
auction values exist, normalize them..."), that curve is reported
NOT_AVAILABLE rather than fabricated -- the two other required curves
are built instead:
  1. PUBLIC_AUCTION_VALUE -- NOT_AVAILABLE (no source exists)
  2. PUBLIC_RANK_TIER -- built from FantasyPros_2026_Draft_ALL_Rankings.csv
  3. EXISTING_PROJECTION_NEUTRAL -- this repo's own suggested_auction_price

NORMALIZATION PROCEDURE (item 6's own steps, applied to the rank/tier
curve so it's comparable to the existing neutral curve's own
methodology): remove keepers/college-rights holds from the pool first
(load_confirmed_pool_and_teams already does this), reserve $1 for every
league-wide open roster slot (108 slots = 12 teams x (15 - 6 keepers)),
then scale a rank-based value index (1/rank, a standard, transparent,
non-fitted decay -- NOT an arbitrary multiplier) proportionally across
the remaining discretionary cash ($3,021 total budget - $108 reserved =
$2,913). This is the SAME $1-floor-plus-proportional-share structure
this repo's own pricing already uses (auction_model.valuation.
_proportional_dollars), applied to a different value signal, so the two
curves are methodologically comparable.

Writes:
  outputs/auction_rebuild/phase3b/public_market_benchmarks.csv
  outputs/auction_rebuild/phase3b/benchmark_summary.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from auction_model import config
from auction_model.confirmed_keeper_pipeline import normalize_name
from mock_draft.data import load_confirmed_pool_and_teams

FANTASYPROS_PATH = BASE_DIR / "FantasyPros_2026_Draft_ALL_Rankings.csv"
SNAPSHOT_PRICE_SHEET = BASE_DIR / "output_mock_draft_snapshot" / "veteran_auction_price_sheet.csv"
OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3b"


def _curve_metrics(df: pd.DataFrame, price_col: str, n_drafted: int) -> dict:
    """df must have columns [player, position, price_col]. Only the top
    n_drafted players by price are treated as 'drafted' for concentration
    and position-spend purposes (everyone else is a $1 background-pool
    price, consistent with how a real 108-pick auction actually plays out)."""
    ranked = df.sort_values(price_col, ascending=False).reset_index(drop=True)
    drafted = ranked.head(n_drafted)
    prices = drafted[price_col].tolist()
    total_spend = sum(prices)
    top12 = sum(sorted(prices, reverse=True)[:12])
    top24 = sum(sorted(prices, reverse=True)[:24])
    pos_spend = drafted.groupby("position")[price_col].sum().to_dict()
    return {
        "top_12_share": round(top12 / total_spend, 4) if total_spend else None,
        "top_24_share": round(top24 / total_spend, 4) if total_spend else None,
        "position_spending": {k: round(v, 2) for k, v in pos_spend.items()},
        "one_dollar_count": int(sum(1 for p in prices if p <= 1.0)),
        "maximum_price": round(max(prices), 2) if prices else None,
        "median_drafted_price": round(float(np.median(prices)), 2) if prices else None,
        "total_spend": round(total_spend, 2),
        "players_priced": len(df),
    }


def main() -> None:
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    states = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv")
    total_budget = float(states["primary_auction_budget"].sum())
    n_open_slots = int((15 - states["n_veteran_keepers"]).sum())
    discretionary_cash = total_budget - config.MIN_PRICE * n_open_slots
    print(f"League budget ${total_budget:.0f}, {n_open_slots} open roster slots, "
          f"${discretionary_cash:.0f} discretionary cash after $1-per-slot reserve.")

    pool_df = pd.DataFrame([
        {"player": p.name, "position": p.position, "_key": normalize_name(p.name)}
        for p in players.values()
    ])

    results = {}

    # --- Curve 1: PUBLIC_AUCTION_VALUE -- not available ---
    results["PUBLIC_AUCTION_VALUE"] = {
        "status": "NOT_AVAILABLE",
        "reason": "No external public dollar-value auction list exists anywhere in this repo (searched "
                  "inputs/, data/, output*/). Not fabricated.",
    }

    # --- Curve 2: PUBLIC_RANK_TIER ---
    fp = pd.read_csv(FANTASYPROS_PATH)
    fp["_key"] = fp["PLAYER NAME"].map(normalize_name)
    fp_ranked = fp[["_key", "RK", "TIERS"]].drop_duplicates("_key")
    rank_pool = pool_df.merge(fp_ranked, on="_key", how="left")
    matched = rank_pool["RK"].notna().sum()
    print(f"PUBLIC_RANK_TIER: matched {matched} of {len(rank_pool)} auction-eligible players to FantasyPros ranks.")

    rank_pool["_value_index"] = np.where(rank_pool["RK"].notna(), 1.0 / rank_pool["RK"].astype(float), 0.0)
    total_index = rank_pool["_value_index"].sum()
    rank_pool["public_rank_tier_price"] = config.MIN_PRICE + (
        rank_pool["_value_index"] / total_index * discretionary_cash if total_index else 0.0
    )
    results["PUBLIC_RANK_TIER"] = {
        "status": "BUILT",
        "matched_to_fantasypros": int(matched), "unmatched": int(len(rank_pool) - matched),
        **_curve_metrics(rank_pool, "public_rank_tier_price", n_open_slots),
    }

    # --- Curve 3: EXISTING_PROJECTION_NEUTRAL (this repo's own sheet) ---
    neutral = pd.read_csv(SNAPSHOT_PRICE_SHEET)
    neutral = neutral[neutral["suggested_auction_price"].notna()][["player", "position", "suggested_auction_price"]]
    results["EXISTING_PROJECTION_NEUTRAL"] = {
        "status": "BUILT",
        **_curve_metrics(neutral, "suggested_auction_price", n_open_slots),
    }

    # --- Write CSV (one row per curve; position_spending flattened) ---
    rows = []
    for curve, r in results.items():
        row = {"curve": curve, "status": r["status"]}
        if r["status"] == "BUILT":
            row.update({
                "top_12_share": r["top_12_share"], "top_24_share": r["top_24_share"],
                "one_dollar_count": r["one_dollar_count"], "maximum_price": r["maximum_price"],
                "median_drafted_price": r["median_drafted_price"], "total_spend": r["total_spend"],
                "players_priced": r["players_priced"],
            })
            for pos in ("QB", "RB", "WR", "TE"):
                row[f"{pos}_spending"] = r["position_spending"].get(pos, 0.0)
        rows.append(row)
    csv_path = OUT_DIR / "public_market_benchmarks.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    summary_path = OUT_DIR / "benchmark_summary.json"
    summary_path.write_text(json.dumps({
        "league_budget": total_budget, "n_open_slots": n_open_slots, "discretionary_cash": discretionary_cash,
        "curves": results,
    }, indent=2))

    print(f"Wrote {csv_path}")
    print(f"Wrote {summary_path}")
    for curve, r in results.items():
        if r["status"] == "BUILT":
            print(f"\n{curve}: top12={r['top_12_share']:.2%}, top24={r['top_24_share']:.2%}, "
                  f"max=${r['maximum_price']:.0f}, median=${r['median_drafted_price']:.2f}, "
                  f"position_spending={r['position_spending']}")
        else:
            print(f"\n{curve}: {r['status']} -- {r.get('reason')}")


if __name__ == "__main__":
    main()
