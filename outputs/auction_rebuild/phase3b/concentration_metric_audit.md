# Concentration Metric Audit (Phase 3B item 4)

## Root cause

`scripts/build_market_clearing_diagnostics.py` (phase 3A) computed top-12/24 spend
share by pooling every sale price from all 200 simulated auctions into one
array, sorting it, and dividing the 12 single highest prices in that pooled array
by the SUM OF ALL SALES ACROSS EVERY AUCTION. The numerator (12 sales) does not
scale with the number of auctions pooled; the denominator does. This mechanically
drives the reported share toward zero as more seeds are added -- exactly the
implausibly low figure the user flagged (2.95% on 40 seeds).

## Manual reconciliation (seed 0)

- 108 organic sales, total spend $2880.66
- Top-12 sale prices (descending): [256.32, 256.0, 234.36111111111114, 202.0, 202.0, 177.0, 116.0, 76.02543888888891, 72.5398888888889, 69.79463678576761, 61.0, 61.0]
- Top-12 sum: $1784.04 / $2880.66 = **61.93%**
- Top-24 sum: $2363.42 / $2880.66 = **82.04%**
- Full sorted sale list with cumulative share: `concentration_manual_reconciliation.csv`

## Corrected metric (per-auction, 200 seeds)

| | Mean | Median | Min | Max |
|---|---|---|---|---|
| top_12_share | 0.6528 | 0.6530 | 0.4363 | 0.8568 |
| top_24_share | 0.8315 | 0.8357 | 0.6405 | 0.9417 |

## Old (buggy, pooled) calculation reproduced for comparison

- Pooled top-12 share: 0.0060 (0.60%)
- Pooled top-24 share: 0.0119 (1.19%)
- Correct per-auction mean top-12 share: 0.6528 (65.28%)
- **The corrected figure is 109.3x higher than the buggy pooled figure**,
  confirming the aggregation-across-simulations diagnosis exactly.

## Synthetic worked example (per item 4's spec)

Sale prices: 100, 80, 60, 40, 20. Total: 300. Top-2 sum: 180. Top-2 share: 60.0%.
Verified by `tests/test_phase3b_concentration.py`.
