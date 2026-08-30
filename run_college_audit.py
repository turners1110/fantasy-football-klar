#!/usr/bin/env python3
"""College prospect audit, debut detection, valuation, and conversion alerts.

    python3 run_college_audit.py
    python3 run_college_audit.py --holdings data/college_holdings.csv \\
        --projections data/college_prospect_projections.csv \\
        --output-dir outputs
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from auction_model import college_prospects, config, data_pipeline, valuation
from run_valuation import merge_projections

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "outputs"

# Manual projected points for conversion valuation when not yet in projections CSV
CONVERSION_MANUAL_POINTS = {
    "fernando mendoza": 285.0,
    "isaiah bond": 120.0,
    "jordan james": 45.0,
    "trevor etienne": 95.0,
    "jalen royals": 35.0,
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--holdings", default=DATA_DIR / "college_holdings.csv")
    p.add_argument("--projections", default=DATA_DIR / "college_prospect_projections.csv")
    p.add_argument("--pick-ownership", default=DATA_DIR / "college_pick_ownership.csv")
    p.add_argument("--nflverse-dir", default=DATA_DIR / "nflverse")
    p.add_argument("--salaries", default=DATA_DIR / "historical_salaries_2025_raw.csv")
    p.add_argument("--fantasypros-rankings", default=BASE_DIR / "FantasyPros_2026_Draft_ALL_Rankings.csv")
    p.add_argument("--projections-veteran", default=DATA_DIR / "projections_2026.csv")
    p.add_argument("--output-dir", default=OUTPUT_DIR)
    p.add_argument("--blend-weight", type=float, default=0.6)
    return p.parse_args()


def _load_pick_ownership(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    df = pd.read_csv(path, comment="#")
    if df.empty:
        return None
    return df


def _value_converted_prospects(
    alerts: pd.DataFrame,
    args: argparse.Namespace,
) -> pd.DataFrame:
    """Attach neutral open-market estimates for conversion alerts."""
    if alerts.empty:
        return alerts

    from auction_model.college_prospects import _normalize

    salaries, _ = data_pipeline.load_historical_salaries(args.salaries)
    fp = data_pipeline.load_fantasypros_rankings(args.fantasypros_rankings)
    pool = data_pipeline.expand_pool_with_full_universe(salaries.copy(), fp)
    pool = data_pipeline.merge_fp_tiers(pool, fp)
    projections = data_pipeline.load_optional_csv(args.projections_veteran)
    pool = merge_projections(pool, projections)
    pool = data_pipeline.fill_anchor_fallback(pool)

    values = []
    for _, row in alerts.iterrows():
        manual = CONVERSION_MANUAL_POINTS.get(_normalize(row["player"]))
        val = college_prospects.estimate_converted_veteran_value(
            row["player"], row["position"], pool, args.blend_weight,
            manual_points=manual,
        )
        keeper_cost = config.COLLEGE_DEBUT_FEE + config.KEEPER_BUMP_STANDARD
        values.append({
            "estimated_open_market_value": val,
            "keeper_cost_next_season": keeper_cost,
            "estimated_keeper_alpha": (val - keeper_cost) if val else None,
        })
    return pd.concat([alerts.reset_index(drop=True), pd.DataFrame(values)], axis=1)


def main() -> None:
    args = parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    audited_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    holdings = college_prospects.load_college_holdings(args.holdings)
    projections = college_prospects.load_prospect_projections(args.projections)
    audit = college_prospects.audit_debut_status(holdings, Path(args.nflverse_dir))
    board = college_prospects.build_prospect_board(holdings, audit, projections)
    picks = college_prospects.build_college_pick_table(_load_pick_ownership(Path(args.pick_ownership)))
    alerts = college_prospects.build_conversion_alerts(audit, board)
    alerts = _value_converted_prospects(alerts, args)

    salaries, _ = data_pipeline.load_historical_salaries(args.salaries)
    master = college_prospects.build_master_player_table(holdings, audit, salaries)

    # Write outputs
    board.to_csv(out / "college_prospect_board.csv", index=False)
    audit.to_csv(out / "college_debut_audit.csv", index=False)
    picks.to_csv(out / "college_draft_pick_values.csv", index=False)
    alerts.to_csv(out / "college_conversion_alerts.csv", index=False)
    master.to_csv(out / "master_player_status.csv", index=False)

    # Summary report
    stale = int(audit["stale_sheet_flag"].sum())
    conversions = len(alerts)
    still_college = int((audit["status"] == "college").sum())
    nfl_not_debuted = int((audit["status"] == "nfl_drafted_not_debuted").sum())

    lines = [
        f"College prospect audit — {audited_at}",
        "",
        f"Total college holdings: {len(holdings)}",
        f"  Still college (verified/no NFL debut): {still_college}",
        f"  NFL drafted/rostered but 0 reg games: {nfl_not_debuted}",
        f"  Debuted — pending $1 conversion: {conversions}",
        f"  Stale sheet flags (college label but should convert): {stale}",
        "",
        "OPEN RULE QUESTIONS (verify with commissioner):",
    ]
    for q in config.COLLEGE_RULE_OPEN_QUESTIONS:
        lines.append(f"  - {q}")
    lines += [
        "",
        "DATA QUALITY WARNING:",
        "  Sam's 6 holdings were manually verified; the other 78 rows inherit",
        "  'status unknown' from the Google Sheet. This audit used nflverse",
        "  regular-season game logs — NOT preseason or roster-only activation.",
        "  Recommend full-league manual verification before trade decisions.",
        "",
        "CONVERSION ALERTS (action required):",
    ]
    if alerts.empty:
        lines.append("  None")
    else:
        for _, a in alerts.iterrows():
            val = a.get("estimated_open_market_value")
            val_s = f"${val:.0f} est. open-market" if pd.notna(val) else "no projection match"
            lines.append(
                f"  [{a['alert_severity']}] {a['owner']} — {a['player']} ({a['position']}): "
                f"{a['debut_evidence']} → convert at ${config.COLLEGE_DEBUT_FEE} ({val_s})"
            )

    lines += ["", "TOP PROSPECT VALUES (pre-debut stash):",]
    for _, r in board[board["status"] == "college"].head(10).iterrows():
        lines.append(
            f"  {r['owner']:8} {r['player']:22} score={r['prospect_value_score']:.1f} "
            f"pick_eq={r['pick_equivalent']} proj={r.get('projected_nfl_draft_round', '?')}"
        )

    summary_path = out / "college_audit_summary.txt"
    summary_path.write_text("\n".join(lines))

    print(f"Audited {len(holdings)} college holdings")
    print(f"  Conversion alerts: {conversions} (stale sheet: {stale})")
    print(f"  Still college: {still_college} | NFL roster no reg game: {nfl_not_debuted}")
    print(f"\nOutputs written to {out}/")
    print(f"  college_prospect_board.csv")
    print(f"  college_conversion_alerts.csv")
    print(f"  college_draft_pick_values.csv")
    print(f"  master_player_status.csv")
    print(f"  college_audit_summary.txt")


if __name__ == "__main__":
    main()
