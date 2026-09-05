"""Single mock-auction run: nominate -> open ascending bidding -> resolve
-> repeat until every team's required roster is full (or the pool runs
out). Turn order is round-robin regardless of who wins each pick, per the
league's actual convention (confirmed default -- flag if your league
actually does 'winner nominates next').

PHASE 2 CHANGE: the forced-final-slot rule (a team's last roster slot cost
its entire remaining budget, overriding the competitive bid) has been
REMOVED. It was manufacturing artificial prices with no bidding basis --
see outputs/auction_rebuild/audit/current_architecture.md section 11 for
why it existed. Every sale price now comes only from resolve_bid's
competitive process; an uncontested nomination sells for $1, and unspent
cash at the end of a draft is legal and expected.

PHASE 2B CHANGE (this was phase 2's own documented gap, and phase 2's
smoke test proved it real: 27 of 240 simulated teams finished missing a
required QB or TE while holding a full, budget-legal 15-player roster --
e.g. one team rostered 11 WR / 3 RB / 1 QB / 0 TE). Every bid is now
gated through mock_draft.feasibility.check_roster_completion_feasibility
BEFORE it is allowed to raise, so a team can never legally construct a
roster with no path to a legal 1QB/2RB/2WR/1TE/3FLEX/15-total lineup.
Illegal construction is PREVENTED during bidding, not repaired afterward.
A companion incremental-utility gate stops a team paying above $1 for a
player who adds zero legal-lineup value (the mechanism behind phase 2's
five-QB roster: a third-string QB is worthless under legal-lineup scoring
but nothing previously stopped a team paying for one anyway).

max_bid_cap (models.py:Team.max_bid_cap) still reserves $1 for every
OTHER remaining roster slot; the feasibility gate is a second, independent
check on top of that cash reserve -- it additionally verifies enough
eligible players remain at each required position and enough roster slots
remain to fit them.
"""

from __future__ import annotations

import copy

import numpy as np

from . import config_bridge as cfg
from . import feasibility as feas_mod
from .archetypes import ARCHETYPE_NAMES, Archetype
from .feasibility import check_roster_completion_feasibility
from .legal_lineup import partial_lineup_value
from .models import Player, Team
from .nomination import choose_nomination
from .valuation import compute_willingness

BID_SAFETY_ROUNDS = 60

# ---------------------------------------------------------------------------
# Bidder-count definitions (official data / simulation repair, Part 9).
#
# The OLD `bidder_count` conflated "touched the bid once" with "credible
# bidder" -- a team that raised the price by $1 before immediately getting
# priced out counted the same as the team that fought to the final dollar.
# Reviewing ten simulated drafts found this made ~12-bidder counts show up
# on nearly every sale, including $100+ ones, which is not a meaningful
# signal. These thresholds define the real tiers requested; they are module
# constants (not hardcoded inline) specifically so tests can assert on them
# and so they can be tuned without touching resolve_bid's logic.
# ---------------------------------------------------------------------------
CREDIBLE_BIDDER_SHARE = 0.85          # a team is "credible" if its own computed
                                       # ceiling reached >= 85% of the final sale price...
CREDIBLE_BIDDER_DOLLAR_RANGE = 5.0    # ...OR its ceiling was within $5 of the final
                                       # sale price (whichever is more permissive --
                                       # matters at low dollar amounts where 85% is a
                                       # tiny absolute gap).
FINAL_STAGE_DOLLAR_BAND = 3.0         # a team is "final-stage" if its own ceiling
                                       # was within $3 of the final sale price -- i.e.
                                       # it was still active in the very last stretch
                                       # of bidding, not just credible at some point.


