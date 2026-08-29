#!/usr/bin/env python3
"""Process fantasy_data.xlsx into CSVs for the auction model."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from auction_model import fantasy_data_xlsx

BASE_DIR = Path(__file__).parent
DEFAULT_XLSX = BASE_DIR / "fantasy_data.xlsx"
DEFAULT_PROJECTIONS = BASE_DIR / "data" / "projections_2026.csv"
DEFAULT_ACTUALS = BASE_DIR / "data" / "actuals_2025.csv"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", default=DEFAULT_XLSX, help="Path to fantasy_data.xlsx")
    p.add_argument("--projections-out", default=DEFAULT_PROJECTIONS)
    p.add_argument("--actuals-out", default=DEFAULT_ACTUALS)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    parsed = fantasy_data_xlsx.load_fantasy_data_xlsx(input_path)

    projections_df, proj_log = parsed["projections_2026"]
    actuals_df, actuals_log = parsed["actuals_2025"]

    projections_out = Path(args.projections_out)
    actuals_out = Path(args.actuals_out)
    projections_out.parent.mkdir(parents=True, exist_ok=True)
    actuals_out.parent.mkdir(parents=True, exist_ok=True)

    fantasy_data_xlsx.to_projections_csv(projections_df).to_csv(projections_out, index=False)
    fantasy_data_xlsx.to_actuals_csv(actuals_df).to_csv(actuals_out, index=False)

    print(f"Parsed {input_path}")
    print(f"  Projections: {len(projections_df)} players -> {projections_out}")
    print(f"  Last Year:   {len(actuals_df)} players -> {actuals_out}")

    for line in proj_log + actuals_log:
        print(f"  [data quality] {line}")

    if not projections_df.empty:
        top = projections_df.sort_values("projected_points", ascending=False).head(5)
        print("\nTop projected players (league scoring):")
        for _, row in top.iterrows():
            print(
                f"  {row['player']} ({row['position']}, {row['nfl_team']}): "
                f"{row['projected_points']:.1f} pts"
            )

    print(
        "\nRe-run valuation with:\n"
        f"  python run_valuation.py --projections {projections_out}"
    )


if __name__ == "__main__":
    main()
