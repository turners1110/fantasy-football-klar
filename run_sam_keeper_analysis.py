#!/usr/bin/env python3
"""Sam-focused keeper analysis with eligibility filtering and exact roster optimizer."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from auction_model import (
    auction_eligibility,
    config,
    confidence,
    contracts,
    data_pipeline,
    keeper_market,
    keepers,
    market_engine,
    projections,
    roster_optimizer,
)
from run_valuation import merge_projections

BASE = Path(__file__).parent
DATA = BASE / "data"
OUT = BASE / "outputs"

AUDIT_PLAYERS = [
    "Garrett Wilson", "David Montgomery", "Tyjae Spears", "CJ Stroud",
    "Jaxson Dart", "Austin Ekeler", "Deebo Samuel",
]


def _load():
    salaries, log = data_pipeline.load_historical_salaries(DATA / "historical_salaries_2025_raw.csv")
    fp = data_pipeline.load_fantasypros_rankings(BASE / "FantasyPros_2026_Draft_ALL_Rankings.csv")
    projections_df = data_pipeline.load_optional_csv(DATA / "projections_2026.csv")
    blend = 0.6 if projections_df is not None else 0.0

    pool = data_pipeline.expand_pool_with_full_universe(salaries.copy(), fp)
    pool = data_pipeline.merge_fp_tiers(pool, fp)
    pool = merge_projections(pool, projections_df)
    pool, blocked = projections.apply_projection_fallbacks(
        pool, fp, str(DATA / "actuals_2025.csv"),
    )
    pool = data_pipeline.fill_anchor_fallback(pool)

    neutral_priced = market_engine.price_neutral_market(pool, blend)
    neutral_value = salaries["player"].map(data_pipeline._normalize_name).map(
        neutral_priced.set_index(neutral_priced["player"].map(data_pipeline._normalize_name))[
            "neutral_redraft_value"
        ]
    )

    nflverse_path = DATA / "nflverse" / "player_stats_reg_2025.csv"
    contract_table, dq = contracts.build_player_contracts(
        salaries, projections_df, fp, None, nflverse_path,
    )
    return salaries, pool, blend, neutral_value, contract_table, dq, log, fp, blocked


def _auction_pool(full_pool, roster, blend, eligibility_audit):
    _, priced = market_engine.price_depleted_market(
        full_pool, roster, blend, market_engine.SCENARIO_DEPLETED_EXPECTED,
    )
    live = priced[~priced["player"].isin(
        roster.loc[roster["will_keep"].astype(bool), "player"]
    )].copy()
    before = len(live)
    filtered = auction_eligibility.filter_veteran_auction_pool(live, eligibility_audit)
    return filtered, before, len(filtered)


def _score_confidence(row, market_converged, market_iters, solver_status, eligibility_ok):
    from auction_model.confidence import score_decision_v2
    return score_decision_v2(
        row, converged=market_converged, iterations=market_iters,
        solver_status=solver_status, eligibility_valid=eligibility_ok,
    )


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    roster_optimizer.clear_caches()

    salaries, full_pool, blend, neutral_value, contracts_df, dq, log, fp, blocked_proj = _load()

    # Always run keeper-market iteration (do not bypass with saved keepers file)
    print("Running keeper market iteration...")
    market = keeper_market.iterate_keeper_market(
        salaries, full_pool, neutral_value, blend, None, max_iterations=20,
    )
    roster = market.roster
    market_converged, market_iters = market.converged, market.iterations

    # Attach projections to roster rows for keeper exact solves
    proj_map = full_pool.drop_duplicates("player")[["player", "projected_points"]]
    roster = roster.merge(proj_map, on="player", how="left", suffixes=("", "_pool"))
    if "projected_points_pool" in roster.columns:
        roster["projected_points"] = roster["projected_points"].fillna(roster["projected_points_pool"])
        roster.drop(columns=["projected_points_pool"], inplace=True)

    eligibility_audit = auction_eligibility.build_eligibility_audit(
        full_pool, salaries, roster,
        holdings_path=DATA / "college_holdings.csv",
        nflverse_dir=DATA / "nflverse",
    )
    eligibility_audit.to_csv(OUT / "auction_eligibility_audit.csv", index=False)
    roster_optimizer.set_eligibility_audit(eligibility_audit)

    auction_pool, pool_before, pool_after = _auction_pool(full_pool, roster, blend, eligibility_audit)
    print(f"Auction pool: {pool_before} -> {pool_after} after eligibility filter")

    cf = market_engine.build_release_counterfactual_audit(
        roster, full_pool, neutral_value, blend, exact_teams={config.SAM_TEAM_NAME},
    )
    cf.to_csv(OUT / "player_release_counterfactuals.csv", index=False)
    if not cf.empty:
        roster = roster.merge(
            cf[["team", "player", "depleted_alpha_expected"]].rename(
                columns={"depleted_alpha_expected": "depleted_market_alpha"}
            ),
            on=["team", "player"], how="left",
        )

    print("Solving Sam portfolios 0-6 (exact)...")
    portfolios = roster_optimizer.solve_portfolios_0_to_6(
        config.SAM_TEAM_NAME, roster, auction_pool,
    )
    pf_df = roster_optimizer.portfolios_to_dataframe(portfolios)
    pf_df["auction_eligibility_valid"] = [p.auction_eligibility_valid for p in portfolios]
    pf_df["keeper_market_converged"] = market_converged
    pf_df.to_csv(OUT / "sam_keeper_portfolios_0_to_6.csv", index=False)

    # Greedy vs exact comparison
    greedy_rows = []
    for p in portfolios:
        greedy_rows.append({
            "keeper_count": p.keeper_count,
            "exact_keeper_set": ", ".join(p.keepers),
            "exact_roster": ", ".join(p.all_players),
            "greedy_starting_points": p.greedy_starting_points,
            "exact_starting_points": p.lineup.starting_points,
            "exact_bench_value": p.lineup.bench_points,
            "exact_spend": p.auction_spend + p.keeper_spend,
            "exact_unused_cash": p.unused_cash,
            "objective_difference": round(
                (p.lineup.starting_points or 0) - (p.greedy_starting_points or 0), 2
            ),
            "solver_status": p.solver_status,
        })
    pd.DataFrame(greedy_rows).to_csv(OUT / "greedy_vs_exact_portfolios.csv", index=False)

    # Portfolio rosters detail
    roster_rows = []
    for p in portfolios:
        audit_idx = eligibility_audit.set_index("canonical_player_id")
        for player in p.all_players:
            key = data_pipeline._normalize_name(player)
            elig = audit_idx.loc[key]["final_auction_status"] if key in audit_idx.index else "UNKNOWN"
            roster_rows.append({
                "keeper_count": p.keeper_count,
                "player": player,
                "position": "",
                "acquisition_type": "keeper" if player in p.keepers else "auction",
                "lineup_role": p.lineup.roles.get(player, ""),
                "eligibility_status": elig,
                "selected": True,
                "solver_status": p.solver_status,
            })
    pd.DataFrame(roster_rows).to_csv(OUT / "sam_keeper_portfolio_rosters.csv", index=False)

    valid_portfolios = [p for p in portfolios if p.solver_status == "OPTIMAL" and p.auction_eligibility_valid]
    best = max(valid_portfolios, key=lambda p: p.objective_value) if valid_portfolios else None

    sam_players = roster[roster["team"] == config.SAM_TEAM_NAME]["player"].tolist()
    comps = []
    deltas = []
    dominance = []
    print(f"Keep vs release for {len(sam_players)} players...")
    for player in sam_players:
        row = roster[(roster["team"] == config.SAM_TEAM_NAME) & (roster["player"] == player)]
        if row.empty or pd.isna(row.iloc[0].get("salary_2025")):
            continue
        comp = roster_optimizer.compare_keep_vs_release(
            config.SAM_TEAM_NAME, player, roster, auction_pool,
        )
        comps.append(comp)
        if "error" not in comp:
            deltas.append({
                "player": player,
                "keepers_if_kept": comp.get("keepers_if_kept"),
                "keepers_if_released": comp.get("keepers_if_released"),
                "players_added_if_released": comp.get("players_added_if_released"),
                "players_removed_if_released": comp.get("players_removed_if_released"),
                "keep_starting_points": comp.get("projected_starting_points_if_kept"),
                "release_starting_points": comp.get("projected_starting_points_if_released"),
                "roster_gain_from_keep": comp.get("roster_value_gained_from_keep"),
            })
            if player in AUDIT_PLAYERS:
                cf_row = cf[(cf["team"] == config.SAM_TEAM_NAME) & (cf["player"] == player)]
                std = keepers.keeper_price(
                    row.iloc[0]["salary_2025"], False, bool(row.iloc[0].get("paul_rule_eligible", False))
                )
                tag_cost = keepers.keeper_price(
                    row.iloc[0]["salary_2025"], True, bool(row.iloc[0].get("paul_rule_eligible", False))
                )
                low = exp = high = np.nan
                if len(cf_row):
                    low = cf_row.iloc[0]["released_low_price"]
                    exp = cf_row.iloc[0]["released_expected_price"]
                    high = cf_row.iloc[0]["released_high_price"]
                dominance.append({
                    "player": player,
                    "keeper_cost": std,
                    "tagged_cost": tag_cost,
                    "depleted_low_price": low,
                    "depleted_expected_price": exp,
                    "depleted_high_price": high,
                    "market_savings_expected": round(exp - std, 2) if pd.notna(exp) else np.nan,
                    "keep_starting_points": comp.get("projected_starting_points_if_kept"),
                    "release_starting_points": comp.get("projected_starting_points_if_released"),
                    "keep_bench_points": comp.get("projected_bench_points_if_kept"),
                    "release_bench_points": comp.get("projected_bench_points_if_released"),
                    "keep_spend": comp.get("auction_budget_if_kept"),
                    "release_spend": comp.get("auction_budget_if_released"),
                    "keepers_if_kept": comp.get("keepers_if_kept"),
                    "keepers_if_released": comp.get("keepers_if_released"),
                    "best_roster_if_kept": comp.get("best_roster_if_kept"),
                    "best_roster_if_released": comp.get("best_roster_if_released"),
                    "players_added_if_released": comp.get("players_added_if_released"),
                    "players_removed_if_released": comp.get("players_removed_if_released"),
                    "roster_gain_from_keep": comp.get("roster_value_gained_from_keep"),
                })

    comp_df = pd.DataFrame(comps)
    pd.DataFrame(deltas).to_csv(OUT / "roster_delta_sam.csv", index=False)
    pd.DataFrame(dominance).to_csv(OUT / "sam_keeper_dominance_audit.csv", index=False)
    roster_optimizer.debug_rows().to_csv(OUT / "optimizer_debug_sam.csv", index=False)

    cf_sam = cf[cf["team"] == config.SAM_TEAM_NAME].set_index("player") if not cf.empty else pd.DataFrame()
    board_rows = []
    for _, row in roster[roster["team"] == config.SAM_TEAM_NAME].iterrows():
        player = row["player"]
        if pd.isna(row.get("salary_2025")):
            continue
        std = keepers.keeper_price(row["salary_2025"], False, bool(row.get("paul_rule_eligible", False)))
        tag_cost = keepers.keeper_price(row["salary_2025"], True, bool(row.get("paul_rule_eligible", False)))
        comp = comp_df[comp_df["player"] == player]
        cf_row = cf_sam.loc[player] if player in cf_sam.index else None
        low = exp = high = np.nan
        if cf_row is not None:
            low, exp, high = cf_row["released_low_price"], cf_row["released_expected_price"], cf_row["released_high_price"]
        rg = comp["roster_value_gained_from_keep"].iloc[0] if len(comp) else np.nan
        in_best = player in (best.keepers if best else [])
        solver_st = best.solver_status if best else "ERROR"
        conf, conf_detail = _score_confidence(
            row, market_converged, market_iters, solver_st, best.auction_eligibility_valid if best else False,
        )
        cat = "DO_NOT_ACT_MODEL_BLOCKED"
        if best and best.solver_status != "OPTIMAL":
            cat = "DO_NOT_ACT_MODEL_BLOCKED"
        elif pd.notna(rg):
            if rg > 5 and pd.notna(low) and low >= std + config.KEEPER_DECISION_MARGIN:
                cat = "STRONG_KEEP"
            elif rg > 0:
                cat = "BORDERLINE_KEEP"
            else:
                cat = "STRONG_RELEASE"
        board_rows.append({
            "player": player,
            "position": row["position"],
            "prior_salary": row["salary_2025"],
            "standard_keeper_cost": std,
            "tagged_keeper_cost": tag_cost,
            "depleted_redraft_low": low,
            "depleted_redraft_expected": exp,
            "depleted_redraft_high": high,
            "market_savings_expected": round(exp - std, 2) if pd.notna(exp) else np.nan,
            "roster_gain_from_keep": rg,
            "selected_in_best_portfolio": in_best,
            "best_portfolio_keeper_count": best.keeper_count if best else np.nan,
            "decision_category": cat,
            "confidence_score": conf,
            "confidence_detail": conf_detail,
            "players_added_if_released": comp["players_added_if_released"].iloc[0] if len(comp) else "",
            "players_removed_if_released": comp["players_removed_if_released"].iloc[0] if len(comp) else "",
        })
    board = pd.DataFrame(board_rows)
    board.to_csv(OUT / "sam_keeper_decision_board.csv", index=False)

    # Keeper iteration outputs
    market.iteration_log.to_csv(OUT / "keeper_iterations.csv", index=False)
    init_keep = int(market.iteration_log.loc[market.iteration_log["iteration"] == 1, "keeper_status"].sum()) if not market.iteration_log.empty else 0
    final_keep = int(roster["will_keep"].astype(bool).sum())
    pd.DataFrame([{
        "initial_keeper_count": init_keep,
        "final_keeper_count": final_keep,
        "iterations_completed": market_iters,
        "converged": market_converged,
        "cycle_detected": market.cycle_detected,
        "update_method": config.KEEPER_MARKET_UPDATE_METHOD,
        "runtime": market.runtime_seconds,
        "auction_pool_before_filter": pool_before,
        "auction_pool_after_filter": pool_after,
    }]).to_csv(OUT / "keeper_convergence_summary.csv", index=False)

    love_blocked = eligibility_audit[
        eligibility_audit["player"].str.contains("Jeremiyah Love", case=False, na=False)
    ]
    lines = [
        f"Sam keeper analysis — {time.strftime('%Y-%m-%d %H:%M')}",
        f"Active roster: {config.ACTIVE_ROSTER_SIZE} ({config.STARTING_ROSTER_SIZE} starters + {config.BENCH_SIZE} bench), IR={config.IR_CAPACITY} optional",
        f"Keeper market: converged={market_converged} iterations={market_iters}",
        f"Auction pool: {pool_before} -> {pool_after} (eligibility filter)",
        f"Jeremiyah Love: eligible={love_blocked['auction_eligible'].iloc[0] if len(love_blocked) else 'N/A'}, status={love_blocked['final_auction_status'].iloc[0] if len(love_blocked) else 'N/A'}",
        f"Solver: {config.FINAL_SOLVER_MODE}",
        "",
        "BEST VALID PORTFOLIO:" if best else "NO VALID PORTFOLIO:",
    ]
    if best:
        lines += [
            f"  Keeper count: {best.keeper_count}",
            f"  Keepers: {', '.join(best.keepers) or 'none'}",
            f"  Keeper spend: ${best.keeper_spend:.0f}",
            f"  Auction budget: ${best.auction_budget:.0f}",
            f"  Starting points: {best.lineup.starting_points}",
            f"  Solver: {best.solver_status}",
            f"  Auction players: {', '.join(best.auction_players[:8])}...",
        ]
    lines += ["", "PORTFOLIOS BY KEEPER COUNT:"]
    for _, r in pf_df.iterrows():
        lines.append(
            f"  {int(r['keeper_count'])} keepers: {r['starting_points']} start pts, "
            f"status={r['solver_status']}, obj={r['objective_value']}"
        )
    lines += ["", "KEY KEEP VS RELEASE:"]
    for player in AUDIT_PLAYERS:
        sub = board[board["player"] == player]
        if len(sub):
            r = sub.iloc[0]
            lines.append(
                f"  {player}: {r['decision_category']} | roster_gain={r['roster_gain_from_keep']} | "
                f"market_save=${r['market_savings_expected']} | conf={r['confidence_score']}"
            )
    (OUT / "sam_keeper_summary.txt").write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\nRuntime {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
