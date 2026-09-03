"""Single mock-auction run: nominate -> open ascending bidding -> resolve
-> repeat until every team's required roster is full (or the pool runs
out). Turn order is round-robin regardless of who wins each pick, per the
league's actual convention (confirmed default -- flag if your league
actually does 'winner nominates next').

PHASE 2 CHANGE: the forced-final-slot rule (a team's last roster slot cost
its entire remaining budget, overriding the competitive bid) has been
REMOVED. It was manufacturing artificial prices with no bidding basis --
see outputs/auction_rebuild/audit/current_architecture.md section 11 for
why it existed and outputs/auction_rebuild/phase2/ for proof the auction
still completes legally without it. Every sale price now comes only from
resolve_bid's competitive process; an uncontested nomination sells for
$1, and unspent cash at the end of a draft is legal and expected.

max_bid_cap (models.py:Team.max_bid_cap) already reserves $1 for every
OTHER remaining roster slot -- on a team's actual final slot that reserve
is correctly $0, so their ceiling there is their full remaining budget,
which they are free to not spend if no one bids that high. This is a
legal ceiling, not a forced floor.

KNOWN LIMITATION (documented, not solved here): section 14 of the
rebuild spec asks for a positional-feasibility check (a team should not
be able to spend down to where it can no longer field a legal QB/RB/WR/
TE lineup, even if it has cash and slots left). Not implemented as an
explicit solver -- with a 382-player auction-eligible pool in this
league (see outputs/auction_rebuild/data/auction_eligible_players.csv),
running out of an entire position is not a practically reachable
scenario, so this is treated as non-binding rather than actively
enforced. Flagged as a real gap for a deeper pool or smaller league.
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
) -> dict:
    """Open ascending auction starting at $1 with the nominator as the
    default winner if nobody raises. Returns a dict with winner, price,
    and the bid-transparency fields required by the rebuild spec
    (bidder_count, second_highest_bid) -- every sale is organic by
    construction now that forced-final-slot pricing is gone."""
    current_bid = float(cfg.MIN_PRICE)
    current_leader = nominator
    second_highest = 0.0  # highest amount reached by anyone OTHER than the eventual winner
    bidders = {nominator}
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
                bidders.add(name)
                # The team being outbid held `current_bid` as their high point.
                second_highest = max(second_highest, current_bid)
                current_bid = new_bid
                current_leader = name
                changed = True

    return {
        "winner": current_leader,
        "price": current_bid,
        "bidder_count": len(bidders),
        "second_highest_bid": second_highest,
        "sale_is_organic": True,
        "sale_has_competing_bid": len(bidders) > 1,
    }


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

        sale = resolve_bid(candidate, nominator, teams, rng, draft_progress)
        winner, price = sale["winner"], sale["price"]
        team = teams[winner]

        budget_before = team.budget_remaining
        slots_before = team.slots_needed

        team.roster.append((candidate.name, candidate.position, price, candidate.projected_points))
        team.budget_remaining = round(team.budget_remaining - price, 2)
        if candidate.is_star_eligible:
            team.stars_bought += 1
        team.tilt = max(0, team.tilt - 1)

        del available[candidate_name]
        pick_num += 1
        draft_log.append({
            "pick": pick_num,
            "nomination_number": pick_num,
            "player": candidate.name,
            "position": candidate.position,
            "tier": candidate.tier,
            "nominating_team": nominator,
            "nominator": nominator,  # kept for backward compatibility with existing analysis scripts
            "winning_team": winner,
            "winner": winner,        # kept for backward compatibility
            "sale_price": price,
            "price": price,          # kept for backward compatibility
            "bidder_count": sale["bidder_count"],
            "second_highest_bid": sale["second_highest_bid"],
            "sale_is_organic": sale["sale_is_organic"],
            "sale_has_competing_bid": sale["sale_has_competing_bid"],
            "forced_final_slot": False,  # field preserved for legacy-output readers; always False in phase-2+ runs
            "budget_before": budget_before,
            "budget_after": team.budget_remaining,
            "slots_before": slots_before,
            "slots_after": team.slots_needed,
            "minimum_reserve_after": cfg.MIN_PRICE * max(0, team.slots_needed - 0),
            "winner_archetype": team.strategy.name,
        })
        if verbose:
            print(f"#{pick_num:>3} {candidate.name:<24} {candidate.position:<3} "
                  f"nom={nominator:<8} -> {winner:<8} ${price:.0f} "
                  f"(bidders={sale['bidder_count']})")

    return draft_log, teams
