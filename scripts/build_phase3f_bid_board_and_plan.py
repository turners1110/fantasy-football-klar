#!/usr/bin/env python3
"""Phase 3F post-processing: safety margins, tiers, nomination/avoid lists,
the master bid board, purchase sequences/sensitivity disclosure, and the
two written roster plans + contingency plan.

Pure post-processing over already-computed Phase 3F CSVs (no new exact
solves) -- run AFTER build_phase3f_sam_auction_plan.py has produced
sam_exact_bid_ceilings_223.csv / sam_exact_bid_ceilings_221.csv /
sam_complete_portfolios_223.csv / sam_complete_portfolios_221.csv /
sam_four_target_scenario_audit.csv.
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3f"
LABEL_AUDIT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3b" / "sam_label_audit.csv"
FOUR_TARGETS = ["Josh Allen", "Rashee Rice", "Terry McLaurin", "George Kittle"]


def safety_deduction_pct(confidence: int) -> float | None:
    if confidence >= 9:
        return 0.05
    if confidence >= 7:
        return 0.10
    if confidence >= 5:
        return 0.15
    return None  # INSUFFICIENT_EVIDENCE


def confidence_for(row, watchlist_index) -> tuple[int, list[str]]:
    conf = 10
    deductions = []
    is_provisional = row["player"] in watchlist_index
    if not is_provisional:
        conf -= 3
        deductions.append("no simulated market price -- PRELIMINARY_NOT_FINAL projection/anchor value used instead (-3)")
    else:
        conf -= 1
        deductions.append("expected price is a single provisional simulated percentile from an uncalibrated model, not a validated market price (-1, per Part 11: expected-price confidence stays below 9 while uncalibrated)")
    ceiling = row.get("exact_ceiling_whole_dollar")
    if ceiling is None or (isinstance(ceiling, float) and math.isnan(ceiling)):
        conf = 1
        deductions.append("SOLVER_FAILURE -- no exact ceiling computed (confidence floored to 1)")
    elif ceiling == 0:
        conf -= 1
        deductions.append("zero-dollar ceiling -- not actionable at any real price (-1)")
    if row.get("monotonic") is False:
        conf -= 2
        deductions.append("ceiling monotonicity check failed -- flagged for manual review (-2)")
    conf = max(1, min(10, conf))
    return conf, deductions


def action_for(surplus, ceiling, price, confidence) -> str:
    if ceiling is None or (isinstance(ceiling, float) and math.isnan(ceiling)):
        return "INSUFFICIENT_EVIDENCE"
    if confidence < 5:
        return "INSUFFICIENT_EVIDENCE"
    if ceiling == 0:
        return "AVOID_AT_EXPECTED_PRICE"
    if surplus is not None and surplus > 15:
        return "PRIORITY_TARGET"
    if surplus is not None and surplus > 0:
        return "TARGET_AT_DISCOUNT"
    if surplus is not None and surplus == 0:
        return "ONE_DOLLAR_FLIER" if price <= 1.5 else "FAIR_PRICE_ONLY"
    return "AVOID_AT_EXPECTED_PRICE"


def build_bid_board():
    watchlist = pd.read_csv(LABEL_AUDIT_PATH).set_index("player")
    ceil223 = pd.read_csv(OUT_DIR / "sam_exact_bid_ceilings_223.csv")
    ceil221 = pd.read_csv(OUT_DIR / "sam_exact_bid_ceilings_221.csv")
    ceil221_idx = ceil221.set_index("player")
    scenario = pd.read_csv(OUT_DIR / "sam_four_target_scenario_audit.csv")
    scenario_223 = scenario[scenario.budget_scenario == "primary_223"].set_index("Candidate")

    rows = []
    for _, r in ceil223.iterrows():
        player = r["player"]
        ceiling_223 = r["exact_ceiling_whole_dollar"]
        ceiling_221 = ceil221_idx.loc[player, "exact_ceiling_whole_dollar"] if player in ceil221_idx.index else None
        has_p50 = player in watchlist.index and pd.notna(watchlist.loc[player, "market_price_p50"])
        p50 = float(watchlist.loc[player, "market_price_p50"]) if has_p50 else None
        p50_source = "PROVISIONAL_SIMULATED_MARKET_PRICE" if has_p50 else "PRELIMINARY_NOT_FINAL"
        conf, deductions = confidence_for(r, watchlist.index)
        safety_pct = safety_deduction_pct(conf)
        exact_ceiling = ceiling_223 if not (isinstance(ceiling_223, float) and math.isnan(ceiling_223)) else None
        if safety_pct is not None and exact_ceiling is not None and exact_ceiling > 0:
            hard_max = math.floor(exact_ceiling * (1 - safety_pct))
        else:
            hard_max = None
        surplus = None
        target_price = None
        if player in scenario_223.index:
            surplus = scenario_223.loc[player, "Total surplus (starting points)"]
            target_price = scenario_223.loc[player, "Test price"]
        action = action_for(surplus, exact_ceiling, target_price if target_price is not None else (p50 or 1), conf)
        budget_sensitivity = None
        if exact_ceiling is not None and ceiling_221 is not None and not (isinstance(ceiling_221, float) and math.isnan(ceiling_221)):
            budget_sensitivity = round(exact_ceiling - ceiling_221, 1)
        rows.append({
            "Player": player, "Position": r["position"],
            "Provisional market P50": p50, "Provisional market P50 label": p50_source,
            "Exact ceiling under $223": exact_ceiling, "Exact ceiling under $221": ceiling_221,
            "Safety deduction pct": safety_pct, "Recommended hard maximum": hard_max,
            "Expected surplus at target": surplus,
            "Confidence 1-10": conf, "Confidence deductions": "; ".join(deductions),
            "Budget-scenario sensitivity ($223 ceiling minus $221 ceiling)": budget_sensitivity,
            "Calculation label": "EXACT_PRE_DRAFT_STATIC_POOL_CEILING (whole-dollar)",
            "Recommended action": action,
            "Reviewer note": ("One of Phase 3E/3F's four audited positive-surplus targets" if player in FOUR_TARGETS
                               else "Ceiling-only candidate; no full purchase/pass scenario audit run this pass"),
        })

    board = pd.DataFrame(rows).sort_values(
        by=["Recommended action", "Expected surplus at target", "Confidence 1-10"],
        ascending=[True, False, False],
    )
    board.to_csv(OUT_DIR / "sam_auction_bid_board.csv", index=False)
    print(f"Wrote sam_auction_bid_board.csv ({len(board)} rows)")
    return board


def build_tiers_nomination_avoid(board: pd.DataFrame):
    tiers = []
    for _, r in board.iterrows():
        pos = r["Position"]
        role = {"TE": "TE1 target", "QB": "QB upgrade target"}.get(pos, "WR/FLEX or RB value target")
        tiers.append({
            "player": r["Player"], "position": pos, "expected_role": role,
            "provisional_p50": r["Provisional market P50"], "exact_ceiling": r["Exact ceiling under $223"],
            "safety_adjusted_hard_maximum": r["Recommended hard maximum"], "confidence": r["Confidence 1-10"],
            "recommended_action": r["Recommended action"],
            "nomination_recommendation": (
                "NOMINATE_TO_DRAIN (fair/no-fit for Sam, priced to force early competitor spend)"
                if r["Recommended action"] in ("AVOID_AT_EXPECTED_PRICE",) and (r["Exact ceiling under $223"] or 0) > 20
                else "TARGET -- nominate late once other bidders are budget-constrained"
                if r["Recommended action"] in ("PRIORITY_TARGET", "TARGET_AT_DISCOUNT")
                else "LOW_PRIORITY"
            ),
        })
    pd.DataFrame(tiers).to_csv(OUT_DIR / "sam_target_tiers.csv", index=False)

    nom = board[board["Recommended action"].isin(["AVOID_AT_EXPECTED_PRICE"]) & (board["Exact ceiling under $223"].fillna(0) > 20)]
    nom[["Player", "Position", "Exact ceiling under $223", "Provisional market P50"]].to_csv(OUT_DIR / "sam_nomination_plan.csv", index=False)

    avoid = board[board["Recommended action"] == "AVOID_AT_EXPECTED_PRICE"]
    avoid[["Player", "Position", "Provisional market P50", "Exact ceiling under $223", "Confidence deductions"]].to_csv(
        OUT_DIR / "sam_avoid_list.csv", index=False)
    print(f"Wrote sam_target_tiers.csv ({len(tiers)}), sam_nomination_plan.csv ({len(nom)}), sam_avoid_list.csv ({len(avoid)})")


def build_purchase_sequences_and_sensitivity():
    rows = []
    sens_rows = []
    for scen_label, out_name in [("primary_223", "sam_complete_portfolios_223.csv"), ("conversions_221", "sam_complete_portfolios_221.csv")]:
        path = OUT_DIR / out_name
        if not path.exists():
            continue
        df = pd.read_csv(path)
        p50 = df[(df.budget_scenario == scen_label) & (df.price_scenario == "P50_WHOLE_DOLLAR")]
        p50_new = p50[~p50["player"].isin(["Garrett Wilson", "Kenneth Walker III", "Quentin Johnston", "David Montgomery", "Cam Skattebo", "Jaxson Dart"])]
        p50_new = p50_new.sort_values("price_paid_whole_dollar", ascending=False)
        for i, (_, r) in enumerate(p50_new.iterrows(), start=1):
            rows.append({
                "budget_scenario": scen_label, "purchase_order": i, "player": r["player"],
                "position": r["position"], "price_whole_dollar": r["price_paid_whole_dollar"],
                "rationale": "highest-price targets nominated/won first while budget flexibility is greatest; "
                              "cheap bench/flier slots filled last, consistent with reserving $1/remaining-slot throughout",
            })
        p75 = df[(df.budget_scenario == scen_label) & (df.price_scenario == "P75_CONSERVATIVE_WHOLE_DOLLAR_HEURISTIC")]
        sens_rows.append({
            "budget_scenario": scen_label,
            "P50_total_starting_points": p50["total_starting_points"].iloc[0] if not p50.empty else None,
            "P75_total_starting_points": p75["total_starting_points"].iloc[0] if not p75.empty else None,
            "P75_note": "P75 uses a disclosed 1.15x heuristic markup over the P50/PRELIMINARY price, NOT a calibrated 75th percentile -- see sam_portfolio_sensitivity.csv",
        })
    pd.DataFrame(rows).to_csv(OUT_DIR / "sam_portfolio_purchase_sequences.csv", index=False)
    with (OUT_DIR / "sam_portfolio_sensitivity.csv").open("w", newline="") as f:
        if sens_rows:
            w = csv.DictWriter(f, fieldnames=list(sens_rows[0].keys()))
            w.writeheader(); w.writerows(sens_rows)
        f.write("\n")
    with (OUT_DIR / "sam_portfolio_sensitivity.csv").open("a") as f:
        f.write("status,detail\n")
        f.write("PARTIAL,\"Only P50 (whole-dollar) and P75 (1.15x heuristic markup, NOT calibrated) price scenarios were built this pass. "
                "P25 and P90 stress portfolios, and the 13 additional portfolio styles listed in spec Part 6 (balanced, premium-WR, TE-first, "
                "no-player-above-$60/$40, Josh-Allen/no-Allen/Rice/no-Rice/McLaurin plans, fallback-excluding-all-three, two-TE, late-value) "
                "were not built this pass -- time budget went to the P50/P75 core plans plus the whole-dollar ceiling sweep per the spec's own "
                "priority order (P50/P75 ranked 4th, fallback portfolios 5th, wider portfolio styles explicitly LAST/8th).\"\n")
    print(f"Wrote sam_portfolio_purchase_sequences.csv ({len(rows)} rows) and sam_portfolio_sensitivity.csv")


def build_written_plans():
    for scen_label, budget, out_name in [("primary_223", 223, "recommended_223_plan.txt"), ("conversions_221", 221, "recommended_221_plan.txt")]:
        pf_path = OUT_DIR / f"sam_complete_portfolios_{'223' if budget==223 else '221'}.csv"
        lines = [f"RECOMMENDED ${budget} ROSTER PLAN (P50 whole-dollar scenario)\n", "=" * 50 + "\n\n"]
        if pf_path.exists():
            df = pd.read_csv(pf_path)
            p50 = df[(df.budget_scenario == scen_label) & (df.price_scenario == "P50_WHOLE_DOLLAR")]
            if not p50.empty and p50["solver_status"].iloc[0] not in (None,):
                total_pts = p50["total_starting_points"].iloc[0]
                spend = p50[~p50["player"].isin(["Garrett Wilson", "Kenneth Walker III", "Quentin Johnston", "David Montgomery", "Cam Skattebo", "Jaxson Dart"])]["price_whole_dollar" if "price_whole_dollar" in p50.columns else "price_paid_whole_dollar"].sum() if "price_paid_whole_dollar" in p50.columns else None
                lines.append(f"Full 15-player roster (exact-solver optimum, whole-dollar prices):\n")
                for _, r in p50.iterrows():
                    price_col = "price_paid_whole_dollar" if "price_paid_whole_dollar" in p50.columns else None
                    price = r[price_col] if price_col else "keeper"
                    lines.append(f"  {r['player']:22s} {r['position']:3s} role={r['role']:12s} price=${price}\n")
                lines.append(f"\nProjected starting points: {total_pts}\n")
                lines.append("\nWHY EACH NEW PLAYER: see sam_auction_bid_board.csv 'Reviewer note' and sam_four_target_scenario_audit.csv "
                              "for the four audited targets (Josh Allen, Rashee Rice, Terry McLaurin, George Kittle); other roster fills "
                              "come from the whole-pool exact optimizer's own choice at PRELIMINARY_NOT_FINAL prices and were not "
                              "individually re-audited this pass -- treat any player NOT in the four-target audit or the ceiling sweep as "
                              "provisional roster filler, not a vetted recommendation.\n")
                lines.append("\nRISK ACCEPTED: this plan spends close to the full budget on PRELIMINARY_NOT_FINAL (uncalibrated) prices for "
                              "most non-watchlist players -- real live prices for those players could be materially higher, which would break "
                              "this exact roster. Re-run scripts/build_phase3f_sam_auction_plan.py's portfolio step with updated real prices "
                              "as they become available during the actual draft, or fall back to the tiered plan in sam_target_tiers.csv.\n")
            else:
                lines.append("SOLVER_FAILURE or no P50 portfolio available -- see sam_complete_portfolios file directly.\n")
        else:
            lines.append("Portfolio file not found -- ceiling/portfolio computation may not have completed this pass.\n")
        with (OUT_DIR / out_name).open("w") as f:
            f.writelines(lines)

    contingency = [
        "AUCTION CONTINGENCY PLAN\n", "=" * 30 + "\n\n",
        "If Josh Allen costs $10 more than the $21.95 provisional price (i.e. ~$32): still a real, if smaller, "
        "surplus per josh_allen_exact_price_ladder.csv (surplus ~+15-23 in that price band) -- still pursue, but "
        "do not exceed the recommended hard maximum in sam_auction_bid_board.csv.\n\n",
        "If Rashee Rice costs $10 more (~$64): approaching but likely still under his $71 exact ceiling -- check "
        "sam_exact_bid_ceilings_223.csv before bidding further; do not exceed the safety-adjusted hard maximum.\n\n",
        "If Terry McLaurin costs $10 more (~$46): close to or above his $50 exact ceiling -- treat as a hard stop "
        "candidate; re-verify against sam_exact_bid_ceilings_223.csv before bidding past $46.\n\n",
        "If the first TE tier disappears (Kittle/Andrews both gone): George Kittle's own surplus was already "
        "near-zero (+0.18) at his provisional price -- losing the tier is a low-cost event; fall back to the "
        "cheapest legal-eligible TE and treat the TE slot as a value/late-round fill per sam_target_tiers.csv.\n\n",
        "If Sam spends $20 too much early: her live remaining-budget reserve rule ($1 x remaining slots) still "
        "applies -- immediately re-check sam_auction_bid_board.csv's cheaper ONE_DOLLAR_FLIER / FAIR_PRICE_ONLY "
        "tiers for the remaining slots rather than chasing further PRIORITY_TARGETs.\n\n",
        "If Sam saves $20 early: she gains headroom to pursue TARGET_AT_DISCOUNT candidates above their listed "
        "target price, up to (but not exceeding) their exact ceiling and hard maximum.\n\n",
        "GENERAL: every number in this plan that is not sourced from a real market_price_p50 (see "
        "sam_auction_bid_board.csv's price-label column) is PRELIMINARY_NOT_FINAL. Treat the $223/$221 plans as a "
        "structured starting point, not a locked script -- always re-check the exact ceiling before exceeding a "
        "recommended target price.\n",
    ]
    with (OUT_DIR / "auction_contingency_plan.txt").open("w") as f:
        f.writelines(contingency)
    print("Wrote recommended_223_plan.txt, recommended_221_plan.txt, auction_contingency_plan.txt")


def main():
    board = build_bid_board()
    build_tiers_nomination_avoid(board)
    build_purchase_sequences_and_sensitivity()
    build_written_plans()


if __name__ == "__main__":
    main()
