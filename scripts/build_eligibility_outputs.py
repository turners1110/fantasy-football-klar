#!/usr/bin/env python3
"""Write outputs/auction_rebuild/data/{auction_eligible_players,excluded_players_audit}.csv
from the confirmed-keeper pool loader (mock_draft/data.py:load_confirmed_pool_and_teams).
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from mock_draft.data import load_confirmed_pool_and_teams
OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "data"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    players, teams, meta = load_confirmed_pool_and_teams(budget_scenario="primary")

    eligible_rows = [
        {"player": p.name, "position": p.position, "base_value": p.base_value,
         "projected_points": p.projected_points, "points_is_real": p.points_is_real,
         "auction_eligible": True, "eligibility_reason": "not a confirmed keeper or college-rights hold"}
        for p in players.values()
    ]
    eligible_path = OUT_DIR / "auction_eligible_players.csv"
    pd.DataFrame(eligible_rows).to_csv(eligible_path, index=False)
    print(f"Wrote {eligible_path} ({len(eligible_rows)} players)")

    confirmed = pd.read_csv(BASE_DIR / "data" / "keepers_2026_confirmed.csv")
    excluded_rows = []
    for _, row in confirmed.iterrows():
        excluded_rows.append({
            "player": row["player_name"], "team": row["team_id"], "position": row["position"],
            "exclusion_reason": row["keeper_status"],
            "counts_as_keeper": row["counts_as_keeper"],
            "keeper_cost": row["keeper_cost"], "source": row["source"],
        })
    excluded_path = OUT_DIR / "excluded_players_audit.csv"
    pd.DataFrame(excluded_rows).to_csv(excluded_path, index=False)
    print(f"Wrote {excluded_path} ({len(excluded_rows)} excluded players)")

    print(f"\nSanity: {meta['excluded_count']} excluded from pool "
          f"({len(confirmed)} in confirmed file) -- {'MATCH' if meta['excluded_count'] == len(confirmed) else 'MISMATCH'}")


if __name__ == "__main__":
    main()
