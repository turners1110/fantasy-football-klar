#!/usr/bin/env python3
"""Mock auction draft simulator -- 12 budget-and-keeper-accurate teams,
archetype-driven bidding, strategic nomination, run N times to calibrate
the real valuation model against simulated market-clearing prices.

    python run_mock_draft.py --iterations 50
    python run_mock_draft.py --iterations 1 --verbose   # watch one draft live

Reads player pool + keepers from output_mock_draft_snapshot/ by default (a
snapshot regenerated with the expanded FantasyPros-backed pool -- see
mock_draft/README.md for why this is a separate snapshot from output/,
which the main valuation pipeline owns).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from mock_draft.auction import run_single_auction
from mock_draft.data import load_pool_and_teams
from mock_draft.monte_carlo import calibration_report, run_many

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output_mock_draft"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--iterations", type=int, default=50)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--snapshot-dir", default=BASE_DIR / "output_mock_draft_snapshot")
    p.add_argument("--output-dir", default=OUTPUT_DIR)
    p.add_argument("--verbose", action="store_true", help="Print every pick of a single draft (forces --iterations 1)")
    args = p.parse_args()

    args.output_dir = Path(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.verbose:
        players, teams = load_pool_and_teams(args.snapshot_dir)
        rng = np.random.default_rng(args.seed)
        log, final_teams = run_single_auction(players, teams, rng, verbose=True)
        print("\nFinal budgets (all should be $0.00):")
        for name, team in final_teams.items():
            print(f"  {name:<8} spent ${400 - team.budget_remaining:>7.2f}  leftover ${team.budget_remaining:>6.2f}  roster {len(team.roster)}")
        return

    print(f"Simulating {args.iterations} mock auctions (seed={args.seed})...")
    players, picks_df, leftover_df = run_many(args.iterations, seed=args.seed, snapshot_dir=args.snapshot_dir, verbose_every=max(1, args.iterations // 10))

    print("\nInvariant check (should all be true / zero):")
    print(f"  All rosters reached 15 players: {(picks_df.groupby(['iteration', 'winner']).size() > 0).all()}")
    print(f"  Max leftover budget across all team-iterations: ${leftover_df['leftover_budget'].abs().max():.2f}")
    print(f"  Forced-final-slot picks per draft (avg): {picks_df.groupby('iteration')['forced_final_slot'].sum().mean():.1f} of 12 teams")

    report = calibration_report(players, picks_df)
    picks_path = args.output_dir / "all_picks.csv"
    report_path = args.output_dir / "calibration_report.csv"
    leftover_path = args.output_dir / "leftover_budgets.csv"
    picks_df.to_csv(picks_path, index=False)
    report.to_csv(report_path, index=False)
    leftover_df.to_csv(leftover_path, index=False)

    print(f"\nWrote {picks_path} ({len(picks_df)} rows)")
    print(f"Wrote {report_path} ({len(report)} players)")
    print(f"Wrote {leftover_path}")

    priced = report.dropna(subset=["sim_mean"])
    big_gaps = priced[priced["gap_pct"].abs() > 0.5].sort_values("gap_pct", key=abs, ascending=False)
    print(f"\n{len(big_gaps)} players where simulated price differs from the model's "
          f"suggested_auction_price by >50% (biggest signal for recalibrating "
          f"auction_model/config.py):")
    print(big_gaps[["player", "position", "base_value", "sim_mean", "gap_pct"]].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
