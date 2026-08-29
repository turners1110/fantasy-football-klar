#!/usr/bin/env python3
"""Fancy Football League auction price sheet generator.

Re-run this any time projections, injuries, or keeper decisions change:

    python run_valuation.py
    python run_valuation.py --projections data/projections_2026.csv \\
        --keeper-overrides data/keeper_overrides.csv \\
        --rookie-pool data/rookie_pool.csv \\
        --blend-weight 0.6

With no --projections file, prices are pure historical-salary anchoring
(this league's actual 2025 salaries, reshaped for keeper removal + the
2RB/2WR/TE/3FLEX/no-K-DEF roster math) -- see README.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from auction_model import config, data_pipeline, keepers, rookie_board, valuation

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--salaries", default=DATA_DIR / "historical_salaries_2025_raw.csv")
    p.add_argument(
        "--fantasypros-rankings", default=BASE_DIR / "FantasyPros_2026_Draft_ALL_Rankings.csv",
        help="FantasyPros 'ALL Rankings' export, used only to widen the draftable "
        "pool beyond players who happened to be on a 2025 league roster. Rank/tier "
        "data only -- never used to fabricate a point projection.",
    )
    p.add_argument("--projections", default=DATA_DIR / "projections_2026.csv")
    p.add_argument("--keeper-overrides", default=DATA_DIR / "keeper_overrides.csv")
    p.add_argument("--rookie-pool", default=DATA_DIR / "rookie_pool.csv")
    p.add_argument(
        "--blend-weight", type=float, default=0.6,
        help="Weight given to VBD-from-projections vs. historical-salary anchor "
        "(0=pure anchor, 1=pure VBD). Only applies where a projection exists; "
        "ignored (forced to 0) if no projections file is supplied at all.",
    )
    p.add_argument("--output-dir", default=OUTPUT_DIR)
    return p.parse_args()


def merge_projections(pool: pd.DataFrame, projections: pd.DataFrame | None) -> pd.DataFrame:
    pool = pool.copy()
    pool["projected_points"] = pd.NA
    if projections is None:
        return pool

    proj = projections.copy()
    if "projected_points" not in proj.columns or proj["projected_points"].isna().all():
        proj["projected_points"] = proj.apply(
            lambda row: config.score_from_stats(row.to_dict()), axis=1
        )

    proj["_key"] = proj["player"].astype(str).str.strip().str.lower()
    pool["_key"] = pool["player"].astype(str).str.strip().str.lower()

    lookup = proj.set_index("_key")["projected_points"].to_dict()
    pool["projected_points"] = pool["_key"].map(lookup)
    pool["projected_points"] = pd.to_numeric(pool["projected_points"], errors="coerce")

    unmatched = proj[~proj["_key"].isin(pool["_key"])]
    if len(unmatched):
        print(
            f"NOTE: {len(unmatched)} players in the projections file did not match "
            f"anyone in the historical pool (new-to-this-pool players, e.g. rookies "
            f"who debuted, or a name-spelling mismatch). They are not included in "
            f"the auction price sheet in this run; expand historical_salaries "
            f"or check spelling if that's unexpected."
        )
    return pool.drop(columns=["_key"])


def main() -> None:
    args = parse_args()
    args.output_dir = Path(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    salaries, log = data_pipeline.load_historical_salaries(args.salaries)
    print(f"Loaded {len(salaries)} historical roster rows.")
    for line in log:
        print(f"  [data quality] {line}")

    overrides = data_pipeline.load_optional_csv(args.keeper_overrides)
    with_keepers = keepers.apply_keeper_overrides(salaries, overrides)
    with_keepers = keepers.price_keepers(with_keepers)

    inflation = keepers.inflation_summary(with_keepers)
    print("\nKeeper / inflation summary:")
    for k, v in inflation.items():
        print(f"  {k}: {v}")

    pool = with_keepers[~with_keepers["will_keep"]].copy()

    fp_rankings = data_pipeline.load_fantasypros_rankings(args.fantasypros_rankings)
    n_before = len(pool)
    pool = data_pipeline.expand_pool_with_full_universe(pool, fp_rankings)
    n_added = len(pool) - n_before
    if fp_rankings is not None:
        print(
            f"\nWidened draftable pool with {n_added} players from "
            f"{args.fantasypros_rankings} who weren't on a 2025 league roster "
            "(no historical salary; unpriced until a projection is supplied)."
        )

    projections = data_pipeline.load_optional_csv(args.projections)
    blend_weight = args.blend_weight
    if projections is None:
        print(
            "\nNo projections file found at "
            f"{args.projections} -- pricing from historical-salary anchor only "
            "(blend_weight forced to 0). Supply a projections CSV (see "
            "data/projections_2026.template.csv) and re-run to blend in "
            "2026 point projections."
        )
        blend_weight = 0.0
    pool = merge_projections(pool, projections)

    priced = valuation.price_pool(
        pool,
        remaining_budget=inflation["remaining_budget"],
        inflation_multiplier=inflation["inflation_multiplier"],
        blend_weight=blend_weight,
    )

    report = valuation.run_sanity_checks(priced, inflation["remaining_budget"])
    print("\nSanity checks:")
    print(f"  Remaining budget to distribute: ${report['remaining_budget']}")
    print(f"  Total suggested prices sum to:  ${report['total_priced']}")
    print(f"  Within tolerance: {report['budget_within_tolerance']}")
    print(f"  Prices out of [$1, $100] range: {report['n_out_of_range']}")
    print(f"  Players with no data to price at all: {report['n_unpriced_no_data']}")
    print(f"  Players priced >2x or <0.5x their 2025 salary: {report['n_large_moves_vs_2025_salary']}")
    if len(report["large_moves"]):
        print(report["large_moves"].to_string(index=False))

    auction_cols = [
        "player", "position", "nfl_team", "projected_points", "VBD_score",
        "suggested_auction_price", "salary_2025", "notes",
    ]
    priced_out = priced.copy()
    if "nfl_team" not in priced_out.columns:
        priced_out["nfl_team"] = ""
    else:
        priced_out["nfl_team"] = priced_out["nfl_team"].fillna("")
    priced_out = priced_out.rename(columns={"salary_2025": "historical_salary_if_known"})
    auction_cols = [c if c != "salary_2025" else "historical_salary_if_known" for c in auction_cols]
    auction_out_path = args.output_dir / "veteran_auction_price_sheet.csv"
    priced_out[auction_cols].sort_values("suggested_auction_price", ascending=False, na_position="last").to_csv(
        auction_out_path, index=False
    )
    print(f"\nWrote {auction_out_path}")

    keeper_cols = ["team", "player", "position", "salary_2025", "tag_used", "on_ir", "keeper_price_2026", "keep_source"]
    kept_out_path = args.output_dir / "keepers_2026.csv"
    with_keepers[with_keepers["will_keep"]][keeper_cols].to_csv(kept_out_path, index=False)
    print(f"Wrote {kept_out_path}")

    rookie_pool_df = data_pipeline.load_optional_csv(args.rookie_pool)
    board = rookie_board.build_rookie_board(rookie_pool_df)
    rookie_out_path = args.output_dir / "college_rookie_draft_board.csv"
    board.to_csv(rookie_out_path, index=False)
    print(f"Wrote {rookie_out_path} ({len(board)} rows)")
    if rookie_pool_df is None:
        print(
            f"  (no rookie pool data found at {args.rookie_pool} -- see "
            "data/rookie_pool.template.csv to supply prospect data)"
        )


if __name__ == "__main__":
    main()
