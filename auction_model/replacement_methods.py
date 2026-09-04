"""Phase 3D item 3: production entry point selecting between
FIXED_RANK_LEGACY, GREEDY_LEAGUEWIDE_ALLOCATION, and
EXACT_LEAGUEWIDE_ALLOCATION for replacement-level computation, and
recomputing base_value (VBD-derived dollars) from whichever method is
selected -- conserving the total dollar pool so only the SHAPE of
spending changes, not the total.

GREEDY_LEAGUEWIDE_ALLOCATION is the RENAMED phase 3C method (previously,
incorrectly, "C_OPTIMIZATION_DERIVED" -- a single-pass greedy heuristic
is not an exact optimization and must never be labeled one).
EXACT_LEAGUEWIDE_ALLOCATION is the real MIP in
auction_model.exact_leaguewide_allocation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .exact_leaguewide_allocation import solve_exact_leaguewide_allocation
from .valuation import _proportional_dollars

REQUIRED_STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
FLEX_ELIGIBLE = ("RB", "WR", "TE")


def greedy_leaguewide_selection(
    pool_points: dict[str, tuple[str, float]], team_keepers: dict[str, list[tuple[str, str, float]]],
) -> dict:
    """Single-pass greedy fill (RENAMED from phase 3C's
    "C_OPTIMIZATION_DERIVED"): required starters first (by points), then
    FLEX from the combined RB/WR/TE pool, then remaining bench slots from
    every position, by points. NOT an exact optimization -- a heuristic,
    labeled as such.

    Returns both the replacement points AND the actual selected-player set
    from ONE pass, so a caller wanting the selection (e.g. for a
    greedy-vs-exact comparison) never needs a second, separately-written
    reimplementation of this fill logic that could silently drift from it."""
    n_teams = len(team_keepers)
    by_pos: dict[str, list[tuple[str, float]]] = {"QB": [], "RB": [], "WR": [], "TE": []}
    for name, (pos, pts) in pool_points.items():
        if pos in by_pos:
            by_pos[pos].append((name, pts))
    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: x[1], reverse=True)

    used = set()
    selected_by_pos = {"QB": [], "RB": [], "WR": [], "TE": []}
    keeper_pts_by_pos = {"QB": [], "RB": [], "WR": [], "TE": []}
    for keepers in team_keepers.values():
        for name, pos, pts in keepers:
            if pos in keeper_pts_by_pos:
                keeper_pts_by_pos[pos].append(pts)

    for pos, need in REQUIRED_STARTERS.items():
        target = need * n_teams
        for name, pts in by_pos[pos]:
            if name in used or len(selected_by_pos[pos]) >= target:
                continue
            used.add(name)
            selected_by_pos[pos].append(pts)

    total_open_slots = sum(15 - len(k) for k in team_keepers.values())
    n_starters_filled = sum(len(v) for v in selected_by_pos.values())
    flex_slots = 3 * n_teams
    flex_pool = sorted(
        [(name, pts, pos) for pos in FLEX_ELIGIBLE for name, pts in by_pos[pos] if name not in used],
        key=lambda x: x[1], reverse=True,
    )
    for name, pts, pos in flex_pool[:flex_slots]:
        used.add(name)
        selected_by_pos[pos].append(pts)

    remaining_slots = total_open_slots - n_starters_filled - min(flex_slots, len(flex_pool))
    all_remaining = sorted(
        [(name, pts, pos) for pos in ("QB", "RB", "WR", "TE") for name, pts in by_pos[pos] if name not in used],
        key=lambda x: x[1], reverse=True,
    )
    for name, pts, pos in all_remaining[:max(0, remaining_slots)]:
        used.add(name)
        selected_by_pos[pos].append(pts)

    replacement = {}
    for pos in ("QB", "RB", "WR", "TE"):
        all_selected = selected_by_pos[pos] + keeper_pts_by_pos[pos]
        replacement[pos] = min(all_selected) if all_selected else 0.0
    return {"replacement": replacement, "selected_players": used}


def greedy_leaguewide_replacement(
    pool_points: dict[str, tuple[str, float]], team_keepers: dict[str, list[tuple[str, str, float]]],
) -> dict[str, float]:
    """Thin wrapper over greedy_leaguewide_selection for callers that only
    need replacement points (the common case, e.g. recompute_base_value)."""
    return greedy_leaguewide_selection(pool_points, team_keepers)["replacement"]


def exact_leaguewide_replacement(
    pool_points: dict[str, tuple[str, float]], team_keepers: dict[str, list[tuple[str, str, float]]],
) -> dict[str, float]:
    result = solve_exact_leaguewide_allocation(pool_points, team_keepers)
    return {pos: (v["points"] or 0.0) for pos, v in result.replacement_by_position.items()}


def recompute_base_value(
    prices_df: pd.DataFrame, pool_points: dict[str, tuple[str, float]],
    team_keepers: dict[str, list[tuple[str, str, float]]], method: str,
) -> pd.Series:
    """Returns a new base_value Series (same index as prices_df) with VBD
    recomputed from the given method's replacement points, redistributed
    proportionally to VBD**VBD_DOLLAR_POWER over the SAME total dollar
    pool prices_df["base_value"] already summed to -- conserves the
    league's total dollars, changes only the position/player SHAPE."""
    if method == config.GREEDY_LEAGUEWIDE_ALLOCATION:
        replacement = greedy_leaguewide_replacement(pool_points, team_keepers)
    elif method == config.EXACT_LEAGUEWIDE_ALLOCATION:
        replacement = exact_leaguewide_replacement(pool_points, team_keepers)
    else:
        raise ValueError(f"recompute_base_value called with unsupported method: {method!r}")

    points = prices_df["player"].map(lambda n: pool_points.get(n, (None, 0.0))[1])
    replacement_points = prices_df["position"].map(replacement).fillna(0.0)
    vbd = (points - replacement_points).clip(lower=0.0)

    total_dollars = float(prices_df["base_value"].sum())
    n_players = len(prices_df)
    discretionary = max(0.0, total_dollars - config.MIN_PRICE * n_players)
    surplus = _proportional_dollars(vbd ** config.VBD_DOLLAR_POWER, discretionary)
    return (surplus + config.MIN_PRICE).round(2)
