#!/usr/bin/env python3
"""Co-evolutionary bidding optimizer -- teams learn to out-draft each
other for maximum roster projected points (not dollar efficiency).

    python run_evolution.py --generations 20 --population 24 --matches-per-generation 40

Re-run this any time; it resumes from mock_draft_learned_population.json
if that file exists, so intelligence keeps building across separate runs
instead of restarting cold every time (the "continuously learning"
framework this was actually asked for).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from mock_draft.auction import run_single_auction
from mock_draft.data import load_pool_and_teams
from mock_draft.evolution import DEFAULT_STATE_PATH, run_evolution

BASE_DIR = Path(__file__).parent


def benchmark_hand_designed_archetypes(players, teams, rng, n_matches=40) -> float:
    """Average roster points achieved by the hand-designed archetypes
    (random assignment, same as run_mock_draft.py) -- the bar evolution
    needs to clear to prove it found something genuinely better."""
    totals = []
    for _ in range(n_matches):
        _, final_teams = run_single_auction(players, teams, rng)
        totals.extend(t.total_points for t in final_teams.values())
    return float(np.mean(totals))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--generations", type=int, default=20)
    p.add_argument("--population", type=int, default=24)
    p.add_argument("--matches-per-generation", type=int, default=40)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--snapshot-dir", default=BASE_DIR / "output_mock_draft_snapshot")
    p.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    p.add_argument("--reset", action="store_true", help="Ignore any saved population and start fresh.")
    args = p.parse_args()

    if args.reset and Path(args.state_path).exists():
        Path(args.state_path).unlink()
        print(f"--reset: removed {args.state_path}")

    players, teams = load_pool_and_teams(args.snapshot_dir)
    print(f"Loaded {len(players)} players, {len(teams)} teams.\n")

    population, history = run_evolution(
        generations=args.generations,
        population_size=args.population,
        matches_per_generation=args.matches_per_generation,
        players=players,
        teams_template=teams,
        seed=args.seed,
        state_path=Path(args.state_path),
    )

    print(f"\nSaved learned population to {args.state_path} ({len(population)} genomes, "
          f"{history[-1].generation + 1} total generations across all runs).")

    if len(history) >= 2:
        first, last = history[0], history[-1]
        print(f"\nLearning curve (mean fitness): gen {first.generation} = {first.mean_fitness:.1f} pts "
              f"-> gen {last.generation} = {last.mean_fitness:.1f} pts "
              f"({'+' if last.mean_fitness >= first.mean_fitness else ''}{last.mean_fitness - first.mean_fitness:.1f})")

    rng = np.random.default_rng(args.seed + 1)
    print("\nRe-evaluating final population to find the current best genome...")
    from mock_draft.evolution import evaluate_generation
    fitness = evaluate_generation(population, players, teams, args.matches_per_generation, rng)
    best_idx = int(np.nanargmax(fitness))
    best = population[best_idx]
    print(f"Best genome: {best.name} (avg {fitness[best_idx]:.1f} pts)")
    for field in ("max_stars", "star_ceiling_pct", "price_ceiling_pct", "tier_aggression",
                  "strict_value_ceiling", "noise_std", "jump_bid_prob", "position_weight"):
        print(f"  {field}: {getattr(best, field)}")

    print("\nBenchmarking hand-designed archetypes for comparison...")
    archetype_avg = benchmark_hand_designed_archetypes(players, teams, np.random.default_rng(args.seed + 2), n_matches=args.matches_per_generation)
    print(f"Hand-designed archetypes (random mix): {archetype_avg:.1f} avg roster points")
    print(f"Evolved population (this generation):  {float(np.nanmean(fitness)):.1f} avg roster points")
    print(f"Best evolved genome:                   {fitness[best_idx]:.1f} avg roster points")


if __name__ == "__main__":
    main()
