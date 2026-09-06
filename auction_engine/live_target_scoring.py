"""Sunday Final Build Stage 1: multi-factor target decision score.

Replaces raw-marginal-value ranking (which over-favored any player at a
position Sam has zero players at, since the greedy lineup optimizer gives
a "required starter" full-value credit to literally any warm body filling
an empty slot). This module blends several independently-visible
components into one score, with position-need capped so it cannot alone
outrank strong player quality + good price evidence.

Every component is returned alongside the total score -- callers must
display them separately, not just the final number.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Position-need weight is capped at this share of the combined score so a
# merely-open slot can never outrank a genuinely stronger, cheaper player.
MAX_POSITION_NEED_SHARE = 0.15

RECOMMENDATION_CLASSES = (
    "PRIORITY_VALUE", "STARTER_NEED", "TIER_CLIFF", "BUY_AT_DISCOUNT", "FAIR_PRICE",
    "WAIT_FOR_ALTERNATIVE", "BENCH_DEPTH_ONLY", "PASS_ABOVE_LIMIT", "NOMINATE_TO_DRAIN",
    "INSUFFICIENT_EVIDENCE",
)


@dataclass
class TargetScore:
    player: str
    position: str
    total_score: float
    recommendation_class: str
    # components, all shown separately per the spec's explicit requirement:
    expected_surplus_at_price: float
    starting_lineup_gain: float
    team_specific_value: float
    role_probability_score: float  # blends starting/flex/bench probability into one 0-1 scalar
    scarcity_score: float
    tier_cliff_bonus: float
    remaining_alternatives_count: int
    price_confidence: float
    position_need_score: float  # capped contribution
    price_evidence_score: float  # cheaper-relative-to-ceiling = higher
    bench_probability: float
    portfolio_risk_penalty: float


def _role_probability_score(expected_role: str) -> float:
    return {"required starter": 1.0, "FLEX starter": 0.75, "bench depth": 0.15}.get(expected_role, 0.3)


def compute_target_score(
    player: str, position: str,
    team_specific_value_dollars: float, expected_role: str,
    expected_market_price_dollars: float, exact_or_approximate_ceiling_dollars: float,
    hard_max: float | None,
    remaining_alternatives_count: int,
    is_last_legal_alternative: bool,
    price_confidence: float,  # 0-1
    position_need_score: float,  # 0-1, raw (uncapped) need signal
    portfolio_paths_broken_if_missed: int,
) -> TargetScore:
    """V3.1 CLEANUP D: parameter names are explicit about units (dollars
    vs points) -- this function is always called with a DOLLAR value in
    team_specific_value_dollars (the governed team-specific ceiling),
    never raw fantasy points; a caller that accidentally passes points
    here is now much harder to miss in review, since the parameter name
    itself asserts the unit. Do not rename these back to unit-ambiguous
    names like the old `marginal_value`."""
    ceiling = exact_or_approximate_ceiling_dollars if exact_or_approximate_ceiling_dollars else 1.0
    expected_surplus_dollars = team_specific_value_dollars - expected_market_price_dollars
    starting_lineup_gain = team_specific_value_dollars if expected_role in ("required starter", "FLEX starter") else 0.0
    role_prob = _role_probability_score(expected_role)
    bench_probability = 1.0 - role_prob if expected_role != "required starter" else 0.0

    # price evidence: how much cheaper the expected market price is than the ceiling
    price_evidence = max(-1.0, min(1.0, (ceiling - expected_market_price_dollars) / ceiling)) if ceiling > 0 else 0.0

    scarcity_score = 1.0 if is_last_legal_alternative else max(0.0, 1.0 - 0.15 * remaining_alternatives_count)
    tier_cliff_bonus = 0.3 if is_last_legal_alternative else 0.0

    # position need contribution is capped to MAX_POSITION_NEED_SHARE of the total weight
    raw_need = max(0.0, min(1.0, position_need_score))
    capped_need_contribution = raw_need * MAX_POSITION_NEED_SHARE

    portfolio_risk_penalty = 0.1 * portfolio_paths_broken_if_missed

    # Combine: quality/price evidence dominates (0.75 share), need capped at 0.25 share.
    # IMPORTANT: normalize expected_surplus against a FIXED absolute scale
    # (not each candidate's own ceiling) -- normalizing by the candidate's
    # own ceiling let a small-ceiling player trivially max out this term
    # regardless of his tiny absolute value, which is exactly the
    # empty-slot-bias bug this scoring function exists to fix.
    ABSOLUTE_VALUE_SCALE = 150.0
    quality_component = (
        0.40 * max(0.0, min(1.0, expected_surplus_dollars / ABSOLUTE_VALUE_SCALE))
        + 0.15 * role_prob
        + 0.15 * max(0.0, price_evidence)
        + 0.10 * scarcity_score
        + 0.10 * price_confidence
    )  # sums to 0.90 max quality weight before need
    total_score = round(quality_component * (1 - MAX_POSITION_NEED_SHARE) / 0.90 + capped_need_contribution
                         + tier_cliff_bonus + portfolio_risk_penalty, 4)

    hard_max_val = hard_max if hard_max is not None else ceiling
    if expected_market_price_dollars > hard_max_val:
        rec = "PASS_ABOVE_LIMIT"
    elif expected_role == "bench depth" and not is_last_legal_alternative:
        rec = "BENCH_DEPTH_ONLY"
    elif is_last_legal_alternative:
        rec = "TIER_CLIFF"
    elif raw_need > 0.7 and expected_role != "bench depth":
        rec = "STARTER_NEED"
    elif price_evidence > 0.25 and expected_surplus_dollars > 0:
        rec = "BUY_AT_DISCOUNT"
    elif expected_surplus_dollars > ceiling * 0.3:
        rec = "PRIORITY_VALUE"
    elif expected_surplus_dollars >= 0:
        rec = "FAIR_PRICE"
    elif ceiling <= 2:
        rec = "INSUFFICIENT_EVIDENCE"
    else:
        rec = "WAIT_FOR_ALTERNATIVE"

    return TargetScore(
        player=player, position=position, total_score=total_score, recommendation_class=rec,
        expected_surplus_at_price=round(expected_surplus_dollars, 2), starting_lineup_gain=round(starting_lineup_gain, 2),
        team_specific_value=round(team_specific_value_dollars, 2), role_probability_score=round(role_prob, 3),
        scarcity_score=round(scarcity_score, 3), tier_cliff_bonus=tier_cliff_bonus,
        remaining_alternatives_count=remaining_alternatives_count, price_confidence=round(price_confidence, 3),
        position_need_score=round(capped_need_contribution, 4), price_evidence_score=round(price_evidence, 3),
        bench_probability=round(bench_probability, 3), portfolio_risk_penalty=round(portfolio_risk_penalty, 3),
    )
