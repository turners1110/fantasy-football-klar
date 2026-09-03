#!/usr/bin/env python3
"""Fancy Football League auction price sheet generator.

Re-run this any time projections, injuries, or keeper decisions change:

    python run_valuation.py
    python run_valuation.py --projections data/projections_2026.csv \\
        --keeper-overrides data/keeper_overrides.csv \\
        --rookie-pool data/rookie_pool.csv \\
        --blend-weight 0.6

With no --projections file, prices are pure historical-salary anchoring
(this league's actual 2025 salaries, reshaped for keeper removal + the
2RB/2WR/TE/3FLEX/no-K-DEF roster math) -- see README.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from auction_model import auction_eligibility, config, data_pipeline, keepers, rookie_board, valuation
from auction_model.confirmed_keeper_pipeline import (
    compute_identity_issues, compute_team_states, normalize_name, unresolved_duplicate_identities,
)

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
CONFIRMED_KEEPER_REQUIRED_COLUMNS = {
    "season", "team_id", "team_name", "player_id", "player_name", "position",
    "prior_salary", "keeper_cost", "franchise_tag", "keeper_status", "counts_as_keeper",
    "counts_as_active_roster", "auction_eligible", "source", "source_date", "confidence", "notes",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--salaries", default=DATA_DIR / "historical_salaries_2025_raw.csv")
    p.add_argument(
        "--fantasypros-rankings", default=BASE_DIR / "FantasyPros_2026_Draft_ALL_Rankings.csv",
        help="FantasyPros 'ALL Rankings' export, used only to widen the draftable "
        "pool beyond players who happened to be on a 2025 league roster. Rank/tier "
        "data only -- never used to fabricate a point projection.",
    )
    p.add_argument("--projections", default=DATA_DIR / "projections_2026.csv")
    p.add_argument("--keeper-overrides", default=DATA_DIR / "keeper_overrides.csv")
    p.add_argument("--rookie-pool", default=DATA_DIR / "rookie_pool.csv")
    p.add_argument(
        "--blend-weight", type=float, default=0.6,
        help="Weight given to VBD-from-projections vs. historical-salary anchor "
        "(0=pure anchor, 1=pure VBD). Only applies where a projection exists; "
        "ignored (forced to 0) if no projections file is supplied at all.",
    )
    p.add_argument("--output-dir", default=OUTPUT_DIR)
    p.add_argument(
        "--keepers",
        default=None,
        help="Authoritative projected keeper file (default: outputs/keepers_2026.csv if present).",
    )
    p.add_argument(
        "--keeper-mode",
        choices=["authoritative", "fallback_neutral", "confirmed"],
        default="authoritative",
        help="authoritative loads --keepers file; fallback_neutral uses single-pass neutral alpha; "
        "confirmed loads --keepers-file + --budget-adjustments-file (the phase-2 tracked, "
        "commissioner-sourced pipeline) and fails loudly rather than falling back on any gap.",
    )
    p.add_argument(
        "--keepers-file", default=None,
        help="Confirmed keeper CSV for --keeper-mode confirmed (e.g. data/keepers_2026_confirmed.csv). "
        "Distinct from --keepers, which is the older authoritative-mode file.",
    )
    p.add_argument(
        "--budget-adjustments-file", default=None,
        help="Confirmed cash-trade/budget-adjustment CSV for --keeper-mode confirmed "
        "(e.g. data/team_budget_adjustments_2026.csv).",
    )
    return p.parse_args()


def merge_projections(pool: pd.DataFrame, projections: pd.DataFrame | None) -> pd.DataFrame:
    pool = pool.copy()
    pool["projected_points"] = pd.NA
    if projections is None:
        return pool

    proj = projections.copy()
    if "projected_points" not in proj.columns or proj["projected_points"].isna().all():
        proj["projected_points"] = proj.apply(
            lambda row: config.score_from_stats(row.to_dict()), axis=1
        )

    proj["_key"] = proj["player"].astype(str).str.strip().str.lower()
    pool["_key"] = pool["player"].astype(str).str.strip().str.lower()

    lookup = proj.set_index("_key")["projected_points"].to_dict()
    pool["projected_points"] = pool["_key"].map(lookup)
    pool["projected_points"] = pd.to_numeric(pool["projected_points"], errors="coerce")

    unmatched = proj[~proj["_key"].isin(pool["_key"])]
    if len(unmatched):
        print(
            f"NOTE: {len(unmatched)} players in the projections file did not match "
            f"anyone in the historical pool (new-to-this-pool players, e.g. rookies "
            f"who debuted, or a name-spelling mismatch). They are not included in "
            f"the auction price sheet in this run; expand historical_salaries "
            f"or check spelling if that's unexpected."
        )
    return pool.drop(columns=["_key"])


def _load_authoritative_keepers(path: Path, salaries: pd.DataFrame) -> pd.DataFrame:
    """Apply keeper flags from authoritative CSV onto salary roster."""
    keepers_df = pd.read_csv(path)
    required = {"team", "player"}
    if not required.issubset(keepers_df.columns):
        raise ValueError(f"Keeper file {path} missing columns {required - set(keepers_df.columns)}")

    roster = salaries.copy()
    roster["will_keep"] = False
    roster["tag_used"] = False
    key_set = set(zip(keepers_df["team"], keepers_df["player"]))
    for idx, row in roster.iterrows():
        if (row["team"], row["player"]) in key_set:
            roster.loc[idx, "will_keep"] = True
    if "tag_used" in keepers_df.columns:
        tag_rows = keepers_df[keepers_df["tag_used"].astype(bool)]
        for _, tr in tag_rows.iterrows():
            mask = (roster["team"] == tr["team"]) & (roster["player"] == tr["player"])
            roster.loc[mask, "tag_used"] = True
    return roster


def _load_confirmed_keepers(
    keepers_path: Path, adjustments_path: Path, salaries: pd.DataFrame, output_dir: Path,
) -> tuple[pd.DataFrame, dict, list[dict], pd.DataFrame]:
    """Confirmed-mode keeper/budget loader -- the one authoritative pipeline
    per the rebuild spec. Validates the tracked input files, computes team
    starting states via auction_model.confirmed_keeper_pipeline (the SAME
    function scripts/build_team_states.py uses), and fails loudly (raises)
    rather than silently falling back to neutral-alpha on any gap.

    Also writes the same audit-trail CSVs scripts/build_team_states.py
    writes, so running this CLI alone is a complete, self-contained
    authoritative pipeline (not dependent on a separate script having
    already been run).

    Returns (with_keepers, inflation, state_rows, confirmed_keepers).
    """
    if not keepers_path.exists():
        raise FileNotFoundError(f"Confirmed keepers file not found: {keepers_path}")
    if not adjustments_path.exists():
        raise FileNotFoundError(f"Confirmed budget adjustments file not found: {adjustments_path}")

    confirmed_keepers = pd.read_csv(keepers_path)
    missing_cols = CONFIRMED_KEEPER_REQUIRED_COLUMNS - set(confirmed_keepers.columns)
    if missing_cols:
        raise ValueError(f"Confirmed keepers file {keepers_path} missing required columns: {sorted(missing_cols)}")
    adjustments = pd.read_csv(adjustments_path)

    identity_rows = compute_identity_issues(confirmed_keepers)
    unresolved = unresolved_duplicate_identities(identity_rows)
    if unresolved:
        raise ValueError(
            f"Confirmed mode: unresolved duplicate/ambiguous player identities in {keepers_path}, "
            f"refusing to proceed (never silently merge conflicting identities): {unresolved}"
        )

    state_rows, conflict_rows = compute_team_states(confirmed_keepers, adjustments)
    negative = [r for r in state_rows if r["primary_auction_budget"] < 0 or r["conversions_scenario_auction_budget"] < 0]
    if negative:
        raise ValueError(f"Confirmed mode: negative auction budget computed for team(s), refusing to proceed: {negative}")

    audit_dir = BASE_DIR / "outputs" / "auction_rebuild" / "audit"
    out_data_dir = BASE_DIR / "outputs" / "auction_rebuild" / "data"
    audit_dir.mkdir(parents=True, exist_ok=True)
    out_data_dir.mkdir(parents=True, exist_ok=True)
    if conflict_rows:
        pd.DataFrame(conflict_rows).to_csv(audit_dir / "keeper_source_conflicts.csv", index=False)
    else:
        (audit_dir / "keeper_source_conflicts.csv").write_text(
            "team,field,winning_source,winning_value,losing_source,losing_value,detail\n"
        )
    pd.DataFrame(state_rows).to_csv(out_data_dir / "team_starting_states.csv", index=False)

    veteran_rows = confirmed_keepers[confirmed_keepers["counts_as_keeper"].astype(bool)]
    with_keepers = salaries.copy()
    with_keepers["will_keep"] = False
    with_keepers["tag_used"] = False
    with_keepers["keeper_price_2026"] = pd.NA
    with_keepers["keep_source"] = ""
    salary_norm = with_keepers["player"].map(normalize_name)

    unmatched_synthetic_rows = []
    for _, kr in veteran_rows.iterrows():
        mask = salary_norm == normalize_name(kr["player_name"])
        if not mask.any():
            # Not found in historical_salaries_2025_raw.csv by normalized name --
            # either a nickname/full-name mismatch (e.g. "Tet" vs "Tetairoa"
            # McMillan) or a player genuinely absent from that extraction
            # (e.g. a 2025 rookie never on a captured roster snapshot). NOT
            # silently dropped: logged to keeper_identity_issues.csv below,
            # and a synthetic roster row is built directly from the
            # confirmed keeper file's own (authoritative) prior_salary /
            # keeper_cost so this keeper still counts correctly everywhere
            # downstream (auction exclusion, keepers_2026.csv export).
            identity_rows.append({
                "issue_type": "UNMATCHED_TO_HISTORICAL_SALARY", "player_name": kr["player_name"],
                "normalized": normalize_name(kr["player_name"]), "team": kr["team_name"],
                "detail": f"No row in historical_salaries_2025_raw.csv matched by normalized name "
                          f"-- using confirmed file's own prior_salary (${kr['prior_salary']}) / "
                          f"keeper_cost (${kr['keeper_cost']}) directly instead of silently dropping.",
            })
            unmatched_synthetic_rows.append({
                "team": kr["team_name"], "player": kr["player_name"], "position": kr["position"],
                "salary_2025": float(kr["prior_salary"]) if pd.notna(kr["prior_salary"]) else pd.NA,
                "notes": "confirmed-mode synthetic row: not found in historical_salaries_2025_raw.csv",
                "has_confirmed_salary": True, "is_tagged_2025": bool(kr["franchise_tag"]), "on_ir": False,
                "games_played_note": "", "paul_rule_eligible": False, "paul_rule_verified": False,
                "paul_rule_source": "", "salary_origin": "confirmed_keeper_file_prior_salary", "origin_confidence": 0.9,
                "will_keep": True, "tag_used": bool(kr["franchise_tag"]),
                "keeper_price_2026": float(kr["keeper_cost"]), "keep_source": "confirmed_unmatched_synthetic",
            })
            continue
        with_keepers.loc[mask, "will_keep"] = True
        with_keepers.loc[mask, "tag_used"] = bool(kr["franchise_tag"])
        with_keepers.loc[mask, "keeper_price_2026"] = float(kr["keeper_cost"])
        with_keepers.loc[mask, "keep_source"] = "confirmed"
    if unmatched_synthetic_rows:
        with_keepers = pd.concat([with_keepers, pd.DataFrame(unmatched_synthetic_rows)], ignore_index=True)

    pd.DataFrame(identity_rows).to_csv(audit_dir / "keeper_identity_issues.csv", index=False)

    total_keeper_spend = float(veteran_rows["keeper_cost"].astype(float).sum())
    historical_value_removed = float(veteran_rows["prior_salary"].astype(float).sum())
    remaining_budget = float(sum(r["primary_auction_budget"] for r in state_rows))
    denom = config.TOTAL_LEAGUE_BUDGET - historical_value_removed
    inflation = {
        "n_keepers": int(len(veteran_rows)),
        "total_keeper_spend": round(total_keeper_spend, 2),
        "remaining_budget": round(remaining_budget, 2),
        "historical_value_removed": round(historical_value_removed, 2),
        "historical_value_remaining_in_pool": round(
            float(salaries.loc[~with_keepers["will_keep"], "salary_2025"].dropna().sum()), 2
        ),
        "inflation_multiplier": round(remaining_budget / denom, 4) if denom > 0 else 1.0,
    }
    naive_total = total_keeper_spend + remaining_budget
    if abs(naive_total - config.TOTAL_LEAGUE_BUDGET) > 1.0:
        print(
            f"  NOTE (not a stop condition -- per-team real budgets, not the naive league-wide "
            f"total, are authoritative here): sum of confirmed per-team auction budgets "
            f"(${remaining_budget:.0f}) + confirmed keeper spend (${total_keeper_spend:.0f}) = "
            f"${naive_total:.0f}, vs. the naive {config.TOTAL_LEAGUE_BUDGET / 12:.0f}-per-team x 12 "
            f"total of ${config.TOTAL_LEAGUE_BUDGET:.0f} (gap ${config.TOTAL_LEAGUE_BUDGET - naive_total:+.0f}). "
            f"Logged, not silently reconciled -- see outputs/auction_rebuild/audit/keeper_source_conflicts.csv."
        )

    return with_keepers, inflation, state_rows, confirmed_keepers


def _reconcile_keeper_budget(roster: pd.DataFrame, inflation: dict) -> None:
    total = inflation["total_keeper_spend"] + inflation["remaining_budget"]
    if abs(total - config.TOTAL_LEAGUE_BUDGET) > 1.0:
        raise ValueError(
            f"Budget reconciliation failed: keepers ${inflation['total_keeper_spend']:.0f} + "
            f"auction ${inflation['remaining_budget']:.0f} = ${total:.0f} (expected ${config.TOTAL_LEAGUE_BUDGET})"
        )
    n_keepers = int(roster["will_keep"].astype(bool).sum())
    print(f"  Reconciliation OK: {n_keepers} keepers, ${inflation['total_keeper_spend']:.0f} keeper spend, "
          f"${inflation['remaining_budget']:.0f} auction pool")


def main() -> None:
    args = parse_args()
    args.output_dir = Path(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    salaries, log = data_pipeline.load_historical_salaries(args.salaries)
    print(f"Loaded {len(salaries)} historical roster rows.")
    for line in log:
        print(f"  [data quality] {line}")

    # v4 Part 3: the default keeper forecast is neutral-alpha-based, which
    # needs a neutral (talent-VBD) value for every player BEFORE any
    # keeper decision exists. Build that pre-pass pool first -- it doesn't
    # need or use will_keep at all, since talent VBD is defined against
    # the full universe regardless of who ends up kept.
    fp_rankings = data_pipeline.load_fantasypros_rankings(args.fantasypros_rankings)
    projections = data_pipeline.load_optional_csv(args.projections)
    blend_weight = args.blend_weight
    if projections is None:
        print(
            "\nNo projections file found at "
            f"{args.projections} -- pricing from historical-salary anchor only "
            "(blend_weight forced to 0). Supply a projections CSV (see "
            "data/projections_2026.template.csv) and re-run to blend in "
            "2026 point projections."
        )
        blend_weight = 0.0

    neutral_pool = data_pipeline.expand_pool_with_full_universe(salaries.copy(), fp_rankings)
    neutral_pool = data_pipeline.merge_fp_tiers(neutral_pool, fp_rankings)
    neutral_pool = merge_projections(neutral_pool, projections)
    neutral_pool = data_pipeline.fill_anchor_fallback(neutral_pool)
    neutral_priced = valuation.price_neutral_value(neutral_pool, blend_weight)
    neutral_value_by_key = neutral_priced.set_index(
        neutral_priced["player"].map(data_pipeline._normalize_name)
    )["hypothetical_open_market_value"]
    neutral_value = salaries["player"].map(data_pipeline._normalize_name).map(neutral_value_by_key)

    overrides = data_pipeline.load_optional_csv(args.keeper_overrides)
    neutral_value_by_key = neutral_priced.set_index(
        neutral_priced["player"].map(data_pipeline._normalize_name)
    )["hypothetical_open_market_value"]
    neutral_value = salaries["player"].map(data_pipeline._normalize_name).map(neutral_value_by_key)

    keepers_path = Path(args.keepers) if args.keepers else BASE_DIR / config.AUTHORITATIVE_KEEPERS_PATH
    use_authoritative = args.keeper_mode == "authoritative" and keepers_path.exists()
    use_confirmed = args.keeper_mode == "confirmed"
    confirmed_keepers_df = None
    confirmed_state_rows = None

    if use_confirmed:
        if not args.keepers_file or not args.budget_adjustments_file:
            raise ValueError(
                "--keeper-mode confirmed requires both --keepers-file and --budget-adjustments-file "
                "(e.g. --keepers-file data/keepers_2026_confirmed.csv "
                "--budget-adjustments-file data/team_budget_adjustments_2026.csv)."
            )
        print(f"\nLoading confirmed keepers from {args.keepers_file} + {args.budget_adjustments_file}")
        with_keepers, inflation, confirmed_state_rows, confirmed_keepers_df = _load_confirmed_keepers(
            Path(args.keepers_file), Path(args.budget_adjustments_file), salaries, args.output_dir,
        )
        print(f"  Loaded {int(with_keepers['will_keep'].astype(bool).sum())} confirmed veteran keepers "
              f"({len(confirmed_keepers_df) - int(with_keepers['will_keep'].astype(bool).sum())} college-rights holds)")
    elif use_authoritative:
        print(f"\nLoading authoritative keepers from {keepers_path}")
        with_keepers = _load_authoritative_keepers(keepers_path, salaries)
        with_keepers = keepers.apply_keeper_overrides(
            with_keepers, overrides, neutral_value=neutral_value, skip_default_forecast=True
        )
        with_keepers = keepers.price_keepers(with_keepers)
        print(f"  Loaded {int(with_keepers['will_keep'].astype(bool).sum())} projected keepers from file")
        inflation = keepers.inflation_summary(with_keepers)
        _reconcile_keeper_budget(with_keepers, inflation)
    elif args.keeper_mode == "authoritative":
        raise FileNotFoundError(
            f"Authoritative keeper file not found at {keepers_path}. "
            "Run run_keeper_decisions.py first or pass --keeper-mode fallback_neutral."
        )
    else:
        print("\nUsing fallback neutral-alpha keeper projection (single pass)")
        with_keepers = keepers.apply_keeper_overrides(salaries, overrides, neutral_value=neutral_value)
        with_keepers["will_keep"] = keepers.neutral_alpha_keep_flag(with_keepers, neutral_value)
        with_keepers = keepers.price_keepers(with_keepers)
        inflation = keepers.inflation_summary(with_keepers)
        _reconcile_keeper_budget(with_keepers, inflation)

    if use_authoritative:
        file_keepers = pd.read_csv(keepers_path)
        file_count = len(file_keepers)
        file_spend = float(file_keepers["keeper_price_2026"].sum()) if "keeper_price_2026" in file_keepers.columns else None
        loaded_count = int(with_keepers["will_keep"].astype(bool).sum())
        if loaded_count != file_count:
            raise ValueError(
                f"Keeper count mismatch: file has {file_count}, roster loaded {loaded_count}"
            )
        if file_spend is not None and abs(file_spend - inflation["total_keeper_spend"]) > 0.01:
            raise ValueError(
                f"Keeper spend mismatch: file ${file_spend:.0f} vs loaded ${inflation['total_keeper_spend']:.0f}"
            )
    print("\nKeeper / inflation summary:")
    for k, v in inflation.items():
        print(f"  {k}: {v}")

    # Price against the FULL rostered universe (keepers included) -- see
    # valuation.price_full_market_and_live for why this gives a stable
    # "hypothetical open-market value" instead of one distorted by
    # redistributing this year's shrunken live budget across whoever's left.
    full_pool = with_keepers.copy()
    n_before = len(full_pool)
    full_pool = data_pipeline.expand_pool_with_full_universe(full_pool, fp_rankings)
    full_pool = data_pipeline.merge_fp_tiers(full_pool, fp_rankings)
    n_added = len(full_pool) - n_before
    if fp_rankings is not None:
        print(
            f"\nWidened draftable pool with {n_added} players from "
            f"{args.fantasypros_rankings} who weren't on a 2025 league roster "
            "(no historical salary; unpriced until a projection is supplied)."
        )

    full_pool = merge_projections(full_pool, projections)
    full_pool = data_pipeline.fill_anchor_fallback(full_pool)
    n_flagged = full_pool["notes"].fillna("").str.contains("manual review").sum()
    n_imputed = full_pool["notes"].fillna("").str.contains("anchor imputed").sum()
    print(
        f"\nAnchor fallback (Priority 5): {n_imputed} players with no salary and no "
        f"projection got a position/tier-median imputed anchor; {n_flagged} had no "
        "comparable at all and are flagged 'manual review' in their notes."
    )

    if use_confirmed:
        # ONE eligibility decision for the veteran price sheet: run the real
        # auction_model.auction_eligibility classifier (nflverse debut /
        # college-rights aware) for the general pool, THEN force-exclude
        # every player named in the confirmed keeper file (both veteran
        # keepers and college-rights holds) as a hard override -- the
        # confirmed file is the higher-priority, commissioner-sourced
        # identity for these specific players regardless of what the
        # more general classifier concludes about them.
        eligibility_audit = auction_eligibility.build_eligibility_audit(full_pool, salaries, roster=with_keepers)
        full_pool = auction_eligibility.filter_veteran_auction_pool(full_pool, eligibility_audit, fail_on_ineligible=False)
        confirmed_excluded_names = set(confirmed_keepers_df["player_name"].map(normalize_name))
        full_pool_key = full_pool["player"].map(normalize_name)
        still_present = full_pool.loc[full_pool_key.isin(confirmed_excluded_names), "player"].tolist()
        full_pool = full_pool.loc[~full_pool_key.isin(confirmed_excluded_names)].reset_index(drop=True)
        audit_dir = BASE_DIR / "outputs" / "auction_rebuild" / "audit"
        eligibility_audit.to_csv(audit_dir / "run_valuation_eligibility_audit.csv", index=False)
        print(f"\nApplied auction_model.auction_eligibility to the veteran price-sheet pool "
              f"(wrote {audit_dir / 'run_valuation_eligibility_audit.csv'}); confirmed-file override "
              f"additionally force-excluded {len(confirmed_excluded_names)} named keepers/holds "
              f"({len(still_present)} of whom the general classifier alone would NOT have excluded).")

    priced, priced_hypothetical = valuation.price_live_and_hypothetical(full_pool, inflation, blend_weight)

    if use_confirmed:
        priced_key = priced["player"].map(normalize_name)
        leaked = priced.loc[priced_key.isin(confirmed_excluded_names), "player"].tolist()
        if leaked:
            raise ValueError(
                f"Confirmed mode: {len(leaked)} confirmed keeper(s)/college-rights hold(s) remain in "
                f"the priced auction pool after eligibility filtering -- refusing to publish a "
                f"contaminated price sheet: {leaked}"
            )

    report = valuation.run_sanity_checks(priced, inflation["remaining_budget"])
    print("\nSanity checks:")
    print(f"  Remaining budget to distribute: ${report['remaining_budget']}")
    print(f"  Total suggested prices sum to:  ${report['total_priced']}")
    print(f"  Within tolerance: {report['budget_within_tolerance']}")
    print(f"  Prices out of [$1, $100] range: {report['n_out_of_range']}")
    print(f"  Players with no data to price at all: {report['n_unpriced_no_data']}")
    print(f"  Players priced >2x or <0.5x their 2025 salary: {report['n_large_moves_vs_2025_salary']}")
    if len(report["large_moves"]):
        print(report["large_moves"].to_string(index=False))

    auction_cols = [
        "player", "position", "nfl_team", "projected_points", "VBD_score",
        "suggested_auction_price", "hypothetical_open_market_value", "salary_2025", "notes",
    ]
    priced_out = priced.copy()
    if "nfl_team" not in priced_out.columns:
        priced_out["nfl_team"] = ""
    else:
        priced_out["nfl_team"] = priced_out["nfl_team"].fillna("")
    priced_out = priced_out.rename(columns={"salary_2025": "historical_salary_if_known"})
    auction_cols = [c if c != "salary_2025" else "historical_salary_if_known" for c in auction_cols]
    priced_out["keeper_mode"] = args.keeper_mode
    auction_out_path = args.output_dir / "veteran_auction_price_sheet.csv"
    priced_out[auction_cols + ["keeper_mode"]].sort_values("suggested_auction_price", ascending=False, na_position="last").to_csv(
        auction_out_path, index=False
    )
    print(f"\nWrote {auction_out_path}")

    keeper_cols = ["team", "player", "position", "salary_2025", "tag_used", "on_ir", "keeper_price_2026", "keep_source"]
    kept_out = with_keepers[with_keepers["will_keep"]][keeper_cols].copy()
    kept_out["keeper_mode"] = args.keeper_mode
    kept_out_path = args.output_dir / "keepers_2026.csv"
    kept_out.to_csv(kept_out_path, index=False)
    print(f"Wrote {kept_out_path}")

    if use_confirmed:
        manifest = {
            "keeper_mode": "confirmed",
            "keepers_file": str(Path(args.keepers_file).resolve()),
            "budget_adjustments_file": str(Path(args.budget_adjustments_file).resolve()),
            "salaries_file": str(Path(args.salaries).resolve()),
            "fantasypros_rankings_file": str(Path(args.fantasypros_rankings).resolve()) if Path(args.fantasypros_rankings).exists() else None,
            "projections_file": str(Path(args.projections).resolve()) if Path(args.projections).exists() else None,
            "n_veteran_keepers": int(with_keepers["will_keep"].astype(bool).sum()),
            "n_college_rights_holds": int(len(confirmed_keepers_df)) - int(with_keepers["will_keep"].astype(bool).sum()),
            "n_teams": len(confirmed_state_rows),
            "sum_primary_auction_budget": round(sum(r["primary_auction_budget"] for r in confirmed_state_rows), 2),
            "sum_confirmed_keeper_spend": inflation["total_keeper_spend"],
            "outputs": {
                "veteran_auction_price_sheet": str(auction_out_path.resolve()),
                "keepers_2026": str(kept_out_path.resolve()),
                "team_starting_states": str((BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv").resolve()),
                "eligibility_audit": str((BASE_DIR / "outputs" / "auction_rebuild" / "audit" / "run_valuation_eligibility_audit.csv").resolve()),
            },
            "rerun_command": (
                f"python3 run_valuation.py --keeper-mode confirmed "
                f"--keepers-file {args.keepers_file} --budget-adjustments-file {args.budget_adjustments_file}"
            ),
        }
        manifest_dir = BASE_DIR / "outputs" / "auction_rebuild" / "audit"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "run_valuation_confirmed_input_manifest.json"
        import json
        manifest_path.write_text(json.dumps(manifest, indent=2))
        print(f"Wrote {manifest_path}")

    rookie_pool_df = data_pipeline.load_optional_csv(args.rookie_pool)
    board = rookie_board.build_rookie_board(rookie_pool_df)
    rookie_out_path = args.output_dir / "college_rookie_draft_board.csv"
    board.to_csv(rookie_out_path, index=False)
    print(f"Wrote {rookie_out_path} ({len(board)} rows)")
    if rookie_pool_df is None:
        print(
            f"  (no rookie pool data found at {args.rookie_pool} -- see "
            "data/rookie_pool.template.csv to supply prospect data)"
        )


if __name__ == "__main__":
    main()