def _incremental_utility(team: Team, candidate: Player, price: float) -> float:
    """Partial-lineup value with vs. without the candidate on this team's
    roster right now. <= 0 means the legal-lineup scorer gives this player
    no credit at all -- typically a 3rd+ QB, or a bench slot that would be
    filled by a strictly-worse player than what's already rostered.

    PHASE 3A FIX: this used to call build_production_lineup, whose
    total_roster_utility is hard-zeroed to 0 for ANY illegal (incomplete)
    roster. Since most teams don't hold a full legal lineup for most of a
    live auction, that meant before=0 and after=0 -- and therefore a
    reported $0 of marginal value -- for perfectly good players who simply
    hadn't yet completed the team's lineup by themselves (e.g. a 200-point
    RB added to a QB-only roster). See legal_lineup.partial_lineup_value's
    docstring for the full diagnosis; this was the dominant driver of the
    "zero incremental utility" bid-gate over-blocking phase 3A's own
    diagnostics found (outputs/auction_rebuild/phase3a/
    unspent_cash_decomposition.csv), not the bench-weight tuning that was
    tried first. build_production_lineup itself is untouched and remains
    correct for FINAL-roster fitness (evolution.py, best_response.py)."""
    before = partial_lineup_value(team.roster)
    after = partial_lineup_value(
        team.roster + [(candidate.name, candidate.position, price, candidate.projected_points)]
    )
    return after - before


def _team_can_ever_take(team: Team, candidate: Player, available: dict, position_max: dict | None) -> bool:
    """Cheapest possible legality check (candidate at $1) -- used to decide
    whether a team belongs in the bidder pool AT ALL for this candidate,
    independent of what they'd actually be willing to pay."""
    feas = check_roster_completion_feasibility(
        team.roster, team.budget_remaining, team.slots_needed, available,
        candidate_player=candidate, candidate_price=float(cfg.MIN_PRICE), position_max=position_max,
    )
    return feas.is_feasible


