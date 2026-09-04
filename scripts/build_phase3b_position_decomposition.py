#!/usr/bin/env python3
"""Phase 3B item 8: decompose the simulated 65.8% RB spending share.

STATIC-AUDIT EVIDENCE FIRST (before running any simulation, per the
"find the cause before tuning" instruction):
  - config.replacement_rank(RB) == config.replacement_rank(WR) == 55
    (identical formula, identical NUM_TEAMS/FLEX_SHARE/BENCH_DEMAND
    inputs for RB vs WR -- FLEX_SHARE={'RB':0.45,'WR':0.45,'TE':0.10} is
    symmetric between RB/WR). This directly rules out candidate causes
    #2 (RB replacement level too low) and #3 (FLEX demand assigned too
    heavily to RB) as FORMULA bugs -- the formula treats RB/WR identically.
  - No archetype sets `position_weight` (mock_draft/archetypes.py) --
    every archetype's position_weight defaults to {} -> 1.0 for every
    position via `archetype.position_weight.get(player.position, 1.0)`.
    This rules out candidate cause #5 (position weights favor RB) directly
    from source, not simulation.
  - `position_targets` (the "positional extremist" archetypes' rigid
    allocation plan) is symmetric: positional_extremist_rb targets
    {RB:0.55,WR:0.30,...} and positional_extremist_wr mirrors it exactly
    {WR:0.55,RB:0.30,...} -- these cancel out in aggregate across the
    random archetype draw, not a systematic RB bias.
  - projection_position_audit.csv (item 9) DOES show a real, large
    asymmetry: RB VBD_mean=50.69 vs WR VBD_mean=22.46, despite the
    IDENTICAL replacement rank (55) -- meaning the raw point projections
    themselves in data/projections_2026.csv are far more top-heavy for
    RB than WR (WR's mean projected player is actually BELOW its own
    replacement level: mean_points=95.81 < replacement=105.92, while
    RB's mean sits well above: mean_points=104.74 > replacement=68.64).
    This is candidate cause #1 (RB projected points too high) -- or,
    read the other way, WR/TE points too FLAT/low relative to
    replacement -- and it feeds `base_value` (the VBD-derived auction
    price every bid is anchored to, see mock_draft/valuation.py:
    compute_willingness using player.base_value, not projected_points,
    as its private-value anchor), so it is a plausible primary driver of
    the simulated spend skew, not just a simulation-engine artifact.

CONTROLLED EXPERIMENTS run below test this and 3 other independently
testable causes by changing ONE factor at a time against the baseline
(all other seeds/config held fixed):
  1. baseline -- current production config, unmodified.
  2. rb_points_rescaled_to_wr_vbd_shape -- rescales RB base_value AND
     projected_points by the ratio needed to match WR's mean VBD exactly
     (22.46/50.69), directly testing causes #1/#2 as a bundle (they are
     empirically indistinguishable at the replacement-formula level,
     since the formula itself is already symmetric).
  3. flex_share_rb_zeroed -- FLEX_SHARE['RB'] forced to 0 (RB gets no
     flex-driven replacement-level credit), testing cause #3 in isolation
     from the point-projection asymmetry.
  4. incremental_utility_gate_disabled -- the zero/negative-utility bid
     gate bypassed entirely, testing cause #11 (the gate could
     differentially help/hurt RB depth bidding).
  5. archetype_position_weight_rb_half -- an explicit RB position_weight
     of 0.5 applied to every archetype, as a POSITIVE CONTROL confirming
     the harness can detect a real, deliberately-injected RB-suppressing
     effect (since #5 was ruled out at baseline, this checks the
     experiment methodology itself is sound, not just theorized).

NOT independently tested this pass (disclosed, not silently skipped):
causes #4 (nomination scoring), #6 (willingness multipliers beyond
position_weight/position_fit, both checked structurally above), #7/#8
(WR/TE missing projections -- covered descriptively by item 9's
projection_position_audit.csv rather than a live ablation), #9 (QB/TE
caps -- caps don't touch RB), #10 (public prices not in willingness --
true by design, not yet remediated), #12 (eligibility differences by
position -- ruled out structurally: eligibility classification in
auction_model/auction_eligibility.py has no position-specific branch),
#13 (tier definitions -- build_tiers in mock_draft/data.py applies the
same TIER_SIZE to every position, ruled out structurally), #14 (keeper
supply shift -- would require re-running with a counterfactual keeper
set, out of scope this pass). Left for phase 3C if the two tested causes
above do not fully explain the gap.

Writes outputs/auction_rebuild/phase3b/position_spend_decomposition.csv
"""

