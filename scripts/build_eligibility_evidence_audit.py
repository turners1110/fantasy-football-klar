#!/usr/bin/env python3
"""Phase 3A item 3: for every INCLUDED player in the confirmed-mode
veteran auction pool, record the evidence that made them eligible --
which registry matched, and whether a projection/prior-season-stats
value was present or missing (an explicit missing-value warning, not a
silent gap).

Writes outputs/auction_rebuild/phase3a/eligibility_evidence_audit.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from auction_model import auction_eligibility as ae
from auction_model import data_pipeline

OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "eligibility_evidence_audit.csv"


def main() -> None:
    salaries, _ = data_pipeline.load_historical_salaries(BASE_DIR / "data" / "historical_salaries_2025_raw.csv")
    confirmed_keepers = pd.read_csv(BASE_DIR / "data" / "keepers_2026_confirmed.csv")

    pool = pd.read_csv(BASE_DIR / "output_mock_draft_snapshot" / "veteran_auction_price_sheet.csv")
    has_projection = set(pool.loc[pool["suggested_auction_price"].notna(), "player"])
    pool = pool[pool["suggested_auction_price"].notna()].copy()
    pool = pool.rename(columns={"suggested_auction_price": "base_value"})[["player", "position", "base_value"]]

    roster = salaries.copy()
    roster["will_keep"] = False
    keeper_norm = set(
        confirmed_keepers.loc[confirmed_keepers["counts_as_keeper"].astype(bool), "player_name"]
        .map(ae.normalize_name)
    )
    roster.loc[roster["player"].map(ae.normalize_name).isin(keeper_norm), "will_keep"] = True

    eligible, audit = ae.build_confirmed_veteran_auction_pool(
        pool, salaries, confirmed_keepers, roster=roster, fp_only_fallback_eligible=False,
    )

    registry_evidence = ae._active_player_registry_evidence(BASE_DIR, BASE_DIR / "data" / "nflverse")

    rows = []
    for _, row in eligible.iterrows():
        key = ae.data_pipeline._normalize_name(row["player"])
        evidence = registry_evidence.get(key)
        has_proj = row["player"] in has_projection
        rows.append({
            "player": row["player"],
            "position": row["position"],
            "eligibility_status": row["eligibility_status"],
            "eligibility_reason": row["eligibility_reason"],
            "eligibility_source": row["eligibility_source"],
            "active_player_evidence_source": evidence.get("evidence_source", "") if evidence else "NONE",
            "games_played_evidence": evidence.get("games_played", "") if evidence else "",
            "has_projection": has_proj,
            "missing_projection_warning": not has_proj,
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    n_missing_proj = sum(1 for r in rows if r["missing_projection_warning"])
    n_missing_stats_but_active = sum(
        1 for r in rows if r["games_played_evidence"] in (0, "0") and r["active_player_evidence_source"] != "NONE"
    )
    print(f"Wrote {OUT_PATH} ({len(rows)} included players)")
    print(f"  Missing projection (included anyway, warning flagged): {n_missing_proj}")
    print(f"  Missing prior-season stats but included via active-player evidence "
          f"(e.g. current 2026 projection only): {n_missing_stats_but_active}")
    print(f"  Evidence source breakdown:")
    print(pd.DataFrame(rows)["active_player_evidence_source"].value_counts(dropna=False).to_string())


if __name__ == "__main__":
    main()