def resolve_bid(
    candidate: Player, nominator: str, teams: dict[str, Team], rng: np.random.Generator,
    draft_progress: float, available: dict[str, Player],
    position_max: dict | None = None,
    bid_stats: dict | None = None,
    bid_diagnostics_log: list | None = None,
) -> dict:
    """Open ascending auction starting at $1 with the nominator as the
    default winner if nobody raises. Returns a dict with winner, price,
    and the bid-transparency fields required by the rebuild spec
    (bidder_count, second_highest_bid) -- every sale is organic by
    construction now that forced-final-slot pricing is gone.

    `winner` is None if NO team (nominator included) can legally take this
    candidate at any price -- callers must handle that by not completing a
    sale (see run_single_auction's NO_LEGAL_BUYER handling) rather than
    forcing an illegal purchase.

    bid_stats: optional {team_name: {bids, wins, blocked_zero_utility,
    blocked_feasibility, blocked_budget, blocked_roster_cap}} dict,
    mutated in place if given -- phase 3A diagnostic instrumentation for
    outputs/auction_rebuild/phase3a/unspent_cash_decomposition.csv. Never
    required; every existing caller is unaffected.

    bid_diagnostics_log: optional list, appended to (once per resolve_bid
    call) if given -- phase 3C item 7 instrumentation for
    outputs/auction_rebuild/phase3c/top_sale_bid_decomposition.csv.
    Captures the willingness component breakdown (see
    valuation.compute_willingness's own diagnostics param) for every team
    that successfully raised during this nomination, by reusing the
    SAME compute_willingness call the bidding loop already makes (no
    extra calls, no extra RNG draws, no behavior change) -- purely
    additive. Never required; every existing caller is unaffected."""
    def _stat(name: str, field: str) -> None:
        if bid_stats is not None:
            bid_stats.setdefault(name, {
                "bids": 0, "wins": 0, "blocked_zero_utility": 0,
                "blocked_feasibility": 0, "blocked_budget": 0, "blocked_roster_cap": 0,
            })[field] += 1

    eligible = [name for name, t in teams.items() if not t.is_done]
    legally_biddable = [name for name in eligible if _team_can_ever_take(teams[name], candidate, available, position_max)]

    if not legally_biddable:
        return {
            "winner": None, "price": None, "bidder_count": 0, "second_highest_bid": 0.0,
            "eligible_team_count": 0, "positive_interest_count": 0, "opening_bidder_count": 0,
            "credible_bidder_count": 0, "final_stage_bidder_count": 0,
            "winning_team": None, "winning_ceiling": 0.0, "second_highest_credible_ceiling": 0.0,
            "sale_price": None,
            "sale_is_organic": False, "sale_has_competing_bid": False,
            "block_reason": "POSITIONAL_INFEASIBILITY",
        }

    current_leader = nominator if nominator in legally_biddable else legally_biddable[0]
    current_bid = float(cfg.MIN_PRICE)
    second_highest = 0.0  # highest amount reached by anyone OTHER than the eventual winner
    bidders = {current_leader}  # "opening_bidder_count" -- touched the bid at least once
    positive_interest = set()   # willingness ever exceeded the $1 floor, whether or not they ever raised
    team_ceiling: dict[str, float] = {}  # last-computed real max_can_pay per team this nomination
    team_diagnostics: dict[str, dict] = {}  # last-seen willingness breakdown per team this nomination

    changed = True
    rounds = 0
    while changed and rounds < BID_SAFETY_ROUNDS:
        changed = False
        rounds += 1
        for name in legally_biddable:
            if name == current_leader:
                continue
            team = teams[name]
            diag = {} if bid_diagnostics_log is not None else None
            willingness = compute_willingness(team, candidate, rng, draft_progress, diagnostics=diag, available=available)
            if diag is not None:
                team_diagnostics[name] = diag
            cap = team.max_bid_cap()
            max_can_pay_pre_utility = min(willingness, cap)
            if willingness > cfg.MIN_PRICE:
                positive_interest.add(name)

            # Zero/negative incremental-utility gate: a limited phase-2B
            # safety check (not the full team-specific bid-ceiling engine)
            # -- if the legal-lineup scorer credits this player with no
            # marginal value to THIS team's roster right now, they may
            # still pick it up uncontested for $1, but never bid above it.
            utility_blocked = _incremental_utility(team, candidate, max_can_pay_pre_utility) <= 0
            max_can_pay = min(max_can_pay_pre_utility, float(cfg.MIN_PRICE)) if utility_blocked else max_can_pay_pre_utility
            team_ceiling[name] = max_can_pay  # last-seen real ceiling, per Part 9's tiered definitions

            if max_can_pay <= current_bid:
                if utility_blocked and max_can_pay_pre_utility > current_bid:
                    _stat(name, "blocked_zero_utility")
                elif cap <= current_bid:
                    _stat(name, "blocked_budget")
                continue

            # Positional-feasibility gate at the actual price being considered.
            feas = check_roster_completion_feasibility(
                team.roster, team.budget_remaining, team.slots_needed, available,
                candidate_player=candidate, candidate_price=max_can_pay, position_max=position_max,
            )
            if not feas.is_feasible:
                if feas.failure_reason == "POSITION_CAP_EXCEEDED":
                    _stat(name, "blocked_roster_cap")
                else:
                    _stat(name, "blocked_feasibility")
                continue

            archetype = team.strategy
            increment = 1.0
            if rng.random() < archetype.jump_bid_prob:
                increment = float(rng.integers(2, 6))
            new_bid = min(current_bid + increment, max_can_pay)
            if new_bid > current_bid:
                bidders.add(name)
                _stat(name, "bids")
                # The team being outbid held `current_bid` as their high point.
                second_highest = max(second_highest, current_bid)
                current_bid = new_bid
                current_leader = name
                changed = True

    _stat(current_leader, "wins")

    # ---- Part 9 tiered bidder-count fields -------------------------------
    # winning_ceiling: the winner's own last-computed real ceiling if we
    # ever evaluated them as a challenger earlier in the loop; if they won
    # uncontested as the default nominator and were never evaluated, their
    # true ceiling is unknown -- current_bid (what they actually paid) is
    # the only lower-bound-safe value available, so it's used as a
    # disclosed fallback rather than a fabricated number.
    winning_ceiling = team_ceiling.get(current_leader, current_bid)
    non_winner_ceilings = [c for name, c in team_ceiling.items() if name != current_leader]
    second_highest_credible_ceiling = max(non_winner_ceilings) if non_winner_ceilings else 0.0

    def _is_credible(ceiling: float) -> bool:
        share_threshold = current_bid * CREDIBLE_BIDDER_SHARE
        dollar_threshold = current_bid - CREDIBLE_BIDDER_DOLLAR_RANGE
        return ceiling >= share_threshold or ceiling >= dollar_threshold

    def _is_final_stage(ceiling: float) -> bool:
        return ceiling >= current_bid - FINAL_STAGE_DOLLAR_BAND

    credible_bidders = {name for name, c in team_ceiling.items() if _is_credible(c)}
    credible_bidders.add(current_leader)  # the winner is always credible by definition
    final_stage_bidders = {name for name, c in team_ceiling.items() if _is_final_stage(c)}
    final_stage_bidders.add(current_leader)

    if bid_diagnostics_log is not None:
        willingness_values = sorted(
            (d["final_willingness"] for d in team_diagnostics.values() if d.get("final_willingness") is not None),
            reverse=True,
        )
        bid_diagnostics_log.append({
            "player": candidate.name, "position": candidate.position, "winner": current_leader,
            "sale_price": current_bid, "bidder_count": len(bidders),
            "winner_diagnostics": team_diagnostics.get(current_leader),
            "final_highest_willingness": willingness_values[0] if willingness_values else None,
            "final_second_willingness": willingness_values[1] if len(willingness_values) > 1 else None,
        })
    return {
        "winner": current_leader,
        "price": current_bid,
        # LEGACY fields, kept for backward compatibility with existing
        # consumers -- "bidder_count" is exactly "opening_bidder_count"
        # (touched the bid at least once, no credibility threshold).
        "bidder_count": len(bidders),
        "second_highest_bid": second_highest,
        # Part 9 tiered bidder-count fields (see module-level threshold
        # constants CREDIBLE_BIDDER_SHARE / CREDIBLE_BIDDER_DOLLAR_RANGE /
        # FINAL_STAGE_DOLLAR_BAND for the exact definitions used):
        "eligible_team_count": len(legally_biddable),
        "positive_interest_count": len(positive_interest),
        "opening_bidder_count": len(bidders),
        "credible_bidder_count": len(credible_bidders),
        "final_stage_bidder_count": len(final_stage_bidders),
        "winning_team": current_leader,
        "winning_ceiling": winning_ceiling,
        "second_highest_credible_ceiling": second_highest_credible_ceiling,
        "sale_price": current_bid,
        "sale_is_organic": True,
        "sale_has_competing_bid": len(bidders) > 1,
        "block_reason": None,
    }


