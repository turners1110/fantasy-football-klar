"""Phase 3D item 15: full-pool market-price distributions, and item 13's
extreme-price sanity review built on top of them.

Every price produced here is an UNCALIBRATED_SIMULATED_PRICE or
CALIBRATED_EXPECTED_MARKET_PRICE (see auction_model.labels) depending on
whether the calibration harness's selected parameters were applied to
config before this ran -- NEVER a real market price, and this module
never claims otherwise.

Methodology (disclosed): conditional percentiles (P10/P25/P50/P75/P90) are
computed ONLY over auctions where the player actually sold (a player who
never sells has no "price" to report a percentile of); draft_probability
uses ALL simulations (sold and unsold) as its denominator. A player with
fewer than MIN_SALE_OBSERVATIONS sold outcomes gets
INSUFFICIENT_SIMULATED_SALES instead of a fabricated percentile from too
few data points -- this exact minimum-observation design was established
in phase 3A's sam_label_audit.csv and is reused here at full-pool scale.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

from mock_draft.auction import run_single_auction
from mock_draft.valuation import compute_base_market_anchor

MIN_SALE_OBSERVATIONS = 20
INSUFFICIENT_LABEL = "INSUFFICIENT_SIMULATED_SALES"

# Item 13: fixed extreme-price review thresholds (not tuned per-player).
EXTREME_P50_THRESHOLD = 100.0
EXTREME_P90_THRESHOLD = 150.0
# A flagged price counts as "supported" only if an INDEPENDENT anchor
# (public or historical, not this same simulation) is within this
# relative tolerance of the simulated median -- otherwise the simulated
# price stands unsupported by any outside evidence.
SUPPORT_TOLERANCE = 0.40


def simulate_price_distributions(players: dict, teams: dict, n_sims: int, seed_offset: int = 0) -> pd.DataFrame:
    sold_prices: dict[str, list[float]] = defaultdict(list)
    sold_count: dict[str, int] = defaultdict(int)
    n_sims_run = 0

    for i in range(n_sims):
        rng = np.random.default_rng(seed_offset + i)
        log, _ = run_single_auction(players, teams, rng)
        n_sims_run += 1
        sold_this_sim = {e["player"] for e in log}
        for e in log:
            sold_prices[e["player"]].append(e["sale_price"])
        for name in sold_this_sim:
            sold_count[name] += 1

    rows = []
    for name, player in players.items():
        prices = sold_prices.get(name, [])
        n_sold = len(prices)
        draft_probability = round(n_sold / n_sims_run, 4) if n_sims_run else 0.0
        anchor = compute_base_market_anchor(player)

        row = {
            "player": name, "position": player.position,
            "n_sims": n_sims_run, "n_sold": n_sold, "draft_probability": draft_probability,
            "base_value": player.base_value,
            "public_anchor_value": player.public_anchor_value,
            "historical_anchor_value": player.historical_anchor_value,
            "base_market_anchor": round(anchor, 2),
        }
        if n_sold < MIN_SALE_OBSERVATIONS:
            row.update({
                "p10": INSUFFICIENT_LABEL, "p25": INSUFFICIENT_LABEL, "p50": INSUFFICIENT_LABEL,
                "p75": INSUFFICIENT_LABEL, "p90": INSUFFICIENT_LABEL, "mean_sold_price": INSUFFICIENT_LABEL,
                "extreme_price_flag": False, "extreme_price_review_verdict": None,
            })
        else:
            arr = np.array(prices)
            p50 = float(np.percentile(arr, 50))
            p90 = float(np.percentile(arr, 90))
            extreme = p50 > EXTREME_P50_THRESHOLD or p90 > EXTREME_P90_THRESHOLD
            verdict = None
            if extreme:
                supporting_anchors = [
                    v for v in (player.public_anchor_value, player.historical_anchor_value)
                    if v is not None and abs(v - p50) / p50 <= SUPPORT_TOLERANCE
                ]
                verdict = "SUPPORTED_BY_INDEPENDENT_ANCHOR" if supporting_anchors else "NOT_SUPPORTED_REVIEW_REQUIRED"
            row.update({
                "p10": round(float(np.percentile(arr, 10)), 2),
                "p25": round(float(np.percentile(arr, 25)), 2),
                "p50": round(p50, 2),
                "p75": round(float(np.percentile(arr, 75)), 2),
                "p90": round(p90, 2),
                "mean_sold_price": round(float(arr.mean()), 2),
                "extreme_price_flag": extreme,
                "extreme_price_review_verdict": verdict,
            })
        rows.append(row)

    return pd.DataFrame(rows)
