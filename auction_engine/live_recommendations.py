"""Live MVP Part 4: bid recommendation service.

recommended_final_bid = min(safety_adjusted_team_value_ceiling,
                             legal_max_bid_after_slot_reserves,
                             portfolio_feasibility_limit,
                             critical_confidence_limit)

Expected market price is explicitly NEVER used as a hard cap -- it only
informs the recommendation TEXT (what the room will likely pay), never
the stop price itself (what Sam is allowed/willing to pay).
"""

from __future__ import annotations

from dataclasses import dataclass

RECOMMENDATION_TYPES = (
    "STRONG_BUY_BELOW_LIMIT", "BUY_AT_DISCOUNT", "FAIR_PRICE", "PASS_ABOVE_LIMIT",
    "WAIT_FOR_ALTERNATIVE", "POSITION_NEED_PRIORITY", "TIER_CLIFF_PRIORITY",
    "NOMINATE_TO_DRAIN", "ONE_DOLLAR_OPTION", "INSUFFICIENT_EVIDENCE", "INELIGIBLE",
)


@dataclass
class BidRecommendation:
    player: str
    recommended_final_bid: float
    recommendation_type: str
    reason: str
    limiting_factor: str


def compute_recommended_bid(
    player: str,
    safety_adjusted_ceiling: float | None,
    legal_max_bid: float,
    portfolio_feasibility_limit: float | None,
    confidence: int,
    live_expected_price: float,
    current_bid: float | None = None,
    position_need_score: float = 0.0,
    tier_cliff: bool = False,
    scarcity_note: str = "",
) -> BidRecommendation:
    if safety_adjusted_ceiling is None or confidence < 3:
        return BidRecommendation(
            player=player, recommended_final_bid=0.0, recommendation_type="INSUFFICIENT_EVIDENCE",
            reason="No verified exact ceiling / too little evidence to recommend a stop price.",
            limiting_factor="confidence",
        )

    limits = {"safety_adjusted_ceiling": safety_adjusted_ceiling, "legal_max_bid": legal_max_bid}
    if portfolio_feasibility_limit is not None:
        limits["portfolio_feasibility_limit"] = portfolio_feasibility_limit
    limiting_factor = min(limits, key=lambda k: limits[k])
    recommended_bid = max(0, int(limits[limiting_factor]))  # whole-dollar

    if current_bid is not None and current_bid > recommended_bid:
        rec_type = "PASS_ABOVE_LIMIT"
        reason = (f"Current bid ${current_bid:.0f} exceeds the recommended stop ${recommended_bid} "
                  f"(limited by {limiting_factor}).")
    elif recommended_bid <= 1:
        rec_type = "ONE_DOLLAR_OPTION"
        reason = "Only a $1 flier is justified for this player given current roster fit."
    elif tier_cliff:
        rec_type = "TIER_CLIFF_PRIORITY"
        reason = f"Last comparable option at this tier -- stop ${recommended_bid} (limited by {limiting_factor})."
    elif position_need_score > 0.7:
        rec_type = "POSITION_NEED_PRIORITY"
        reason = f"Unmet starting need -- stop ${recommended_bid} (limited by {limiting_factor})."
    elif live_expected_price < recommended_bid * 0.85:
        rec_type = "BUY_AT_DISCOUNT"
        reason = (f"Live expected price ${live_expected_price:.0f} sits well below the ${recommended_bid} stop "
                  f"(limited by {limiting_factor}).")
    elif live_expected_price > recommended_bid:
        rec_type = "WAIT_FOR_ALTERNATIVE"
        reason = (f"Live expected price ${live_expected_price:.0f} already exceeds the ${recommended_bid} stop "
                  f"(limited by {limiting_factor}) -- likely to run past value.")
    else:
        rec_type = "STRONG_BUY_BELOW_LIMIT" if confidence >= 8 else "FAIR_PRICE"
        reason = f"Stop ${recommended_bid} (limited by {limiting_factor}); expected price ${live_expected_price:.0f}."

    return BidRecommendation(
        player=player, recommended_final_bid=recommended_bid, recommendation_type=rec_type,
        reason=reason, limiting_factor=limiting_factor,
    )
