#!/usr/bin/env python3
"""Best-response comparison: which strategy should YOU personally employ,
assuming your 11 opponents behave like a typical realistic field?

Tests all 8 hand-designed archetypes plus the best genome from
mock_draft_learned_population.json (if it exists), each rotated across
all 12 real team slots against 11 random-archetype opponents. Reports
mean +/- standard error so a real difference can be told from noise --
the self-play evolutionary tournament (run_evolution.py) found none once
keeper-luck was controlled for, so this is deliberately conservative
about declaring a winner.

    python run_best_response.py --matches-per-candidate 40
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from mock_draft.archetypes import ARCHETYPES
from mock_draft.best_response import run_best_response_comparison
from mock_draft.data import load_pool_and_teams
from mock_draft.evolution import DEFAULT_STATE_PATH, compute_team_baselines, genome_from_dict

BASE_DIR = Path(__file__).parent


def load_best_evolved_genome(state_path: Path):
    if not Path(state_path).exists():
        return None
    data = json.loads(Path(state_path).read_text())
    # We don't have per-genome fitness stored, so just take the last
    # generation's elite-0 slot by convention (evolve_step always puts the
    # top-ranked genome first among elites) -- best-effort, re-validated
    # here regardless since this script re-evaluates everything itself.
    population = data["population"]
    for g in population:
        if "elite0" in g["name"] or "elite" in g["name"]:
            return genome_from_dict(g)
    return genome_from_dict(population[0]) if population else None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--matches-per-candidate", type=int, default=40)
    p.add_argument("--baseline-matches", type=int, default=40)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--snapshot-dir", default=BASE_DIR / "output_mock_draft_snapshot")
    p.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    args = p.parse_args()

    players, teams = load_pool_and_teams(args.snapshot_dir)
    rng = np.random.default_rng(args.seed)

    print(f"Computing per-team baselines ({args.baseline_matches} matches)...")
    team_baselines = compute_team_baselines(players, teams, args.baseline_matches, rng)

    candidates = dict(ARCHETYPES)
    best_evolved = load_best_evolved_genome(args.state_path)
    if best_evolved is not None:
        candidates["EVOLVED_best"] = best_evolved
        print(f"Including best evolved genome from {args.state_path}: {best_evolved.name}\n")
    else:
        print(f"No evolved population found at {args.state_path} -- comparing archetypes only.\n")

    print(f"Best-response test ({args.matches_per_candidate} matches/candidate, "
          f"rotated across all 12 real team slots vs. a random-archetype field):")
    print(f"{'strategy':<28} {'pts above/below baseline':<24}")
    results = run_best_response_comparison(
        candidates, players, teams, team_baselines, args.matches_per_candidate, rng
    )

    ranked = sorted(results.items(), key=lambda kv: kv[1]["mean"], reverse=True)
    print("\nRanked (best to worst):")
    for name, r in ranked:
        significant = abs(r["mean"]) > 2 * r["sem"]
        flag = "  <- outside 2*SEM (plausibly real)" if significant else ""
        print(f"  {name:<28} {r['mean']:+7.1f} +/- {r['sem']:5.1f}{flag}")

    top_name, top_r = ranked[0]
    print(f"\nBest strategy found: {top_name} ({top_r['mean']:+.1f} +/- {top_r['sem']:.1f} pts)")
    if abs(top_r["mean"]) <= 2 * top_r["sem"]:
        print("NOTE: this is within ~2 standard errors of zero -- not confidently distinguishable "
              "from 'no strategy matters here,' i.e. don't treat this as a confirmed recommendation "
              "without a larger sample (--matches-per-candidate).")


if __name__ == "__main__":
    main()
