"""Side-by-side per-player price from every valuation model built so far.

  price_yahoo_forward                         -- THE REAL MODEL. Yahoo 2026 forward
                                                  projections blended with this league's
                                                  actual salary history + keeper-inflation
                                                  math. Use this for real bids.
  price_yahoo_actuals_2025                     -- Yahoo/nflverse actual 2025 stats, rescored
                                                  under league rules. Same league-real budget/
                                                  keeper math, different (retrospective) points
                                                  input -- a legitimate second real estimate.
  price_fp_rank_curve_REFERENCE_ONLY           -- generic FantasyPros consensus ordering,
  price_fp_vbd_position_rank_REFERENCE_ONLY       rescaled to this league's budget total but
                                                  NOT adjusted for keeper-driven scarcity.
                                                  Pattern/consensus check only -- do not bid
                                                  off these two columns. See caveat in
                                                  build_fantasypros_valuations.py.

Run build_fantasypros_valuations.py first if fantasypros_rank_valuations.csv is stale.
"""

import pandas as pd

yahoo_fwd = pd.read_csv("output/veteran_auction_price_sheet.csv").rename(columns={
    "projected_points": "yahoo_forward_points",
    "suggested_auction_price": "price_yahoo_forward",
})
yahoo_actuals = pd.read_csv("output_v2_nflverse_baseline/veteran_auction_price_sheet.csv").rename(columns={
    "projected_points": "yahoo_actuals_2025_points",
    "suggested_auction_price": "price_yahoo_actuals_2025",
})
fp = pd.read_csv("output/fantasypros_rank_valuations.csv")

REAL_PRICE_COLS = ["price_yahoo_forward", "price_yahoo_actuals_2025"]
REFERENCE_PRICE_COLS = ["price_fp_rank_curve_REFERENCE_ONLY", "price_fp_vbd_position_rank_REFERENCE_ONLY"]

merged = yahoo_fwd[["player", "position", "yahoo_forward_points", "price_yahoo_forward", "historical_salary_if_known"]].merge(
    yahoo_actuals[["player", "yahoo_actuals_2025_points", "price_yahoo_actuals_2025"]],
    on="player", how="outer",
).merge(
    fp[["player", "fp_overall_rank"] + REFERENCE_PRICE_COLS],
    on="player", how="outer",
)

merged["price_real_median"] = merged[REAL_PRICE_COLS].median(axis=1, skipna=True)
merged["price_real_range"] = merged[REAL_PRICE_COLS].max(axis=1, skipna=True) - merged[REAL_PRICE_COLS].min(axis=1, skipna=True)
merged["n_real_models_priced"] = merged[REAL_PRICE_COLS].notna().sum(axis=1)

merged = merged.sort_values("price_real_range", ascending=False)
merged.to_csv("output/model_comparison_all.csv", index=False)
print(len(merged), "players compared")
print(
    "\nREAL models (bid off these): price_yahoo_forward, price_yahoo_actuals_2025 "
    "-- both use this league's actual budget + keeper inflation.\n"
    "REFERENCE-ONLY columns: FantasyPros rank-based, generic-market ordering -- "
    "pattern check only, not a bid sheet.\n"
)
print("Biggest disagreement between the two REAL models (top 15):")
print(merged[merged["n_real_models_priced"] >= 2].head(15)[
    ["player", "position"] + REAL_PRICE_COLS + REFERENCE_PRICE_COLS + ["price_real_median", "price_real_range"]
].to_string(index=False))
