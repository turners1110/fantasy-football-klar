#!/usr/bin/env python3
"""V2 Part 1: authoritative league roster / player-pool audit.

Reuses mock_draft.data.load_confirmed_pool_and_teams (the same loader
AuctionCLI already uses) -- no new state or eligibility logic, just a
reporting pass over what's already authoritative."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from mock_draft.data import load_confirmed_pool_and_teams

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "live_web_v2"
KEEPERS_PATH = BASE_DIR / "data" / "keepers_2026_confirmed.csv"
COLLEGE_RIGHTS = {"Fernando Mendoza", "Isaiah Bond"}


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    players, teams, meta = load_confirmed_pool_and_teams(budget_scenario="primary")
    confirmed = pd.read_csv(KEEPERS_PATH)

    # ---- team_start_audit.csv ----
    team_rows = []
    total_keepers = 0
    total_open_slots = 0
    for team_name, t in teams.items():
        team_keepers = confirmed[(confirmed["team_id"] == team_name) & (confirmed["counts_as_keeper"].astype(bool))]
        college = confirmed[(confirmed["team_id"] == team_name) & (confirmed["keeper_status"] == "COLLEGE_RIGHTS_HOLD")]
        keeper_names = "; ".join(f"{r['player_name']} ({r['position']}) ${r['keeper_cost']:.0f}" for _, r in team_keepers.iterrows())
        pos_counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
        for n, pos, price, pts in t.roster:
            pos_counts[pos] = pos_counts.get(pos, 0) + 1
        open_slots = 15 - len(t.roster)
        total_keepers += len(t.roster)
        total_open_slots += open_slots
        team_rows.append({
            "team": team_name, "keeper_count": len(t.roster), "keeper_names_positions_prices": keeper_names,
            "keeper_spend": round(sum(p for _, _, p, _ in t.roster), 2),
            "reported_auction_budget": t.budget_remaining, "formula_auction_budget": 400 - sum(p for _, _, p, _ in t.roster),
            "confirmed_cash_adjustments": t.budget_remaining - (400 - sum(p for _, _, p, _ in t.roster)),
            "open_roster_slots": open_slots, "QB_count": pos_counts["QB"], "RB_count": pos_counts["RB"],
            "WR_count": pos_counts["WR"], "TE_count": pos_counts["TE"],
            "college_rights_players": "; ".join(college["player_name"].tolist()) if not college.empty else "",
            "confirmation_status": "CONFIRMED" if not team_keepers.empty else "NO_KEEPERS_ON_FILE",
            "source": "data/keepers_2026_confirmed.csv", "unresolved_conflicts": "",
        })
    with (OUT_DIR / "team_start_audit.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(team_rows[0].keys()))
        w.writeheader(); w.writerows(team_rows)
    print(f"Wrote team_start_audit.csv ({len(team_rows)} teams)")

    # ---- master_player_status.csv ----
    player_rows = []
    seen_lower = {}
    duplicates = 0
    for _, row in confirmed.iterrows():
        status = "VETERAN_KEEPER" if row["counts_as_keeper"] else (
            "COLLEGE_RIGHTS_HELD" if row["keeper_status"] == "COLLEGE_RIGHTS_HOLD" else "UNKNOWN_REVIEW_REQUIRED")
        key = str(row["player_name"]).lower().strip()
        dup = key in seen_lower
        if dup:
            duplicates += 1
        seen_lower[key] = row["team_id"]
        player_rows.append({
            "canonical_player_id": row["player_name"], "display_name": row["player_name"],
            "normalized_name": key, "nfl_team": "", "position": row["position"],
            "2026_keeper_owner": row["team_id"] if row["counts_as_keeper"] else "",
            "keeper_price": row["keeper_cost"] if row["counts_as_keeper"] else "",
            "college_rights_owner": row["team_id"] if row["keeper_status"] == "COLLEGE_RIGHTS_HOLD" else "",
            "auction_eligibility": "EXCLUDED", "eligibility_reason": status,
            "source_file": "data/keepers_2026_confirmed.csv", "conflict_status": "NONE",
            "duplicate_status": "DUPLICATE_IDENTITY_REVIEW" if dup else "UNIQUE",
        })
    eligible_count = 0
    for name, p in players.items():
        key = name.lower().strip()
        dup = key in seen_lower
        if dup:
            duplicates += 1
        seen_lower[key] = "veteran_pool"
        eligible_count += 1
        player_rows.append({
            "canonical_player_id": name, "display_name": name, "normalized_name": key, "nfl_team": "",
            "position": p.position, "2026_keeper_owner": "", "keeper_price": "", "college_rights_owner": "",
            "auction_eligibility": "ELIGIBLE", "eligibility_reason": "VETERAN_AUCTION_ELIGIBLE",
            "source_file": "mock_draft.data.load_confirmed_pool_and_teams", "conflict_status": "NONE",
            "duplicate_status": "DUPLICATE_IDENTITY_REVIEW" if dup else "UNIQUE",
        })
    with (OUT_DIR / "master_player_status.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(player_rows[0].keys()))
        w.writeheader(); w.writerows(player_rows)
    print(f"Wrote master_player_status.csv ({len(player_rows)} rows)")

    # ---- reconciliation ----
    total_keeper_confirmed = int(confirmed["counts_as_keeper"].sum())
    total_college_rights = int((confirmed["keeper_status"] == "COLLEGE_RIGHTS_HOLD").sum())
    required_auction_purchases = 12 * 15 - total_keeper_confirmed
    reconciliation = {
        "total_known_players": len(player_rows),
        "total_confirmed_keepers": total_keeper_confirmed,
        "total_college_rights_players": total_college_rights,
        "total_veteran_auction_eligible": eligible_count,
        "total_duplicate_rows": duplicates,
        "open_roster_spots_leaguewide": total_open_slots,
        "required_auction_purchases_leaguewide": required_auction_purchases,
        "leaguewide_slot_check_180": total_keeper_confirmed + total_open_slots,
        "reconciles": (total_keeper_confirmed + total_open_slots) == 180,
        "no_keeper_in_veteran_pool": len(COLLEGE_RIGHTS & set(players.keys())) == 0 and
                                      len(set(confirmed[confirmed["counts_as_keeper"]]["player_name"]) & set(players.keys())) == 0,
        "no_college_rights_in_veteran_pool": len(COLLEGE_RIGHTS & set(players.keys())) == 0,
    }
    (OUT_DIR / "player_pool_reconciliation.json").write_text(json.dumps(reconciliation, indent=2))
    print(json.dumps(reconciliation, indent=2))


if __name__ == "__main__":
    main()
