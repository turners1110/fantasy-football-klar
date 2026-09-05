"""V2.1 Part 5: Monte Carlo distribution quality classification tests."""
from __future__ import annotations

from auction_engine.monte_carlo_quality import classify_distribution


def test_insufficient_sales():
    q = classify_distribution(sale_count=10, p10=50, p90=60)
    assert q.label == "INSUFFICIENT_SIMULATED_SALES"
    assert q.display_percentiles == []
    assert q.confidence_multiplier == 0.0


def test_degenerate_directional_only():
    q = classify_distribution(sale_count=100, p10=89, p90=90)
    assert q.label == "DEGENERATE_DIRECTIONAL_ONLY"
    assert q.display_percentiles == ["p50"]
    assert "p10" not in q.display_percentiles and "p90" not in q.display_percentiles


def test_high_quality_requires_both_sample_size_and_spread():
    q = classify_distribution(sale_count=100, p10=40, p90=60)
    assert q.label == "HIGH_QUALITY_DISTRIBUTION"
    assert set(q.display_percentiles) == {"p10", "p25", "p50", "p75", "p90"}


def test_high_quality_denied_if_sample_size_too_low_despite_wide_spread():
    q = classify_distribution(sale_count=25, p10=40, p90=60)
    assert q.label != "HIGH_QUALITY_DISTRIBUTION"
    assert q.label == "LIMITED_DISTRIBUTION"


def test_limited_distribution_reduced_confidence():
    q = classify_distribution(sale_count=30, p10=45, p90=48)
    assert q.label == "LIMITED_DISTRIBUTION"
    assert q.confidence_multiplier < 1.0


def test_unstable_distribution_detected():
    q = classify_distribution(sale_count=100, p10=40, p90=60, first_half_p50=50, second_half_p50=65)
    assert q.label == "UNSTABLE_DISTRIBUTION"
    assert "must NOT support a higher recommended stop" in q.note


def test_stable_distribution_not_flagged_unstable():
    q = classify_distribution(sale_count=100, p10=40, p90=60, first_half_p50=50, second_half_p50=51)
    assert q.label != "UNSTABLE_DISTRIBUTION"


def test_degenerate_never_shows_p10_p90_as_meaningful():
    q = classify_distribution(sale_count=250, p10=91.7, p90=91.7)  # real Josh Jacobs case
    assert q.label == "DEGENERATE_DIRECTIONAL_ONLY"
    assert q.display_percentiles == ["p50"]


def test_confidence_multiplier_never_exceeds_one():
    for sc, p10, p90 in [(200, 10, 100), (60, 20, 30), (25, 1, 1.5), (5, 1, 2)]:
        q = classify_distribution(sale_count=sc, p10=p10, p90=p90)
        assert 0.0 <= q.confidence_multiplier <= 1.0
