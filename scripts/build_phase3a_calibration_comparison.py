#!/usr/bin/env python3
"""Phase 3A item 12: modest calibration comparison. Only one league
season of (mostly unreliable) historical salary data exists (see
outputs/auction_rebuild/phase3a/salary_origin_audit.csv -- most rows are
UNKNOWN origin, not confirmed competitive-auction results), so this
deliberately stays simple rather than fitting anything complex, per the
instruction to avoid ML absent held-out evidence it helps.

Compares three ALREADY-EXISTING, simple predictors against the
reliability-weighted historical salary subset:
  1. existing_neutral_value -- this repo's own suggested_auction_price
     (VBD-anchored valuation, no historical calibration).
  2. public_value_scaled -- hypothetical_open_market_value (a public/
     open-market-style price scaled to this league's $400 format).
  3. public_rank_tier -- a simple, non-fitted heuristic: rank each player
     within position by suggested_auction_price, then price = the
     position's mean historical-if-known salary for players in the same
     rank quartile. (Falls back to the position's overall mean where no
     quartile data exists.)
No new model is fit; all three predictors already exist in
output_mock_draft_snapshot/veteran_auction_price_sheet.csv or are a
direct, transparent rank transform of it.

Evaluation target: historical_salary_if_known, weighted by
reliability from salary_origin_audit.csv (item 11) -- rows with
reliability 0 (e.g. ADMINISTRATIVE_DOLLAR_ONE) are EXCLUDED from
evaluation entirely, consistent with "unknown $1 prices receive zero
weight by default." This is explicitly labeled a SAME-LEAGUE HISTORICAL
CALIBRATION check, not a validated predictive model -- one season, one
league, mostly-unconfirmed salary origins.

Writes outputs/auction_rebuild/phase3a/calibration_comparison.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from auction_model.confirmed_keeper_pipeline import normalize_name

OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "calibration_comparison.json"


def spearmanr(a: pd.Series, b: pd.Series) -> tuple[float | None, None]:
    """Spearman rank correlation via rank-transform + Pearson (scipy is
    not available in this environment) -- identical result for the
    non-tied-heavy case here."""
    if len(a) < 3:
        return None, None
    ra, rb = pd.Series(a).rank(), pd.Series(b).rank()
    return float(ra.corr(rb)), None


def main() -> None:
    sheet = pd.read_csv(BASE_DIR / "output_mock_draft_snapshot" / "veteran_auction_price_sheet.csv")
    audit = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "salary_origin_audit.csv")

    sheet["_key"] = sheet["player"].map(normalize_name)
    audit["_key"] = audit["player"].map(normalize_name)
    audit_reliability = audit.groupby("_key")["reliability"].max()
    sheet["reliability"] = sheet["_key"].map(audit_reliability).fillna(0.0)

    df = sheet.dropna(subset=["historical_salary_if_known"]).copy()
    df = df[df["reliability"] > 0].copy()  # zero-reliability ($1 admin) rows excluded from evaluation
    print(f"Evaluation set: {len(df)} of {sheet['historical_salary_if_known'].notna().sum()} "
          f"historically-matched rows have reliability > 0")

    # Predictor 3: public_rank_tier -- non-fitted rank-quartile heuristic.
    df["_pos_rank"] = df.groupby("position")["suggested_auction_price"].rank(ascending=False, method="first")
    df["_pos_count"] = df.groupby("position")["position"].transform("count")
    df["_quartile"] = np.ceil(df["_pos_rank"] / (df["_pos_count"] / 4).clip(lower=1)).clip(upper=4)
    quartile_means = df.groupby(["position", "_quartile"])["historical_salary_if_known"].transform("mean")
    df["public_rank_tier"] = quartile_means

    predictors = {
        "existing_neutral_value": "suggested_auction_price",
        "public_value_scaled": "hypothetical_open_market_value",
        "public_rank_tier": "public_rank_tier",
    }

    def _spend_shape(prices: pd.Series) -> dict:
        sorted_desc = prices.sort_values(ascending=False).values
        total = sorted_desc.sum()
        return {
            "top12_spend_share": round(float(sorted_desc[:12].sum() / total), 4) if total else None,
            "top24_spend_share": round(float(sorted_desc[:24].sum() / total), 4) if total else None,
            "dollar_one_rate": round(float((prices <= 1).mean()), 4),
        }

    results = {"n_evaluated": len(df), "note": "SAME-LEAGUE HISTORICAL CALIBRATION ONLY -- one league, one season, "
               "most salaries UNKNOWN origin (see salary_origin_audit.csv); not a validated external model."}

    actual = df["historical_salary_if_known"].astype(float)
    weights = df["reliability"].astype(float)

    for label, col in predictors.items():
        pred = df[col].astype(float)
        valid = pred.notna() & actual.notna()
        p, a, w = pred[valid], actual[valid], weights[valid]
        abs_err = (p - a).abs()
        rho, _ = spearmanr(p, a) if len(p) > 2 else (None, None)
        results[label] = {
            "sample_count": int(valid.sum()),
            "mean_absolute_error": round(float(abs_err.mean()), 2),
            "median_absolute_error": round(float(abs_err.median()), 2),
            "weighted_mean_absolute_error": round(float((abs_err * w).sum() / w.sum()), 2) if w.sum() else None,
            "spearman_correlation": round(float(rho), 4) if rho is not None else None,
            "spend_shape_of_predictions": _spend_shape(p),
        }
        # 95% CI on mean absolute error via normal approximation (n is
        # modest -- this is a rough interval, not a rigorous bootstrap).
        se = abs_err.std(ddof=1) / np.sqrt(len(abs_err)) if len(abs_err) > 1 else 0.0
        results[label]["mae_95pct_confidence_interval"] = [
            round(float(abs_err.mean() - 1.96 * se), 2), round(float(abs_err.mean() + 1.96 * se), 2),
        ]

    results["actual_historical_spend_shape"] = _spend_shape(actual)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"Wrote {OUT_PATH}")
    for label in predictors:
        r = results[label]
        print(f"\n{label}: n={r['sample_count']}, MAE=${r['mean_absolute_error']}, "
              f"MedAE=${r['median_absolute_error']}, weighted_MAE=${r['weighted_mean_absolute_error']}, "
              f"Spearman={r['spearman_correlation']}, 95% CI={r['mae_95pct_confidence_interval']}")
    print(f"\nActual historical spend shape: {results['actual_historical_spend_shape']}")


if __name__ == "__main__":
    main()
