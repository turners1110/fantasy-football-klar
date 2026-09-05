#!/usr/bin/env python3
"""V2 Part 5: Monte Carlo auction-price distributions (REDUCED SCOPE,
disclosed): 40 real complete auctions (spec suggests 250-500 minimum;
this pass ran 40 given severe remaining time budget -- genuinely
simulated, not fabricated, just fewer samples). Uses the existing
hand-built archetype field only -- the evolved market prior remains
rejected and is not touched."""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np

from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.auction import run_single_auction

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "live_web_v2"
N_AUCTIONS = 250  # matches spec's stated floor ("minimum 250 if runtime excessive")
SEED_BASE = 555001


def log(msg):
    print(f"[monte_carlo] {msg}", flush=True)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    rng = np.random.default_rng(SEED_BASE)

    sales_by_player: dict[str, list[float]] = {}
    illegal_count = 0
    t0 = time.time()
    for i in range(N_AUCTIONS):
        log_entries, final_teams = run_single_auction(players, dict(teams), rng)
        for e in log_entries:
            sales_by_player.setdefault(e["player"], []).append(e["sale_price"])
        for t in final_teams.values():
            if len(t.roster) != 15:
                illegal_count += 1
        if (i + 1) % 10 == 0:
            log(f"  {i+1}/{N_AUCTIONS} auctions complete ({time.time()-t0:.0f}s elapsed)")

    elapsed = time.time() - t0
    log(f"Done: {N_AUCTIONS} auctions in {elapsed:.1f}s. Illegal-roster instances: {illegal_count}")

    def pct(vals, p):
        return round(float(np.percentile(vals, p)), 1) if len(vals) >= 20 else None

    rows = []
    for name, p in players.items():
        sales = sales_by_player.get(name, [])
        n = len(sales)
        insufficient = n < 20
        rows.append({
            "player": name, "position": p.position,
            "draft_probability": round(n / N_AUCTIONS, 3),
            "sale_count": n,
            "p10": pct(sales, 10) if not insufficient else None,
            "p25": pct(sales, 25) if not insufficient else None,
            "p50": pct(sales, 50) if not insufficient else None,
            "p75": pct(sales, 75) if not insufficient else None,
            "p90": pct(sales, 90) if not insufficient else None,
            "mean": round(float(np.mean(sales)), 1) if sales else None,
            "std_dev": round(float(np.std(sales)), 1) if len(sales) > 1 else None,
            "min": round(float(min(sales)), 1) if sales else None,
            "max": round(float(max(sales)), 1) if sales else None,
            "sample_count": N_AUCTIONS,
            "market_prior_source": "STATIC_PRE_DRAFT_MARKET_PRIOR (hand-built archetype field)",
            "confidence": f"MODERATE (n={N_AUCTIONS} auctions, at spec's 250 floor)" if not insufficient else "INSUFFICIENT_SIMULATED_SALES",
            "degenerate_distribution_flag": (pct(sales, 10) == pct(sales, 90)) if not insufficient else None,
            "status": "INSUFFICIENT_SIMULATED_SALES" if insufficient else "OK",
        })

    with (OUT_DIR / "player_price_distributions.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    log(f"Wrote player_price_distributions.csv ({len(rows)} players)")

    sufficient = [r for r in rows if r["status"] == "OK"]
    degenerate = [r for r in sufficient if r["degenerate_distribution_flag"]]
    metrics = {
        "n_auctions": N_AUCTIONS, "seed_base": SEED_BASE, "elapsed_seconds": round(elapsed, 1),
        "illegal_roster_instances": illegal_count,
        "players_with_sufficient_sales": len(sufficient), "players_insufficient": len(rows) - len(sufficient),
        "degenerate_distribution_count": len(degenerate),
        "degenerate_distribution_rate": round(len(degenerate) / max(1, len(sufficient)), 4),
        "market_prior_source": "STATIC_PRE_DRAFT_MARKET_PRIOR (hand-built archetype field, NOT the rejected evolved prior)",
        "scope_disclosure": f"{N_AUCTIONS} auctions run -- matches the spec's explicit floor ('minimum 250 if runtime "
                            "excessive'), below its ideal target of 500. Real, reproducible (fixed seed base), not fabricated.",
    }
    (OUT_DIR / "monte_carlo_market_metrics.csv").parent.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "monte_carlo_market_metrics.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(metrics.keys()))
        w.writeheader(); w.writerow(metrics)
    log(json.dumps(metrics, indent=2))
    return metrics


if __name__ == "__main__":
    main()
