"""Single mock-auction run: nominate -> open ascending bidding -> resolve
-> repeat until every team's required roster is full (or the pool runs
out). Turn order is round-robin regardless of who wins each pick, per the
league's actual convention (confirmed default -- flag if your league
actually does 'winner nominates next').
"""

from __future__ import annotations

import copy

import numpy as np

from . import config_bridge as cfg
from .archetypes import ARCHETYPE_NAMES, Archetype
from .models import Player, Team
from .nomination import choose_nomination
from .valuation import compute_willingness

BID_SAFETY_ROUNDS = 60


def resolve_bid(
    candidate: Player, nominator: str, teams: dict[str, Team], rng: np.random.Generator, draft_progress: float
) -> tuple[str, float]:
    """Open ascending auction starting at $1 with the nominator as the
    default winner if nobody raises."""
    current_bid = float(cfg.MIN_PRICE)
    current_leader = nominator
    eligible = [name for name, t in teams.items() if not t.is_done]

    changed = True
    rounds = 0
    while changed and rounds < BID_SAFETY_ROUNDS:
        changed = False
        rounds += 1
        for name in eligible:
            if name == current_leader:
                continue
            team = teams[name]
            willingness = compute_willingness(team, candidate, rng, draft_progress)
            cap = team.max_bid_cap()
            max_can_pay = min(willingness, cap)
            if max_can_pay <= current_bid:
                continue
            archetype = team.strategy
            increment = 1.0
            if rng.random() < archetype.jump_bid_prob:
                increment = float(rng.integers(2, 6))
            new_bid = min(current_bid + increment, max_can_pay)
            if new_bid > current_bid:
                current_bid = new_bid
                current_leader = name
                changed = True

    return current_leader, current_bid


def run_single_auction(
    players: dict[str, Player], teams: dict[str, Team], rng: np.random.Generator,
    verbose: bool = False, strategies: dict[str, Archetype] | None = None,
):
    """strategies: optional {team_name: Archetype} to drive bidding from an
    evolved genome instead of a random named archetype -- used by
    evolution.py. Teams not present in `strategies` fall back to the
    normal random-archetype assignment."""
    teams = copy.deepcopy(teams)
    available = dict(players)

    for team in teams.values():
        if strategies is not None and team.name in strategies:
            team.custom_strategy = strategies[team.name]
        elif team.custom_strategy is None:
            team.archetype = rng.choice(ARCHETYPE_NAMES)

    turn_order = list(teams.keys())
    rng.shuffle(turn_order)

    draft_log = []
    idx = 0
    pick_num = 0
    safety = 0
    max_picks = cfg.NUM_TEAMS * cfg.REQUIRED_ROSTER_SIZE + len(available)
    total_initial_slots = sum(t.slots_needed for t in teams.values())

    while available and any(not t.is_done for t in teams.values()) and safety < max_picks:
        safety += 1
        nominator = turn_order[idx % len(turn_order)]
        idx += 1
        if teams[nominator].is_done:
            continue

        candidate_name = choose_nomination(nominator, teams, available, rng)
        candidate = available[candidate_name]

        remaining_slots = sum(t.slots_needed for t in teams.values())
        draft_progress = 1.0 - (remaining_slots / total_initial_slots if total_initial_slots else 0.0)

        winner, price = resolve_bid(candidate, nominator, teams, rng, draft_progress)
        team = teams[winner]

        # Hard rule (explicit, not emergent): the player that completes a
        # team's 15th and final roster slot costs their ENTIRE remaining
        # budget, overriding whatever the competitive bid settled at. This
        # is the only way to guarantee both "roster full" and "every
        # dollar spent" -- ordinary ascending-bid mechanics can't force
        # spend beyond a winning bid when nobody contests a nomination.
        forced_final_slot = team.slots_needed == 1
        if forced_final_slot:
            price = team.budget_remaining

        team.roster.append((candidate.name, candidate.position, price, candidate.projected_points))
        team.budget_remaining = round(team.budget_remaining - price, 2)
        if candidate.is_star_eligible:
            team.stars_bought += 1
        team.tilt = max(0, team.tilt - 1)

        del available[candidate_name]
        pick_num += 1
        draft_log.append({
            "pick": pick_num, "player": candidate.name, "position": candidate.position,
            "tier": candidate.tier, "nominator": nominator, "winner": winner, "price": price,
            "winner_archetype": team.strategy.name, "forced_final_slot": forced_final_slot,
        })
        if verbose:
            print(f"#{pick_num:>3} {candidate.name:<24} {candidate.position:<3} "
                  f"nom={nominator:<8} -> {winner:<8} ${price:.0f}")

    return draft_log, teams
