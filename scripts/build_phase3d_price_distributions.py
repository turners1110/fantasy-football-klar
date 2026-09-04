#!/usr/bin/env python3
"""Phase 3D item 15: full-pool market-price distributions (and item 13's
extreme-price review flags, computed on top).

Every price here is UNCALIBRATED_SIMULATED_PRICE unless this was run AFTER
applying the calibration harness's selected parameters to production
config, in which case it is CALIBRATED_EXPECTED_MARKET_PRICE (see
auction_model.labels) -- never a real market price either way.

Writes:
  outputs/auction_rebuild/phase3d/price_distributions.csv
  outputs/auction_rebuild/phase3d/extreme_price_review.csv
"""

from __future__ import annotations

import multiprocessing as mp
import sys
import time
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from auction_model.price_distributions import (
    EXTREME_P50_THRESHOLD, EXTREME_P90_THRESHOLD, MIN_SALE_OBSERVATIONS, SUPPORT_TOLERANCE,
)
from mock_draft.auction import run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.valuation import compute_base_market_anchor

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3d"
N_SIMS = 200
N_WORKERS = min(4, mp.cpu_count())


def _run_chunk(args):
    players, teams, seed_start, n = args
    sold_prices: dict[str, list[float]] = defaultdict(list)
    sold_count: dict[str, int] = defaultdict(int)
    for i in range(n):
        rng = np.random.default_rng(seed_start + i)
        log, _ = run_single_auction(players, teams, rng)
        for e in log:
            sold_prices[e["player"]].append(e["sale_price"])
            sold_count[e["player"]] += 1
    return dict(sold_prices), dict(sold_count), n


def main() -> None:
    t0 = time.time()
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")

    per_worker = N_SIMS // N_WORKERS
    chunks = [(players, teams, i * per_worker, per_worker) for i in range(N_WORKERS)]
    remainder = N_SIMS - per_worker * N_WORKERS
    if remainder:
        chunks.append((players, teams, N_WORKERS * per_worker, remainder))

    print(f"Running {N_SIMS} auctions across {len(chunks)} workers...")
    with mp.Pool(processes=N_WORKERS) as pool:
        results = pool.map(_run_chunk, chunks)

    sold_prices: dict[str, list[float]] = defaultdict(list)
    sold_count: dict[str, int] = defaultdict(int)
    n_sims_run = 0
    for prices_part, count_part, n in results:
        n_sims_run += n
        for name, plist in prices_part.items():
            sold_prices[name].extend(plist)
        for name, c in count_part.items():
            sold_count[name] += c

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
            row.update({k: "INSUFFICIENT_SIMULATED_SALES" for k in
                        ("p10", "p25", "p50", "p75", "p90", "mean_sold_price")})
            row["extreme_price_flag"] = False
            row["extreme_price_review_verdict"] = None
        else:
            arr = np.array(prices)
            p50 = float(np.percentile(arr, 50))
            p90 = float(np.percentile(arr, 90))
            extreme = p50 > EXTREME_P50_THRESHOLD or p90 > EXTREME_P90_THRESHOLD
            verdict = None
            if extreme:
                supporting = [
                    v for v in (player.public_anchor_value, player.historical_anchor_value)
                    if v is not None and abs(v - p50) / p50 <= SUPPORT_TOLERANCE
                ]
                verdict = "SUPPORTED_BY_INDEPENDENT_ANCHOR" if supporting else "NOT_SUPPORTED_REVIEW_REQUIRED"
            row.update({
                "p10": round(float(np.percentile(arr, 10)), 2), "p25": round(float(np.percentile(arr, 25)), 2),
                "p50": round(p50, 2), "p75": round(float(np.percentile(arr, 75)), 2), "p90": round(p90, 2),
                "mean_sold_price": round(float(arr.mean()), 2),
                "extreme_price_flag": extreme, "extreme_price_review_verdict": verdict,
            })
        rows.append(row)

    df = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dist_path = OUT_DIR / "price_distributions.csv"
    df.to_csv(dist_path, index=False)
    print(f"Wrote {dist_path} ({len(df)} players) in {time.time() - t0:.1f}s")

    extreme = df[df["extreme_price_flag"] == True]  # noqa: E712
    extreme_path = OUT_DIR / "extreme_price_review.csv"
    extreme.to_csv(extreme_path, index=False)
    print(f"Wrote {extreme_path} ({len(extreme)} flagged players)")
    print(f"  P50 > ${EXTREME_P50_THRESHOLD} or P90 > ${EXTREME_P90_THRESHOLD}")
    if len(extreme):
        supported = (extreme["extreme_price_review_verdict"] == "SUPPORTED_BY_INDEPENDENT_ANCHOR").sum()
        print(f"  {supported}/{len(extreme)} supported by an independent anchor; "
              f"{len(extreme) - supported} NOT_SUPPORTED_REVIEW_REQUIRED")

    insufficient = int((df["p50"] == "INSUFFICIENT_SIMULATED_SALES").sum())
    print(f"  {insufficient}/{len(df)} players had <{MIN_SALE_OBSERVATIONS} sold observations "
          f"(INSUFFICIENT_SIMULATED_SALES, no fabricated percentile)")


if __name__ == "__main__":
    main()
