#!/usr/bin/env python3
"""Phase 3G Part 9 (shock tests, REDUCED to the primary $223 PRIMARY_EXPECTED
portfolio only -- see final_report.md scope disclosure) + Part 10 (final
auction sheet, built from the selected-player audit set, not the full pool)."""
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
UNSUPPORTED = {"Austin Ekeler", "AJ Barner", "Cade Otton"}


def _keepers_to_exact_df(roster):
    return pd.DataFrame([
        {"player": name, "position": pos, "projected_points": pts, "keeper_price_2026": price}
        for name, pos, price, pts in roster
    ])


def build_pool_df(players, planning_prices, exclude, price_bump=None):
    rows = []
    for name, p in players.items():
        if name in exclude or name in UNSUPPORTED:
            continue
        if name in planning_prices:
            price = min(planning_prices[name]["P50_WHOLE_DOLLAR"], planning_prices[name]["hard_max"] or planning_prices[name]["P50_WHOLE_DOLLAR"])
        else:
            price = math.ceil(max(1.0, p.base_value))
        if price_bump and name in price_bump:
            price += price_bump[name]
        rows.append({"player": name, "position": p.position, "projected_points": p.projected_points,
                     "suggested_auction_price": float(max(1.0, price))})
    return pd.DataFrame(rows)