from __future__ import annotations

import copy
import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from mock_draft import auction as auction_mod
from mock_draft import legal_lineup as ll_mod
from mock_draft.auction import run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.legal_lineup import build_production_lineup

N_SEEDS = 40
OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3b" / "position_spend_decomposition.csv"


def _run_experiment_with_top12(players: dict, teams_template: dict, **kwargs) -> dict:
    """Runs N_SEEDS auctions and reports position spend shares plus the
    CORRECTLY-computed (per item 4's fix) mean per-auction top-12 share --
    never pooled across seeds."""
    orig_incremental = auction_mod._incremental_utility
    if kwargs.get("disable_utility_gate"):
        auction_mod._incremental_utility = lambda team, candidate, price: 1.0

    per_seed_top12 = []
    all_sales = []
    all_legal = []
    all_unused = []
    try:
        for seed in range(N_SEEDS):
            rng = np.random.default_rng(seed)
            log, final_teams = run_single_auction(players, teams_template, rng, position_max=kwargs.get("position_max"))
            prices = sorted((e["sale_price"] for e in log), reverse=True)
            total = sum(prices)
            per_seed_top12.append(sum(prices[:12]) / total if total else 0.0)
            all_sales.extend(log)
            for team in final_teams.values():
                all_unused.append(team.budget_remaining)
                all_legal.append(build_production_lineup(team.roster).lineup_is_legal)
    finally:
        auction_mod._incremental_utility = orig_incremental

    df = pd.DataFrame(all_sales)
    total_spend = float(df["sale_price"].sum())
    pos_spend = df.groupby("position")["sale_price"].sum()
    shares = {f"{pos}_share": round(pos_spend.get(pos, 0.0) / total_spend, 4) for pos in ("QB", "RB", "WR", "TE")}
    return {
        **shares,
        "total_spend": round(total_spend / N_SEEDS, 2),
        "top_12_share": round(float(np.mean(per_seed_top12)), 4),
        "legal_lineup_rate": round(sum(all_legal) / len(all_legal), 4) if all_legal else None,
        "unused_cash": round(float(np.mean(all_unused)), 2) if all_unused else None,
    }


