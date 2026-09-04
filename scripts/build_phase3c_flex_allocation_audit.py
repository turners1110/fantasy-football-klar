#!/usr/bin/env python3
"""Phase 3C item 3: audit the hardcoded 45% RB / 45% WR / 10% TE FLEX
split (auction_model.config.FLEX_SHARE) against how FLEX is ACTUALLY
filled by this repo's own legal-lineup optimizer, rather than preserving
the assumption unexamined.

Derives the real FLEX mix from mock_draft.legal_lineup.build_production_lineup's
own optimal-fill logic (already proven optimal for points -- see that
function's docstring), applied to: (a) every team's REAL final roster
after a batch of live simulated auctions (post-keeper, under primary
projections), (b) a "before keepers" comparison using the full
auction-eligible pool with no keeper pre-allocation, and (c) low/high
projection-scenario sensitivity via a simple +/-15% point perturbation.

Writes outputs/auction_rebuild/phase3c/flex_allocation_audit.csv
"""

from __future__ import annotations

import copy
import csv
import sys
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np

from mock_draft.auction import run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.legal_lineup import build_production_lineup

OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3c" / "flex_allocation_audit.csv"
N_SEEDS = 20


def _flex_mix_for_rosters(rosters: list[list[tuple]]) -> Counter:
    mix = Counter()
    for roster in rosters:
        lineup = build_production_lineup(roster)
        if lineup.lineup_is_legal:
            for name in lineup.starting_flex:
                pos = next(p for n, p, _pr, _pts in roster if n == name)
                mix[pos] += 1
    return mix


def main() -> None:
    players, teams_template, _ = load_confirmed_pool_and_teams(budget_scenario="primary")

    rows = []

    # (a) Primary projections, after keepers, real simulated final rosters.
    all_rosters = []
    per_team_rosters = {}
    for seed in range(N_SEEDS):
        rng = np.random.default_rng(seed)
        _log, final_teams = run_single_auction(players, teams_template, rng)
        for name, team in final_teams.items():
            all_rosters.append(team.roster)
            per_team_rosters.setdefault(name, []).append(team.roster)

    mix_primary = _flex_mix_for_rosters(all_rosters)
    total_primary = sum(mix_primary.values())
    rows.append({
        "scenario": "primary_after_keepers", "RB_flex_slots": mix_primary.get("RB", 0),
        "WR_flex_slots": mix_primary.get("WR", 0), "TE_flex_slots": mix_primary.get("TE", 0),
        "RB_share": round(mix_primary.get("RB", 0) / total_primary, 4) if total_primary else None,
        "WR_share": round(mix_primary.get("WR", 0) / total_primary, 4) if total_primary else None,
        "TE_share": round(mix_primary.get("TE", 0) / total_primary, 4) if total_primary else None,
        "hardcoded_config_RB": 0.45, "hardcoded_config_WR": 0.45, "hardcoded_config_TE": 0.10,
        "n_observations": total_primary,
    })

    # By-team breakdown.
    for team_name, rosters in per_team_rosters.items():
        mix = _flex_mix_for_rosters(rosters)
        total = sum(mix.values())
        rows.append({
            "scenario": f"by_team_{team_name}", "RB_flex_slots": mix.get("RB", 0),
            "WR_flex_slots": mix.get("WR", 0), "TE_flex_slots": mix.get("TE", 0),
            "RB_share": round(mix.get("RB", 0) / total, 4) if total else None,
            "WR_share": round(mix.get("WR", 0) / total, 4) if total else None,
            "TE_share": round(mix.get("TE", 0) / total, 4) if total else None,
            "hardcoded_config_RB": 0.45, "hardcoded_config_WR": 0.45, "hardcoded_config_TE": 0.10,
            "n_observations": total,
        })

    # (b) Low/high projection scenarios (+/-15% point perturbation on the
    # SAME rosters already drafted -- isolates the FLEX-selection effect
    # of point uncertainty from re-running the whole auction).
    for label, factor in (("low_projection_scenario", 0.85), ("high_projection_scenario", 1.15)):
        perturbed_rosters = []
        for roster in all_rosters:
            perturbed_rosters.append([(n, p, pr, round(pts * factor, 1)) for n, p, pr, pts in roster])
        mix = _flex_mix_for_rosters(perturbed_rosters)
        total = sum(mix.values())
        rows.append({
            "scenario": label, "RB_flex_slots": mix.get("RB", 0), "WR_flex_slots": mix.get("WR", 0),
            "TE_flex_slots": mix.get("TE", 0),
            "RB_share": round(mix.get("RB", 0) / total, 4) if total else None,
            "WR_share": round(mix.get("WR", 0) / total, 4) if total else None,
            "TE_share": round(mix.get("TE", 0) / total, 4) if total else None,
            "hardcoded_config_RB": 0.45, "hardcoded_config_WR": 0.45, "hardcoded_config_TE": 0.10,
            "n_observations": total,
        })

    # (c) Before keepers -- FLEX mix if the FULL auction-eligible pool
    # (no keepers pre-assigned) filled every team's roster from scratch,
    # via the same greedy optimal-fill logic on the pool alone.
    pool_roster = [(p.name, p.position, 1.0, p.projected_points) for p in players.values()]
    # Split into 12 "virtual teams" of 15 by points, greedily, purely to
    # get a FLEX-mix reading independent of any keeper pre-allocation.
    by_pos_sorted = sorted(pool_roster, key=lambda r: r[3], reverse=True)
    virtual_teams = [[] for _ in range(12)]
    for i, entry in enumerate(by_pos_sorted[:180]):  # 12 teams x 15
        virtual_teams[i % 12].append(entry)
    mix_before = _flex_mix_for_rosters(virtual_teams)
    total_before = sum(mix_before.values())
    rows.append({
        "scenario": "before_keepers_virtual_snake_allocation", "RB_flex_slots": mix_before.get("RB", 0),
        "WR_flex_slots": mix_before.get("WR", 0), "TE_flex_slots": mix_before.get("TE", 0),
        "RB_share": round(mix_before.get("RB", 0) / total_before, 4) if total_before else None,
        "WR_share": round(mix_before.get("WR", 0) / total_before, 4) if total_before else None,
        "TE_share": round(mix_before.get("TE", 0) / total_before, 4) if total_before else None,
        "hardcoded_config_RB": 0.45, "hardcoded_config_WR": 0.45, "hardcoded_config_TE": 0.10,
        "n_observations": total_before,
    })

    fieldnames = ["scenario", "RB_flex_slots", "WR_flex_slots", "TE_flex_slots", "RB_share", "WR_share",
                  "TE_share", "hardcoded_config_RB", "hardcoded_config_WR", "hardcoded_config_TE", "n_observations"]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {OUT_PATH}")
    for label in ("primary_after_keepers", "low_projection_scenario", "high_projection_scenario",
                  "before_keepers_virtual_snake_allocation"):
        r = next(r for r in rows if r["scenario"] == label)
        print(f"{label}: RB={r['RB_share']}, WR={r['WR_share']}, TE={r['TE_share']} "
              f"(hardcoded assumption: RB=0.45, WR=0.45, TE=0.10)")


if __name__ == "__main__":
    main()
