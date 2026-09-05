#!/usr/bin/env python3
"""Sunday Final Build Stage 5-7: bounded evolutionary search, evolved
price distributions (Field A/B/C), and old-vs-evolved validation.

REDUCED, DISCLOSED BOUNDS (see outputs/auction_rebuild/sunday_final/final_report.md
for the full disclosure): population 24, 6 generations (spec suggests
10-20), 24 matches/generation (12 evals/genome/generation at pop 24),
baseline 30 matches (spec suggests 100), validation 40 auctions,
held-out 60 auctions (spec suggests >=100 / >=200), distribution fields
built from 80 auctions each (spec suggests >=250-500). Every number
below is REAL -- genuinely simulated, not fabricated -- just fewer
samples than the spec's full suggested schedule, traded off deliberately
against total runtime per the spec's own explicit permission to reduce
population/generations/samples before cutting legality checks or fixed
opponents.

Disjoint seeds: TRAIN uses rng seeded 42, VALIDATION uses a fresh
np.random.default_rng seeded 10042, HELD_OUT uses 20042 -- non-
overlapping streams, matching this project's established seed-
disjointness convention (auction_model/calibration.py).
"""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np

from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.auction import run_single_auction
from mock_draft.legal_lineup import build_production_lineup
from mock_draft import evolution as eng
from mock_draft.genome import genome_to_dict, random_genome
from mock_draft.archetypes import Archetype

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "evolution_sunday"
SUNDAY_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "sunday_final"
STATE_PATH = OUT_DIR / "sunday_evolved_population.json"

POPULATION_SIZE = 24
GENERATIONS = 6
MATCHES_PER_GEN = 24  # 12 evals/genome/generation at population 24
BASELINE_MATCHES = 30
VALIDATION_AUCTIONS = 40
HELD_OUT_AUCTIONS = 60
DISTRIBUTION_AUCTIONS_PER_FIELD = 80
ENSEMBLE_TARGET_SIZE = 8

SAM_FOCUS_PLAYERS = {"Josh Allen", "Rashee Rice", "Terry McLaurin", "George Kittle",
                     "Travis Etienne", "DeVonta Smith", "Mark Andrews"}


def log(msg):
    print(f"[evolution] {msg}", flush=True)


def roster_utility(team):
    return build_production_lineup(team.roster).total_roster_utility


def genome_distance(g1: Archetype, g2: Archetype) -> float:
    d1, d2 = genome_to_dict(g1), genome_to_dict(g2)
    dist = 0.0
    for key in ("max_stars", "star_ceiling_pct", "price_ceiling_pct", "tier_aggression",
                "noise_std", "jump_bid_prob", "tilt_after_losses", "tilt_boost"):
        v1, v2 = d1.get(key, 0), d2.get(key, 0)
        dist += (float(v1) - float(v2)) ** 2
    for pos in ("QB", "RB", "WR", "TE"):
        v1 = d1.get("position_weight", {}).get(pos, 1.0)
        v2 = d2.get("position_weight", {}).get(pos, 1.0)
        dist += (float(v1) - float(v2)) ** 2
    return dist ** 0.5


def select_diverse_ensemble(population, fitness, target_size, min_distance=0.3):
    order = np.argsort(-np.nan_to_num(fitness, nan=-1e9))
    ensemble = []
    for idx in order:
        g = population[idx]
        if all(genome_distance(g, e) >= min_distance for e in ensemble):
            ensemble.append(g)
        if len(ensemble) >= target_size:
            break
    return ensemble


