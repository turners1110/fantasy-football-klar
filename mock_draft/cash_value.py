"""Phase 3A item 8: the auction-only value of one remaining dollar.

Unused cash has zero fantasy value once the auction ends -- but it isn't
worthless DURING the auction either, since it's optionality against
future opportunities. This module gives that optionality a number so
counterfactual comparisons (mock_draft/counterfactual.py) can weigh
"spend $30 now" against "keep the $30 for later" on the same scale
(utility points), instead of treating leftover cash as free.

FORMULA (documented, hand-designed, NOT empirically fitted -- a
transparent starting point, not a validated result):

    marginal_dollar_value =
        base_rate * scarcity_multiplier * pool_depth_multiplier * slots_remaining_fraction

    base_rate:
        the best remaining pool player's projected_points at a position
        this team still NEEDS (a required-starter deficit, or any
        FLEX-eligible position if no hard deficit remains), divided by
        MIN_PRICE -- "points obtainable per marginal dollar, at the
        cheapest a player can legally be won." Scaled by
        POINTS_TO_UTILITY_SCALE so it lands in the same rough magnitude
        as legal_lineup's own utility units.

    scarcity_multiplier = 1 + scarcity_risk
        (mock_draft.feasibility.position_urgency's scarcity_risk, 0-1) --
        rises as required positions run low on remaining slots to fill
        them, i.e. exactly "a scarce required position remains open."

    pool_depth_multiplier = 1 / (1 + n_relevant_alternatives / POOL_DEPTH_SOFTENER)
        falls as more real alternatives exist at the needed position(s)
        ("few useful alternatives remain" raises it; a deep pool lowers
        it) -- this is "tier cliff approaches" in miniature: the fewer
        good options left, the more one dollar of flexibility is worth.

    slots_remaining_fraction = team.slots_needed / TOTAL_ROSTER_SIZE
        falls as the roster fills up ("the auction nears completion" /
        "few roster slots remain" both push this down toward 0).

Together: the value of a dollar RISES with more slots remaining, a
thinner remaining pool at a position of need, and higher scarcity_risk;
it FALLS as the roster nears completion, as alternatives pile up, and
(implicitly, via the caller using this as a per-dollar rate rather than a
total) as a team's own cash pile grows relative to what it can usefully
spend -- excess cash does not raise the RATE, but a team's OWN excess
cash matters at the aggregate level (see item 10's diagnostics), not this
per-dollar formula.
"""

from __future__ import annotations

from . import config_bridge as cfg
from .feasibility import FLEX_ELIGIBLE, REQUIRED_STARTERS, TOTAL_ROSTER_SIZE, position_urgency

POINTS_TO_UTILITY_SCALE = 1.0  # legal_lineup's utility units already track points 1:1
POOL_DEPTH_SOFTENER = 20.0


def marginal_dollar_value(team, pool: dict) -> float:
    if team.slots_needed <= 0:
        return 0.0

    urgency = position_urgency(team.roster, team.slots_needed)
    deficit_positions = [pos for pos in REQUIRED_STARTERS if urgency[f"{pos.lower()}_deficit"] > 0]
    needed_positions = deficit_positions or list(FLEX_ELIGIBLE)

    relevant = [p for p in pool.values() if p.position in needed_positions]
    if not relevant:
        return 0.0

    best_points = max(p.projected_points for p in relevant)
    base_rate = (best_points / max(cfg.MIN_PRICE, 1.0)) * POINTS_TO_UTILITY_SCALE * 0.01

    scarcity_multiplier = 1.0 + urgency["scarcity_risk"]
    pool_depth_multiplier = 1.0 / (1.0 + len(relevant) / POOL_DEPTH_SOFTENER)
    slots_remaining_fraction = team.slots_needed / TOTAL_ROSTER_SIZE

    return base_rate * scarcity_multiplier * pool_depth_multiplier * slots_remaining_fraction
