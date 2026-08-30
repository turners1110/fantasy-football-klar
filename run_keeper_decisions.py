#!/usr/bin/env python3
"""Keeper lock decision system — neutral/depleted alpha, iteration, trades.

    python3 run_keeper_decisions.py --mode fast
    python3 run_keeper_decisions.py --mode full
    python3 run_keeper_decisions.py --mode full --overrides inputs/keeper_overrides.csv
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from auction_model import (
    assets,
    config,
    confidence,
    contracts,
    data_pipeline,
    flex_demand,
    keeper_market,
    keepers,
    market_engine,
    roster_optimizer,
    sensitivity,
    trade_engine,
    valuation,
)
from run_valuation import merge_projections

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
INPUT_DIR = BASE_DIR / "inputs"
OUTPUT_DIR = BASE_DIR / "outputs"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["fast", "full"], default="fast")
    p.add_argument("--salaries", default=DATA_DIR / "historical_salaries_2025_raw.csv")
    p.add_argument("--projections", default=DATA_DIR / "projections_2026.csv")
    p.add_argument("--fantasypros-rankings", default=BASE_DIR / "FantasyPros_2026_Draft_ALL_Rankings.csv")
    p.add_argument("--overrides", default=INPUT_DIR / "keeper_overrides.csv")
    p.add_argument("--blend-weight", type=float, default=0.6)
    p.add_argument("--output-dir", default=OUTPUT_DIR)
    p.add_argument("--scenario-keeper", choices=["A", "B"], default="A", help="A=max 6, B=exact 6")
    p.add_argument("--scenario-tag", choices=["C", "D"], default="C", help="C=one tag, D=no tag")
    return p.parse_args()


def _scenario_label(args: argparse.Namespace) -> str:
    keeper = "max_six" if args.scenario_keeper == "A" else "exact_six"
    tag = "one_optional_tag" if args.scenario_tag == "C" else "no_tag"
    return f"keeper={keeper}, tag={tag}, flex={config.FLEX_ALLOCATION_MODE}"


def _load_pipeline(args: argparse.Namespace):
    config.KEEPER_COUNT_IS_EXACT = args.scenario_keeper == "B"
    config.SCENARIO_TAG = args.scenario_tag
    if args.mode == "fast":
        config.DEPLETED_ALPHA_COUNTERFACTUAL_MODE = "position_ratio_fallback"
        config.MAX_KEEPER_MARKET_ITERATIONS = 5
        config.KEEPER_MARKET_UPDATE_METHOD = "SIMULTANEOUS"
    else:
        config.DEPLETED_ALPHA_COUNTERFACTUAL_MODE = "player_counterfactual"
        config.MAX_KEEPER_MARKET_ITERATIONS = config.MAX_KEEPER_MARKET_ITERATIONS

    salaries, log = data_pipeline.load_historical_salaries(args.salaries)
    fp_rankings = data_pipeline.load_fantasypros_rankings(args.fantasypros_rankings)
    projections = data_pipeline.load_optional_csv(args.projections)
    overrides = data_pipeline.load_optional_csv(args.overrides)
    blend_weight = args.blend_weight if projections is not None else 0.0

    nflverse_path = DATA_DIR / "nflverse" / "player_stats_reg_2025.csv"
    contract_table, dq_issues = contracts.build_player_contracts(
        salaries, projections, fp_rankings, overrides, nflverse_path
    )

    neutral_pool = data_pipeline.expand_pool_with_full_universe(salaries.copy(), fp_rankings)
    neutral_pool = data_pipeline.merge_fp_tiers(neutral_pool, fp_rankings)
    neutral_pool = merge_projections(neutral_pool, projections)
    neutral_pool = data_pipeline.fill_anchor_fallback(neutral_pool)
    neutral_priced = valuation.price_neutral_value(neutral_pool, blend_weight)
    neutral_value = salaries["player"].map(data_pipeline._normalize_name).map(
        neutral_priced.set_index(neutral_priced["player"].map(data_pipeline._normalize_name))[
            "hypothetical_open_market_value"
        ]
    )

    full_pool = data_pipeline.expand_pool_with_full_universe(salaries.copy(), fp_rankings)
    full_pool = data_pipeline.merge_fp_tiers(full_pool, fp_rankings)
    full_pool = merge_projections(full_pool, projections)
    full_pool = data_pipeline.fill_anchor_fallback(full_pool)

    return {
        "salaries": salaries,
        "log": log,
        "fp_rankings": fp_rankings,
        "projections": projections,
        "overrides": overrides,
        "blend_weight": blend_weight,
        "contract_table": contract_table,
        "dq_issues": dq_issues,
        "neutral_value": neutral_value,
        "full_pool": full_pool,
    }


def _build_draft_target_board(
    full_pool: pd.DataFrame,
    roster: pd.DataFrame,
    cf_audit: pd.DataFrame,
    blend_weight: float,
) -> pd.DataFrame:
    """Auction targets for Sam after keeper lock."""
    kept_players = set(roster.loc[roster["will_keep"].astype(bool), "player"])
    sam_kept = set(
        roster.loc[(roster["team"] == config.SAM_TEAM_NAME) & roster["will_keep"].astype(bool), "player"]
    )
    pool = full_pool[~full_pool["player"].isin(kept_players)].copy()
    pool = pool[pool["projected_points"].notna()].sort_values("projected_points", ascending=False)

    cf_by_player = {}
    if not cf_audit.empty:
        for _, r in cf_audit.iterrows():
            cf_by_player[r["player"]] = r

    inflation = keepers.inflation_summary(roster)
    _, priced = market_engine.price_depleted_market(
        full_pool, roster, blend_weight, market_engine.SCENARIO_DEPLETED_HIGH
    )
    price_map = priced.set_index("player")

    rows = []
    for _, row in pool.head(120).iterrows():
        player = row["player"]
        cf = cf_by_player.get(player)
        exp_price = float(price_map.loc[player, "suggested_auction_price"]) if player in price_map.index else np.nan
        high = float(cf["released_high_price"]) if cf is not None else exp_price
        low = float(cf["released_low_price"]) if cf is not None else exp_price * 0.85
        expected = float(cf["released_expected_price"]) if cf is not None else exp_price

        priority = "VALUE_TARGET"
        if expected >= 40:
            priority = "CORE_TARGET"
        elif expected < 5:
            priority = "LATE_VALUE"
        if pd.notna(row.get("projected_points")) and expected > row["projected_points"] * 2:
            priority = "OVERPRICED"

        rows.append({
            "player": player,
            "position": row["position"],
            "projected_points": row.get("projected_points"),
            "neutral_value": row.get("hypothetical_open_market_value"),
            "depleted_low_price": round(low, 2) if pd.notna(low) else None,
            "depleted_expected_price": round(expected, 2) if pd.notna(expected) else None,
            "depleted_high_price": round(high, 2) if pd.notna(high) else None,
            "auction_budget_price": round(high, 2) if pd.notna(high) else None,
            "target_priority": priority,
            "recommended_budget_range": f"${round(high * 0.85)}-${round(high)}" if pd.notna(high) else "",
            "reason": "Projected auction pool after keepers",
        })
    return pd.DataFrame(rows)


def _merge_cf_into_roster(roster: pd.DataFrame, cf_audit: pd.DataFrame) -> pd.DataFrame:
    if cf_audit.empty:
        return roster
    cf = cf_audit.rename(columns={
        "released_expected_price": "counterfactual_release_price",
        "depleted_alpha_expected": "depleted_market_alpha",
    })
    merge_cols = [
        "team", "player", "released_low_price", "released_expected_price", "released_high_price",
        "depleted_alpha_low", "depleted_alpha_expected", "depleted_alpha_high",
        "neutral_alpha", "calculation_method", "counterfactual_release_price", "depleted_market_alpha",
    ]
    avail = [c for c in merge_cols if c in cf.columns]
    out = roster.merge(cf[avail], on=["team", "player"], how="left", suffixes=("", "_cf"))
    for col in ("depleted_market_alpha", "counterfactual_release_price"):
        if f"{col}_cf" in out.columns:
            out[col] = out[f"{col}_cf"].combine_first(out.get(col))
            out = out.drop(columns=[f"{col}_cf"])
    return out


def _sam_decision_board(
    merged: pd.DataFrame,
    roster_comps: pd.DataFrame,
    contract_table: pd.DataFrame,
    sam_team: str,
    converged: bool,
    cycle: bool,
) -> pd.DataFrame:
    sam = merged[merged["team"] == sam_team].copy()
    if not roster_comps.empty:
        comp = roster_comps.set_index("player")
        for col in roster_comps.columns:
            if col != "player":
                sam[col] = sam["player"].map(comp[col])

    ct = contract_table.set_index("player") if not contract_table.empty else pd.DataFrame()
    for col in ("salary_origin", "data_quality_status", "data_quality_notes", "projection_available"):
        if col in ct.columns:
            sam[col] = sam["player"].map(ct[col])

    scores = []
    for _, row in sam.iterrows():
        sel = float(row.get("keeper_selection_rate", row.get("selection_rate", 0.5)) or 0.5)
        score, cat, missing = confidence.score_decision(
            row, selection_rate=sel, converged=converged, cycle_detected=cycle
        )
        scores.append({"confidence_score": score, "decision_category": cat, "confidence_missing_points": missing})
    score_df = pd.DataFrame(scores, index=sam.index)
    sam = pd.concat([sam, score_df], axis=1)
    sam["recommended_action"] = sam.apply(_recommended_action, axis=1)
    sam["main_reason"] = sam.apply(_main_reason, axis=1)
    sam["main_risk"] = sam.apply(_main_risk, axis=1)
    order = {"LOCK": 0, "STRONG_KEEP": 1, "BORDERLINE_KEEP": 2, "STRONG_RELEASE": 3, "DATA_BLOCKED": 4}
    sam["sort_key"] = sam["decision_category"].map(order).fillna(5)
    sort_col = "depleted_alpha_expected" if "depleted_alpha_expected" in sam.columns else "depleted_market_alpha"
    sam = sam.sort_values(["sort_key", sort_col], ascending=[True, False], na_position="last")
    cols = [
        "player", "position", "salary_2025", "standard_keeper_cost", "tagged_keeper_cost",
        "selected_keeper_cost", "neutral_value", "released_low_price", "released_expected_price",
        "released_high_price", "neutral_alpha", "depleted_alpha_low", "depleted_alpha_expected",
        "depleted_alpha_high", "counterfactual_release_price", "depleted_market_alpha",
        "best_replacement_if_released", "projected_starting_points_if_kept",
        "projected_starting_points_if_released", "roster_value_gained_from_keep",
        "keeper_selection_rate", "tag_selection_rate", "decision_category", "confidence_score",
        "confidence_missing_points", "recommended_action", "main_reason", "main_risk",
        "calculation_method", "data_quality_notes",
    ]
    return sam[[c for c in cols if c in sam.columns]]


def _main_reason(row: pd.Series) -> str:
    low = row.get("depleted_alpha_low", row.get("depleted_market_alpha"))
    exp = row.get("depleted_alpha_expected", row.get("depleted_market_alpha"))
    gain = row.get("roster_value_gained_from_keep", row.get("final_keep_score"))
    parts = [f"Depleted alpha low ${low:.0f}, expected ${exp:.0f}" if pd.notna(low) else "Missing depleted alpha"]
    if pd.notna(gain):
        parts.append(f"roster gain from keep {gain:+.1f}")
    return "; ".join(parts)


def _main_risk(row: pd.Series) -> str:
    if float(row.get("confidence_score", 0)) >= 9:
        return "High confidence path"
    if row.get("decision_stability") == "stable":
        return "Stable across scenarios; confidence limited by data/method"
    return "Sensitive to keeper-market and pricing assumptions"


def _recommended_action(row: pd.Series) -> str:
    cat = row.get("decision_category", row.get("decision_confidence", ""))
    r_gain = float(row.get("roster_value_gained_from_keep", row.get("final_keep_score", 0)) or 0)
    if cat == "DATA_BLOCKED":
        return "CONFIRM DATA"
    if r_gain <= 0:
        return "RELEASE"
    if cat in {"LOCK", "STRONG_KEEP", "BORDERLINE_KEEP"}:
        if row.get("tag_selection_rate", 0) > 0.5 and bool(row.get("will_keep")):
            return "TAG"
        return "KEEP"
    return "RELEASE"


def _sam_summary_text(
    board: pd.DataFrame,
    result: pd.DataFrame,
    inflation: dict,
    sam_team: str,
    scenario: str,
    dq_issues: pd.DataFrame,
    trade_targets: pd.DataFrame,
    draft_board: pd.DataFrame,
    converged: bool,
    iterations: int,
) -> str:
    sam = result[result["team"] == sam_team]
    kept = sam[sam["will_keep"].astype(bool)]
    tagged = kept[kept["tag_used"].astype(bool)]
    keeper_spend = float(kept["keeper_price_2026"].sum())
    auction_budget = config.BUDGET_PER_TEAM - keeper_spend
    open_spots = config.TOTAL_ROSTER_SPOTS_PER_TEAM - len(kept)

    lines = [
        f"Active scenario: {scenario}",
        f"Keeper market converged: {converged} ({iterations} iterations)",
        "",
        "RECOMMENDED KEEPERS:",
    ]
    for _, r in kept.iterrows():
        tag_note = " [TAG]" if r.get("tag_used") else ""
        lines.append(f"  - {r['player']} ({r['position']}) @ ${r['keeper_price_2026']:.0f}{tag_note}")

    lines += ["", "RECOMMENDED RELEASES (top candidates):"]
    releases = board[board["recommended_action"] == "RELEASE"].head(8)
    for _, r in releases.iterrows():
        lines.append(f"  - {r['player']} (conf {r.get('confidence_score', '?')}/10)")

    lines += [
        "",
        f"TAG: {tagged['player'].iloc[0] if len(tagged) else 'None recommended'}",
        f"Keeper spend: ${keeper_spend:.0f}",
        f"Auction budget: ${auction_budget:.0f}",
        f"Open roster spots entering auction: {open_spots}",
        "",
        "Sam player redraft prices (low / expected / high):",
    ]
    for _, r in board.iterrows():
        low = r.get("released_low_price", r.get("depleted_alpha_low"))
        exp = r.get("released_expected_price", r.get("counterfactual_release_price"))
        high = r.get("released_high_price")
        repl = r.get("best_replacement_if_released", "")
        conf = r.get("confidence_score", "?")
        lines.append(
            f"  - {r['player']}: ${low} / ${exp} / ${high} | repl={repl or 'n/a'} | conf={conf}/10"
        )

    high_conf = board[board["confidence_score"] >= 9] if "confidence_score" in board.columns else board.iloc[0:0]
    low_conf = board[board["confidence_score"] < 9] if "confidence_score" in board.columns else board
    lines += ["", f"High confidence (>=9/10): {len(high_conf)} players"]
    for _, r in high_conf.iterrows():
        lines.append(f"  - {r['player']}: {r.get('recommended_action')}")
    lines += ["", f"Below 9/10 confidence: {len(low_conf)} players"]

    lines += ["", "Data needing confirmation today:"]
    if dq_issues.empty:
        lines.append("  - None flagged urgent")
    else:
        for _, issue in dq_issues.head(12).iterrows():
            lines.append(f"  - {issue.get('player', '?')}: {issue.get('detail', issue.get('data_quality_notes', ''))}")

    lines += ["", "Best trade targets:"]
    if trade_targets.empty:
        lines.append("  - None screened")
    else:
        for _, t in trade_targets.head(5).iterrows():
            lines.append(f"  - {t.get('target_player', t.get('player', '?'))} ({t.get('target_team', t.get('team', '?'))})")

    lines += ["", "Best draft targets:"]
    if draft_board.empty:
        lines.append("  - Run full mode for draft board")
    else:
        for _, d in draft_board[draft_board["target_priority"].isin(["CORE_TARGET", "VALUE_TARGET"])].head(8).iterrows():
            lines.append(f"  - {d['player']} ({d['position']}) budget ${d.get('auction_budget_price', '?')}")

    lines += [
        "",
        f"League keeper spend: ${inflation['total_keeper_spend']:.0f}",
        f"League auction remainder: ${inflation['remaining_budget']:.0f}",
        f"Budget reconciliation: ${inflation['total_keeper_spend'] + inflation['remaining_budget']:.0f} (target $4800)",
    ]
    return "\n".join(lines)


def _team_keeper_summary(result: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for team, tdf in result.groupby("team"):
        kept = tdf[tdf["will_keep"]]
        positive = tdf[tdf["depleted_market_alpha"] > 0].sort_values("depleted_market_alpha", ascending=False)
        alphas = positive["depleted_market_alpha"].tolist()
        rows.append({
            "team": team,
            "projected_keeper_count": len(kept),
            "projected_keeper_cost": round(float(kept["keeper_price_2026"].sum()), 2),
            "neutral_keeper_value": round(float(kept["neutral_value"].sum()), 2),
            "depleted_keeper_value": round(float(kept["counterfactual_release_price"].sum()), 2),
            "neutral_keeper_surplus": round(float(kept["neutral_alpha"].sum()), 2),
            "depleted_keeper_surplus": round(float(kept["depleted_market_alpha"].sum()), 2),
            "remaining_auction_budget": round(config.BUDGET_PER_TEAM - float(kept["keeper_price_2026"].sum()), 2),
            "recommended_tag": kept.loc[kept["tag_used"], "player"].iloc[0] if kept["tag_used"].any() else "",
            "keeper_set": ", ".join(kept["player"].tolist()),
            "keeper_squeeze": max(len(positive) - config.MAX_KEEPERS_PER_TEAM, 0),
            "sixth_best_keeper_alpha": round(alphas[5], 2) if len(alphas) >= 6 else pd.NA,
            "seventh_best_keeper_alpha": round(alphas[6], 2) if len(alphas) >= 7 else pd.NA,
        })
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    t0 = time.time()
    args.output_dir = Path(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    INPUT_DIR.mkdir(parents=True, exist_ok=True)

    ctx = _load_pipeline(args)
    scenario = _scenario_label(args)
    print(f"Active scenario: {scenario}")

    market = keeper_market.iterate_keeper_market(
        ctx["salaries"],
        ctx["full_pool"],
        ctx["neutral_value"],
        ctx["blend_weight"],
        ctx["overrides"],
    )

    exact_teams = {config.SAM_TEAM_NAME}
    if args.mode == "full":
        exact_teams = None
    cf_audit = market_engine.build_release_counterfactual_audit(
        market.roster,
        ctx["full_pool"],
        ctx["neutral_value"],
        ctx["blend_weight"],
        exact_teams=exact_teams,
    )
    cf_audit.to_csv(args.output_dir / "player_release_counterfactuals.csv", index=False)

    merged_roster = _merge_cf_into_roster(market.roster, cf_audit)

    sens = sensitivity.run_sensitivity(
        ctx["salaries"], ctx["full_pool"], ctx["neutral_value"], ctx["overrides"],
        full_mode=(args.mode == "full"),
    )
    merged = merged_roster.merge(
        sens, on=["team", "player"], how="left", suffixes=("", "_sens")
    )

    # compare_keep_vs_release -> evaluate_portfolio -> exact_roster_solver
    # all expect an auction_pool that's already priced (suggested_auction_price
    # present) -- ctx["full_pool"] is raw (projections/anchors merged, never
    # run through valuation). Reuse the same pricing market_engine already
    # does for the "expected" depleted-market scenario, rather than pricing
    # it a third, inconsistent way here.
    _, priced_auction_pool = market_engine.price_depleted_market(
        ctx["full_pool"], merged_roster, ctx["blend_weight"], market_engine.SCENARIO_DEPLETED_EXPECTED
    )

    roster_comps = []
    sam_players = merged_roster[merged_roster["team"] == config.SAM_TEAM_NAME]["player"].tolist()
    print(f"\nRunning keep-vs-release roster comparisons for {len(sam_players)} Sam players...")
    for player in sam_players:
        comp = roster_optimizer.compare_keep_vs_release(
            config.SAM_TEAM_NAME, player, merged_roster, priced_auction_pool,
        )
        roster_comps.append(comp)
    roster_comps_df = pd.DataFrame(roster_comps) if roster_comps else pd.DataFrame()

    ctx["contract_table"].to_csv(args.output_dir / "player_contracts.csv", index=False)
    ctx["dq_issues"].to_csv(args.output_dir / "data_quality_report.csv", index=False)
    market.iteration_log.to_csv(args.output_dir / "keeper_iterations.csv", index=False)
    flex_demand.compute_position_demand_audit(ctx["full_pool"], market.roster).to_csv(
        args.output_dir / "position_demand_audit.csv", index=False
    )

    team_summary = _team_keeper_summary(merged_roster)
    team_summary.to_csv(args.output_dir / "team_keeper_summary.csv", index=False)

    sam_board = _sam_decision_board(
        merged, roster_comps_df, ctx["contract_table"], config.SAM_TEAM_NAME,
        market.converged, market.cycle_detected,
    )
    sam_board.to_csv(args.output_dir / "sam_keeper_decision_board.csv", index=False)

    asset_board = assets.build_asset_board(merged_roster, cf_audit, team_summary)
    asset_board.to_csv(args.output_dir / "asset_board.csv", index=False)

    draft_board = _build_draft_target_board(ctx["full_pool"], market.roster, cf_audit, ctx["blend_weight"])
    draft_board.to_csv(args.output_dir / "sam_draft_target_board.csv", index=False)

    targets = trade_engine.level1_trade_screen(merged_roster)
    targets.to_csv(args.output_dir / "sam_trade_targets.csv", index=False)
    trade_engine.build_trade_packages(targets, merged_roster).to_csv(
        args.output_dir / "sam_trade_packages.csv", index=False
    )

    summary = _sam_summary_text(
        sam_board, merged_roster, market.inflation, config.SAM_TEAM_NAME, scenario,
        ctx["dq_issues"], targets, draft_board, market.converged, market.iterations,
    )
    (args.output_dir / "sam_keeper_summary.txt").write_text(summary)

    keeper_out = merged_roster[merged_roster["will_keep"].astype(bool)].copy()
    keeper_cols = [
        "team", "player", "position", "salary_2025", "tag_used", "keeper_price_2026",
        "standard_keeper_cost", "depleted_alpha_low", "depleted_alpha_expected", "depleted_market_alpha",
    ]
    keeper_out[[c for c in keeper_cols if c in keeper_out.columns]].to_csv(
        args.output_dir / "keepers_2026.csv", index=False
    )

    n_keepers = int(market.roster["will_keep"].astype(bool).sum())
    sam_kept = market.roster[
        (market.roster["team"] == config.SAM_TEAM_NAME) & market.roster["will_keep"].astype(bool)
    ]

    print(f"\nConverged: {market.converged} in {market.iterations} iterations "
          f"(cache hits {market.cache_hits}, misses {market.cache_misses})")
    print(f"League keepers: {n_keepers}, spend ${market.inflation['total_keeper_spend']:.0f}, "
          f"auction ${market.inflation['remaining_budget']:.0f}")
    print(f"Sam keepers ({len(sam_kept)}): {', '.join(sam_kept['player'].tolist()) or 'none'}")
    for margin in config.KEEPER_CONSERVATIVE_MARGINS:
        n = int((merged_roster["depleted_alpha_low"].fillna(-999) > margin).sum()) if "depleted_alpha_low" in merged_roster.columns else 0
        print(f"  Positive depleted_alpha_low > ${margin}: {n} league-wide")
    print(f"Runtime: {time.time() - t0:.1f}s (market solver {market.runtime_seconds:.1f}s)")
    print(f"\nOutputs written to {args.output_dir}/")


if __name__ == "__main__":
    main()