def run_bounded_evolution(players, teams):
    if STATE_PATH.exists():
        STATE_PATH.unlink()  # fresh run, not resuming from any prior population
    t0 = time.time()
    log(f"Starting bounded evolution: population={POPULATION_SIZE} generations={GENERATIONS} "
        f"matches/gen={MATCHES_PER_GEN} baseline_matches={BASELINE_MATCHES}")
    population, history = eng.run_evolution(
        generations=GENERATIONS, population_size=POPULATION_SIZE, matches_per_generation=MATCHES_PER_GEN,
        players=players, teams_template=teams, seed=42, state_path=STATE_PATH, verbose=True,
        baseline_matches=BASELINE_MATCHES,
    )
    elapsed = time.time() - t0
    log(f"Evolution complete in {elapsed:.1f}s ({elapsed/60:.1f} min)")

    rows = []
    for h in history:
        rows.append({"generation": h.generation, "best_fitness": round(h.best_fitness, 2),
                     "mean_fitness": round(h.mean_fitness, 2), "worst_fitness": round(h.worst_fitness, 2),
                     "best_genome_name": h.best_genome_name})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (SUNDAY_DIR / "evolution_training_history.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    log(f"Wrote evolution_training_history.csv ({len(rows)} generations)")
    return population, history, elapsed


def evaluate_ensemble(ensemble, players, teams, n_auctions, seed_base, baselines):
    """Run each ensemble genome, assigned round-robin + rng-shuffled across
    the 12 real team slots for balanced exposure, over n_auctions auctions
    seeded from a disjoint stream. Returns per-genome adjusted-utility
    samples plus legality/cash stats."""
    rng = np.random.default_rng(seed_base)
    team_names = list(teams.keys())
    n_teams = len(team_names)
    per_genome_utils = {g.name: [] for g in ensemble}
    legal_count = 0
    total_count = 0
    unused_cash_samples = []

    for a in range(n_auctions):
        genome_idx = rng.choice(len(ensemble), size=n_teams, replace=len(ensemble) < n_teams)
        rng.shuffle(genome_idx)
        strategies = {team_names[i]: ensemble[genome_idx[i]] for i in range(n_teams)}
        _, final_teams = run_single_auction(players, teams, rng, strategies=strategies)
        for i, tname in enumerate(team_names):
            team = final_teams[tname]
            total_count += 1
            legal = len(team.roster) == 15
            if legal:
                legal_count += 1
            util = roster_utility(team) - baselines.get(tname, 0.0)
            per_genome_utils[ensemble[genome_idx[i]].name].append(util)
            unused_cash_samples.append(max(0.0, team.budget_remaining))

    return per_genome_utils, legal_count / max(1, total_count), float(np.mean(unused_cash_samples))


def summarize_utils(per_genome_utils):
    all_vals = [v for vals in per_genome_utils.values() for v in vals]
    if not all_vals:
        return {"mean": None, "median": None, "p25": None, "p10": None, "worst_decile": None}
    arr = np.array(all_vals)
    return {
        "mean": round(float(np.mean(arr)), 2), "median": round(float(np.median(arr)), 2),
        "p25": round(float(np.percentile(arr, 25)), 2), "p10": round(float(np.percentile(arr, 10)), 2),
        "worst_decile": round(float(np.mean(arr[arr <= np.percentile(arr, 10)])), 2) if len(arr) else None,
    }


def build_field_distribution(field_name, strategies_per_auction_fn, players, teams, n_auctions, seed_base):
    """Run n_auctions auctions, each with a strategy assignment produced by
    strategies_per_auction_fn(rng), collect every sale price per player."""
    rng = np.random.default_rng(seed_base)
    sales_by_player: dict[str, list[float]] = {}
    n_drafted = 0
    for a in range(n_auctions):
        strategies = strategies_per_auction_fn(rng)
        log_entries, _ = run_single_auction(players, teams, rng, strategies=strategies)
        n_drafted += 1
        for e in log_entries:
            sales_by_player.setdefault(e["player"], []).append(e["sale_price"])
        if (a + 1) % 20 == 0:
            log(f"  [{field_name}] {a+1}/{n_auctions} auctions complete")
    return sales_by_player


def main():
    t_start = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SUNDAY_DIR.mkdir(parents=True, exist_ok=True)

    log("Loading confirmed pool/teams...")
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")

    # ---- Stage 5: bounded evolution ----
    population, history, evo_elapsed = run_bounded_evolution(players, teams)

    # Recompute baselines once (reuse same baseline_matches, but seeded to
    # match the run) for later utility-adjustment during validation/held-out.
    log("Recomputing team baselines for validation/held-out scoring...")
    baseline_rng = np.random.default_rng(777)
    team_baselines = eng.compute_team_baselines(players, teams, BASELINE_MATCHES, baseline_rng)

    final_fitness = eng.evaluate_generation(population, players, teams, MATCHES_PER_GEN,
                                             np.random.default_rng(999), team_baselines)
    ensemble = select_diverse_ensemble(population, final_fitness, ENSEMBLE_TARGET_SIZE)
    log(f"Selected diverse ensemble of {len(ensemble)} genomes (target {ENSEMBLE_TARGET_SIZE}).")

    ensemble_rows = []
    for g in ensemble:
        d = genome_to_dict(g)
        ensemble_rows.append({"name": g.name, **{k: v for k, v in d.items() if k != "position_weight"},
                              **{f"weight_{p}": v for p, v in d.get("position_weight", {}).items()}})
    with (SUNDAY_DIR / "final_strategy_ensemble.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(ensemble_rows[0].keys()))
        w.writeheader(); w.writerows(ensemble_rows)
    log(f"Wrote final_strategy_ensemble.csv ({len(ensemble_rows)} genomes)")

    # ---- Validation (disjoint seed stream, NEVER used for selection) ----
    log(f"Validating ensemble across {VALIDATION_AUCTIONS} auctions (disjoint seed stream)...")
    val_utils, val_legal_rate, val_unused_cash = evaluate_ensemble(
        ensemble, players, teams, VALIDATION_AUCTIONS, seed_base=10042, baselines=team_baselines)
    val_summary = summarize_utils(val_utils)
    val_summary.update({"legal_roster_rate": round(val_legal_rate, 4), "avg_unused_cash": round(val_unused_cash, 2),
                        "n_auctions": VALIDATION_AUCTIONS, "seed_base": 10042})
    with (SUNDAY_DIR / "evolution_validation_results.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(val_summary.keys()))
        w.writeheader(); w.writerow(val_summary)
    log(f"Validation: {val_summary}")

    # ---- Held-out (disjoint from both train and validation, evaluated ONCE) ----
    log(f"Held-out evaluation across {HELD_OUT_AUCTIONS} auctions (disjoint seed stream, evaluated once)...")
    ho_utils, ho_legal_rate, ho_unused_cash = evaluate_ensemble(
        ensemble, players, teams, HELD_OUT_AUCTIONS, seed_base=20042, baselines=team_baselines)
    ho_summary = summarize_utils(ho_utils)
    ho_summary.update({"legal_roster_rate": round(ho_legal_rate, 4), "avg_unused_cash": round(ho_unused_cash, 2),
                       "n_auctions": HELD_OUT_AUCTIONS, "seed_base": 20042})
    with (SUNDAY_DIR / "evolution_held_out_results.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(ho_summary.keys()))
        w.writeheader(); w.writerow(ho_summary)
    log(f"Held-out: {ho_summary}")

    close_to_validation = (val_summary["mean"] is not None and ho_summary["mean"] is not None and
                           abs(ho_summary["mean"] - val_summary["mean"]) < max(20.0, 0.25 * abs(val_summary["mean"])))
    log(f"Held-out close to validation: {close_to_validation}")

    # ---- Stage 6: Field A/B/C evolved price distributions ----
    log("Building Field A (fixed hand-built archetypes only)...")
    field_a = build_field_distribution("FieldA_fixed", lambda rng: None, players, teams,
                                        DISTRIBUTION_AUCTIONS_PER_FIELD, seed_base=30042)

    log("Building Field B (validated evolved ensemble only)...")
    def field_b_strategies(rng):
        team_names = list(teams.keys())
        idx = rng.choice(len(ensemble), size=len(team_names), replace=len(ensemble) < len(team_names))
        rng.shuffle(idx)
        return {team_names[i]: ensemble[idx[i]] for i in range(len(team_names))}
    field_b = build_field_distribution("FieldB_evolved", field_b_strategies, players, teams,
                                        DISTRIBUTION_AUCTIONS_PER_FIELD, seed_base=40042)

    log("Building Field C (blended: fixed + evolved + fresh random)...")
    def field_c_strategies(rng):
        team_names = list(teams.keys())
        pool = ensemble + [random_genome(rng, name=f"fresh_random_{i}") for i in range(4)]
        n_fixed = max(1, len(team_names) // 3)  # ~1/3 stay on default fixed archetypes (None strategy)
        idx = rng.choice(len(pool), size=len(team_names) - n_fixed, replace=True)
        strategies = {team_names[i]: pool[idx[i - n_fixed]] for i in range(n_fixed, len(team_names))}
        return strategies
    field_c = build_field_distribution("FieldC_blended", field_c_strategies, players, teams,
                                        DISTRIBUTION_AUCTIONS_PER_FIELD, seed_base=50042)

    def pct(vals, p):
        return float(np.percentile(vals, p)) if len(vals) >= 5 else None

    dist_rows = []
    all_players = set(field_a) | set(field_b) | set(field_c)
    for name in sorted(all_players):
        a_sales, b_sales, c_sales = field_a.get(name, []), field_b.get(name, []), field_c.get(name, [])
        row = {
            "player": name,
            "field_a_n_sales": len(a_sales), "field_a_mean": round(float(np.mean(a_sales)), 2) if a_sales else None,
            "field_a_p50": round(pct(a_sales, 50), 2) if pct(a_sales, 50) is not None else None,
            "field_b_n_sales": len(b_sales), "field_b_mean": round(float(np.mean(b_sales)), 2) if b_sales else None,
            "field_b_p50": round(pct(b_sales, 50), 2) if pct(b_sales, 50) is not None else None,
            "field_c_n_sales": len(c_sales), "field_c_mean": round(float(np.mean(c_sales)), 2) if c_sales else None,
            "field_c_p10": round(pct(c_sales, 10), 2) if pct(c_sales, 10) is not None else None,
            "field_c_p25": round(pct(c_sales, 25), 2) if pct(c_sales, 25) is not None else None,
            "field_c_p50": round(pct(c_sales, 50), 2) if pct(c_sales, 50) is not None else None,
            "field_c_p75": round(pct(c_sales, 75), 2) if pct(c_sales, 75) is not None else None,
            "field_c_p90": round(pct(c_sales, 90), 2) if pct(c_sales, 90) is not None else None,
            "field_c_insufficient_sales": len(c_sales) < 20,
            "draft_probability_field_c": round(len(c_sales) / DISTRIBUTION_AUCTIONS_PER_FIELD, 3),
        }
        dist_rows.append(row)
    with (SUNDAY_DIR / "evolved_price_distributions.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(dist_rows[0].keys()))
        w.writeheader(); w.writerows(dist_rows)
    log(f"Wrote evolved_price_distributions.csv ({len(dist_rows)} players)")

    total_elapsed = time.time() - t_start
    manifest = {
        "evolution_elapsed_seconds": round(evo_elapsed, 1),
        "total_elapsed_seconds": round(total_elapsed, 1),
        "population_size": POPULATION_SIZE, "generations": GENERATIONS, "matches_per_generation": MATCHES_PER_GEN,
        "baseline_matches": BASELINE_MATCHES, "validation_auctions": VALIDATION_AUCTIONS,
        "held_out_auctions": HELD_OUT_AUCTIONS, "distribution_auctions_per_field": DISTRIBUTION_AUCTIONS_PER_FIELD,
        "ensemble_size": len(ensemble), "held_out_close_to_validation": close_to_validation,
        "validation_summary": val_summary, "held_out_summary": ho_summary,
    }
    (OUT_DIR / "run_manifest.json").write_text(json.dumps(manifest, indent=2))
    log(f"DONE. Total elapsed: {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    log(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
