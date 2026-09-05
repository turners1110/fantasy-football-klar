"""V2.1 Part 5: Monte Carlo distribution quality classification.

Pure function, no state. Classifies a player's simulated price
distribution so weak/flat/insufficient results are never displayed with
false precision or allowed to influence a dollar recommendation. This
module NEVER touches compute_governed_dollar_ceiling or any dollar
ceiling -- Monte Carlo output is display-only (expected market price,
draft probability) per the spec's explicit rule: "Monte Carlo must not
raise team value."
"""
from __future__ import annotations

from dataclasses import dataclass

QUALITY_LABELS = (
    "HIGH_QUALITY_DISTRIBUTION", "LIMITED_DISTRIBUTION", "DEGENERATE_DIRECTIONAL_ONLY",
    "INSUFFICIENT_SIMULATED_SALES", "UNSTABLE_DISTRIBUTION",
)


@dataclass
class DistributionQuality:
    label: str
    display_percentiles: list  # which of p10/p25/p50/p75/p90 to actually show
    confidence_multiplier: float  # 1.0 = full confidence, lower = reduced
    note: str


def classify_distribution(
    sale_count: int, p10: float | None, p90: float | None,
    first_half_p50: float | None = None, second_half_p50: float | None = None,
) -> DistributionQuality:
    if sale_count < 20:
        return DistributionQuality(
            label="INSUFFICIENT_SIMULATED_SALES", display_percentiles=[], confidence_multiplier=0.0,
            note=f"Only {sale_count} conditional sales (<20 required) -- draft probability and sample count only, no percentiles.",
        )

    # Instability check first -- an unstable range must not be presented
    # as trustworthy even if it happens to look wide.
    if first_half_p50 is not None and second_half_p50 is not None and first_half_p50 > 0:
        pct_diff = abs(second_half_p50 - first_half_p50) / first_half_p50
        if pct_diff > 0.20 or abs(second_half_p50 - first_half_p50) > 10:
            return DistributionQuality(
                label="UNSTABLE_DISTRIBUTION", display_percentiles=["p10", "p25", "p50", "p75", "p90"],
                confidence_multiplier=0.3,
                note=f"First-half vs second-half seed P50 differs by {pct_diff:.0%} (${abs(second_half_p50-first_half_p50):.0f}) -- "
                     "shown for reference only, must NOT support a higher recommended stop.",
            )

    spread = (p90 - p10) if (p10 is not None and p90 is not None) else None
    if spread is not None and spread < 2:
        return DistributionQuality(
            label="DEGENERATE_DIRECTIONAL_ONLY", display_percentiles=["p50"], confidence_multiplier=0.4,
            note=f"P90-P10 spread is only ${spread:.1f} (<$2) -- one directional expected-price estimate only; "
                 "flat P10/P90 must not be presented as meaningful uncertainty.",
        )
    # Per spec: HIGH_QUALITY requires BOTH >=50 sales AND spread >= $5;
    # everything else with sufficient sales (>=20) and spread >= $2 is
    # LIMITED_DISTRIBUTION (the spec's own bands leave a $4-$5 gap for
    # 20-49 sales -- treated as LIMITED here, a conservative choice: never
    # rounds a marginal case UP to HIGH_QUALITY).
    if sale_count >= 50 and spread is not None and spread >= 5:
        return DistributionQuality(
            label="HIGH_QUALITY_DISTRIBUTION", display_percentiles=["p10", "p25", "p50", "p75", "p90"],
            confidence_multiplier=1.0, note=f"P90-P10 spread ${spread:.1f}, {sale_count} sales, stable -- full range shown.",
        )
    spread_str = f"{spread:.1f}" if spread is not None else "n/a"
    return DistributionQuality(
        label="LIMITED_DISTRIBUTION", display_percentiles=["p25", "p50", "p75"], confidence_multiplier=0.7,
        note=f"P90-P10 spread ${spread_str}, {sale_count} sales -- "
             "does not meet the >=50-sales/>=$5-spread bar for HIGH_QUALITY -- limited range shown.",
    )