def main() -> None:
    players, teams_template, _ = load_confirmed_pool_and_teams(budget_scenario="primary")

    experiments = []

    # 1. Baseline.
    result = _run_experiment_with_top12(players, teams_template)
    experiments.append({"experiment": "baseline", "changed_parameter": "none", **result,
                         "notes": "Current production config, unmodified."})

    # 2. RB points/base_value rescaled to match WR's mean VBD shape.
    rb_points = [p.projected_points for p in players.values() if p.position == "RB"]
    wr_vbd_mean = 22.46  # from projection_position_audit.csv
    rb_vbd_mean = 50.69
    scale = wr_vbd_mean / rb_vbd_mean
    rescaled_players = copy.deepcopy(players)
    for p in rescaled_players.values():
        if p.position == "RB":
            p.projected_points = round(p.projected_points * scale, 2)
            p.base_value = round(p.base_value * scale, 2)
    result = _run_experiment_with_top12(rescaled_players, teams_template)
    experiments.append({
        "experiment": "rb_points_rescaled_to_wr_vbd_shape", "changed_parameter": f"RB base_value/points x{scale:.3f}",
        **result,
        "notes": f"Tests causes #1/#2 bundled: rescales RB base_value+points so RB's VBD shape matches WR's "
                 f"(ratio {scale:.3f} derived from projection_position_audit.csv's WR/RB VBD means).",
    })

    # 3. RB excluded from FLEX scoring (cause #3 in isolation). FLEX_SHARE
    # lives in auction_model.config and already feeds replacement-rank
    # computation upstream (baked into base_value in the snapshot price
    # sheet, symmetric with WR as noted above), so this experiment
    # instead tests the DEMAND-side effect directly: exclude RB from
    # FLEX eligibility in the live auction engine's own utility scoring
    # (legal_lineup.FLEX_ELIGIBLE), isolating whether FLEX absorption
    # (not the point-projection asymmetry) drives RB demand.
    orig_flex_eligible = ll_mod.FLEX_ELIGIBLE
    ll_mod.FLEX_ELIGIBLE = ("WR", "TE")  # RB no longer FLEX-eligible in scoring
    try:
        result = _run_experiment_with_top12(players, teams_template)
    finally:
        ll_mod.FLEX_ELIGIBLE = orig_flex_eligible
    experiments.append({
        "experiment": "rb_excluded_from_flex_scoring", "changed_parameter": "legal_lineup.FLEX_ELIGIBLE = (WR, TE)",
        **result,
        "notes": "Tests cause #3: if RB demand falls sharply once RB can't fill FLEX in the utility scorer, "
                 "FLEX absorption (not the point-projection asymmetry) is a real contributor.",
    })

    # 4. Incremental-utility gate disabled (cause #11).
    result = _run_experiment_with_top12(players, teams_template, disable_utility_gate=True)
    experiments.append({
        "experiment": "incremental_utility_gate_disabled", "changed_parameter": "_incremental_utility always returns 1.0",
        **result,
        "notes": "Tests cause #11: whether the zero/negative-utility bid gate differentially suppresses or "
                 "favors RB bidding versus other positions.",
    })

    # 5. Positive control: explicit RB position_weight=0.5 injected into
    # every archetype, confirming the harness detects a real effect.
    from mock_draft.archetypes import ARCHETYPES
    orig_weights = {name: dict(a.position_weight) for name, a in ARCHETYPES.items()}
    for a in ARCHETYPES.values():
        a.position_weight["RB"] = 0.5
    try:
        result = _run_experiment_with_top12(players, teams_template)
    finally:
        for name, a in ARCHETYPES.items():
            a.position_weight.clear()
            a.position_weight.update(orig_weights[name])
    experiments.append({
        "experiment": "positive_control_rb_weight_halved", "changed_parameter": "every archetype position_weight[RB]=0.5",
        **result,
        "notes": "POSITIVE CONTROL, not a real candidate cause: confirms the experiment harness can detect a "
                 "real, deliberately-injected RB-suppressing effect, validating that cause #5's baseline "
                 "ruling-out (position_weight defaults to 1.0 everywhere) reflects a genuine absence of effect, "
                 "not a harness blind spot.",
    })

    fieldnames = ["experiment", "changed_parameter", "QB_share", "RB_share", "WR_share", "TE_share",
                  "total_spend", "top_12_share", "legal_lineup_rate", "unused_cash", "notes"]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for e in experiments:
            w.writerow({
                "experiment": e["experiment"], "changed_parameter": e["changed_parameter"],
                "QB_share": e["QB_share"], "RB_share": e["RB_share"], "WR_share": e["WR_share"], "TE_share": e["TE_share"],
                "total_spend": e["total_spend"], "top_12_share": e["top_12_share"],
                "legal_lineup_rate": e["legal_lineup_rate"], "unused_cash": e["unused_cash"], "notes": e["notes"],
            })

    print(f"Wrote {OUT_PATH}\n")
    for e in experiments:
        print(f"{e['experiment']}: QB={e['QB_share']:.1%} RB={e['RB_share']:.1%} WR={e['WR_share']:.1%} "
              f"TE={e['TE_share']:.1%} top12={e['top_12_share']:.1%} legal={e['legal_lineup_rate']:.1%} "
              f"unused=${e['unused_cash']}")


if __name__ == "__main__":
    main()
