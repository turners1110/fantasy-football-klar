#!/usr/bin/env python3
"""Compute each team's confirmed starting state (keepers + budget) and
write the audit trail required by the auction-rebuild spec: source
conflicts (never silently merged) and player-identity issues.

Reads data/keepers_2026_confirmed.csv + data/team_budget_adjustments_2026.csv
(tracked inputs). Writes:
  outputs/auction_rebuild/data/team_starting_states.csv
  outputs/auction_rebuild/audit/keeper_source_conflicts.csv
  outputs/auction_rebuild/audit/keeper_identity_issues.csv

The actual keeper/budget arithmetic lives in
auction_model/confirmed_keeper_pipeline.py, shared with run_valuation.py's
--keeper-mode confirmed so there is exactly one authoritative computation.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from auction_model.confirmed_keeper_pipeline import (  # noqa: E402
    compute_identity_issues, compute_team_states,
)

DATA_DIR = BASE_DIR / "data"
AUDIT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "audit"
OUT_DATA_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "data"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
OUT_DATA_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    keepers = pd.read_csv(DATA_DIR / "keepers_2026_confirmed.csv")
    adjustments = pd.read_csv(DATA_DIR / "team_budget_adjustments_2026.csv")

    identity_rows = compute_identity_issues(keepers)
    identity_path = AUDIT_DIR / "keeper_identity_issues.csv"
    with identity_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["issue_type", "player_name", "normalized", "team", "detail"])
        w.writeheader()
        w.writerows(identity_rows)
    print(f"Wrote {identity_path} ({len(identity_rows)} rows)")

    state_rows, conflict_rows = compute_team_states(keepers, adjustments)

    conflicts_path = AUDIT_DIR / "keeper_source_conflicts.csv"
    if conflict_rows:
        with conflicts_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(conflict_rows[0].keys()))
            w.writeheader()
            w.writerows(conflict_rows)
    else:
        conflicts_path.write_text("team,field,winning_source,winning_value,losing_source,losing_value,detail\n")
    print(f"Wrote {conflicts_path} ({len(conflict_rows)} conflicts logged)")

    states_path = OUT_DATA_DIR / "team_starting_states.csv"
    with states_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(state_rows[0].keys()))
        w.writeheader()
        w.writerows(state_rows)
    print(f"Wrote {states_path} ({len(state_rows)} teams)")

    # --- required assertions -------------------------------------------
    sam = next(r for r in state_rows if r["team_id"] == "Sam")
    assert sam["n_veteran_keepers"] == 6, sam
    assert sam["keeper_spend"] == 162, sam
    assert sam["primary_auction_budget"] == 223, sam
    assert sam["conversions_scenario_auction_budget"] == 221, sam
    walker = keepers[keepers["player_name"] == "Kenneth Walker III"].iloc[0]
    assert walker["keeper_cost"] == 36 and walker["franchise_tag"], walker
    skattebo = keepers[keepers["player_name"] == "Cam Skattebo"].iloc[0]
    assert skattebo["keeper_cost"] == 28, skattebo
    for team, group in keepers.groupby("team_name"):
        assert int(group["counts_as_keeper"].sum()) <= 6, f"{team} exceeds 6 veteran keepers"
    dupes = [r for r in identity_rows if r["issue_type"] == "DUPLICATE_NORMALIZED_NAME"]
    assert len(dupes) == 0  # already logged above if present, not silently ignored -- this asserts there are none
    print("\nAll required assertions PASSED: Sam 6 keepers / $162 spend / $223 primary / $221 conversions; "
          "Walker $36 tag; Skattebo $28; no team exceeds 6 veteran keepers.")


if __name__ == "__main__":
    main()
