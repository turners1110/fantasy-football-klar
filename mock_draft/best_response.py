"""Best-response test: "if I personally adopt strategy X while my 11
opponents behave like a typical realistic field (random archetype mix),
how many points do I get relative to that team's baseline?"

This is the actually-useful question for "what strategy should I employ,"
as distinct from the self-play evolutionary tournament in evolution.py
(which tests strategies against each other in a shared population, and
found no detectable improvement over the hand-designed archetypes once
keeper-luck was controlled for -- see mock_draft/README.md). Here, the
field is held realistic and fixed; only "my" team's strategy varies.
"""

from __future__ import annotations

import numpy as np

from .archetypes import ARCHETYPE_NAMES, Archetype
from .auction import run_single_auction
from .models import Player, Team


def evaluate_best_response(
    candidate: Archetype,
    players: dict[str, Player],
    teams_template: dict[str, Team],
    team_baselines: dict[str, float],
    n_matches: int,
    rng: np.random.Generator,
) -> dict:
    """Run n_matches drafts, each time assigning `candidate` to a
    different (rotating) real team slot while the other 11 get a random
    hand-designed archetype (the realistic field). Returns baseline-
    adjusted points stats for the candidate's performance."""
    team_names = list(teams_template.keys())
    results = []
    for i in range(n_matches):
        my_team = team_names[i % len(team_names)]
        strategies = {my_team: candidate}
        _, final_teams = run_single_auction(players, teams_template, rng, strategies=strategies)
        adjusted = final_teams[my_team].total_points - team_baselines[my_team]
        results.append(adjusted)
    arr = np.array(results)
    return {
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "sem": float(arr.std() / np.sqrt(len(arr))),
        "n": len(arr),
    }


def run_best_response_comparison(
    candidates: dict[str, Archetype],
    players: dict[str, Player],
    teams_template: dict[str, Team],
    team_baselines: dict[str, float],
    n_matches: int,
    rng: np.random.Generator,
    verbose: bool = True,
) -> dict[str, dict]:
    results = {}
    for name, candidate in candidates.items():
        results[name] = evaluate_best_response(candidate, players, teams_template, team_baselines, n_matches, rng)
        if verbose:
            r = results[name]
            print(f"  {name:<28} {r['mean']:+7.1f} +/- {r['sem']:5.1f} (n={r['n']})")
    return results
