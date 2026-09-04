#!/usr/bin/env python3
"""Phase 3C item 2: audit the fixed replacement-rank method (RB=WR=55)
against two alternatives derived from THIS league's actual current
keeper/roster state, rather than assuming the symmetric formula is
correct just because it's symmetric.

Method A -- FIXED_RANK_LEGACY: auction_model.config.replacement_rank(),
unchanged, for comparison only.

Method B -- DEMAND_DERIVED: replacement rank = the number of OPEN slots
this league's actual keeper composition leaves at that position (open
required starters + FLEX share of 36 league-wide FLEX slots + bench
demand), i.e. the Nth-best REMAINING auction-eligible player, where N
comes from real post-keeper demand (phase 3B's
keeper_adjusted_position_benchmark.csv), not a generic 12-team formula
that assumes zero keepers exist yet.

Method C -- OPTIMIZATION_DERIVED: a single leaguewide greedy allocation
(not a full multi-team competitive ILP, which is a much larger and
separate problem -- disclosed simplification) that fills ALL 108 open
roster slots leaguewide in priority order (required starters first, by
points; then FLEX slots from the combined RB/WR/TE pool, by points;
then bench slots from every remaining position, by points) and reports
the worst (marginal) selected player at each position as that
position's replacement.

For each method: QB/RB/WR/TE replacement rank + points, VBD distribution,
position value share (VBD-proportional dollar split, this repo's own
convex VBD_DOLLAR_POWER convention), and top-12/24 concentration of
that value split.

Writes outputs/auction_rebuild/phase3c/replacement_level_comparison.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from auction_model import config as auction_cfg
from auction_model.valuation import _proportional_dollars
from mock_draft.data import load_confirmed_pool_and_teams

OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3c" / "replacement_level_comparison.csv"
REQUIRED_STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
FLEX_ELIGIBLE = ("RB", "WR", "TE")


def _value_metrics(pool_points: dict[str, float], replacement: dict[str, float], n_teams=12, total_slots=108):
    """pool_points: {player: points}. replacement: {position: replacement_points}.
    Returns VBD per player + a proportional dollar split (this repo's own
    convex-VBD-to-dollar convention) for concentration/position-share metrics."""
    df = pd.DataFrame([{"player": p, "points": pts, "position": pos} for (p, pos), pts in pool_points.items()])
    df["replacement_points"] = df["position"].map(replacement)
    df["VBD"] = (df["points"] - df["replacement_points"]).clip(lower=0)
    discretionary = 2913.0  # league discretionary cash, matches phase 3B/3C's own figure
    df["dollars"] = _proportional_dollars(df["VBD"] ** auction_cfg.VBD_DOLLAR_POWER, discretionary) + auction_cfg.MIN_PRICE
    ranked = df.sort_values("dollars", ascending=False)
    total = ranked["dollars"].sum()
    top12 = ranked["dollars"].head(12).sum()
    top24 = ranked["dollars"].head(24).sum()
    pos_share = ranked.groupby("position")["dollars"].sum() / total
    return df, {
        "top_12_share": round(top12 / total, 4) if total else None,
        "top_24_share": round(top24 / total, 4) if total else None,
        "position_value_share": {k: round(v, 4) for k, v in pos_share.to_dict().items()},
    }


def main() -> None:
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    pool_points = {(p.name, p.position): p.projected_points for p in players.values()}
    by_pos = {"QB": [], "RB": [], "WR": [], "TE": []}
    for (name, pos), pts in pool_points.items():
        by_pos[pos].append((name, pts))
    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: x[1], reverse=True)

    rows = []

    # --- Method A: fixed-rank legacy ---
    replacement_a = {}
    for pos in ("QB", "RB", "WR", "TE"):
        rank = auction_cfg.replacement_rank(pos)
        replacement_a[pos] = by_pos[pos][rank - 1][1] if rank <= len(by_pos[pos]) else by_pos[pos][-1][1]
    df_a, metrics_a = _value_metrics(pool_points, replacement_a)
    for pos in ("QB", "RB", "WR", "TE"):
        rank = auction_cfg.replacement_rank(pos)
        vbd_pos = df_a[df_a["position"] == pos]["VBD"]
        rows.append({
            "method": "A_FIXED_RANK_LEGACY", "position": pos, "replacement_rank": rank,
            "replacement_points": round(replacement_a[pos], 1),
            "vbd_mean": round(float(vbd_pos.mean()), 2), "vbd_median": round(float(vbd_pos.median()), 2),
            "position_value_share": metrics_a["position_value_share"].get(pos),
            "top_12_share": metrics_a["top_12_share"], "top_24_share": metrics_a["top_24_share"],
        })

    # --- Method B: demand-derived (real post-keeper open demand) ---
    keeper_bench = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "phase3b" / "keeper_adjusted_position_benchmark.csv")
    keeper_bench = keeper_bench.set_index("position")
    replacement_b, rank_b = {}, {}
    for pos in ("QB", "RB", "WR", "TE"):
        demand = (keeper_bench.loc[pos, "open_required_starter_slots"]
                  + keeper_bench.loc[pos, "open_flex_demand"] + keeper_bench.loc[pos, "expected_bench_demand"])
        rank = max(1, round(demand))
        rank_b[pos] = rank
        replacement_b[pos] = by_pos[pos][rank - 1][1] if rank <= len(by_pos[pos]) else by_pos[pos][-1][1]
    df_b, metrics_b = _value_metrics(pool_points, replacement_b)
    for pos in ("QB", "RB", "WR", "TE"):
        vbd_pos = df_b[df_b["position"] == pos]["VBD"]
        rows.append({
            "method": "B_DEMAND_DERIVED", "position": pos, "replacement_rank": rank_b[pos],
            "replacement_points": round(replacement_b[pos], 1),
            "vbd_mean": round(float(vbd_pos.mean()), 2), "vbd_median": round(float(vbd_pos.median()), 2),
            "position_value_share": metrics_b["position_value_share"].get(pos),
            "top_12_share": metrics_b["top_12_share"], "top_24_share": metrics_b["top_24_share"],
        })

    # --- Method C: optimization-derived (single leaguewide greedy fill) ---
    used = set()
    counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    selected_by_pos = {"QB": [], "RB": [], "WR": [], "TE": []}
    n_teams = 12
    # 1. Required starters first (across the whole league, by points).
    for pos, need in REQUIRED_STARTERS.items():
        target = need * n_teams
        for name, pts in by_pos[pos]:
            if name in used or counts[pos] >= target:
                continue
            used.add(name)
            counts[pos] += 1
            selected_by_pos[pos].append((name, pts))
    # 2. FLEX slots (36 total) from the combined RB/WR/TE pool, by points.
    flex_pool = sorted(
        [(name, pts, pos) for pos in FLEX_ELIGIBLE for name, pts in by_pos[pos] if name not in used],
        key=lambda x: x[1], reverse=True,
    )
    for name, pts, pos in flex_pool[:36]:
        used.add(name)
        counts[pos] += 1
        selected_by_pos[pos].append((name, pts))
    # 3. Remaining bench slots (108 - starters - flex) from ALL positions, by points.
    remaining_slots = 108 - sum(counts.values())
    all_remaining = sorted(
        [(name, pts, pos) for pos in ("QB", "RB", "WR", "TE") for name, pts in by_pos[pos] if name not in used],
        key=lambda x: x[1], reverse=True,
    )
    for name, pts, pos in all_remaining[:remaining_slots]:
        used.add(name)
        counts[pos] += 1
        selected_by_pos[pos].append((name, pts))

    replacement_c, rank_c = {}, {}
    for pos in ("QB", "RB", "WR", "TE"):
        rank_c[pos] = counts[pos]
        replacement_c[pos] = min(p[1] for p in selected_by_pos[pos]) if selected_by_pos[pos] else by_pos[pos][-1][1]
    df_c, metrics_c = _value_metrics(pool_points, replacement_c)
    for pos in ("QB", "RB", "WR", "TE"):
        vbd_pos = df_c[df_c["position"] == pos]["VBD"]
        rows.append({
            "method": "C_OPTIMIZATION_DERIVED", "position": pos, "replacement_rank": rank_c[pos],
            "replacement_points": round(replacement_c[pos], 1),
            "vbd_mean": round(float(vbd_pos.mean()), 2), "vbd_median": round(float(vbd_pos.median()), 2),
            "position_value_share": metrics_c["position_value_share"].get(pos),
            "top_12_share": metrics_c["top_12_share"], "top_24_share": metrics_c["top_24_share"],
        })

    fieldnames = ["method", "position", "replacement_rank", "replacement_points", "vbd_mean", "vbd_median",
                  "position_value_share", "top_12_share", "top_24_share"]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {OUT_PATH}")
    for method in ("A_FIXED_RANK_LEGACY", "B_DEMAND_DERIVED", "C_OPTIMIZATION_DERIVED"):
        method_rows = [r for r in rows if r["method"] == method]
        print(f"\n{method}: top12={method_rows[0]['top_12_share']:.2%} top24={method_rows[0]['top_24_share']:.2%}")
        for r in method_rows:
            print(f"  {r['position']}: rank={r['replacement_rank']}, pts={r['replacement_points']}, "
                  f"share={r['position_value_share']:.2%}" if r['position_value_share'] else f"  {r['position']}: rank={r['replacement_rank']}")


if __name__ == "__main__":
    main()
