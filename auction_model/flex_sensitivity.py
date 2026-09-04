"""Phase 3D item 4: FLEX demand recomputed from EXACT_LEAGUEWIDE_ALLOCATION's
own legal FLEX assignments (RB/WR/TE share), replacing the retired hardcoded
FLEX_SHARE = {"RB": 0.45, "WR": 0.45, "TE": 0.10} split for production use
(FLEX_SHARE stays in auction_model.config as the FIXED_RANK_LEGACY method's
own input only -- auction_model.flex_demand's marginal_flex_allocation is a
separate, earlier phase-3C audit tool and is untouched by this module).

Also runs a player-specific (NOT a uniform scalar) projection-uncertainty
sensitivity analysis of that FLEX mix.

ASSUMPTION (disclosed, not fabricated data): data/projections_2026.csv has
no per-player low/high projection range, so this uses POSITION-level
low/high uncertainty multipliers grounded in well-established fantasy-
projection volatility patterns -- RB point totals are the most volatile
position (workload is exposed to committee/injury risk), TE the noisiest
(touchdown-dependent scoring on a small target share), QB the most stable
(starters see a high, largely guaranteed passing-attempt volume), WR
moderate (target-competition variance without RB's workload risk or TE's
touchdown dependency). Each of the N_DRAWS Monte Carlo draws samples an
INDEPENDENT multiplier per player from their position's [low, high] range
-- not one shared scalar applied to an entire position or the whole pool
-- so realized point totals differ player-by-player within a draw, the way
real season-to-season variance would, rather than moving every player at a
position in lockstep.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .exact_leaguewide_allocation import solve_exact_leaguewide_allocation

# ASSUMPTION: position-level low/high projection-uncertainty multipliers
# (see module docstring for the rationale behind each range's width).
PROJECTION_UNCERTAINTY = {
    "QB": (0.90, 1.10),
    "RB": (0.75, 1.25),
    "WR": (0.82, 1.18),
    "TE": (0.72, 1.28),
}
DEFAULT_FALLBACK_RANGE = (0.85, 1.15)
N_DRAWS = 200
DEFAULT_SEED = 20260904


def _flex_share_pct(flex_mix: dict[str, int]) -> dict[str, float]:
    total = sum(flex_mix.values())
    if total == 0:
        return {pos: 0.0 for pos in ("RB", "WR", "TE")}
    return {pos: round(100.0 * flex_mix.get(pos, 0) / total, 2) for pos in ("RB", "WR", "TE")}


def compute_flex_allocation_percentiles(
    pool_points: dict[str, tuple[str, float]],
    team_keepers: dict[str, list[tuple[str, str, float]]],
    n_draws: int = N_DRAWS,
    seed: int = DEFAULT_SEED,
) -> dict:
    """Returns the baseline (unperturbed) exact-allocation FLEX mix plus
    P10/P50/P90 FLEX-share percentiles across n_draws independent,
    player-specific projection-uncertainty draws."""
    rng = np.random.default_rng(seed)
    names = list(pool_points.keys())
    positions = {n: pool_points[n][0] for n in names}
    base_points = {n: pool_points[n][1] for n in names}

    baseline = solve_exact_leaguewide_allocation(pool_points, team_keepers)

    draw_rows = []
    for _ in range(n_draws):
        perturbed = {}
        for n in names:
            pos = positions[n]
            lo, hi = PROJECTION_UNCERTAINTY.get(pos, DEFAULT_FALLBACK_RANGE)
            mult = rng.uniform(lo, hi)  # independent draw PER PLAYER, not per position
            perturbed[n] = (pos, max(0.0, base_points[n] * mult))
        result = solve_exact_leaguewide_allocation(perturbed, team_keepers)
        draw_rows.append(_flex_share_pct(result.flex_mix))

    draws_df = pd.DataFrame(draw_rows)
    percentiles = {}
    for pos in ("RB", "WR", "TE"):
        col = draws_df[pos] if pos in draws_df else pd.Series([0.0] * n_draws)
        percentiles[pos] = {
            "p10": round(float(col.quantile(0.10)), 2),
            "p50": round(float(col.quantile(0.50)), 2),
            "p90": round(float(col.quantile(0.90)), 2),
            "mean": round(float(col.mean()), 2),
        }

    return {
        "baseline_flex_mix": dict(baseline.flex_mix),
        "baseline_flex_share_pct": _flex_share_pct(baseline.flex_mix),
        "percentiles": percentiles,
        "n_draws": n_draws,
        "seed": seed,
        "draws_df": draws_df,
    }
