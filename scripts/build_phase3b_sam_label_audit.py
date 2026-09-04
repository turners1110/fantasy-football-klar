#!/usr/bin/env python3
"""Phase 3B item 14: audit the phase 3A Sam sanity-test labels
("Premium WR: Rashee Rice", "Premium TE: TJ Hockenson", "Premium RB:
Josh Jacobs") against real public rank/tier and simulated market price,
rather than internal marginal-utility points alone -- and make the
units unmistakable: 179.43 / 92.79 etc. in the phase 3A report were
UTILITY POINTS, never dollars, and must never be read as prices.

Runs a real market simulation batch to get each watchlist player's
CONDITIONAL sale-price distribution (item 10's own rule: percentiles
computed only over sold outcomes, never $0-filled), then reports it
alongside FantasyPros public rank/tier for the same players.

Writes outputs/auction_rebuild/phase3b/sam_label_audit.csv
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from auction_model.confirmed_keeper_pipeline import normalize_name
from mock_draft.auction import run_single_auction
from mock_draft.cash_value import marginal_dollar_value
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.legal_lineup import partial_lineup_value

N_SEEDS = 60
OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3b" / "sam_label_audit.csv"
FANTASYPROS_PATH = BASE_DIR / "FantasyPros_2026_Draft_ALL_Rankings.csv"

WATCHLIST = [
    "Geno Smith", "Dak Prescott", "Josh Allen", "Rashee Rice",
    "TJ Hockenson", "Josh Jacobs", "Terry McLaurin", "Bucky Irving",
]


def main() -> None:
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    sam = teams["Sam"]
    base_value = partial_lineup_value(sam.roster)

    fp = pd.read_csv(FANTASYPROS_PATH)
    fp["_key"] = fp["PLAYER NAME"].map(normalize_name)
    fp = fp.sort_values("RK").drop_duplicates("_key")  # keep the higher-ranked (lower RK) entry on any collision
    fp_lookup = fp.set_index("_key")[["RK", "TIERS", "POS"]].to_dict("index")

    # Run a real market batch, tracking every sale price for watchlist players.
    watch_keys = {normalize_name(n) for n in WATCHLIST}
    sale_prices = defaultdict(list)
    n_auctions = 0
    for seed in range(N_SEEDS):
        rng = np.random.default_rng(seed)
        log, _ = run_single_auction(players, teams, rng)
        n_auctions += 1
        for e in log:
            key = normalize_name(e["player"])
            if key in watch_keys:
                sale_prices[key].append(e["sale_price"])

    rows = []
    dollar_rate = marginal_dollar_value(sam, players)
    for name in WATCHLIST:
        key = normalize_name(name)
        p = players.get(name)
        if p is None:
            continue
        fp_row = fp_lookup.get(key)
        prices = sale_prices.get(key, [])
        n_sold = len(prices)
        draft_probability = round(n_sold / n_auctions, 4)

        assumed_price = float(p.base_value)
        after_roster = sam.roster + [(p.name, p.position, assumed_price, p.projected_points)]
        marginal_utility = round(partial_lineup_value(after_roster) - base_value, 2)
        rows.append({
            "player": name,
            "position": p.position,
            "projected_points": p.projected_points,
            "public_rank": int(fp_row["RK"]) if fp_row is not None else None,
            "public_tier": int(fp_row["TIERS"]) if fp_row is not None else None,
            "public_position_rank": fp_row["POS"] if fp_row is not None else None,
            "assumed_purchase_price": round(assumed_price, 2),
            "draft_probability": draft_probability,
            "n_sale_observations": n_sold,
            "market_price_p50": round(float(np.median(prices)), 2) if n_sold >= 5 else None,
            "market_price_p75": round(float(np.percentile(prices, 75)), 2) if n_sold >= 5 else None,
            "marginal_lineup_points_UNITS_ARE_UTILITY_POINTS_NOT_DOLLARS": marginal_utility,
            "marginal_roster_utility_UNITS_ARE_UTILITY_POINTS_NOT_DOLLARS": marginal_utility,
            "budget_after_purchase_dollars": round(sam.budget_remaining - assumed_price, 2),
            "remaining_roster_slots_after_purchase": max(0, 15 - len(after_roster)),
        })

    fieldnames = list(rows[0].keys())
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {OUT_PATH} ({n_auctions} auctions simulated)\n")
    for r in rows:
        p50 = r["market_price_p50"]
        p50_str = f"${p50}" if p50 is not None else f"N/A (only {r['n_sale_observations']} sale observations)"
        print(f"{r['player']:16s} pos={r['position']} public_rank={r['public_rank']} tier={r['public_tier']} "
              f"draft_prob={r['draft_probability']:.0%} market_p50={p50_str} "
              f"marginal_utility_points={r['marginal_lineup_points_UNITS_ARE_UTILITY_POINTS_NOT_DOLLARS']}")

    print(
        "\nUNIT CLARIFICATION: 'marginal_lineup_points'/'marginal_roster_utility' above are the SAME kind of "
        "quantity as phase 3A's sam_sanity_tests.json 179.43/92.79/etc. figures -- UTILITY POINTS (a blend of "
        "legal-lineup points and bench-weighted option value), NEVER dollars. The only dollar figures in this "
        "table are assumed_purchase_price, market_price_p50/p75, and budget_after_purchase_dollars -- do not "
        "compare a *_points_NOT_DOLLARS field against a price field as if they were on the same scale."
    )


if __name__ == "__main__":
    main()
