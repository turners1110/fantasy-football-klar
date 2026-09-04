#!/usr/bin/env python3
"""Phase 3B item 4 (and the root cause of item 1): rebuild top-12/24
concentration metrics from first principles, PER AUCTION, not pooled.

ROOT CAUSE (confirmed by manual reconciliation, see
concentration_metric_audit.md): the phase 3A script
(scripts/build_market_clearing_diagnostics.py) computed
"simulated_top12_spend_share" by pooling every sale price from all
N_SEEDS auctions into ONE array, sorting it, taking the 12 single
highest prices across the ENTIRE batch, and dividing by the SUM OF ALL
SALES ACROSS EVERY AUCTION. The numerator (12 sales) does not scale with
the number of auctions pooled, but the denominator does -- so pooling
more seeds mechanically drives the reported share toward zero. This is
exactly the bug the user flagged: "might reflect aggregation across all
simulation rows rather than calculation within each auction." Fixed here
by computing top-12/24 share WITHIN each individual auction, then
averaging (and taking percentiles) ACROSS auctions.

Writes:
  outputs/auction_rebuild/phase3b/concentration_by_simulation.csv
  outputs/auction_rebuild/phase3b/concentration_manual_reconciliation.csv
  outputs/auction_rebuild/phase3b/concentration_metric_audit.md
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from mock_draft.auction import run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3b"
N_SEEDS = 200
MANUAL_SEED = 0


def concentration_for_one_auction(log: list[dict]) -> dict:
    """The correct, per-auction procedure (item 4's 8 steps):
    1. Filter to organic completed sales (every sale in this engine is
       organic by construction -- phase 2 removed forced-final-slot
       pricing entirely; see mock_draft/auction.py's module docstring).
    2. Sum total auction spending.
    3. Sort sales by winning price, descending.
    4. Sum the twelve highest sale prices.
    5. Divide by total spending -> top_12_share.
    6. Repeat for the top twenty-four.
    7. (Caller stores the per-auction result.)
    8. (Caller averages/percentiles across auctions.)
    """
    organic = [e for e in log if e.get("sale_is_organic", True) and e["sale_price"] is not None]
    prices = sorted((float(e["sale_price"]) for e in organic), reverse=True)
    total_spend = sum(prices)
    top12 = sum(prices[:12])
    top24 = sum(prices[:24])
    return {
        "total_spend": round(total_spend, 2),
        "top_12_spend": round(top12, 2),
        "top_12_share": round(top12 / total_spend, 4) if total_spend else 0.0,
        "top_24_spend": round(top24, 2),
        "top_24_share": round(top24 / total_spend, 4) if total_spend else 0.0,
        "highest_price": prices[0] if prices else 0.0,
        "twelfth_highest_price": prices[11] if len(prices) > 11 else (prices[-1] if prices else 0.0),
        "twenty_fourth_highest_price": prices[23] if len(prices) > 23 else (prices[-1] if prices else 0.0),
        "organic_sales": len(organic),
        "one_dollar_sales": sum(1 for p in prices if p <= 1.0),
    }


def main() -> None:
    players, teams_template, _ = load_confirmed_pool_and_teams(budget_scenario="primary")

    # --- Manual reconciliation for ONE seed, every sale price visible ---
    rng = np.random.default_rng(MANUAL_SEED)
    manual_log, _ = run_single_auction(players, teams_template, rng)
    manual_rows = sorted(
        [{"player": e["player"], "position": e["position"], "winning_team": e["winning_team"],
          "sale_price": e["sale_price"], "bidder_count": e["bidder_count"]} for e in manual_log],
        key=lambda r: r["sale_price"], reverse=True,
    )
    manual_total = sum(r["sale_price"] for r in manual_rows)
    running = 0.0
    for i, r in enumerate(manual_rows):
        running += r["sale_price"]
        r["rank_by_price"] = i + 1
        r["cumulative_spend"] = round(running, 2)
        r["cumulative_share"] = round(running / manual_total, 4) if manual_total else 0.0
        r["in_top_12"] = i < 12
        r["in_top_24"] = i < 24

    manual_path = OUT_DIR / "concentration_manual_reconciliation.csv"
    with manual_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(manual_rows[0].keys()))
        w.writeheader()
        w.writerows(manual_rows)

    manual_result = concentration_for_one_auction(manual_log)
    print(f"=== Manual reconciliation, seed {MANUAL_SEED} ===")
    print(f"Total sales: {len(manual_rows)}, total spend: ${manual_total:.2f}")
    print(f"Top-12 sale prices: {[r['sale_price'] for r in manual_rows[:12]]}")
    print(f"Top-12 sum: ${manual_result['top_12_spend']:.2f}, share: {manual_result['top_12_share']:.4f}")
    print(f"Top-24 sum: ${manual_result['top_24_spend']:.2f}, share: {manual_result['top_24_share']:.4f}")
    print(f"Wrote {manual_path}")

    # --- Per-auction concentration across N_SEEDS auctions (one pass; the
    # logs are reused below to also reproduce the OLD buggy pooled
    # calculation, so the auction engine only has to run N_SEEDS times). ---
    rows = []
    all_sales_pooled = []
    for seed in range(N_SEEDS):
        rng = np.random.default_rng(seed)
        log, _ = run_single_auction(players, teams_template, rng)
        result = concentration_for_one_auction(log)
        result["simulation_id"] = seed
        result["seed"] = seed
        rows.append(result)
        all_sales_pooled.extend(e["sale_price"] for e in log)

    fieldnames = ["simulation_id", "seed", "total_spend", "top_12_spend", "top_12_share",
                  "top_24_spend", "top_24_share", "highest_price", "twelfth_highest_price",
                  "twenty_fourth_highest_price", "organic_sales", "one_dollar_sales"]
    by_sim_path = OUT_DIR / "concentration_by_simulation.csv"
    with by_sim_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in fieldnames})

    df = pd.DataFrame(rows)
    correct_mean_top12 = df["top_12_share"].mean()
    correct_median_top12 = df["top_12_share"].median()
    correct_mean_top24 = df["top_24_share"].mean()

    # --- Reproduce the OLD (buggy) pooled calculation for direct comparison ---
    pooled_sorted = sorted(all_sales_pooled, reverse=True)
    pooled_total = sum(all_sales_pooled)
    buggy_top12_share = sum(pooled_sorted[:12]) / pooled_total if pooled_total else 0.0
    buggy_top24_share = sum(pooled_sorted[:24]) / pooled_total if pooled_total else 0.0

    audit_md = f"""# Concentration Metric Audit (Phase 3B item 4)

## Root cause

`scripts/build_market_clearing_diagnostics.py` (phase 3A) computed top-12/24 spend
share by pooling every sale price from all {N_SEEDS} simulated auctions into one
array, sorting it, and dividing the 12 single highest prices in that pooled array
by the SUM OF ALL SALES ACROSS EVERY AUCTION. The numerator (12 sales) does not
scale with the number of auctions pooled; the denominator does. This mechanically
drives the reported share toward zero as more seeds are added -- exactly the
implausibly low figure the user flagged (2.95% on 40 seeds).

## Manual reconciliation (seed {MANUAL_SEED})

- {len(manual_rows)} organic sales, total spend ${manual_total:.2f}
- Top-12 sale prices (descending): {[r['sale_price'] for r in manual_rows[:12]]}
- Top-12 sum: ${manual_result['top_12_spend']:.2f} / ${manual_total:.2f} = **{manual_result['top_12_share']:.2%}**
- Top-24 sum: ${manual_result['top_24_spend']:.2f} / ${manual_total:.2f} = **{manual_result['top_24_share']:.2%}**
- Full sorted sale list with cumulative share: `concentration_manual_reconciliation.csv`

## Corrected metric (per-auction, {N_SEEDS} seeds)

| | Mean | Median | Min | Max |
|---|---|---|---|---|
| top_12_share | {correct_mean_top12:.4f} | {correct_median_top12:.4f} | {df['top_12_share'].min():.4f} | {df['top_12_share'].max():.4f} |
| top_24_share | {correct_mean_top24:.4f} | {df['top_24_share'].median():.4f} | {df['top_24_share'].min():.4f} | {df['top_24_share'].max():.4f} |

## Old (buggy, pooled) calculation reproduced for comparison

- Pooled top-12 share: {buggy_top12_share:.4f} ({buggy_top12_share:.2%})
- Pooled top-24 share: {buggy_top24_share:.4f} ({buggy_top24_share:.2%})
- Correct per-auction mean top-12 share: {correct_mean_top12:.4f} ({correct_mean_top12:.2%})
- **The corrected figure is {correct_mean_top12 / buggy_top12_share:.1f}x higher than the buggy pooled figure**,
  confirming the aggregation-across-simulations diagnosis exactly.

## Synthetic worked example (per item 4's spec)

Sale prices: 100, 80, 60, 40, 20. Total: 300. Top-2 sum: 180. Top-2 share: 60.0%.
Verified by `tests/test_phase3b_concentration.py`.
"""
    audit_path = OUT_DIR / "concentration_metric_audit.md"
    audit_path.write_text(audit_md)

    print(f"\n=== Corrected per-auction concentration ({N_SEEDS} seeds) ===")
    print(f"Mean top-12 share: {correct_mean_top12:.4f} ({correct_mean_top12:.2%})")
    print(f"Median top-12 share: {correct_median_top12:.4f}")
    print(f"Mean top-24 share: {correct_mean_top24:.4f} ({correct_mean_top24:.2%})")
    print(f"\nOLD buggy pooled top-12 share: {buggy_top12_share:.4f} ({buggy_top12_share:.2%})")
    print(f"Correction factor: {correct_mean_top12 / buggy_top12_share:.1f}x")
    print(f"\nWrote {by_sim_path}")
    print(f"Wrote {audit_path}")


if __name__ == "__main__":
    main()
