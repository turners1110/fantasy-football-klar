#!/usr/bin/env python3
"""Phase 3C item 6 (run FIRST, per explicit instruction: measure how much
excess concentration comes from the bidding layer alone, before touching
replacement levels, projections, or position weights).

Phase 3B found the simulator turns a ~30-31% base-value top-12 share
(this repo's own EXISTING_PROJECTION_NEUTRAL curve, static, no bidding)
into 65.3% final SIMULATED spending concentration. This script isolates,
one factor at a time, how much of that ~34-point gap each bidding-layer
mechanism contributes, by disabling/replacing ONE mechanism per
experiment against a common baseline.

SCOPING NOTES (disclosed):
  - Experiments 9/10 (alternate pricing rules) are implemented as
    POST-HOC REPRICING of the same recorded auction logs (using the
    already-captured second_highest_bid field), not a rerun with a
    different ascending-bid mechanic -- this cleanly isolates "does the
    PRICING RULE matter" from "does the WILLINGNESS COMPUTATION matter,"
    which the results below directly speak to.
  - Experiment 13 ("marginal utility only") uses a crude, explicitly
    labeled 1-utility-point = $1 proxy (mock_draft.legal_lineup.
    partial_lineup_value's before/after delta) rather than the full,
    computationally expensive counterfactual grid-search ceiling
    (mock_draft.counterfactual.hard_bid_ceiling) -- running the real
    ceiling engine as the LIVE per-bid-round willingness function across
    a full multi-seed batch is not practical (it does its own internal
    grid search + binary refinement per call). This experiment's
    absolute numbers are a rough proxy; its DIRECTIONAL finding
    (does removing the shared market anchor entirely change concentration)
    is still meaningful.
  - Experiment 12 ("public anchors only") uses the PUBLIC_RANK_TIER
    curve already built in phase 3B (public_market_benchmarks.csv) as
    the shared anchor value in place of base_value*noise.

Each experiment runs N_SEEDS auctions with ONE change from baseline.

Writes outputs/auction_rebuild/phase3c/concentration_root_cause.csv
"""

from __future__ import annotations

import copy
import csv
import sys
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from auction_model.confirmed_keeper_pipeline import normalize_name
from mock_draft import archetypes as arche_mod
from mock_draft import config_bridge as cfg
from mock_draft import nomination as nomination_mod
from mock_draft import valuation as val_mod
from mock_draft.auction import run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.legal_lineup import partial_lineup_value
from mock_draft.models import Player, Team

N_SEEDS = 30
OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3c" / "concentration_root_cause.csv"


@contextmanager
def _patched_archetypes(**field_overrides):
    """Temporarily override one or more fields on EVERY archetype
    (Archetype is a frozen dataclass, so field-by-field object.__setattr__
    is used rather than reassignment -- restored exactly on exit)."""
    originals = {name: {f: getattr(a, f) for f in field_overrides} for name, a in arche_mod.ARCHETYPES.items()}
    try:
        for a in arche_mod.ARCHETYPES.values():
            for f, v in field_overrides.items():
                object.__setattr__(a, f, v)
        yield
    finally:
        for name, a in arche_mod.ARCHETYPES.items():
            for f, v in originals[name].items():
                object.__setattr__(a, f, v)


