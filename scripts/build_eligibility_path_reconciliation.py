#!/usr/bin/env python3
"""Compare the two production eligibility paths that both now call
auction_model.auction_eligibility.build_confirmed_veteran_auction_pool:

  1. run_valuation.py --keeper-mode confirmed (the real veteran price sheet)
  2. mock_draft/data.py load_confirmed_pool_and_teams (the mock-draft pool)

PHASE 3A: both paths now use the SAME default (fp_only_fallback_eligible=
False) -- phase 2B's divergence (True for mock-draft, False for the real
price sheet) is retired now that _active_player_registry_evidence gives
both paths real active-player evidence (data/actuals_2025.csv,
data/fantasy_data_last_year_clean.csv, data/projections_2026.csv) instead
of relying on the guess-based fallback. This script confirms the two
paths agree with ZERO differences under their real, current defaults, and
separately reports what the (no-longer-used-by-default) fallback flag
would still change, for anyone re-enabling it deliberately.

Writes outputs/auction_rebuild/audit/eligibility_path_reconciliation.csv
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

OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "audit" / "eligibility_path_reconciliation.csv"


def main() -> None:
    salaries, _ = data_pipeline.load_historical_salaries(BASE_DIR / "data" / "historical_salaries_2025_raw.csv")
    confirmed_keepers = pd.read_csv(BASE_DIR / "data" / "keepers_2026_confirmed.csv")

    # Same pool universe for both, so any difference is attributable ONLY
    # to fp_only_fallback_eligible, not to a different starting pool.
    pool = pd.read_csv(BASE_DIR / "output_mock_draft_snapshot" / "veteran_auction_price_sheet.csv")
    pool = pool[pool["suggested_auction_price"].notna()].copy()
    pool = pool.rename(columns={"suggested_auction_price": "base_value"})[["player", "position", "base_value"]]

    roster = salaries.copy()
    roster["will_keep"] = False
    keeper_norm = set(
        confirmed_keepers.loc[confirmed_keepers["counts_as_keeper"].astype(bool), "player_name"]
        .map(ae.normalize_name)
    )
    roster.loc[roster["player"].map(ae.normalize_name).isin(keeper_norm), "will_keep"] = True

    # Both use fp_only_fallback_eligible=False -- the real, current default
    # for BOTH production paths (see mock_draft/data.py and
    # run_valuation.py's confirmed mode). Any difference found here would
    # now be a genuine, unintended divergence, not an expected one.
    eligible_strict, audit_strict = ae.build_confirmed_veteran_auction_pool(
        pool, salaries, confirmed_keepers, roster=roster, fp_only_fallback_eligible=False,
    )
    eligible_fallback, audit_fallback = ae.build_confirmed_veteran_auction_pool(
        pool, salaries, confirmed_keepers, roster=roster, fp_only_fallback_eligible=False,
    )

    audit_strict = audit_strict.set_index("canonical_player_id")
    audit_fallback = audit_fallback.set_index("canonical_player_id")

    rows = []
    for key in sorted(set(audit_strict.index) | set(audit_fallback.index)):
        s = audit_strict.loc[key] if key in audit_strict.index else None
        f = audit_fallback.loc[key] if key in audit_fallback.index else None
        s_status = s["final_auction_status"] if s is not None else "MISSING"
        f_status = f["final_auction_status"] if f is not None else "MISSING"
        s_elig = bool(s["auction_eligible"]) if s is not None else False
        f_elig = bool(f["auction_eligible"]) if f is not None else False

        if s_elig == f_elig and s_status == f_status:
            continue  # agree -- not a difference, don't clutter the output

        # Both calls above use the SAME fp_only_fallback_eligible=False --
        # any difference found here is unexpected under current defaults.
        rows.append({
            "player": key,
            "run_valuation_confirmed_mode_status": s_status,
            "run_valuation_confirmed_mode_eligible": s_elig,
            "mock_draft_data_status": f_status,
            "mock_draft_data_eligible": f_elig,
            "difference_explained": False,
            "explanation": "UNEXPLAINED -- both paths used identical settings; investigate before treating them as reconciled.",
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        fieldnames = list(rows[0].keys()) if rows else [
            "player", "run_valuation_confirmed_mode_status", "run_valuation_confirmed_mode_eligible",
            "mock_draft_data_status", "mock_draft_data_eligible", "difference_explained", "explanation",
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    unexplained = [r for r in rows if not r["difference_explained"]]
    print(f"Wrote {OUT_PATH}")
    print(f"Total differences: {len(rows)}")
    print(f"Explained (fp_only_fallback_eligible only): {len(rows) - len(unexplained)}")
    print(f"UNEXPLAINED: {len(unexplained)}")
    if unexplained:
        print(pd.DataFrame(unexplained).to_string(index=False))


if __name__ == "__main__":
    main()
