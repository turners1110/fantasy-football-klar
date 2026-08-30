"""Transparent 0-10 confidence scoring for keeper decisions."""

from __future__ import annotations

import pandas as pd

from . import config

APPROX_METHODS = {"APPROXIMATE_POSITION_RATIO", "position_ratio_fallback"}


def _apply_hard_caps(score: float, missing: list[str], **flags) -> float:
    if flags.get("ineligible_roster"):
        score = min(score, 2.0)
        missing.append("ineligible player in roster (cap 2.0)")
    if flags.get("partial_roster"):
        score = min(score, 2.0)
        missing.append("partial roster (cap 2.0)")
    if flags.get("greedy_solver"):
        score = min(score, 5.0)
        missing.append("greedy solver (cap 5.0)")
    if flags.get("zero_iterations"):
        score = min(score, 4.0)
        missing.append("zero keeper iterations (cap 4.0)")
    if flags.get("nonconverged"):
        score = min(score, 6.0)
        missing.append("nonconverged keeper market (cap 6.0)")
    if flags.get("approx_alpha"):
        score = min(score, 5.0)
        missing.append("approximate depleted alpha (cap 5.0)")
    if flags.get("missing_projection"):
        score = min(score, 5.0)
        missing.append("missing projection (cap 5.0)")
    if flags.get("reversed_price"):
        score = min(score, 2.0)
        missing.append("reversed price range (cap 2.0)")
    return score


def score_decision_v2(
    row: pd.Series,
    selection_rate: float = 1.0,
    converged: bool = True,
    cycle_detected: bool = False,
    iterations: int = 1,
    solver_status: str = "OPTIMAL",
    eligibility_valid: bool = True,
) -> tuple[float, str]:
    """Return (score_0_to_10, missing_points_explanation)."""
    points = 0.0
    missing: list[str] = []

    if config.SCENARIO_KEEPER_COUNT in {"A", "B"} and config.SCENARIO_TAG in {"C", "D"}:
        points += 1.0
    else:
        points += 0.5
        missing.append("unresolved keeper/tag scenario")

    origin = str(row.get("salary_origin", "UNKNOWN"))
    if pd.isna(row.get("prior_salary")) and pd.isna(row.get("salary_2025")):
        missing.append("missing salary")
    elif origin.endswith("_CONFIRMED"):
        points += 1.5
    elif origin.startswith("UNKNOWN"):
        points += 0.5
        missing.append("unknown salary origin")
    else:
        points += 1.0

    if row.get("projection_available"):
        points += 1.0
    else:
        points += 0.2
        missing.append("missing projection")

    method = str(row.get("depleted_alpha_method", ""))
    if method in APPROX_METHODS:
        points += 0.5
        missing.append("approximate counterfactual")
    else:
        points += 1.5

    if converged and not cycle_detected and iterations >= 1:
        points += 1.5
    elif converged:
        points += 1.0
        missing.append("cycle detected")
    else:
        points += 0.5
        missing.append("market not converged")

    if selection_rate >= 0.9:
        points += 1.5
    elif selection_rate >= 0.75:
        points += 1.0
    else:
        points += 0.5
        missing.append(f"selection rate {selection_rate:.0%}")

    if solver_status == "OPTIMAL" and eligibility_valid:
        points += 1.0
    elif solver_status in {"FEASIBLE_NOT_PROVEN_OPTIMAL"}:
        points += 0.7
    else:
        points += 0.2
        missing.append(f"solver status {solver_status}")

    points += 0.3
    missing.append("external cross-check not run")
    points += 0.3
    missing.append("partial test coverage")

    flags = {
        "ineligible_roster": not eligibility_valid,
        "partial_roster": solver_status == "partial_roster",
        "greedy_solver": solver_status in {"GREEDY_APPROXIMATION", "optimal_greedy"},
        "zero_iterations": iterations < 1,
        "nonconverged": not converged,
        "approx_alpha": method in APPROX_METHODS,
        "missing_projection": not row.get("projection_available"),
    }
    score = _apply_hard_caps(min(round(points, 1), 10.0), missing, **flags)
    return score, "; ".join(missing)


