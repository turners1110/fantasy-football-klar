"""Projection fallback chain for keeper decisions."""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config, data_pipeline

HAIRCUT_2025 = 0.85  # conservative year-over-year discount on prior actuals

# Conservative rank → points mapping (position median tier proxy)
RANK_TO_POINTS = {
    "QB": {1: 320, 5: 280, 10: 250, 15: 220, 20: 200, 30: 170, 40: 140},
    "RB": {1: 280, 5: 220, 10: 180, 15: 150, 20: 130, 30: 100, 40: 80},
    "WR": {1: 260, 5: 210, 10: 175, 15: 145, 20: 125, 30: 95, 40: 75},
    "TE": {1: 200, 5: 160, 10: 130, 15: 110, 20: 90, 30: 70, 40: 55},
}


def _interp_rank_points(position: str, rank: float) -> float:
    pos = str(position).upper()
    curve = RANK_TO_POINTS.get(pos, RANK_TO_POINTS["WR"])
    ranks = sorted(curve.keys())
    if pd.isna(rank):
        return curve[ranks[-1]]
    r = float(rank)
    if r <= ranks[0]:
        return curve[ranks[0]]
    if r >= ranks[-1]:
        return curve[ranks[-1]]
    for i in range(len(ranks) - 1):
        if ranks[i] <= r <= ranks[i + 1]:
            lo, hi = ranks[i], ranks[i + 1]
            t = (r - lo) / (hi - lo)
            return curve[lo] + t * (curve[hi] - curve[lo])
    return curve[ranks[-1]]


def apply_projection_fallbacks(
    pool: pd.DataFrame,
    fp_rankings: pd.DataFrame | None = None,
    actuals_2025_path: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fill projection metadata; mark DATA_BLOCKED where no fallback exists."""
    out = pool.copy()
    for col in (
        "projection_method", "projection_source", "raw_projection",
        "haircut", "final_projection", "projection_confidence", "projection_warning",
    ):
        if col not in out.columns:
            out[col] = pd.NA

    # Primary projection already in projected_points
    has_primary = out["projected_points"].notna()
    out.loc[has_primary, "projection_method"] = "primary_2026"
    out.loc[has_primary, "projection_source"] = "projections_2026.csv"
    out.loc[has_primary, "raw_projection"] = out.loc[has_primary, "projected_points"]
    out.loc[has_primary, "final_projection"] = out.loc[has_primary, "projected_points"]
    out.loc[has_primary, "projection_confidence"] = 0.9

    missing = ~has_primary

    # FP rank fallback
    if fp_rankings is not None and not fp_rankings.empty and missing.any():
        fp = fp_rankings.copy()
        fp["_key"] = fp["player"].map(data_pipeline._normalize_name)
        rank_lookup = fp.set_index("_key")["fp_position_rank"].to_dict()
        out["_key"] = out["player"].map(data_pipeline._normalize_name)
        for idx in out.index[missing]:
            key = out.at[idx, "_key"]
            rank = rank_lookup.get(key)
            if pd.notna(rank):
                pts = _interp_rank_points(out.at[idx, "position"], float(rank))
                out.at[idx, "projected_points"] = round(pts, 2)
                out.at[idx, "projection_method"] = "fp_rank_conservative"
                out.at[idx, "projection_source"] = "FantasyPros_position_rank"
                out.at[idx, "raw_projection"] = pts
                out.at[idx, "haircut"] = 0.0
                out.at[idx, "final_projection"] = pts
                out.at[idx, "projection_confidence"] = 0.6
                out.at[idx, "projection_warning"] = "rank_mapped_not_stat_projection"
        missing = out["projected_points"].isna()
        out.drop(columns=["_key"], errors="ignore")

    # 2025 actuals fallback
    if actuals_2025_path and missing.any():
        try:
            act = pd.read_csv(actuals_2025_path)
            if "projected_points" in act.columns or "fantasy_points" in act.columns:
                pts_col = "projected_points" if "projected_points" in act.columns else "fantasy_points"
                act["_key"] = act["player"].map(data_pipeline._normalize_name)
                act_lookup = act.set_index("_key")[pts_col].to_dict()
                out["_key"] = out["player"].map(data_pipeline._normalize_name)
                for idx in out.index[missing]:
                    key = out.at[idx, "_key"]
                    raw = act_lookup.get(key)
                    if pd.notna(raw) and float(raw) > 0:
                        final = round(float(raw) * HAIRCUT_2025, 2)
                        out.at[idx, "projected_points"] = final
                        out.at[idx, "projection_method"] = "2025_actual_haircut"
                        out.at[idx, "projection_source"] = "actuals_2025.csv"
                        out.at[idx, "raw_projection"] = float(raw)
                        out.at[idx, "haircut"] = HAIRCUT_2025
                        out.at[idx, "final_projection"] = final
                        out.at[idx, "projection_confidence"] = 0.55
                        out.at[idx, "projection_warning"] = "prior_year_haircut"
                missing = out["projected_points"].isna()
                out.drop(columns=["_key"], errors="ignore")
        except OSError:
            pass

    blocked = out[missing].copy()
    out.loc[missing, "projection_method"] = "DATA_BLOCKED"
    out.loc[missing, "projection_confidence"] = 0.0
    out.loc[missing, "projection_warning"] = "no_projection_available"

    return out, blocked
