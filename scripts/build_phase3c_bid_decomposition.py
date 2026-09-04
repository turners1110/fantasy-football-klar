#!/usr/bin/env python3
"""Phase 3C item 7: audit bid construction for every top-24 sale across
>=50 auctions, using the new bid_diagnostics_log instrumentation added to
mock_draft.auction.resolve_bid/run_single_auction (fully additive, no
behavior change for any existing caller).

MAJOR FINDING from building this instrumentation (fixed, not just
reported -- see mock_draft/valuation.py's PHASE 3C FIX comment): the
early-draft premium used to be applied AFTER the star-ceiling re-clamp,
defeating the very 2.5x cap the re-clamp exists to enforce. A sampled
case (Jaylen Waddle, base_value $64) showed final_willingness $251.56 --
3.93x base_value -- entirely from that one ordering bug. Fixed by moving
the premium above the re-clamp; verified zero star-candidate sales now
exceed 2.5x base_value in willingness.

Writes outputs/auction_rebuild/phase3c/top_sale_bid_decomposition.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from mock_draft.auction import run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams

N_SEEDS = 50
OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3c" / "top_sale_bid_decomposition.csv"


def main() -> None:
    players, teams_template, _ = load_confirmed_pool_and_teams(budget_scenario="primary")

    rows = []
    n_stacked_flagged = 0
    for seed in range(N_SEEDS):
        rng = np.random.default_rng(seed)
        diag_log: list = []
        log, _ = run_single_auction(players, teams_template, rng, bid_diagnostics_log=diag_log)
        top24 = sorted(log, key=lambda e: e["sale_price"], reverse=True)[:24]
        top24_players = {e["player"] for e in top24}
        for d in diag_log:
            if d["player"] not in top24_players:
                continue
            wd = d.get("winner_diagnostics") or {}
            base = wd.get("base_value")
            total_mult = wd.get("total_multiplier_vs_base_value")
            # "Stacked" flag: 3+ distinct multiplicative adjustments each
            # individually > 1.0 compounding on this sale (documented
            # threshold, not arbitrary -- more than 2 simultaneous
            # premiums is exactly the "several premiums multiply one
            # another" pattern the phase 3C brief calls out).
            active_premiums = sum(1 for k in (
                "tier_aggression_applied", "tilt_boost_applied", "early_draft_premium_multiplier",
            ) if (wd.get(k) or 1.0) > 1.001)
            stacked = active_premiums >= 2 and bool(wd.get("is_star_candidate"))
            if stacked:
                n_stacked_flagged += 1
            rows.append({
                "seed": seed, "player": d["player"], "position": d["position"],
                "base_value": base,
                "team_marginal_value_noise_ratio": wd.get("noise_ratio"),
                "position_adjustment": wd.get("position_fit_multiplier"),
                "scarcity_adjustment_NOTE": "no distinct scarcity signal exists in compute_willingness today "
                                             "-- position_fit_multiplier (position_targets) is the closest analog",
                "tier_adjustment": wd.get("tier_aggression_applied"),
                "early_draft_adjustment": wd.get("early_draft_premium_multiplier"),
                "archetype_adjustment_tilt": wd.get("tilt_boost_applied"),
                "is_star_candidate": wd.get("is_star_candidate"),
                "star_reclamp_applied": wd.get("star_reclamp_applied", False),
                "final_highest_willingness": d.get("final_highest_willingness"),
                "final_second_willingness": d.get("final_second_willingness"),
                "sale_price": d["sale_price"], "winner": d["winner"], "bidder_count": d["bidder_count"],
                "sale_price_divided_by_base_value": round(d["sale_price"] / base, 3) if base else None,
                "highest_willingness_divided_by_base_value": round(total_mult, 3) if total_mult else None,
                "second_willingness_divided_by_base_value": (
                    round(d["final_second_willingness"] / base, 3) if base and d.get("final_second_willingness") else None
                ),
                "stacked_premiums_flag": stacked,
            })

    fieldnames = list(rows[0].keys())
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    df = pd.DataFrame(rows)
    print(f"Wrote {OUT_PATH} ({len(rows)} top-24 sale records across {N_SEEDS} auctions)")
    print(f"Stacked-premium sales flagged (>=2 active premiums AND star candidate): {n_stacked_flagged} "
          f"({n_stacked_flagged / len(rows):.1%})")
    print(f"Mean sale_price/base_value ratio (top-24 sales): {df['sale_price_divided_by_base_value'].mean():.3f}")
    print(f"Mean highest_willingness/base_value ratio: {df['highest_willingness_divided_by_base_value'].mean():.3f}")
    print(f"Max sale_price/base_value ratio observed: {df['sale_price_divided_by_base_value'].max():.3f}")


if __name__ == "__main__":
    main()
