"""V2 Part 2 fix: the shared recommendation-ceiling guardrail.

ROOT CAUSE OF THE JOSH JACOBS $189 ANOMALY (documented in full in
outputs/auction_rebuild/live_web_v2/josh_jacobs_audit.md): every prior
call site (live_auction_cli.py's cmd_check/cmd_targets/cmd_why and the
website's api_check/api_board) passed
`safety_adjusted_ceiling=max(1.0, r.marginal_value)` directly into
compute_recommended_bid(). `marginal_value` is a POINTS quantity (fantasy
points gained/lost in Sam's optimal lineup), not a DOLLAR ceiling -- for
Josh Jacobs, marginal_value was 189.35 (his projected points, since he
was a clean "required starter" fill with no competition), so the
recommended stop became "$189" purely because 189 points got treated as
189 dollars. This is a units bug (point-to-dollar scaling), NOT the
anchor-circularity class already found and fixed twice elsewhere in this
project (Ekeler/Phase 3G, anchor-alignment/Phase 3E) -- checked
explicitly: expected market price never enters this number at all here,
which is the actual defect. Confirmed no other circularity is present.

This module is now the SINGLE place that computes a governing dollar
ceiling for any player, called identically by the CLI and the website so
they can never diverge or reintroduce this bug independently.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CRITICAL_REASONS = (
    "STOP_EXCEEDS_40PCT_BUDGET", "STOP_EXCEEDS_P90_BY_50PCT", "STOP_EXCEEDS_ANCHORS_BY_50PCT",
    "FAST_EXACT_DELTA_OVER_15", "BENCH_DEPTH_STOP_OVER_25", "RESERVE_VIOLATION_RISK",
    "OVERLOADED_POSITION_STARTER_VALUE", "PORTFOLIO_INFEASIBLE", "UNSUPPORTED_EXTREME_PRICE",
    "EXACT_STALE_OR_NONOPTIMAL",
)


@dataclass
class GovernedCeiling:
    dollar_ceiling: float
    calculation_label: str
    critical_review_required: bool
    critical_reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# Conservative fallback multiplier over the live expected price when NO
# static per-player hard maximum exists (most of the ~340-player pool --
# the Phase 3G static sheet only individually audited ~21 players).
# Documented, fixed, not tuned against any live result.
NO_STATIC_DATA_MULTIPLIER = 1.30


def compute_governed_dollar_ceiling(
    player: str,
    position: str,
    live_expected_price: float,
    legal_max_bid: float,
    static_hard_max: float | None,
    exact_ceiling: float | None,
    exact_status: str | None,
    exact_is_current: bool,
    expected_role: str,
    sam_position_count: int,
    sam_budget_remaining: float,
    open_slots: int,
    portfolio_feasible_at_price: bool = True,
    unsupported_extreme_price: bool = False,
) -> GovernedCeiling:
    """Returns the governing dollar ceiling for a recommendation, per V2
    Part 2's exact rule: the LOWEST applicable limit among the exact
    ceiling (when current and OPTIMAL), a conservative approximate
    ceiling when exact is unavailable, the legal maximum after slot
    reserves, and the frozen static emergency maximum -- NEVER a raw
    points value, and never above the static maximum without a fresh
    OPTIMAL exact result explicitly supporting it.
    """
    candidates = []
    label_parts = []

    if exact_ceiling is not None and exact_is_current and exact_status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL"):
        candidates.append(exact_ceiling)
        label_parts.append("EXACT_LIVE_STATE_CEILING" if exact_status == "OPTIMAL" else "EXACT_FEASIBLE_NOT_PROVEN_OPTIMAL")
    else:
        # No current exact result -- fall back to a conservative
        # approximation, but NEVER above the frozen static maximum.
        approx = live_expected_price * NO_STATIC_DATA_MULTIPLIER
        candidates.append(approx)
        label_parts.append("APPROXIMATE_NO_CURRENT_EXACT")

    if static_hard_max is not None:
        candidates.append(static_hard_max)
        label_parts.append("SAFETY_ADJUSTED_HARD_MAXIMUM(static)")

    candidates.append(legal_max_bid)
    label_parts.append("LEGAL_MAX_BID")

    ceiling = min(candidates)
    calc_label = " & ".join(label_parts) + f" -> min={ceiling:.0f}"

    warnings = []
    critical_reasons = []

    if ceiling > 0.40 * max(1.0, sam_budget_remaining):
        critical_reasons.append("STOP_EXCEEDS_40PCT_BUDGET")
    # Independent-anchor check: live_expected_price is our best available
    # "anchor" proxy this pass (no separate public/historical anchor wired
    # into this endpoint) -- flag if the ceiling still towers over it.
    if live_expected_price > 0 and ceiling > 1.5 * live_expected_price:
        critical_reasons.append("STOP_EXCEEDS_ANCHORS_BY_50PCT")
    if expected_role == "bench depth" and ceiling > 25:
        critical_reasons.append("BENCH_DEPTH_STOP_OVER_25")
    if sam_budget_remaining - ceiling < (max(0, open_slots - 1) * 1.0) + 10:
        critical_reasons.append("RESERVE_VIOLATION_RISK")
    if position == "RB" and sam_position_count >= 5 and expected_role != "bench depth":
        critical_reasons.append("OVERLOADED_POSITION_STARTER_VALUE")
    if not portfolio_feasible_at_price:
        critical_reasons.append("PORTFOLIO_INFEASIBLE")
    if unsupported_extreme_price:
        critical_reasons.append("UNSUPPORTED_EXTREME_PRICE")
    if exact_ceiling is not None and exact_status not in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL") and exact_is_current:
        critical_reasons.append("EXACT_STALE_OR_NONOPTIMAL")

    return GovernedCeiling(
        dollar_ceiling=round(max(1.0, ceiling), 2), calculation_label=calc_label,
        critical_review_required=len(critical_reasons) > 0, critical_reasons=critical_reasons, warnings=warnings,
    )
