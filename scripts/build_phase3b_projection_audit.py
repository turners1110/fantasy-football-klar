#!/usr/bin/env python3
"""Phase 3B item 9: audit projection distributions per position, using
this repo's own replacement-level/VBD machinery (auction_model.valuation)
rather than reinventing it, and check for the 9 specific data-quality
failure modes item 9 lists.

Writes:
  outputs/auction_rebuild/phase3b/projection_position_audit.csv
  outputs/auction_rebuild/phase3b/top_projection_audit.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from auction_model import config, valuation
from auction_model.confirmed_keeper_pipeline import normalize_name

PROJ_PATH = BASE_DIR / "data" / "projections_2026.csv"
OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3b"


def main() -> None:
    proj = pd.read_csv(PROJ_PATH)
    proj = valuation.add_vbd_scores(proj, points_col="projected_points")

    rows = []
    checks = []
    for position in ("QB", "RB", "WR", "TE"):
        pos_df = proj[proj["position"] == position]
        with_proj = pos_df.dropna(subset=["projected_points"])
        replacement = float(valuation.compute_replacement_baseline(proj).get(position, float("nan")))
        rows.append({
            "position": position,
            "player_count": len(pos_df),
            "players_with_projection": len(with_proj),
            "projection_coverage": round(len(with_proj) / len(pos_df), 4) if len(pos_df) else None,
            "mean_points": round(float(with_proj["projected_points"].mean()), 2) if len(with_proj) else None,
            "median_points": round(float(with_proj["projected_points"].median()), 2) if len(with_proj) else None,
            "90th_percentile_points": round(float(with_proj["projected_points"].quantile(0.9)), 2) if len(with_proj) else None,
            "maximum_points": round(float(with_proj["projected_points"].max()), 2) if len(with_proj) else None,
            "replacement_points": round(replacement, 2) if replacement == replacement else None,
            "VBD_mean": round(float(with_proj["VBD_score"].mean()), 2) if len(with_proj) else None,
            "VBD_median": round(float(with_proj["VBD_score"].median()), 2) if len(with_proj) else None,
            "VBD_maximum": round(float(with_proj["VBD_score"].max()), 2) if len(with_proj) else None,
        })

    # --- Top 30 by points and by VBD ---
    top_points = proj.dropna(subset=["projected_points"]).sort_values("projected_points", ascending=False).head(30)
    top_vbd = proj.dropna(subset=["VBD_score"]).sort_values("VBD_score", ascending=False).head(30)
    top_out_path = OUT_DIR / "top_projection_audit.csv"
    with top_out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank_by_points", "player", "position", "projected_points",
                    "rank_by_vbd", "player_vbd", "position_vbd", "VBD_score"])
        tp = top_points.reset_index(drop=True)
        tv = top_vbd.reset_index(drop=True)
        for i in range(30):
            r1 = tp.iloc[i] if i < len(tp) else None
            r2 = tv.iloc[i] if i < len(tv) else None
            w.writerow([
                i + 1, r1["player"] if r1 is not None else "", r1["position"] if r1 is not None else "",
                r1["projected_points"] if r1 is not None else "",
                i + 1, r2["player"] if r2 is not None else "", r2["position"] if r2 is not None else "",
                r2["VBD_score"] if r2 is not None else "",
            ])

    # --- The 9 required data-quality checks ---
    dupes = proj["player"].map(normalize_name).value_counts()
    dupes = dupes[dupes > 1]
    known_positions = {"QB", "RB", "WR", "TE"}
    bad_positions = proj[~proj["position"].isin(known_positions)]

    zero_or_missing = proj[proj["projected_points"].fillna(0) <= 0]
    te_no_receptions = proj[(proj["position"] == "TE") & (proj["reception"].fillna(0) == 0)]
    qb_missing_pass_td = proj[(proj["position"] == "QB") & proj["pass_td"].isna()]
    missing_rush_or_rec = proj[proj[["rush_yd", "reception"]].isna().all(axis=1) & (proj["position"].isin({"RB", "WR"}))]

    checks_md_lines = [
        f"1. Missing receptions (any position, reception column null): {int(proj['reception'].isna().sum())}",
        f"2. Missing passing categories (QB rows with pass_td null): {len(qb_missing_pass_td)}",
        f"3. NaN replacement level: {[p for p in ('QB','RB','WR','TE') if not (valuation.compute_replacement_baseline(proj).get(p) == valuation.compute_replacement_baseline(proj).get(p))]}",
        f"4. Duplicate players (same normalized name appears >1x): {len(dupes)} -- {dupes.to_dict() if len(dupes) else 'none'}",
        f"5. Position-label errors (position not in QB/RB/WR/TE): {len(bad_positions)} -- {bad_positions['position'].unique().tolist() if len(bad_positions) else 'none'}",
        f"6. QB passing-TD scoring rule in effect: pass_td={config.SCORING.pass_td} pts/TD "
        f"({'CORRECT -- matches the documented 4pt pass TD league rule' if config.SCORING.pass_td == 4.0 else 'MISMATCH -- expected 4.0'})",
        f"7. TE reception value: reception={config.SCORING.reception} pts (half-PPR, applies equally to TEs); "
        f"{len(te_no_receptions)} of {(proj['position']=='TE').sum()} TE rows show 0 recorded receptions "
        f"(may be legitimately low-target players, not necessarily a data bug -- flagged for review, not asserted broken).",
        f"8. Rookie/no-2025-record projections present in the active pool: cross-referenced separately in "
        f"eligibility_evidence_audit.csv (phase 3A) -- {proj['player'].map(normalize_name).isin(pd.read_csv(BASE_DIR/'outputs'/'auction_rebuild'/'phase3a'/'eligibility_evidence_audit.csv')['player'].map(normalize_name)).sum() if (BASE_DIR/'outputs'/'auction_rebuild'/'phase3a'/'eligibility_evidence_audit.csv').exists() else 'N/A'} "
        f"of {len(proj)} projected players also appear in the phase 3A eligibility-evidence audit.",
        f"9. Zero-or-missing-projection rows present in the raw file (would need the fallback-ratio imputation "
        f"in mock_draft/points.py to become a valid $1 target rather than a true zero): {len(zero_or_missing)} "
        f"of {len(proj)} rows.",
    ]

    fieldnames = list(rows[0].keys())
    pos_out_path = OUT_DIR / "projection_position_audit.csv"
    with pos_out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {pos_out_path}")
    print(f"Wrote {top_out_path}")
    for r in rows:
        print(f"  {r['position']}: n={r['player_count']}, coverage={r['projection_coverage']}, "
              f"mean={r['mean_points']}, replacement={r['replacement_points']}, VBD_mean={r['VBD_mean']}")
    print("\n--- Data-quality checks ---")
    for line in checks_md_lines:
        print(line)


if __name__ == "__main__":
    main()
