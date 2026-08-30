"""Price tier ordering: low <= expected <= high."""

from __future__ import annotations

import math

import pandas as pd

from . import config


class PriceTierError(ValueError):
    pass


def collect_scenario_prices(
    prices_by_scenario: dict[str, float | None],
    expected_scenario: str = "depleted_expected",
) -> dict[str, float]:
    """Build ordered low/expected/high from scenario prices.

    ``prices_by_scenario`` maps scenario name → price.
    Expected is taken from ``expected_scenario`` if present, else median of valid.
    Low = min(valid); high = max(valid).
    """
    valid: dict[str, float] = {}
    warnings: list[str] = []
    for name, val in prices_by_scenario.items():
        if val is None or (isinstance(val, float) and math.isnan(val)):
            warnings.append(f"missing:{name}")
            continue
        v = float(val)
        if v < 0:
            raise PriceTierError(f"Negative price in scenario {name}: {v}")
        if v > config.TOTAL_LEAGUE_BUDGET:
            raise PriceTierError(f"Price above league budget in {name}: {v}")
        valid[name] = v

    if not valid:
        raise PriceTierError("No valid scenario prices")

    if expected_scenario in valid:
        expected = valid[expected_scenario]
    else:
        expected = float(pd.Series(list(valid.values())).median())
        warnings.append(f"expected_scenario_missing_used_median")

    low = min(valid.values())
    high = max(valid.values())

    if not (low <= expected <= high):
        # Re-order: expected stays as designated base; expand envelope
        expected = min(max(expected, low), high)
        low = min(valid.values())
        high = max(valid.values())

    assert low <= expected <= high, f"Ordering failed: {low} {expected} {high}"

    return {
        "depleted_redraft_low": round(low, 2),
        "depleted_redraft_expected": round(expected, 2),
        "depleted_redraft_high": round(high, 2),
        "redraft_range_width": round(high - low, 2),
        "price_tier_warnings": "; ".join(warnings),
    }


def assert_price_order(row: pd.Series, prefix: str = "depleted_redraft") -> None:
    low = row[f"{prefix}_low"]
    exp = row[f"{prefix}_expected"]
    high = row[f"{prefix}_high"]
    if any(pd.isna(x) for x in (low, exp, high)):
        raise PriceTierError(f"Missing tier prices for {row.get('player', '?')}")
    if not (low <= exp <= high):
        raise PriceTierError(
            f"Price ordering violation for {row.get('player')}: low={low} exp={exp} high={high}"
        )
