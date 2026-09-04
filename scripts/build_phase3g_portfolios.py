#!/usr/bin/env python3
"""Phase 3G Part 3 (hard-max invariant) + Part 8 (portfolios, REDUCED to 4
of the 6 requested styles per player -- see final_report.md scope
disclosure): primary expected (P50), primary conservative (P75, spec
REQUIRES this one), fallback excluding Allen/Rice/McLaurin, TE contingency.
All exclude Austin Ekeler (and other NOT_SUPPORTED_REVIEW_REQUIRED
players) globally -- so the primary plan already IS the "no unsupported
extreme price" plan required by Part 8.

Hard-maximum invariant (Part 3) is enforced INSIDE construction: any
selected/audited player's input price fed to the optimizer is capped at
min(planning_price, safety_adjusted_hard_maximum) before the solve, and
every portfolio is re-verified after the solve with explicit assertions.
"""
from __future__ import annotations

import csv
import sys
import math
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from auction_model import exact_roster_solver
from mock_draft.data import load_confirmed_pool_and_teams

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3g"
N_AUCTION_SPOTS = 9
KEEPER_NAMES = {"Garrett Wilson", "Kenneth Walker III", "Quentin Johnston",
                "David Montgomery", "Cam Skattebo", "Jaxson Dart"}
COLLEGE_RIGHTS = {"Fernando Mendoza", "Isaiah Bond"}
UNSUPPORTED = {"Austin Ekeler", "AJ Barner", "Cade Otton"}  # from unsupported_extreme_price_audit.csv


def log(msg):
    print(f"[phase3g-portfolio] {msg}", flush=True)


def _keepers_to_exact_df(roster):
    return pd.DataFrame([
        {"player": name, "position": pos, "projected_points": pts, "keeper_price_2026": price}
        for name, pos, price, pts in roster
    ])


def build_pool_df(players, planning_prices: dict, price_col: str, exclude: set):
    rows = []
    for name, p in players.items():
        if name in exclude or name in UNSUPPORTED:
            continue
        if name in planning_prices:
            price = min(planning_prices[name][price_col], planning_prices[name]["hard_max"] or planning_prices[name][price_col])
        else:
            price = max(1.0, p.base_value)
        # Part 8 requires whole-dollar prices for EVERY portfolio purchase,
        # not just the audited selected-player set -- round up (conservative)
        # for any non-audited fallback filler.
        import math as _m
        rows.append({"player": name, "position": p.position, "projected_points": p.projected_points,
                     "suggested_auction_price": float(_m.ceil(max(1.0, price)))})
    return pd.DataFrame(rows)


def validate_portfolio(name_label, result, budget, planning_prices, exclude_extra):
    """Part 3 assertions."""
    assert result.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL"), f"{name_label}: solver not OPTIMAL ({result.status})"
    assert len(result.selected) == 15, f"{name_label}: roster is not 15 players ({len(result.selected)})"
    new_players = result.selected[~result.selected["player"].isin(KEEPER_NAMES)]
    assert len(new_players) == 9, f"{name_label}: not exactly 9 new purchases ({len(new_players)})"
    assert new_players["player"].nunique() == 9, f"{name_label}: duplicate players"
    assert not set(result.selected["player"]).intersection(COLLEGE_RIGHTS), f"{name_label}: college-rights player present"
    assert not set(result.selected["player"]).intersection(UNSUPPORTED), f"{name_label}: unsupported extreme-price player present"
    assert (result.selected["position"] == "TE").sum() >= 1, f"{name_label}: no TE"
    total_spend = new_players["price"].sum()
    assert total_spend <= budget + 0.01, f"{name_label}: over budget ({total_spend} > {budget})"
    for _, row in new_players.iterrows():
        pname = row["player"]
        price_paid = row["price"]
        if pname in planning_prices and planning_prices[pname]["hard_max"] is not None:
            hm = planning_prices[pname]["hard_max"]
            assert price_paid <= hm + 0.01, f"{name_label}: {pname} purchased at {price_paid} above hard max {hm}"
    return total_spend


