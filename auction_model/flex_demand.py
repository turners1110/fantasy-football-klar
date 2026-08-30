"""Position and FLEX demand — marginal optimization vs fixed-share fallback."""

from __future__ import annotations

import pandas as pd

from . import config

FLEX_ELIGIBLE = {"RB", "WR", "TE"}


def _fixed_flex_allocation() -> dict[str, float]:
    n = config.STARTING_LINEUP["FLEX"] * config.NUM_TEAMS
    return {p: n * config.FLEX_SHARE.get(p, 0) for p in FLEX_ELIGIBLE}


def marginal_flex_allocation(
    pool: pd.DataFrame,
    n_flex_spots: int,
    points_col: str = "projected_points",
) -> dict[str, float]:
    """Assign FLEX demand to highest marginal projected value players."""
    if n_flex_spots <= 0:
        return {p: 0.0 for p in FLEX_ELIGIBLE}
    eligible = pool[pool["position"].isin(FLEX_ELIGIBLE)].dropna(subset=[points_col])
    eligible = eligible.sort_values(points_col, ascending=False).head(n_flex_spots)
    counts = eligible["position"].value_counts().to_dict()
    return {p: float(counts.get(p, 0)) for p in FLEX_ELIGIBLE}


def compute_position_demand_audit(
    full_pool: pd.DataFrame,
    keepers: pd.DataFrame,
    points_col: str = "projected_points",
) -> pd.DataFrame:
    """League-wide and remaining demand after projected keepers."""
    kept = keepers[keepers["will_keep"]].copy()
    rows = []
    league_flex_spots = config.STARTING_LINEUP["FLEX"] * config.NUM_TEAMS
    total_roster_spots = config.NUM_TEAMS * config.TOTAL_ROSTER_SPOTS_PER_TEAM
    n_keepers = int(kept["will_keep"].sum()) if "will_keep" in kept.columns else len(kept)
    remaining_spots = max(total_roster_spots - n_keepers, 0)

    if config.FLEX_ALLOCATION_MODE == "marginal":
        full_flex = marginal_flex_allocation(full_pool, league_flex_spots, points_col)
        remaining_flex = marginal_flex_allocation(
            full_pool[~full_pool.index.isin(kept.index)], max(league_flex_spots - len(kept), 0), points_col
        )
    else:
        full_flex = _fixed_flex_allocation()
        ratio = remaining_spots / total_roster_spots if total_roster_spots else 0
        remaining_flex = {p: v * ratio for p, v in full_flex.items()}

    live_pool = full_pool[~full_pool.index.isin(kept.index)]

    for position in ("QB", "RB", "WR", "TE"):
        required = config.STARTING_LINEUP.get(position, 0) * config.NUM_TEAMS
        bench = config.BENCH_DEMAND_PER_TEAM.get(position, 0) * config.NUM_TEAMS
        flex_full = full_flex.get(position, 0) if position in FLEX_ELIGIBLE else 0.0
        flex_rem = remaining_flex.get(position, 0) if position in FLEX_ELIGIBLE else 0.0
        keepers_at = int((kept["position"] == position).sum()) if len(kept) else 0
        remaining_required = max(required - keepers_at, 0)
        remaining_bench = bench * (remaining_spots / total_roster_spots) if total_roster_spots else 0
        remaining_total = remaining_required + flex_rem + remaining_bench
        available = int((live_pool["position"] == position).sum())
        pos_sorted = live_pool[live_pool["position"] == position].dropna(subset=[points_col]).sort_values(
            points_col, ascending=False
        )
        talent_rep_pts = auction_rep_pts = pd.NA
        if len(pos_sorted) >= max(int(round(remaining_total)), 1):
            rank = max(int(round(remaining_total)), 1)
            talent_rep_pts = float(pos_sorted.iloc[min(rank - 1, len(pos_sorted) - 1)][points_col])
            auction_rep_pts = talent_rep_pts

        rows.append({
            "position": position,
            "required_full_league_starters": required,
            "full_league_flex_allocation": round(flex_full, 2),
            "full_league_bench_demand": round(bench, 2),
            "full_league_total_demand": round(required + flex_full + bench, 2),
            "keepers_at_position": keepers_at,
            "remaining_required_openings": round(remaining_required, 2),
            "remaining_flex_openings": round(flex_rem, 2),
            "remaining_bench_demand": round(remaining_bench, 2),
            "remaining_total_demand": round(remaining_total, 2),
            "available_players": available,
            "talent_replacement_player": pos_sorted.iloc[min(int(round(remaining_total)), len(pos_sorted)) - 1]["player"]
            if len(pos_sorted) else pd.NA,
            "auction_replacement_player": pos_sorted.iloc[min(int(round(remaining_total)), len(pos_sorted)) - 1]["player"]
            if len(pos_sorted) else pd.NA,
            "talent_replacement_points": talent_rep_pts,
            "auction_replacement_points": auction_rep_pts,
        })
    return pd.DataFrame(rows)
