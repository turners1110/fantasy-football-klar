#!/usr/bin/env python3
"""Sunday Final Build Stage 7: rigorous old-vs-evolved distribution
validation. Does NOT soften the rejection criteria -- rejects the
evolved prior if it fails any of the spec's stated conditions."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from mock_draft.data import load_confirmed_pool_and_teams

SUNDAY_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "sunday_final"
EVO_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "evolution_sunday"

FOCUS_PLAYERS = {"Josh Allen", "Rashee Rice", "Terry McLaurin", "George Kittle",
                 "Travis Etienne", "DeVonta Smith", "Mark Andrews"}


def log(msg):
    print(f"[validation] {msg}", flush=True)


def main():
    dist = pd.read_csv(SUNDAY_DIR / "evolved_price_distributions.csv")
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    old_price = {name: max(1.0, p.base_value) for name, p in players.items()}
    position = {name: p.position for name, p in players.items()}

    reject_reasons = []
    checks = {}

    # ---- MARKET-LEVEL CHECKS ----
    val_summary = json.load(open(EVO_DIR / "run_manifest.json"))
    checks["validation_legal_roster_rate"] = val_summary["validation_summary"]["legal_roster_rate"]
    checks["held_out_legal_roster_rate"] = val_summary["held_out_summary"]["legal_roster_rate"]
    if checks["validation_legal_roster_rate"] < 0.99 or checks["held_out_legal_roster_rate"] < 0.99:
        reject_reasons.append(f"legal roster rate below 0.99 (val={checks['validation_legal_roster_rate']}, "
                              f"held_out={checks['held_out_legal_roster_rate']})")

    # Degenerate-price rate: P10 == P90 for Field C (sufficient-sample players only)
    dist_c = dist[~dist["field_c_insufficient_sales"]]
    degenerate = dist_c[(dist_c["field_c_p90"] - dist_c["field_c_p10"]).abs() < 0.01]
    checks["field_c_degenerate_price_rate"] = round(len(degenerate) / max(1, len(dist_c)), 4)
    if checks["field_c_degenerate_price_rate"] > 0.10:
        reject_reasons.append(f"degenerate-price rate too high: {checks['field_c_degenerate_price_rate']:.1%} of players "
                              f"with sufficient sales have P10==P90")

    # Extreme prices: Field C mean > 3x old static price -- flag and require independent support
    dist["old_price"] = dist["player"].map(old_price)
    dist["position"] = dist["player"].map(position)
    extreme = dist[(dist["field_c_mean"].notna()) & (dist["old_price"].notna()) &
                   (dist["field_c_mean"] > 3.0 * dist["old_price"]) & (dist["old_price"] > 5)]
    checks["extreme_price_count"] = len(extreme)
    checks["extreme_price_players"] = extreme["player"].tolist()[:10]
    if len(extreme) > 5:
        reject_reasons.append(f"{len(extreme)} players show Field C mean price >3x the old static price "
                              f"with no independent support check available this pass: {extreme['player'].tolist()[:10]}")

    # Position spending shares: Field A vs Field C (using field means as a spend proxy)
    def position_share(df, col):
        totals = df.groupby("position")[col].sum()
        total = totals.sum()
        return (totals / total).to_dict() if total else {}

    dist_valid_a = dist.dropna(subset=["field_a_mean"])
    dist_valid_c = dist.dropna(subset=["field_c_mean"])
    share_a = position_share(dist_valid_a, "field_a_mean")
    share_c = position_share(dist_valid_c, "field_c_mean")
    checks["position_share_field_a"] = {k: round(v, 3) for k, v in share_a.items()}
    checks["position_share_field_c"] = {k: round(v, 3) for k, v in share_c.items()}
    max_share_shift = max(abs(share_a.get(p, 0) - share_c.get(p, 0)) for p in ("QB", "RB", "WR", "TE"))
    checks["max_position_share_shift"] = round(max_share_shift, 3)
    if max_share_shift > 0.15:
        reject_reasons.append(f"position spending share shifted by {max_share_shift:.1%} between Field A and Field C "
                              f"(baseline vs blended-evolved) -- less realistic position spending, exceeds 15pp tolerance")

    # Extra-QB exploitation check: QB share should not spike in the evolved fields relative to fixed
    qb_share_shift = share_c.get("QB", 0) - share_a.get("QB", 0)
    checks["qb_share_shift"] = round(qb_share_shift, 3)
    if qb_share_shift > 0.08:
        reject_reasons.append(f"QB spending share rose by {qb_share_shift:.1%} in the evolved/blended field vs "
                              f"the fixed-archetype field -- possible extra-QB exploitation pattern")

    # Stability across seed groups: compare Field B (pure evolved) vs Field C (blended) means correlation
    both = dist.dropna(subset=["field_b_mean", "field_c_mean"])
    if len(both) > 10:
        corr = float(np.corrcoef(both["field_b_mean"], both["field_c_mean"])[0, 1])
    else:
        corr = None
    checks["field_b_vs_field_c_correlation"] = round(corr, 4) if corr is not None else None
    if corr is not None and corr < 0.5:
        reject_reasons.append(f"Field B and Field C player prices correlate only {corr:.2f} -- unstable values "
                              f"across seed groups / field compositions")

    # ---- CIRCULARITY DISCLOSURE (explicit, not skipped) ----
    circularity_note = (
        "The 'old' static price used throughout this validation is base_value (auction_model's "
        "projection/anchor-derived suggested_auction_price). Per Phase 3E/3G's own findings, base_value "
        "for SOME players (e.g. Austin Ekeler, historically) can itself be shaped by a circular anchor "
        "chain independent of any real market observation. This validation compares evolved output "
        "AGAINST that same base_value as the 'old' reference -- it is NOT an independent, uncontaminated "
        "anchor. This is disclosed explicitly rather than presented as a clean external check. Where a "
        "player's old base_value is itself suspect (see Phase 3G's unsupported_extreme_price_audit.csv), "
        "an apparent 'large change' in this validation could reflect a bad OLD number, not a bad new one -- "
        "this validation does not attempt to disentangle that this pass; it flags large deltas either way."
    )

    # ---- SAM-SPECIFIC BOARD (focus players) ----
    sam_rows = []
    for name in sorted(FOCUS_PLAYERS):
        row = dist[dist["player"] == name]
        if row.empty:
            sam_rows.append({"player": name, "note": "not found in distribution output"})
            continue
        r = row.iloc[0]
        old_p50 = old_price.get(name)
        new_p50 = r["field_c_p50"]
        pct_change = ((new_p50 - old_p50) / old_p50 * 100) if (old_p50 and new_p50 is not None and not pd.isna(new_p50)) else None
        sam_rows.append({
            "player": name, "old_p50_static": round(old_p50, 2) if old_p50 else None,
            "new_p50_field_c": round(new_p50, 2) if new_p50 is not None and not pd.isna(new_p50) else None,
            "pct_change": round(pct_change, 1) if pct_change is not None else None,
            "large_unexplained_change": abs(pct_change) > 30 if pct_change is not None else None,
            "field_c_draft_probability": r["draft_probability_field_c"],
        })
    large_changes = [r for r in sam_rows if r.get("large_unexplained_change")]
    checks["sam_focus_large_changes"] = [r["player"] for r in large_changes]
    if len(large_changes) > 3:
        reject_reasons.append(f"{len(large_changes)} of {len(FOCUS_PLAYERS)} Sam-focus players show >30% unexplained "
                              f"price change: {[r['player'] for r in large_changes]}")

    with (SUNDAY_DIR / "sam_old_vs_evolved_board.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(sam_rows[0].keys()))
        w.writeheader(); w.writerows(sam_rows)
    log(f"Wrote sam_old_vs_evolved_board.csv ({len(sam_rows)} focus players)")

    # ---- market metrics + player price change files ----
    market_metrics_rows = [
        {"metric": "validation_legal_roster_rate", "value": checks["validation_legal_roster_rate"]},
        {"metric": "held_out_legal_roster_rate", "value": checks["held_out_legal_roster_rate"]},
        {"metric": "field_c_degenerate_price_rate", "value": checks["field_c_degenerate_price_rate"]},
        {"metric": "extreme_price_count", "value": checks["extreme_price_count"]},
        {"metric": "max_position_share_shift", "value": checks["max_position_share_shift"]},
        {"metric": "qb_share_shift", "value": checks["qb_share_shift"]},
        {"metric": "field_b_vs_field_c_correlation", "value": checks["field_b_vs_field_c_correlation"]},
        {"metric": "position_share_field_a_QB", "value": share_a.get("QB")},
        {"metric": "position_share_field_a_RB", "value": share_a.get("RB")},
        {"metric": "position_share_field_a_WR", "value": share_a.get("WR")},
        {"metric": "position_share_field_a_TE", "value": share_a.get("TE")},
        {"metric": "position_share_field_c_QB", "value": share_c.get("QB")},
        {"metric": "position_share_field_c_RB", "value": share_c.get("RB")},
        {"metric": "position_share_field_c_WR", "value": share_c.get("WR")},
        {"metric": "position_share_field_c_TE", "value": share_c.get("TE")},
    ]
    with (SUNDAY_DIR / "old_vs_evolved_market_metrics.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["metric", "value"])
        w.writeheader(); w.writerows(market_metrics_rows)

    dist["price_change_pct"] = ((dist["field_c_mean"] - dist["old_price"]) / dist["old_price"] * 100).round(1)
    dist["large_move"] = dist["price_change_pct"].abs() > 30
    top25 = dist.dropna(subset=["field_c_mean"]).sort_values("field_c_mean", ascending=False).head(25)
    player_price_rows = dist[["player", "position", "old_price", "field_c_mean", "field_c_p50", "price_change_pct", "large_move"]].copy()
    player_price_rows.to_csv(SUNDAY_DIR / "old_vs_evolved_player_prices.csv", index=False)
    log(f"Wrote old_vs_evolved_player_prices.csv ({len(player_price_rows)} players)")
    log(f"Top 25 by Field C mean price: {top25['player'].tolist()[:10]}...")

    # ---- FINAL VERDICT ----
    passed = len(reject_reasons) == 0
    result = {
        "PRODUCTION_DISTRIBUTION_PASS": passed,
        "checks": checks,
        "reject_reasons": reject_reasons,
        "circularity_disclosure": circularity_note,
        "decision": "ACCEPT evolved blended (Field C) distribution as EVOLVED_ENSEMBLE_MARKET_PRIOR" if passed else
                    "REJECT evolved distribution -- retain STATIC_PRE_DRAFT_MARKET_PRIOR",
    }
    (EVO_DIR / "validation_verdict.json").write_text(json.dumps(result, indent=2))
    log(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    main()
