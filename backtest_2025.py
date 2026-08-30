#!/usr/bin/env python3
"""Same-season backtest: predict 2025 auction prices from ONLY 2025
preseason signals, compare against actual 2025 salaries. Used to pick
VBD_DOLLAR_POWER and TIER_SHRINKAGE_PCT empirically instead of by feel.

    python backtest_2025.py

READ BEFORE TRUSTING THE NUMBERS -- two real data gaps in what this repo
has archived, both disclosed rather than papered over:

1. No preseason 2025 FantasyPros tier file exists (only the current
   2026-draft one). Tiers here are a SYNTHETIC proxy: preseason ECR
   position rank (e.g. "RB1") bucketed into groups of 10. This is close in
   spirit to real tiers but not the real analyst-drawn breakpoints, so the
   tier-shrinkage result should be treated as directionally useful, not
   precise.
2. No 2024 salary data or a real 2025 keeper list exists in this repo, so
   this backtest CANNOT isolate keeper-driven effects (Priority 1's
   auction-VBD mechanism, or the anchor half of the price blend, which
   needs a prior-year salary). It tests blend_weight=1.0 (pure VBD-based
   pricing) against the full 2025 open-market outcome as if nobody had
   been kept -- which is exactly the part Priorities 3/4 need tuned
   (VBD_DOLLAR_POWER, TIER_SHRINKAGE_PCT), just not the keeper-supply
   mechanism itself (that's validated qualitatively in run_valuation.py's
   output instead -- see changelog).

Preseason signal used: `vendor_weekly_proj` (2025 preseason PPG estimate)
x 17 games, from data/actuals_2025.csv's ECR/Proj columns (the only
preseason-only numbers archived -- no preseason raw stat line exists to
rescore under our league's exact rules, another disclosed limitation).
"""

from __future__ import annotations

import re
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).parent))

from auction_model import config, data_pipeline, valuation

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

POWER_GRID = [1.4, 1.7, 2.0, 2.2, 2.5, 3.0, 3.5, 4.0]
SHRINKAGE_GRID = [0.25, 0.35, 0.5, 0.65, 0.75]
ASSUMED_SEASON_GAMES = 17


def build_backtest_frame() -> pd.DataFrame:
    actuals = pd.read_csv(DATA_DIR / "actuals_2025.csv")
    salaries, _ = data_pipeline.load_historical_salaries(DATA_DIR / "historical_salaries_2025_raw.csv")

    actuals["projected_points"] = pd.to_numeric(actuals["vendor_weekly_proj"], errors="coerce") * ASSUMED_SEASON_GAMES
    actuals["ecr_rank"] = actuals["ecr"].astype(str).str.extract(r"(\d+)$")[0]
    actuals["ecr_rank"] = pd.to_numeric(actuals["ecr_rank"], errors="coerce")
    # Synthetic tier proxy -- see module docstring gap #1.
    actuals["fp_tier"] = np.ceil(actuals["ecr_rank"] / 10)

    actuals["_key"] = actuals["player"].map(data_pipeline._normalize_name)
    salaries["_key"] = salaries["player"].map(data_pipeline._normalize_name)
    salaries = salaries[salaries["has_confirmed_salary"]][["_key", "salary_2025"]].drop_duplicates("_key")

    merged = actuals.merge(salaries, on="_key", how="inner", suffixes=("", "_actual"))
    merged = merged.dropna(subset=["projected_points", "salary_2025"])
    merged = merged[merged["position"].isin(["QB", "RB", "WR", "TE"])].reset_index(drop=True)
    return merged


def predict(frame: pd.DataFrame, power: float, shrinkage: float) -> pd.Series:
    pool = frame.copy()
    # blend_weight=1.0 below zeroes out the anchor term regardless, but
    # price_pool still touches these columns internally -- has_confirmed_salary
    # is a required column, not a leak (anchor_dollars is computed but always
    # multiplied by (1-effective_weight)=0 before reaching the final price).
    pool["has_confirmed_salary"] = True
    pool = valuation.add_vbd_scores(pool, points_col="projected_points")
    pool = valuation.apply_tier_shrinkage(pool, shrinkage, vbd_col="VBD_score")

    old_power = config.VBD_DOLLAR_POWER
    config.VBD_DOLLAR_POWER = power
    try:
        priced = valuation.price_pool(
            pool,
            remaining_budget=config.TOTAL_LEAGUE_BUDGET,
            inflation_multiplier=1.0,
            blend_weight=1.0,
            points_col="projected_points",
        )
    finally:
        config.VBD_DOLLAR_POWER = old_power
    return priced["suggested_auction_price"]


