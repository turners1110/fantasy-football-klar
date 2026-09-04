#!/usr/bin/env python3
"""Phase 3A item 10: aggregate market-clearing diagnostics across a batch
of simulated auctions, for comparison against real 2025 auction-shape
history where reliable records exist (similar SHAPE required, not exact
equality, per the instruction).

Writes outputs/auction_rebuild/phase3a/market_clearing_diagnostics.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from mock_draft.auction import run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams

N_SEEDS = 40
OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "market_clearing_diagnostics.json"


def main() -> None:
    players, teams_template, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    team_states = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv")
    league_starting_cash = float(team_states["primary_auction_budget"].sum())

    all_sales = []
    all_unused = []
    for seed in range(N_SEEDS):
        rng = np.random.default_rng(seed)
        log, final_teams = run_single_auction(players, teams_template, rng)
        for entry in log:
            all_sales.append(entry)
        for team in final_teams.values():
            all_unused.append(team.budget_remaining)

    sales_df = pd.DataFrame(all_sales)
    unused = np.array(all_unused)

    league_total_spend = float(sales_df["sale_price"].sum()) / N_SEEDS
    league_unused_cash = float(unused.sum()) / N_SEEDS
    pct_spent = league_total_spend / league_starting_cash if league_starting_cash else 0.0

    spend_by_position = sales_df.groupby("position")["sale_price"].sum().to_dict()
    spend_by_position = {k: v / N_SEEDS for k, v in spend_by_position.items()}

    bands = [(1, 1), (2, 10), (11, 25), (26, 50), (51, 1000)]
    spend_by_band = {}
    for lo, hi in bands:
        mask = (sales_df["sale_price"] >= lo) & (sales_df["sale_price"] <= hi)
        spend_by_band[f"${lo}-{hi if hi < 1000 else '+'}"] = int(mask.sum()) / N_SEEDS

    n_dollar_one = int((sales_df["sale_price"] == 1).sum()) / N_SEEDS
    n_uncontested = int((sales_df["bidder_count"] == 1).sum()) / N_SEEDS

    per_team_spend = sales_df.groupby("winning_team")["sale_price"].sum() / N_SEEDS

    summary = {
        "n_seeds": N_SEEDS,
        "league_starting_auction_cash": league_starting_cash,
        "league_total_spend_per_auction": round(league_total_spend, 2),
        "league_unused_cash_per_auction": round(league_unused_cash, 2),
        "percentage_cash_spent": round(pct_spent, 4),
        "spend_by_position_per_auction": {k: round(v, 2) for k, v in spend_by_position.items()},
        "spend_by_price_band_per_auction": spend_by_band,
        "number_of_dollar_one_sales_per_auction": round(n_dollar_one, 2),
        "number_of_uncontested_sales_per_auction": round(n_uncontested, 2),
        "median_team_unused_cash": float(np.median(unused)),
        "maximum_team_unused_cash": float(np.max(unused)),
        "per_team_avg_spend": {k: round(v, 2) for k, v in per_team_spend.to_dict().items()},
        "note_on_top_player_concentration": (
            "Top-12/24 spend-share concentration requires a comparable REAL 2025 competitive-auction price "
            "list, which this repo does not have (see salary_origin_audit.csv -- most 2025 salaries are "
            "UNKNOWN origin, not confirmed auction prices). Computed here for the SIMULATED market only, "
            "as a self-consistency figure, not a validated comparison against real history."
        ),
    }

    # Top-12/24 concentration among the simulated sale prices themselves (self-consistency figure).
    sorted_desc = np.sort(sales_df["sale_price"].values)[::-1]
    total_spend_all = sorted_desc.sum()
    summary["simulated_top12_spend_share"] = round(float(sorted_desc[:12].sum() / total_spend_all), 4) if total_spend_all else 0.0
    summary["simulated_top24_spend_share"] = round(float(sorted_desc[:24].sum() / total_spend_all), 4) if total_spend_all else 0.0

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(summary, indent=2))

    print(f"Wrote {OUT_PATH}")
    for k, v in summary.items():
        if not isinstance(v, dict):
            print(f"  {k}: {v}")
    print(f"  spend_by_position_per_auction: {summary['spend_by_position_per_auction']}")
    print(f"  spend_by_price_band_per_auction: {summary['spend_by_price_band_per_auction']}")


if __name__ == "__main__":
    main()
