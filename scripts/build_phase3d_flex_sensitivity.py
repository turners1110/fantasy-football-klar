#!/usr/bin/env python3
"""Phase 3D item 4: FLEX demand recomputed from EXACT_LEAGUEWIDE_ALLOCATION's
own legal FLEX assignments, plus a player-specific projection-uncertainty
sensitivity analysis of that RB/WR/TE FLEX mix (200 independent draws).

Writes:
  outputs/auction_rebuild/phase3d/flex_allocation_sensitivity.csv
"""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from auction_model import config as auction_cfg
from auction_model.flex_sensitivity import compute_flex_allocation_percentiles, N_DRAWS, PROJECTION_UNCERTAINTY
from mock_draft.data import load_confirmed_pool_and_teams

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3d"


def main() -> None:
    t0 = time.time()
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    pool_points = {p.name: (p.position, p.projected_points) for p in players.values()}
    team_keepers = {name: [(n, p, pts) for n, p, _pr, pts in t.roster] for name, t in teams.items()}

    result = compute_flex_allocation_percentiles(pool_points, team_keepers, n_draws=N_DRAWS)

    rows = []
    for pos in ("RB", "WR", "TE"):
        lo, hi = PROJECTION_UNCERTAINTY[pos]
        rows.append({
            "position": pos,
            "baseline_flex_slots": result["baseline_flex_mix"].get(pos, 0),
            "baseline_flex_share_pct": result["baseline_flex_share_pct"][pos],
            "sensitivity_p10_pct": result["percentiles"][pos]["p10"],
            "sensitivity_p50_pct": result["percentiles"][pos]["p50"],
            "sensitivity_p90_pct": result["percentiles"][pos]["p90"],
            "sensitivity_mean_pct": result["percentiles"][pos]["mean"],
            "projection_uncertainty_low_multiplier": lo,
            "projection_uncertainty_high_multiplier": hi,
            "n_draws": result["n_draws"],
            "seed": result["seed"],
            "retired_hardcoded_flex_share_for_comparison_only": auction_cfg.FLEX_SHARE.get(pos),
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "flex_allocation_sensitivity.csv"
    fieldnames = list(rows[0].keys())
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"Wrote {out_path} ({len(rows)} positions, {result['n_draws']} draws, "
          f"total runtime {round(time.time() - t0, 1)}s)")
    for r in rows:
        print(f"  {r['position']}: baseline {r['baseline_flex_share_pct']}%, "
              f"P10/P50/P90 = {r['sensitivity_p10_pct']}/{r['sensitivity_p50_pct']}/{r['sensitivity_p90_pct']}% "
              f"(legacy hardcoded split: {r['retired_hardcoded_flex_share_for_comparison_only']*100:.0f}%)")


if __name__ == "__main__":
    main()