@contextmanager
def _patched_cfg(**overrides):
    originals = {k: getattr(cfg, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(cfg, k, v)
        yield
    finally:
        for k, v in originals.items():
            setattr(cfg, k, v)


@contextmanager
def _patched_nomination(**overrides):
    originals = {k: getattr(nomination_mod, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(nomination_mod, k, v)
        yield
    finally:
        for k, v in originals.items():
            setattr(nomination_mod, k, v)


@contextmanager
def _patched_willingness(fn):
    original = val_mod.compute_willingness
    import mock_draft.auction as auction_mod
    original_in_auction = auction_mod.compute_willingness
    try:
        val_mod.compute_willingness = fn
        auction_mod.compute_willingness = fn
        yield
    finally:
        val_mod.compute_willingness = original
        auction_mod.compute_willingness = original_in_auction


def _metrics_for_logs(all_logs: list[list[dict]], players: dict, repricer=None) -> dict:
    """all_logs: one list of sale-dicts per seed."""
    per_seed_top12 = []
    per_seed_top24 = []
    per_seed_top12_prices = []  # PER-AUCTION top-12 prices, collected across seeds (never pooled-then-sorted)
    all_rows = []
    bidder_counts = []
    sale_to_base = []
    top12_sale_to_base = []
    for log in all_logs:
        rows = [dict(e) for e in log]
        if repricer is not None:
            for r in rows:
                r["sale_price"] = repricer(r)
        prices = sorted((r["sale_price"] for r in rows), reverse=True)
        total = sum(prices)
        per_seed_top12.append(sum(prices[:12]) / total if total else 0.0)
        per_seed_top24.append(sum(prices[:24]) / total if total else 0.0)
        per_seed_top12_prices.extend(prices[:12])
        all_rows.extend(rows)
        bidder_counts.extend(r["bidder_count"] for r in rows)
        for r in sorted(rows, key=lambda e: e["sale_price"], reverse=True)[:12]:
            base = players[r["player"]].base_value if r["player"] in players else None
            if base and base > 0:
                top12_sale_to_base.append(r["sale_price"] / base)
        for r in rows:
            base = players[r["player"]].base_value if r["player"] in players else None
            if base and base > 0:
                sale_to_base.append(r["sale_price"] / base)

    df = pd.DataFrame(all_rows)
    total_spend = df["sale_price"].sum()
    pos_spend = df.groupby("position")["sale_price"].sum()
    n_seeds = len(all_logs)
    return {
        "top_12_share": round(float(np.mean(per_seed_top12)), 4),
        "top_24_share": round(float(np.mean(per_seed_top24)), 4),
        "maximum_price": round(float(df["sale_price"].max()), 2),
        # Median price among each auction's OWN top-12 sales, pooled across
        # seeds only AFTER the per-auction top-12 selection -- never take
        # the pooled top-N across seeds (that is exactly item 4's phase 3B
        # aggregation bug, being carefully avoided here).
        "median_top_12_price": round(float(np.median(per_seed_top12_prices)), 2) if per_seed_top12_prices else None,
        "QB_spend_share": round(pos_spend.get("QB", 0.0) / total_spend, 4) if total_spend else None,
        "RB_spend_share": round(pos_spend.get("RB", 0.0) / total_spend, 4) if total_spend else None,
        "WR_spend_share": round(pos_spend.get("WR", 0.0) / total_spend, 4) if total_spend else None,
        "TE_spend_share": round(pos_spend.get("TE", 0.0) / total_spend, 4) if total_spend else None,
        "total_spending_per_auction": round(total_spend / n_seeds, 2),
        "one_dollar_sale_rate": round(float((df["sale_price"] <= 1.0).mean()), 4),
        "average_bidder_count": round(float(np.mean(bidder_counts)), 2),
        "average_sale_to_base_value_ratio": round(float(np.mean(sale_to_base)), 3) if sale_to_base else None,
        "average_top_12_sale_to_base_value_ratio": round(float(np.mean(top12_sale_to_base)), 3) if top12_sale_to_base else None,
    }


def _run_batch(players: dict, teams_template: dict, seed_offset: int = 0) -> list[list[dict]]:
    logs = []
    for i in range(N_SEEDS):
        rng = np.random.default_rng(seed_offset + i)
        log, final_teams = run_single_auction(players, teams_template, rng)
        logs.append(log)
    return logs


def main() -> None:
    players, teams_template, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    states = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv")

    experiments = []

    def _record(name: str, logs: list[list[dict]], notes: str, repricer=None):
        m = _metrics_for_logs(logs, players, repricer=repricer)
        experiments.append({"experiment": name, "notes": notes, **m})
        print(f"{name}: top12={m['top_12_share']:.1%} top24={m['top_24_share']:.1%} "
              f"max=${m['maximum_price']} spend/auction=${m['total_spending_per_auction']} "
              f"avg_sale/base={m['average_sale_to_base_value_ratio']}")

    # 15. Baseline (current unequal budgets, current everything).
    baseline_logs = _run_batch(players, teams_template)
    _record("15_current_unequal_budgets_baseline", baseline_logs, "Current production config, unmodified.")

    # 1. All archetype multipliers set to one.
    with _patched_archetypes(tier_aggression=1.0, tilt_boost=1.0, noise_std=0.0, jump_bid_prob=0.0,
                              max_stars=0, position_targets={}):
        logs = _run_batch(players, teams_template)
    _record("1_all_archetype_multipliers_one", logs,
            "tier_aggression=1.0, tilt_boost=1.0, noise_std=0, jump_bid_prob=0, max_stars=0, position_targets={} "
            "for every archetype simultaneously.")

    # 2. No early-draft premium.
    with _patched_cfg(EARLY_DRAFT_PREMIUM_MAX=0.0):
        logs = _run_batch(players, teams_template)
    _record("2_no_early_draft_premium", logs, "cfg.EARLY_DRAFT_PREMIUM_MAX = 0.0")

    # 3. No tier aggression.
    with _patched_archetypes(tier_aggression=1.0):
        logs = _run_batch(players, teams_template)
    _record("3_no_tier_aggression", logs, "Every archetype's tier_aggression = 1.0")

    # 4. No star multiplier.
    with _patched_archetypes(max_stars=0):
        logs = _run_batch(players, teams_template)
    _record("4_no_star_multiplier", logs, "Every archetype's max_stars = 0 (removes the star-ceiling override)")

    # 5. No price-enforcer behavior -- interpreted as pure value-purist
    # discipline (strict_value_ceiling=True) applied to EVERY archetype,
    # since there is no separate "enforcement" mechanism beyond that flag.
    with _patched_archetypes(strict_value_ceiling=True):
        logs = _run_batch(players, teams_template)
    _record("5_strict_value_ceiling_everywhere", logs,
            "Every archetype's strict_value_ceiling = True -- no star override, no early-draft premium applies "
            "(both are already skipped for strict_value_ceiling archetypes), private_val never exceeded.")

    # 6. No position-run pressure.
    with _patched_nomination(W_POSITION_RUN=0.0):
        logs = _run_batch(players, teams_template)
    _record("6_no_position_run_pressure", logs, "nomination.W_POSITION_RUN = 0.0")

    # 7. No emotional noise.
    with _patched_archetypes(noise_std=0.0):
        logs = _run_batch(players, teams_template)
    _record("7_no_emotional_noise", logs, "Every archetype's noise_std = 0.0 (private_val = base_value exactly)")

    # 8. No nomination-value pull.
    with _patched_nomination(W_VALUE=0.0):
        logs = _run_batch(players, teams_template)
    _record("8_no_nomination_value_pull", logs, "nomination.W_VALUE = 0.0")

    # 9. Winner pays second-highest willingness + $1 (post-hoc repricing
    # of the baseline logs -- see module docstring).
    def _reprice_second_plus_one(row):
        return min(row["sale_price"], row["second_highest_bid"] + 1.0) if row["second_highest_bid"] > 0 else row["sale_price"]
    _record("9_winner_pays_second_highest_plus_one", baseline_logs,
            "Post-hoc repricing of the SAME baseline logs: price = min(actual sale_price, second_highest_bid+1). "
            "Since this is already an ascending $1-increment auction, actual sale_price is usually already very "
            "close to this -- if concentration barely changes, the pricing RULE is not the driver.",
            repricer=_reprice_second_plus_one)

    # 10. Winner pays its own highest willingness -- the CURRENT mechanic
    # already caps sale_price at the winner's own max_can_pay, so baseline
    # IS this experiment; reported for direct comparison, not re-run.
    _record("10_winner_pays_own_highest_willingness_SAME_AS_BASELINE", baseline_logs,
            "Identical to baseline: resolve_bid already caps the winning price at the winner's own willingness "
            "(max_can_pay) by construction -- there is no separate 'overpay beyond own willingness' mechanic to "
            "remove. Reported for completeness, not a distinct rerun.")

    # 11. Shared base values only -- private_val = base_value exactly for
    # every team, bypassing the entire multiplier chain.
    def _shared_value_willingness(team, player, rng, draft_progress=0.0, diagnostics=None):
        return player.base_value
    with _patched_willingness(_shared_value_willingness):
        logs = _run_batch(players, teams_template)
    _record("11_shared_base_values_only", logs,
            "compute_willingness replaced entirely: willingness = player.base_value, no noise, no star/tier/"
            "early-draft/position adjustments at all.")

    # 12. Public anchors only -- use phase 3B's PUBLIC_RANK_TIER curve.
    public_bench_path = BASE_DIR / "outputs" / "auction_rebuild" / "phase3b" / "public_market_benchmarks.csv"
    if public_bench_path.exists():
        import json
        summary = json.loads((BASE_DIR / "outputs" / "auction_rebuild" / "phase3b" / "benchmark_summary.json").read_text())
        # Rebuild the per-player public price the same way build_phase3b_public_benchmarks.py did.
        fp = pd.read_csv(BASE_DIR / "FantasyPros_2026_Draft_ALL_Rankings.csv")
        fp["_key"] = fp["PLAYER NAME"].map(normalize_name)
        fp = fp.sort_values("RK").drop_duplicates("_key")
        discretionary_cash = summary["discretionary_cash"]
        rank_lookup = fp.set_index("_key")["RK"].to_dict()
        value_index = {name: (1.0 / rank_lookup[normalize_name(name)]) for name in players if normalize_name(name) in rank_lookup}
        total_index = sum(value_index.values())
        public_price = {
            name: cfg.MIN_PRICE + (value_index.get(name, 0.0) / total_index * discretionary_cash if total_index else 0.0)
            for name in players
        }

        def _public_anchor_willingness(team, player, rng, draft_progress=0.0, diagnostics=None):
            return public_price.get(player.name, cfg.MIN_PRICE)

        with _patched_willingness(_public_anchor_willingness):
            logs = _run_batch(players, teams_template)
        _record("12_public_anchors_only", logs,
                "compute_willingness replaced entirely: willingness = phase 3B's PUBLIC_RANK_TIER normalized "
                "price for that player, identical across every team (no noise, no behavioral adjustments).")
    else:
        print("Skipping experiment 12 (public_market_benchmarks.csv not found -- run phase 3B first)")

    # 13. Marginal utility only -- crude 1-point=$1 proxy, see docstring.
    def _marginal_utility_willingness(team, player, rng, draft_progress=0.0, diagnostics=None):
        before = partial_lineup_value(team.roster)
        after = partial_lineup_value(team.roster + [(player.name, player.position, 1.0, player.projected_points)])
        return max(cfg.MIN_PRICE, after - before)
    with _patched_willingness(_marginal_utility_willingness):
        logs = _run_batch(players, teams_template)
    _record("13_marginal_utility_only_CRUDE_PROXY", logs,
            "compute_willingness replaced entirely: willingness = partial_lineup_value(roster+player) - "
            "partial_lineup_value(roster), using a CRUDE, explicitly-labeled 1-utility-point=$1 conversion "
            "(not a calibrated dollar rate) -- directional finding only, not a precise dollar comparison.")

    # 14. Equal budgets. primary_auction_budget is ALREADY the post-keeper
    # remaining budget (team_starting_states.csv), so the equal figure is
    # set directly as budget_remaining -- subtracting keeper cost again
    # would double-count it (a bug caught in a 2-seed smoke test: budgets
    # went implausibly low, collapsing total spend to a third of normal).
    equal_budget = float(states["primary_auction_budget"].mean())
    equal_teams = copy.deepcopy(teams_template)
    for t in equal_teams.values():
        t.budget_remaining = round(equal_budget, 2)
    logs = _run_batch(players, equal_teams)
    _record("14_equal_budgets", logs, f"Every team's starting budget set to the league average (${equal_budget:.2f}).")

    fieldnames = ["experiment", "top_12_share", "top_24_share", "maximum_price", "median_top_12_price",
                  "QB_spend_share", "RB_spend_share", "WR_spend_share", "TE_spend_share",
                  "total_spending_per_auction", "one_dollar_sale_rate", "average_bidder_count",
                  "average_sale_to_base_value_ratio", "average_top_12_sale_to_base_value_ratio", "notes"]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for e in experiments:
            w.writerow({k: e.get(k) for k in fieldnames})

    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
