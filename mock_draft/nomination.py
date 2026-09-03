"""Strategic nomination: who gets put up for bid, and why.

Implements the shared logic behind several archetypes' "how to attack"
notes without hand-coding 8 bespoke behaviors: nominate a player a flush
rival privately wants (drain them), nominate the player at a tier cliff
(trigger panic), nominate into a position run (multiple rivals still need
bodies there), and generally avoid nominating your own top targets while
plenty of alternatives remain.
"""

from __future__ import annotations

import numpy as np

from . import config_bridge as cfg
from .models import Player, Team
from .valuation import get_private_value

W_DRAIN = 1.0
W_TIER_CLIFF = 0.8
W_POSITION_RUN = 0.5
W_SELF_AVOID = 0.6
W_VALUE = 2.5
TEMPERATURE = 0.6
TOP_K = 12


def _drain_score(candidate: Player, nominator: str, teams: dict[str, Team], rng: np.random.Generator) -> float:
    best = 0.0
    for name, team in teams.items():
        if name == nominator or team.is_done:
            continue
        budget_per_slot = team.budget_remaining / max(1, team.slots_needed)
        rival_interest = get_private_value(team, candidate, rng) / max(1.0, candidate.base_value)
        best = max(best, budget_per_slot / cfg.BUDGET_PER_TEAM * rival_interest)
    return best


def _tier_cliff_score(candidate: Player) -> float:
    return 1.0 if candidate.tier_rank >= candidate.tier_size - 1 else 0.0


def _position_run_score(candidate: Player, teams: dict[str, Team]) -> float:
    need_starter = sum(
        1 for team in teams.values()
        if not team.is_done and team.position_count(candidate.position) < cfg.STARTING_LINEUP.get(candidate.position, 0)
    )
    return need_starter / cfg.NUM_TEAMS


def _self_avoid_score(candidate: Player, nominator: str, teams: dict[str, Team], rng: np.random.Generator) -> float:
    team = teams[nominator]
    return get_private_value(team, candidate, rng) / max(1.0, candidate.base_value)


def choose_nomination(
    nominator: str, teams: dict[str, Team], available: dict[str, Player], rng: np.random.Generator
) -> str:
    names = list(available.keys())
    # Root-cause fix from the first 100-sim run: the global top-138 players
    # by real value sum to ~$3,685, almost exactly the ~$3,700 live-auction
    # budget -- the real valuation model is already self-consistent. But
    # the drafted set only overlapped that real top-138 by 20 players,
    # because every other nomination signal here is RATIO-based (a rival's
    # private value / the player's own base_value), which scores a $1
    # player a rival slightly overvalues identically to a $100 star a
    # rival slightly overvalues. Nothing was pulling genuinely good players
    # into the auction at all -- matching real strategy notes ("nominate
    # stars you don't want early"), this adds that missing pull directly.
    max_value = max((p.base_value for p in available.values()), default=1.0) or 1.0

    scores = np.empty(len(names))
    for i, name in enumerate(names):
        candidate = available[name]
        scores[i] = (
            W_DRAIN * _drain_score(candidate, nominator, teams, rng)
            + W_TIER_CLIFF * _tier_cliff_score(candidate)
            + W_POSITION_RUN * _position_run_score(candidate, teams)
            + W_VALUE * (candidate.base_value / max_value)
            - W_SELF_AVOID * _self_avoid_score(candidate, nominator, teams, rng)
        )

    top_idx = np.argsort(scores)[-min(TOP_K, len(names)):]
    top_scores = scores[top_idx]
    weights = np.exp((top_scores - top_scores.max()) / TEMPERATURE)
    weights /= weights.sum()
    chosen = rng.choice(top_idx, p=weights)
    return names[chosen]