def score(frame: pd.DataFrame, predicted: pd.Series) -> dict:
    actual = frame["salary_2025"]
    err = predicted - actual
    abs_err = err.abs()
    spearman = spearmanr(predicted, actual).correlation

    by_position = {}
    for pos in ("QB", "RB", "WR", "TE"):
        mask = frame["position"] == pos
        if mask.sum() == 0:
            continue
        by_position[pos] = {
            "n": int(mask.sum()),
            "mae": round(abs_err[mask].mean(), 2),
            "bias": round(err[mask].mean(), 2),
        }

    bands = [(0, 5), (5, 15), (15, 30), (30, 50), (50, 200)]
    by_band = {}
    for lo, hi in bands:
        mask = (actual >= lo) & (actual < hi)
        if mask.sum() == 0:
            continue
        by_band[f"${lo}-{hi}"] = {
            "n": int(mask.sum()),
            "mae": round(abs_err[mask].mean(), 2),
            "bias": round(err[mask].mean(), 2),
        }

    calib = {}
    for lo, hi in bands:
        mask = (predicted >= lo) & (predicted < hi)
        if mask.sum() == 0:
            continue
        calib[f"pred_${lo}-{hi}"] = {
            "n": int(mask.sum()),
            "mean_predicted": round(predicted[mask].mean(), 1),
            "mean_actual": round(actual[mask].mean(), 1),
        }

    total_budget_players = len(frame)
    top12_idx = actual.sort_values(ascending=False).index[:12]
    top36_idx = actual.sort_values(ascending=False).index[:36]
    spend_shape = {
        "actual_top12_share": round(actual.loc[top12_idx].sum() / actual.sum(), 3),
        "predicted_top12_share": round(predicted.loc[top12_idx].sum() / predicted.sum(), 3),
        "actual_top36_share": round(actual.loc[top36_idx].sum() / actual.sum(), 3),
        "predicted_top36_share": round(predicted.loc[top36_idx].sum() / predicted.sum(), 3),
        "actual_floor_share": round((actual <= 5).sum() / total_budget_players, 3),
        "predicted_floor_share": round((predicted <= 5).sum() / total_budget_players, 3),
    }

    return {
        "n": len(frame),
        "mae": round(abs_err.mean(), 2),
        "median_ae": round(abs_err.median(), 2),
        "bias": round(err.mean(), 2),
        "bias_pct": round(err.mean() / actual.mean() * 100, 1),
        "spearman": round(float(spearman), 3),
        "by_position": by_position,
        "by_price_band": by_band,
        "calibration_by_predicted_bucket": calib,
        "spend_shape": spend_shape,
    }


def main() -> None:
    frame = build_backtest_frame()
    print(f"Backtest frame: {len(frame)} players (2025 preseason signal -> actual 2025 salary)")
    print(f"  Positions: {frame['position'].value_counts().to_dict()}")
    print()

    results = []
    for power, shrinkage in product(POWER_GRID, SHRINKAGE_GRID):
        predicted = predict(frame, power, shrinkage)
        m = score(frame, predicted)
        results.append({"power": power, "shrinkage": shrinkage, **m})

    results_df = pd.DataFrame(results)
    results_df["top12_gap"] = results_df["spend_shape"].apply(
        lambda s: abs(s["predicted_top12_share"] - s["actual_top12_share"])
    )
    results_df["top36_gap"] = results_df["spend_shape"].apply(
        lambda s: abs(s["predicted_top36_share"] - s["actual_top36_share"])
    )
    results_df["floor_gap"] = results_df["spend_shape"].apply(
        lambda s: abs(s["predicted_floor_share"] - s["actual_floor_share"])
    )
    # Composite: MAE alone barely discriminates power in this grid (all
    # land within a few dollars of each other) while spend-shape varies a
    # lot -- weight both, normalized to comparable scale, per the
    # instruction to select on error AND real spending-shape match, not
    # error alone.
    results_df["mae_norm"] = (results_df["mae"] - results_df["mae"].min()) / (
        results_df["mae"].max() - results_df["mae"].min()
    )
    results_df["shape_norm"] = (
        results_df["top12_gap"] + results_df["top36_gap"] + results_df["floor_gap"]
    )
    results_df["shape_norm"] = (results_df["shape_norm"] - results_df["shape_norm"].min()) / (
        results_df["shape_norm"].max() - results_df["shape_norm"].min()
    )
    results_df["composite_score"] = 0.5 * results_df["mae_norm"] + 0.5 * results_df["shape_norm"]

    results_df.drop(columns=["by_position", "by_price_band", "calibration_by_predicted_bucket", "spend_shape"]).to_csv(
        BASE_DIR / "output" / "backtest_2025_grid_results.csv", index=False
    )

    best = results_df.sort_values("composite_score").iloc[0]
    best_mae_row = results_df.sort_values("mae").iloc[0]
    print(f"Best by MAE alone: power={best_mae_row['power']}, shrinkage={best_mae_row['shrinkage']}, MAE=${best_mae_row['mae']}")
    print(f"Best by composite (MAE + spend-shape match): power={best['power']}, shrinkage={best['shrinkage']}, "
          f"MAE=${best['mae']}, median_AE=${best['median_ae']}, bias=${best['bias']} ({best['bias_pct']}%), "
          f"spearman={best['spearman']}, top12_gap={best['top12_gap']:.3f}, top36_gap={best['top36_gap']:.3f}")
    print()

    print("Full grid (sorted by composite score):")
    print(results_df[["power", "shrinkage", "mae", "median_ae", "bias_pct", "spearman", "top12_gap", "top36_gap", "composite_score"]]
          .sort_values("composite_score").head(15).to_string(index=False))
    print()

    best_full = score(frame, predict(frame, best["power"], best["shrinkage"]))
    print(f"Detail for the selected combo (power={best['power']}, shrinkage={best['shrinkage']}):")
    print("  By position:", best_full["by_position"])
    print("  By price band:", best_full["by_price_band"])
    print("  Calibration by predicted bucket:", best_full["calibration_by_predicted_bucket"])
    print("  Spend shape:", best_full["spend_shape"])

    print(f"\nWrote full grid to output/backtest_2025_grid_results.csv ({len(results_df)} combos)")
    print(f"\nRECOMMENDATION: set VBD_DOLLAR_POWER = {best['power']}, TIER_SHRINKAGE_PCT = {best['shrinkage']} in config.py")


if __name__ == "__main__":
    main()
