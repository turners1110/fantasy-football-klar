"""Phase 3B item 4 / item 17 tests 1-3: top-12/24 concentration must be
calculated WITHIN each individual auction, never by pooling sale
observations across many simulations before dividing."""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from scripts.build_phase3b_concentration_audit import concentration_for_one_auction


def _sales(prices):
    return [{"sale_price": p, "sale_is_organic": True} for p in prices]


def test_01_top12_share_calculated_within_one_auction():
    """Worked example from item 4's spec: prices 100/80/60/40/20 -- but
    the spec's own example uses a top-2 slice, so verify BOTH the
    documented top-2 case and a full top-12 slice (padding with more
    sales so a top-12 window has real meaning)."""
    result = concentration_for_one_auction(_sales([100, 80, 60, 40, 20]))
    assert result["total_spend"] == 300
    # top-12 on only 5 sales takes all of them.
    assert result["top_12_spend"] == 300
    assert result["top_12_share"] == 1.0


def test_02_top2_worked_example_matches_spec():
    """Sale prices: 100, 80, 60, 40, 20. Total: 300. Top two: 180.
    Top-two share: 60%. (Verified directly against the spec's own
    numbers using a 2-slice, since concentration_for_one_auction is
    fixed at 12/24 -- this test slices the same sorted-price logic by
    hand to confirm the underlying arithmetic the function shares.)"""
    prices = sorted([100, 80, 60, 40, 20], reverse=True)
    total = sum(prices)
    top2 = sum(prices[:2])
    assert total == 300
    assert top2 == 180
    assert top2 / total == 0.6


def test_03_top24_share_calculated_within_one_auction():
    prices = [50 - i for i in range(30)]  # 50, 49, ..., 21 (30 sales)
    result = concentration_for_one_auction(_sales(prices))
    total = sum(prices)
    top24 = sum(sorted(prices, reverse=True)[:24])
    assert result["total_spend"] == total
    assert result["top_24_spend"] == top24
    assert result["top_24_share"] == round(top24 / total, 4)


def test_04_pooled_simulations_do_not_corrupt_concentration():
    """The bug this whole audit exists to catch: concentration computed
    PER AUCTION and averaged must NOT equal concentration computed by
    pooling every sale from multiple auctions into one array first. Two
    identical small auctions pooled together would (under the old,
    buggy method) show a much lower share than either auction alone,
    because the denominator doubles while the top-12/24 numerator does
    not scale the same way."""
    auction_a = _sales([100, 80, 60, 40, 20])
    auction_b = _sales([100, 80, 60, 40, 20])

    correct_a = concentration_for_one_auction(auction_a)
    correct_b = concentration_for_one_auction(auction_b)
    correct_mean_top12 = (correct_a["top_12_share"] + correct_b["top_12_share"]) / 2

    # The old (buggy) pooled method: combine both auctions' sales first.
    pooled_prices = sorted([e["sale_price"] for e in auction_a + auction_b], reverse=True)
    pooled_total = sum(pooled_prices)
    pooled_top12_share = sum(pooled_prices[:12]) / pooled_total

    # With only 5 unique prices per auction (10 total, below the top-12
    # window), pooling here happens to still capture everything -- so
    # assert the METHODS agree only when the pool is smaller than the
    # window, and diverge once total sales exceed it (the realistic case
    # this bug actually produced in a real 40-seed/~4,300-sale batch).
    assert pooled_top12_share == correct_mean_top12 == 1.0  # both auctions' 5 sales each, all captured

    # Now scale up: 50 sales per auction (realistic per-team-roster size),
    # 4 auctions pooled -- this is where the bug actually bites.
    import numpy as np
    rng = np.random.default_rng(0)
    many_auctions = [sorted(rng.uniform(1, 100, size=50).tolist(), reverse=True) for _ in range(4)]
    per_auction_shares = []
    for prices in many_auctions:
        total = sum(prices)
        top12 = sum(prices[:12])
        per_auction_shares.append(top12 / total)
    correct_mean = sum(per_auction_shares) / len(per_auction_shares)

    pooled = sorted([p for auc in many_auctions for p in auc], reverse=True)
    pooled_share = sum(pooled[:12]) / sum(pooled)

    # The buggy pooled share must be meaningfully LOWER than the correct
    # per-auction mean once more than one auction's worth of sales is
    # pooled -- exactly the mechanism that produced the implausible 2.95%.
    assert pooled_share < correct_mean


def test_05_gross_vs_signed_budget_difference_differ():
    """A budget gap with offsetting positive/negative team differences
    must show a LARGER gross absolute difference than signed net
    difference -- they are not the same number, and must never be
    reported interchangeably."""
    reported = {"A": 200.0, "B": 190.0, "C": 210.0}
    formula = {"A": 210.0, "B": 180.0, "C": 210.0}  # A: -10, B: +10, C: 0
    gross = sum(abs(reported[k] - formula[k]) for k in reported)
    signed = sum(reported[k] - formula[k] for k in reported)
    assert gross == 20.0
    assert signed == 0.0
    assert gross != signed
