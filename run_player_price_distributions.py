#!/usr/bin/env python3
"""Per-player price distributions under the recommended strategy.

Simulates the actual auction that would happen if you personally employ
the winning strategy from run_best_response.py while your 11 opponents
draft like a realistic random mix of the hand-designed archetypes --
not an abstract "what if everyone was identical" tournament. Every
player's price across every simulated draft (from any of the 12 teams,
not just "your" seat) feeds the distribution, since the question here is
"what will this player actually go for," not just what you'd pay.

    python run_player_price_distributions.py --iterations 100
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from mock_draft.auction import run_single_auction
from mock_draft.data import load_pool_and_teams
from mock_draft.evolution import DEFAULT_STATE_PATH, genome_from_dict

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output_mock_draft"


def load_strategy(state_path: Path, genome_name: str):
    data = json.loads(Path(state_path).read_text())
    for g in data["population"]:
        if g["name"] == genome_name:
            return genome_from_dict(g)
    raise ValueError(f"Genome {genome_name!r} not found in {state_path}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--iterations", type=int, default=100)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--snapshot-dir", default=BASE_DIR / "output_mock_draft_snapshot")
    p.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    p.add_argument("--genome-name", default="gen15_elite0",
                   help="Which saved genome to use as the recommended strategy (see run_best_response.py output).")
    p.add_argument("--output-dir", default=OUTPUT_DIR)
    args = p.parse_args()

    args.output_dir = Path(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    players, teams = load_pool_and_teams(args.snapshot_dir)
    recommended = load_strategy(Path(args.state_path), args.genome_name)
    print(f"Using recommended strategy: {recommended.name}")

    rng = np.random.default_rng(args.seed)
    team_names = list(teams.keys())
    all_picks = []

    for i in range(args.iterations):
        my_team = team_names[i % len(team_names)]
        strategies = {my_team: recommended}
        log, _ = run_single_auction(players, teams, rng, strategies=strategies)
        for row in log:
            row["iteration"] = i
            row["is_recommended_seat"] = row["winner"] == my_team
            all_picks.append(row)
        if (i + 1) % 20 == 0:
            print(f"  ...{i + 1}/{args.iterations} drafts simulated")

    picks_df = pd.DataFrame(all_picks)
    organic = picks_df[~picks_df["forced_final_slot"]]

    base_values = pd.DataFrame([{"player": pl.name, "position": pl.position, "base_value": pl.base_value} for pl in players.values()])

    def pct(s, q):
        return s.quantile(q)

    agg = organic.groupby("player")["price"].agg(
        n_picks="count", mean_price="mean", median_price="median", std_price="std",
        p10=lambda s: pct(s, 0.10), p25=lambda s: pct(s, 0.25),
        p75=lambda s: pct(s, 0.75), p90=lambda s: pct(s, 0.90),
        min_price="min", max_price="max",
    ).reset_index()

    report = base_values.merge(agg, on="player", how="left")
    report = report.sort_values("base_value", ascending=False)

    out_path = args.output_dir / "player_price_distributions.csv"
    report.to_csv(out_path, index=False)
    print(f"\nWrote {out_path} ({len(report)} players, {report['n_picks'].notna().sum()} with real distributions)")

    priced = report.dropna(subset=["mean_price"])
    print("\nSample (top 15 by real value):")
    cols = ["player", "position", "base_value", "p10", "p25", "median_price", "p75", "p90", "n_picks"]
    print(priced.head(15)[cols].round(1).to_string(index=False))


if __name__ == "__main__":
    main()