def run_single_auction(
    players: dict[str, Player], teams: dict[str, Team], rng: np.random.Generator,
    verbose: bool = False, strategies: dict[str, Archetype] | None = None,
    unsold_log: list | None = None,
    enable_position_max: bool = True,
    position_max: dict | None = None,
    bid_stats: dict | None = None,
    bid_diagnostics_log: list | None = None,
):
    """strategies: optional {team_name: Archetype} to drive bidding from an
    evolved genome instead of a random named archetype -- used by
    evolution.py. Teams not present in `strategies` fall back to the
    normal random-archetype assignment.

    unsold_log: optional list -- if given, every player nobody could
    legally take (see resolve_bid's NO_LEGAL_BUYER handling) is appended
    to it in place. Return signature is unchanged ((draft_log, teams)) so
    this is backward compatible with every existing caller; pass a list
    only if you want to inspect unsold players (e.g. the smoke-test
    reporting script).

    enable_position_max/position_max: soft roster-position caps (default
    feasibility.DEFAULT_POSITION_MAX -- history-grounded, see
    outputs/auction_rebuild/audit/historical_roster_position_counts.csv).
    Pass enable_position_max=False to run without them (phase 2B requires
    testing both configurations).

    bid_diagnostics_log: optional list, threaded straight through to every
    resolve_bid call -- see resolve_bid's own docstring. Never required;
    every existing caller is unaffected."""
    active_position_max = position_max if position_max is not None else (
        feas_mod.DEFAULT_POSITION_MAX if enable_position_max else None
    )
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
    max_picks = (cfg.NUM_TEAMS * cfg.REQUIRED_ROSTER_SIZE + len(available)) * 2
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

        sale = resolve_bid(
            candidate, nominator, teams, rng, draft_progress, available, active_position_max, bid_stats,
            bid_diagnostics_log=bid_diagnostics_log,
        )
        winner = sale["winner"]

        if winner is None:
            # No team (nominator included) can legally take this player at
            # any price right now -- it goes unsold rather than being
            # forced onto a roster that would become illegal. Removed from
            # the pool so it can't cause an infinite re-nomination loop.
            if unsold_log is not None:
                unsold_log.append({
                    "player": candidate.name, "position": candidate.position,
                    "nominating_team": nominator, "reason": sale["block_reason"],
                })
            del available[candidate_name]
            continue

        price = sale["price"]
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