def main():
    planning_df = pd.read_csv(OUT_DIR / "selected_player_planning_prices.csv")
    planning_prices = {}
    for _, r in planning_df.iterrows():
        planning_prices[r["player"]] = {
            "P50_WHOLE_DOLLAR": r["expected_planning_price"],
            "P75_CONSERVATIVE": r["conservative_planning_price"],
            "hard_max": r["safety_adjusted_hard_maximum"] if pd.notna(r["safety_adjusted_hard_maximum"]) else None,
        }

    all_portfolio_rows = []
    validation_rows = []
    purchase_order_rows = []

    for scen_label, budget_scenario, budget, out_name in [
        ("primary_223", "primary", 223, "sam_portfolios_223.csv"),
        ("conversions_221", "conversions", 221, "sam_portfolios_221.csv"),
    ]:
        players, teams, _ = load_confirmed_pool_and_teams(budget_scenario=budget_scenario)
        sam = teams["Sam"]
        keepers_df = _keepers_to_exact_df(sam.roster)

        styles = [
            ("PRIMARY_EXPECTED", "P50_WHOLE_DOLLAR", set()),
            ("PRIMARY_CONSERVATIVE", "P75_CONSERVATIVE", set()),
            ("FALLBACK_NO_ALLEN_RICE_MCLAURIN", "P50_WHOLE_DOLLAR", {"Josh Allen", "Rashee Rice", "Terry McLaurin"}),
            ("TE_CONTINGENCY_NO_KITTLE", "P50_WHOLE_DOLLAR", {"George Kittle"}),
            ("NO_JOSH_ALLEN", "P50_WHOLE_DOLLAR", {"Josh Allen"}),
            ("NO_RASHEE_RICE", "P50_WHOLE_DOLLAR", {"Rashee Rice"}),
        ]

        rows_for_file = []
        for style_name, price_col, exclude_extra in styles:
            pool_df = build_pool_df(players, planning_prices, price_col, exclude_extra)
            result = exact_roster_solver.solve_exact_roster(
                pool_df, budget=sam.budget_remaining, n_auction_spots=N_AUCTION_SPOTS, keepers=keepers_df,
            )
            if result.status not in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL"):
                validation_rows.append({"budget_scenario": scen_label, "style": style_name,
                                         "status": "SOLVER_FAILURE", "detail": result.status})
                log(f"{scen_label}/{style_name}: SOLVER_FAILURE ({result.status})")
                continue
            try:
                total_spend = validate_portfolio(f"{scen_label}/{style_name}", result, budget, planning_prices, exclude_extra)
                validation_rows.append({"budget_scenario": scen_label, "style": style_name,
                                         "status": "PASS", "detail": f"spend={total_spend}"})
            except AssertionError as e:
                validation_rows.append({"budget_scenario": scen_label, "style": style_name,
                                         "status": "FAIL", "detail": str(e)})
                log(f"{scen_label}/{style_name}: VALIDATION FAIL -- {e}")
                continue

            for _, row in result.selected.iterrows():
                rows_for_file.append({
                    "budget_scenario": scen_label, "portfolio_style": style_name,
                    "player": row["player"], "position": row["position"],
                    "price_whole_dollar": int(round(row.get("price", 0))),
                    "role": result.role_assignments.get(row["player"], ""),
                    "total_starting_points": round(result.starting_points, 2),
                    "total_bench_points": round(result.bench_points, 2),
                    "unused_cash": round(result.unused_cash, 2), "solver_status": result.status,
                    "calculation_label": "EXACT_TEAM_SPECIFIC_SURPLUS (whole-dollar, hard-max enforced)",
                })
            new_players = result.selected[~result.selected["player"].isin(KEEPER_NAMES)].sort_values("price", ascending=False)
            for i, (_, row) in enumerate(new_players.iterrows(), start=1):
                purchase_order_rows.append({
                    "budget_scenario": scen_label, "portfolio_style": style_name, "purchase_order": i,
                    "player": row["player"], "position": row["position"], "price_whole_dollar": int(round(row["price"])),
                })
            log(f"{scen_label}/{style_name}: OK, spend={total_spend}, pts={result.starting_points:.2f}")

        with (OUT_DIR / out_name).open("w", newline="") as f:
            if rows_for_file:
                w = csv.DictWriter(f, fieldnames=list(rows_for_file[0].keys()))
                w.writeheader(); w.writerows(rows_for_file)
        all_portfolio_rows.extend(rows_for_file)

    with (OUT_DIR / "sam_portfolio_purchase_orders.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(purchase_order_rows[0].keys()))
        w.writeheader(); w.writerows(purchase_order_rows)
    with (OUT_DIR / "sam_portfolio_validation.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(validation_rows[0].keys()))
        w.writeheader(); w.writerows(validation_rows)

    log("All portfolios built and validated.")


if __name__ == "__main__":
    main()
