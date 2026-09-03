"""Run many mock auctions and aggregate results -- this is the actual
"calibrate the valuation model" output: how do simulated market-clearing
prices compare to the real model's suggested_auction_price?
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .auction import run_single_auction
from .data import load_pool_and_teams


def run_many(n: int, seed: int = 0, snapshot_dir=None, verbose_every: int = 0):
    players, teams = load_pool_and_teams(snapshot_dir) if snapshot_dir else load_pool_and_teams()
    rng = np.random.default_rng(seed)

    all_picks = []
    leftover_budgets = []
    for i in range(n):
        log, final_teams = run_single_auction(players, teams, rng, verbose=False)
        for row in log:
            row["iteration"] = i
            all_picks.append(row)
        for name, team in final_teams.items():
            leftover_budgets.append({"iteration": i, "team": name, "leftover_budget": team.budget_remaining})
        if verbose_every and (i + 1) % verbose_every == 0:
            print(f"  ...{i + 1}/{n} auctions simulated")

    picks_df = pd.DataFrame(all_picks)
    leftover_df = pd.DataFrame(leftover_budgets)
    return players, picks_df, leftover_df


def calibration_report(players: dict, picks_df: pd.DataFrame) -> pd.DataFrame:
    """Compare simulated clearing prices (excluding forced-final-slot
    dumps, which aren't organic market prices) to the real model's
    suggested_auction_price for every player, across all iterations."""
    organic = picks_df[~picks_df["forced_final_slot"]]
    agg = organic.groupby("player")["price"].agg(
        sim_mean="mean", sim_median="median", sim_std="std", n_sim_picks="count",
    ).reset_index()

    base_values = pd.DataFrame(
        [{"player": p.name, "position": p.position, "base_value": p.base_value, "tier": p.tier} for p in players.values()]
    )
    report = base_values.merge(agg, on="player", how="left")
    report["gap"] = report["sim_mean"] - report["base_value"]
    report["gap_pct"] = report["gap"] / report["base_value"].replace(0, pd.NA)
    return report.sort_values("base_value", ascending=False)