def score_decision(
    row: pd.Series,
    selection_rate: float = 1.0,
    converged: bool = True,
    cycle_detected: bool = False,
) -> tuple[float, str, str]:
    """Return (score_0_to_10, category, missing_points_explanation)."""
    points = 0.0
    missing: list[str] = []

    # Rule confidence (1.0)
    if config.SCENARIO_KEEPER_COUNT in {"A", "B"} and config.SCENARIO_TAG in {"C", "D"}:
        points += 1.0
    else:
        points += 0.5
        missing.append("unresolved keeper/tag scenario (+0.5 max)")

    # Contract confidence (1.5)
    origin = str(row.get("salary_origin", "UNKNOWN"))
    if pd.isna(row.get("prior_salary")) or pd.isna(row.get("salary_2025")):
        missing.append("missing salary (-1.5)")
    elif origin.endswith("_CONFIRMED"):
        points += 1.5
    elif origin.startswith("UNKNOWN"):
        points += 0.5
        missing.append("unknown salary origin (-1.0)")
    else:
        points += 1.0

    # Player data (1.0)
    if row.get("projection_available") and row.get("fantasypros_match"):
        points += 1.0
    elif row.get("projection_available"):
        points += 0.7
        missing.append("weak identity match (-0.3)")
    else:
        points += 0.2
        missing.append("missing projection (-0.8)")

    # Counterfactual (1.5)
    method = str(row.get("calculation_method", row.get("depleted_alpha_method", "")))
    if method in APPROX_METHODS or "APPROX" in method.upper():
        points += 0.5
        missing.append("approximate counterfactual (-1.0, cannot reach 9/10)")
    else:
        points += 1.5

    # Keeper market (1.5)
    if converged and not cycle_detected:
        points += 1.5
    elif converged:
        points += 1.0
        missing.append("cycle detected (-0.5)")
    else:
        points += 0.5
        missing.append("market not converged (-1.0)")

    # Scenario robustness (1.5)
    if selection_rate >= 0.9:
        points += 1.5
    elif selection_rate >= 0.75:
        points += 1.0
        missing.append(f"selection rate {selection_rate:.0%} (-0.5)")
    elif selection_rate >= 0.3:
        points += 0.5
        missing.append(f"borderline selection rate {selection_rate:.0%} (-1.0)")
    else:
        missing.append(f"low selection rate {selection_rate:.0%} (-1.5)")

    # Roster optimization (1.0)
    r_gain = row.get("roster_value_gained_from_keep", row.get("final_keep_score"))
    if pd.notna(r_gain):
        points += 1.0
    else:
        points += 0.3
        missing.append("no roster-level comparison (-0.7)")

    # Calibration (0.5) — placeholder unless external cross-check present
    points += 0.3
    missing.append("external cross-check not run (-0.2)")

    # Tests (0.5) — partial coverage
    points += 0.3
    missing.append("partial test coverage (-0.2)")

    score = min(round(points, 1), 10.0)
    category = _decision_category(row, selection_rate, score, method)
    return score, category, "; ".join(missing)


def _decision_category(row: pd.Series, selection_rate: float, score: float, method: str) -> str:
    if score < 5 or str(row.get("data_quality_status", "")).startswith("missing"):
        return "DATA_BLOCKED"
    low_alpha = float(row.get("depleted_alpha_low", row.get("depleted_alpha_expected", 0)) or 0)
    exp_alpha = float(row.get("depleted_alpha_expected", row.get("depleted_market_alpha", 0)) or 0)
    r_gain = float(row.get("roster_value_gained_from_keep", row.get("final_keep_score", 0)) or 0)

    if method in APPROX_METHODS and r_gain <= 0:
        return "STRONG_RELEASE"

    # Roster-level outcome is the primary decision driver
    if r_gain <= 0:
        return "STRONG_RELEASE"

    if (
        low_alpha >= 10
        and selection_rate >= config.CONFIDENCE_LOCK_SELECTION_RATE
        and r_gain > 0
        and score >= 9.0
    ):
        return "LOCK"
    if low_alpha >= config.KEEPER_DECISION_MARGIN and r_gain > 0 and selection_rate >= 0.75:
        return "STRONG_KEEP"
    if r_gain > 0:
        return "BORDERLINE_KEEP"
    return "STRONG_RELEASE"
