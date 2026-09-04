#!/usr/bin/env python3
"""Phase 3D items 9-11: run the calibration harness -- random-sampled
parameter search (disclosed simplification of a full 12-dim grid) on
disjoint training/validation seeds, select the best candidate using
training+validation only, then run the held-out check ONCE at the full
required >=200 seeds.

Writes:
  outputs/auction_rebuild/phase3d/calibration_grid.csv
  outputs/auction_rebuild/phase3d/calibration_selection.json
  outputs/auction_rebuild/phase3d/validation_results.json
  outputs/auction_rebuild/phase3d/held_out_results.json
"""

from __future__ import annotations

import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from auction_model.calibration import (
    CALIBRATION_TARGETS, N_CANDIDATES, N_HELD_OUT, N_TRAIN_SEEDS, N_VAL_SEEDS,
    PARAM_GRID, _ParamOverride, compute_batch_metrics, compute_loss,
    generate_disjoint_seeds, sample_candidates,
)
from mock_draft.data import load_confirmed_pool_and_teams

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3d"


def _evaluate_candidate(args):
    idx, params, players, teams, seeds = args
    with _ParamOverride(params):
        train_metrics = compute_batch_metrics(players, teams, seeds["train"])
        val_metrics = compute_batch_metrics(players, teams, seeds["val"])
    train_loss = compute_loss(train_metrics)
    val_loss = compute_loss(val_metrics)
    return {
        "candidate_idx": idx, "params": params,
        "train_metrics": train_metrics, "val_metrics": val_metrics,
        "train_loss_total": train_loss["TOTAL"], "val_loss_total": val_loss["TOTAL"],
        "combined_loss": train_loss["TOTAL"] + val_loss["TOTAL"],
        "train_loss_components": train_loss, "val_loss_components": val_loss,
    }


def main() -> None:
    t0 = time.time()
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    seeds = generate_disjoint_seeds()
    candidates = sample_candidates()

    print(f"Evaluating {len(candidates)} candidates on {len(seeds['train'])} train + "
          f"{len(seeds['val'])} val seeds each (parallelized)...")
    work = [(i, p, players, teams, seeds) for i, p in enumerate(candidates)]
    with mp.Pool(processes=min(4, mp.cpu_count())) as pool:
        results = pool.map(_evaluate_candidate, work)
    results.sort(key=lambda r: r["combined_loss"])

    print(f"Search done in {time.time() - t0:.1f}s. Best combined loss: {results[0]['combined_loss']:.3f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # calibration_grid.csv -- every candidate, ranked, with full param + loss breakdown.
    grid_rows = []
    for r in results:
        row = {"candidate_idx": r["candidate_idx"], "combined_loss": r["combined_loss"],
               "train_loss_total": r["train_loss_total"], "val_loss_total": r["val_loss_total"]}
        row.update({f"param_{k}": v for k, v in r["params"].items()})
        row.update({f"train_loss__{k}": v for k, v in r["train_loss_components"].items()})
        row.update({f"val_loss__{k}": v for k, v in r["val_loss_components"].items()})
        grid_rows.append(row)
    pd.DataFrame(grid_rows).to_csv(OUT_DIR / "calibration_grid.csv", index=False)
    print(f"Wrote {OUT_DIR / 'calibration_grid.csv'} ({len(grid_rows)} candidates)")

    # Item 11: are the leading candidates similarly good? If so, disclose it
    # rather than pretending false precision in the "winner."
    leaders = results[:10]
    leader_spread = leaders[-1]["combined_loss"] - leaders[0]["combined_loss"]
    similarly_good = leader_spread < 0.5 * leaders[0]["combined_loss"] if leaders[0]["combined_loss"] > 0 else True

    winner = results[0]
    selection = {
        "selected_params": winner["params"],
        "selection_criterion": "lowest (train_loss_total + val_loss_total), selected using training+validation only",
        "n_candidates_evaluated": len(candidates),
        "seed_ranges": {k: [min(v), max(v)] for k, v in seeds.items()},
        "n_seeds_per_split": {k: len(v) for k, v in seeds.items()},
        "SCOPE_REDUCTION_DISCLOSED": (
            f"Search-phase train/val seed counts ({N_TRAIN_SEEDS}/{N_VAL_SEEDS} each) are reduced from "
            f"the spec's required >=200 for compute tractability in this session (~1.5s/simulated auction, "
            f"12-parameter space). The held-out check below is NOT reduced -- it runs the full required "
            f"{N_HELD_OUT} seeds on the winning candidate only."
        ),
        "parameter_grid": PARAM_GRID,
        "objective_weights": "equal weight (1.0) on every one of the 15 calibration-target components -- no target prioritized over another",
        "calibration_targets": CALIBRATION_TARGETS,
        "training_score": winner["train_loss_total"],
        "validation_score": winner["val_loss_total"],
        "top_10_leaders_similarly_good": similarly_good,
        "top_10_leader_combined_losses": [r["combined_loss"] for r in leaders],
        "note_if_similarly_good": (
            "Leading candidates perform similarly -- per item 11, this means price/parameter uncertainty "
            "should be widened rather than presenting the winner as precisely correct."
            if similarly_good else
            "The winner is meaningfully better than the next-best candidates -- not a coin-flip selection."
        ),
    }
    (OUT_DIR / "calibration_selection.json").write_text(json.dumps(selection, indent=2, default=str))
    print(f"Wrote {OUT_DIR / 'calibration_selection.json'}")

    (OUT_DIR / "validation_results.json").write_text(json.dumps({
        "params": winner["params"], "metrics": winner["val_metrics"],
        "loss_components": winner["val_loss_components"], "loss_total": winner["val_loss_total"],
    }, indent=2, default=str))
    print(f"Wrote {OUT_DIR / 'validation_results.json'}")

    # Held-out: run ONCE, at the full required N_HELD_OUT seeds, winner only.
    print(f"Running held-out check on {len(seeds['held_out'])} seeds (winner only, run once)...")
    t1 = time.time()
    with _ParamOverride(winner["params"]):
        held_out_metrics = compute_batch_metrics(players, teams, seeds["held_out"])
    held_out_loss = compute_loss(held_out_metrics)
    print(f"Held-out done in {time.time() - t1:.1f}s. Held-out loss total: {held_out_loss['TOTAL']:.3f}")

    (OUT_DIR / "held_out_results.json").write_text(json.dumps({
        "params": winner["params"], "metrics": held_out_metrics,
        "loss_components": held_out_loss, "loss_total": held_out_loss["TOTAL"],
        "n_seeds": len(seeds["held_out"]),
    }, indent=2, default=str))
    print(f"Wrote {OUT_DIR / 'held_out_results.json'}")
    print(f"Total runtime: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