def main():
    planning_df = pd.read_csv(OUT_DIR / "selected_player_planning_prices.csv")
    planning_prices = {r["player"]: {"P50_WHOLE_DOLLAR": r["expected_planning_price"],
                                      "hard_max": r["safety_adjusted_hard_maximum"] if pd.notna(r["safety_adjusted_hard_maximum"]) else None}
                        for _, r in planning_df.iterrows()}

    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    sam = teams["Sam"]
    keepers_df = _keepers_to_exact_df(sam.roster)

    def solve(exclude=set(), bump=None):
        pool_df = build_pool_df(players, planning_prices, exclude, bump)
        return exact_roster_solver.solve_exact_roster(pool_df, budget=sam.budget_remaining, n_auction_spots=N_AUCTION_SPOTS, keepers=keepers_df)

    baseline = solve()
    shocks = [
        ("Josh Allen unavailable", {"Josh Allen"}, None),
        ("Rashee Rice unavailable", {"Rashee Rice"}, None),
        ("Terry McLaurin unavailable", {"Terry McLaurin"}, None),
        ("Allen+Rice+McLaurin all unavailable", {"Josh Allen", "Rashee Rice", "Terry McLaurin"}, None),
        ("Primary TE (George Kittle) unavailable", {"George Kittle"}, None),
        ("Allen costs $10 more", set(), {"Josh Allen": 10}),
        ("Rice costs $10 more", set(), {"Rashee Rice": 10}),
        ("McLaurin costs $10 more", set(), {"Terry McLaurin": 10}),
        ("Primary TE costs $10 more", set(), {"George Kittle": 10}),
    ]
    rows = []
    for label, exclude, bump in shocks:
        result = solve(exclude, bump)
        feasible = result.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL")
        new_players = set(result.selected["player"]) - set(n for n, *_ in sam.roster) if feasible else set()
        base_new = set(baseline.selected["player"]) - set(n for n, *_ in sam.roster)
        changed = sorted(new_players - base_new)
        rows.append({
            "shock": label, "feasible": feasible,
            "new_purchases_vs_baseline": ";".join(changed),
            "new_total_starting_points": round(result.starting_points, 2) if feasible else None,
            "starting_point_change_vs_baseline": round(result.starting_points - baseline.starting_points, 2) if feasible else None,
            "unused_cash": round(result.unused_cash, 2) if feasible else None,
            "solver_status": result.status,
        })
    # Sam overspends/saves $15 on first purchase (Josh Allen), and P75/P90 full-roster shocks
    for label, price_col_bump in [("Sam overspends $15 on first purchase (Allen)", {"Josh Allen": 15}),
                                   ("Sam saves $15 on first purchase (Allen)", {"Josh Allen": -15})]:
        result = solve(set(), price_col_bump)
        feasible = result.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL")
        rows.append({"shock": label, "feasible": feasible,
                     "new_purchases_vs_baseline": "", "new_total_starting_points": round(result.starting_points, 2) if feasible else None,
                     "starting_point_change_vs_baseline": round(result.starting_points - baseline.starting_points, 2) if feasible else None,
                     "unused_cash": round(result.unused_cash, 2) if feasible else None, "solver_status": result.status})

    # P75/P90 full-roster shock (using conservative price col already computed; P90 = ceil(conservative*1.2) already = "stress_planning_price")
    pool_p75 = []
    pool_p90 = []
    for name, p in players.items():
        if name in UNSUPPORTED:
            continue
        if name in planning_prices:
            row = planning_df[planning_df.player == name].iloc[0]
            p75 = min(row["conservative_planning_price"], row["safety_adjusted_hard_maximum"]) if pd.notna(row["safety_adjusted_hard_maximum"]) else row["conservative_planning_price"]
            p90 = row["stress_planning_price"]
        else:
            p75 = math.ceil(max(1.0, p.base_value) * 1.15)
            p90 = math.ceil(max(1.0, p.base_value) * 1.35)
        pool_p75.append({"player": name, "position": p.position, "projected_points": p.projected_points, "suggested_auction_price": float(p75)})
        pool_p90.append({"player": name, "position": p.position, "projected_points": p.projected_points, "suggested_auction_price": float(p90)})
    for label, pool_rows in [("P75 prices across full roster", pool_p75), ("P90 prices across full roster", pool_p90)]:
        result = exact_roster_solver.solve_exact_roster(pd.DataFrame(pool_rows), budget=sam.budget_remaining, n_auction_spots=N_AUCTION_SPOTS, keepers=keepers_df)
        feasible = result.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL")
        rows.append({"shock": label, "feasible": feasible, "new_purchases_vs_baseline": "",
                     "new_total_starting_points": round(result.starting_points, 2) if feasible else None,
                     "starting_point_change_vs_baseline": round(result.starting_points - baseline.starting_points, 2) if feasible else None,
                     "unused_cash": round(result.unused_cash, 2) if feasible else None, "solver_status": result.status})

    with (OUT_DIR / "sam_portfolio_shock_tests.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"wrote {len(rows)} shock rows")
    for r in rows:
        print(r["shock"], "-> feasible=", r["feasible"], "pts_change=", r["starting_point_change_vs_baseline"])

    # ---- Part 10: final auction sheet (selected-player set) ----
    watchlist = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "phase3b" / "sam_label_audit.csv").set_index("player")
    ceilings = pd.read_csv(OUT_DIR / "selected_player_exact_ceilings.csv").set_index("player")
    sheet_rows = []
    for _, r in planning_df.iterrows():
        name = r["player"]
        p = players.get(name)
        ceil_row = ceilings.loc[name] if name in ceilings.index else None
        action = r["price_vs_hardmax_action"]
        if name in UNSUPPORTED:
            action = "INSUFFICIENT_EVIDENCE"
        elif action == "TARGET":
            if r["expected_planning_price"] <= 2:
                action = "ONE_DOLLAR_FLIER"
            elif name in ("Josh Allen", "Rashee Rice") and r["confidence"] >= 9:
                action = "PRIORITY_TARGET"
            else:
                action = "TARGET_AT_DISCOUNT"
        sheet_rows.append({
            "Player": name, "Position": p.position if p else None,
            "Projected points": p.projected_points if p else None,
            "Expected planning price": r["expected_planning_price"], "Expected price label": r["expected_price_label"],
            "Conservative planning price": r["conservative_planning_price"], "Stress planning price": r["stress_planning_price"],
            "Exact ceiling under $223": ceil_row["exact_ceiling_223"] if ceil_row is not None else None,
            "Exact ceiling under $221": ceil_row["exact_ceiling_221"] if ceil_row is not None else None,
            "Safety-adjusted hard maximum": r["safety_adjusted_hard_maximum"],
            "Recommended target price": r["expected_planning_price"] if action not in ("AVOID_AT_EXPECTED_PRICE", "AVOID_AT_CONSERVATIVE_PRICE", "INSUFFICIENT_EVIDENCE") else None,
            "Confidence": r["confidence"], "Extreme-price status": "NOT_SUPPORTED_REVIEW_REQUIRED" if name in UNSUPPORTED else "SUPPORTED",
            "Calculation labels": "EXACT_PRE_DRAFT_STATIC_POOL_CEILING;SAFETY_ADJUSTED_HARD_MAXIMUM;" + r["expected_price_label"],
            "Recommended action": action,
            "Notes": ("Excluded from every recommended portfolio -- see austin_ekeler_price_audit.txt" if name == "Austin Ekeler"
                      else "Cross-budget ceiling gap resolved defensively -- see terry_mclaurin_ceiling_explanation.txt" if name == "Terry McLaurin"
                      else ""),
        })
    pd.DataFrame(sheet_rows).to_csv(OUT_DIR / "sam_final_auction_sheet.csv", index=False)
    print(f"wrote final auction sheet ({len(sheet_rows)} rows)")


if __name__ == "__main__":
    main()
