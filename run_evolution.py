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
from mock_draft.evolution import DEFAULT_STATE_PATH, compute_team_baselines, evaluate_generation, run_evolution
from mock_draft.legal_lineup import build_production_lineup

BASE_DIR = Path(__file__).parent


def benchmark_hand_designed_archetypes(players, teams, team_baselines, rng, n_matches=40) -> float:
    """Baseline-adjusted average for the hand-designed archetypes (random
    assignment, same as run_mock_draft.py). Since team_baselines are
    DEFINED as the hand-designed archetypes' own average points per team,
    this should land very close to 0 by construction -- a genuine sanity
    check, not just a comparison point. If the evolved population's
    adjusted average is meaningfully above 0, that's real evidence of
    improvement; if it's also ~0, evolution hasn't found anything better."""
    totals = []
    for _ in range(n_matches):
        _, final_teams = run_single_auction(players, teams, rng)
        for name, team in final_teams.items():
            utility = build_production_lineup(team.roster).total_roster_utility
            totals.append(utility - team_baselines[name])
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
        print(f"\nLearning curve (mean fitness, pts above/below team baseline): "
              f"gen {first.generation} = {first.mean_fitness:+.1f} "
              f"-> gen {last.generation} = {last.mean_fitness:+.1f} "
              f"({'+' if last.mean_fitness >= first.mean_fitness else ''}{last.mean_fitness - first.mean_fitness:.1f})")

    print("\nRecomputing team baselines for final evaluation (same method used during training)...")
    baseline_rng = np.random.default_rng(args.seed + 3)
    team_baselines = compute_team_baselines(players, teams, args.matches_per_generation, baseline_rng)

    rng = np.random.default_rng(args.seed + 1)
    print("Re-evaluating final population to find the current best genome...")
    fitness = evaluate_generation(population, players, teams, args.matches_per_generation, rng, team_baselines)
    best_idx = int(np.nanargmax(fitness))
    best = population[best_idx]
    print(f"Best genome: {best.name} ({fitness[best_idx]:+.1f} pts above/below team baseline)")
    for field in ("max_stars", "star_ceiling_pct", "price_ceiling_pct", "tier_aggression",
                  "strict_value_ceiling", "noise_std", "jump_bid_prob", "position_weight"):
        print(f"  {field}: {getattr(best, field)}")

    print("\nSanity-checking against hand-designed archetypes (should land near 0 -- "
          "team_baselines are DEFINED as their average)...")
    archetype_avg = benchmark_hand_designed_archetypes(
        players, teams, team_baselines, np.random.default_rng(args.seed + 2), n_matches=args.matches_per_generation
    )
    print(f"Hand-designed archetypes (sanity check, should be ~0): {archetype_avg:+.1f}")
    print(f"Evolved population (this generation):                  {float(np.nanmean(fitness)):+.1f}")
    print(f"Best evolved genome:                                   {fitness[best_idx]:+.1f}")
    print("\n(Positive = genuinely better than a team with that roster would typically do. "
          "If the evolved population's number isn't clearly above the archetype sanity check, "
          "evolution hasn't found anything better yet -- see mock_draft/README.md.)")


if __name__ == "__main__":
    main()
