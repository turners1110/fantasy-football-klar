#!/usr/bin/env python3
"""Phase 3B item 7: a keeper-adjusted position spending benchmark. Raw
2025 spending shares (or even the public rank/tier curve) don't account
for THIS league's actual 2026 post-keeper demand shape -- a position with
heavy keeper coverage has less real open demand than its historical share
implies, regardless of how good the historical or public signal is.

Per-team keeper composition (real data, mock_draft.data.
load_confirmed_pool_and_teams) determines exactly how many starter slots
per position are already filled before the auction even starts; FLEX and
bench demand are then distributed using this repo's own
auction_model.config formulas (FLEX_SHARE, BENCH_DEMAND_PER_TEAM) --
the SAME inputs replacement_rank() already uses, so this benchmark is
methodologically consistent with the rest of the pipeline, not a new
ad hoc formula.

blended_target_share combines three signals with EQUAL weight (a simple,
transparent, non-fitted default -- item 15 is where any fitted weighting
would eventually be calibrated, not here):
  - public_value_share (from public_market_benchmarks.csv's
    PUBLIC_RANK_TIER curve's position_spending)
  - projection_value_share (from EXISTING_PROJECTION_NEUTRAL's
    position_spending)
  - historical_value_share (from historical_concentration_benchmarks.csv
    is NOT position-broken-out -- uses salary_origin_audit.csv's own
    per-position sums on the RELIABILITY_WEIGHTED subset instead)
each first RENORMALIZED so open-demand-weighted positions get more
weight implicitly through the open_required_starter_slots /
open_flex_demand / expected_bench_demand fields reported alongside, not
baked silently into the blend itself.

Writes outputs/auction_rebuild/phase3b/keeper_adjusted_position_benchmark.csv
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from auction_model import config, valuation
from auction_model.confirmed_keeper_pipeline import normalize_name
from mock_draft.data import load_confirmed_pool_and_teams

OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3b" / "keeper_adjusted_position_benchmark.csv"
REQUIRED_STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
FLEX_SLOTS_PER_TEAM = 3


def main() -> None:
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")

    # Per-team, per-position keeper counts and resulting OPEN starter need.
    open_starters = Counter()
    for team in teams.values():
        counts = Counter(pos for _n, pos, _pr, _pts in team.roster)
        for pos, req in REQUIRED_STARTERS.items():
            open_starters[pos] += max(0, req - counts.get(pos, 0))

    total_open_flex = FLEX_SLOTS_PER_TEAM * len(teams)
    total_open_bench = sum(max(0, 15 - len(t.roster)) for t in teams.values()) - sum(open_starters.values())
    # (bench demand is whatever's left after starters+flex are filled from the 108 total open slots)

    keepers_at_position = Counter()
    for team in teams.values():
        for _n, pos, _pr, _pts in team.roster:
            keepers_at_position[pos] += 1

    pool_by_pos = Counter(p.position for p in players.values())

    proj = pd.read_csv(BASE_DIR / "data" / "projections_2026.csv")
    proj = valuation.add_vbd_scores(proj, points_col="projected_points")

    # Public/projection/historical value shares (from files already built this phase).
    public_bench = json.loads((BASE_DIR / "outputs" / "auction_rebuild" / "phase3b" / "benchmark_summary.json").read_text())
    public_pos_spend = public_bench["curves"]["PUBLIC_RANK_TIER"]["position_spending"]
    proj_pos_spend = public_bench["curves"]["EXISTING_PROJECTION_NEUTRAL"]["position_spending"]
    public_total = sum(public_pos_spend.values())
    proj_total = sum(proj_pos_spend.values())

    audit = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "salary_origin_audit.csv")
    reliable = audit[audit["included_in_market_calibration"] == True]  # noqa: E712
    hist_pos_spend = reliable.groupby("position")["salary"].sum()
    hist_total = hist_pos_spend.sum()

    rows = []
    for pos in ("QB", "RB", "WR", "TE"):
        flex_demand = round(total_open_flex * config.FLEX_SHARE.get(pos, 0.0), 1)
        bench_demand = round(config.BENCH_DEMAND_PER_TEAM.get(pos, 0.0) * len(teams), 1)
        pos_pool = proj[proj["position"] == pos].dropna(subset=["projected_points"])
        above_replacement = int((pos_pool["VBD_score"] > 0).sum())
        elite = int((pos_pool["VBD_score"] >= pos_pool["VBD_score"].quantile(0.75)).sum()) if len(pos_pool) else 0

        needy_teams_cash = sum(
            t.budget_remaining for t in teams.values()
            if Counter(p for _n, p, _pr, _pts in t.roster).get(pos, 0) < REQUIRED_STARTERS[pos]
        )

        public_share = round(public_pos_spend.get(pos, 0.0) / public_total, 4) if public_total else 0.0
        proj_share = round(proj_pos_spend.get(pos, 0.0) / proj_total, 4) if proj_total else 0.0
        hist_share = round(float(hist_pos_spend.get(pos, 0.0)) / hist_total, 4) if hist_total else 0.0
        blended = round((public_share + proj_share + hist_share) / 3, 4)

        rows.append({
            "position": pos,
            "keepers_at_position": keepers_at_position.get(pos, 0),
            "open_required_starter_slots": open_starters.get(pos, 0),
            "open_flex_demand": flex_demand,
            "expected_bench_demand": bench_demand,
            "auction_eligible_players": pool_by_pos.get(pos, 0),
            "players_above_replacement": above_replacement,
            "elite_players_available": elite,
            "league_cash_of_needy_teams": round(needy_teams_cash, 2),
            "public_value_share": public_share,
            "projection_value_share": proj_share,
            "historical_value_share": hist_share,
            "blended_target_share": blended,
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {OUT_PATH}")
    print(f"(total_open_bench computed for sanity, not a column: {total_open_bench})")
    for r in rows:
        print(f"{r['position']}: open_starters={r['open_required_starter_slots']}, flex_demand={r['open_flex_demand']}, "
              f"bench_demand={r['expected_bench_demand']}, blended_target_share={r['blended_target_share']:.2%}")

    simulated_shares = {"QB": 0.032, "RB": 0.658, "WR": 0.289, "TE": 0.022}  # from phase 3A market_clearing_diagnostics
    print("\nSimulated (phase 3A, pre-3B) vs blended target:")
    for r in rows:
        pos = r["position"]
        print(f"  {pos}: simulated={simulated_shares[pos]:.1%}, blended_target={r['blended_target_share']:.1%}, "
              f"gap={simulated_shares[pos] - r['blended_target_share']:+.1%}")


if __name__ == "__main__":
    main()
