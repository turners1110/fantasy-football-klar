#!/usr/bin/env python3
"""Build data/keepers_2026_confirmed.csv and data/team_budget_adjustments_2026.csv
from the "2026 Fancy Football League Rosters" Google Sheet (highest-priority
source per the auction-rebuild spec: newer commissioner/final league file),
cross-checked against Sam's explicitly user-confirmed values.

Source: Google Drive file id 1ZE5I2CAFDSU5_dPcehAE_mjgbnxecgi3NGyxGa47H6Q,
"2026 Fancy Football League Rosters", modified 2026-08-31T16:01:54Z (fetched
and hand-transcribed into the structures below -- there is no API access to
re-fetch this programmatically from inside this script, so re-running this
script does NOT re-pull the sheet; edit the data below if the sheet changes).

Tag inference method: this league's rules state standard keeper increase is
+$10, franchise tag increase is +$5. Every player below has an explicit
(prior_salary, keeper_cost) pair; tag status is inferred from the delta
(+5 => tag, +10 => standard) rather than from cell color, which is not
preserved in the plain-text export. Cross-validated: every team's total
keeper spend computed this way reconciles EXACTLY to the sheet's own stated
per-team total (shown as a comment per team below), and Sam's tag (Kenneth
Walker III) matches the user's explicit direct statement independently.
"""

from __future__ import annotations

import csv
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
SOURCE = "google_sheet:2026_Fancy_Football_League_Rosters"
SOURCE_DATE = "2026-08-31"

# (team, player, position, prior_salary, keeper_cost) -- college-rights holds
# (no veteran salary) use keeper_cost=None and counts_as_keeper=False.
# Position looked up from data/historical_salaries_2025_raw.csv / common
# knowledge where the sheet itself doesn't list it.
TEAMS: dict[str, list[tuple[str, str, float | None, float | None]]] = {
    "Brandon": [  # sheet total $221
        ("Jonathan Taylor", "RB", 41, 46),
        ("Justin Jefferson", "WR", 36, 46),
        ("Nico Collins", "WR", 26, 36),
        ("Breece Hall", "RB", 21, 31),
        ("Brock Bowers", "TE", 6, 16),
        ("Christian McCaffrey", "RB", 36, 46),
    ],
    "Coby": [  # sheet total $126
        ("Derrick Henry", "RB", 41, 51),
        ("Kyren Williams", "RB", 21, 31),
        ("Brian Thomas Jr", "WR", 6, 16),
        ("Harold Fannin Jr", "TE", 1, 6),
        ("TreVeyon Henderson", "RB", 1, 11),
        ("Quinshon Judkins", "RB", 1, 11),
    ],
    "Brad": [  # sheet total $119
        ("Alec Pierce", "WR", 1, 11),
        ("Jaxon Smith-Njigba", "WR", 21, 31),
        ("Jaydon Blue", "RB", 4, 14),
        ("Jameson Williams", "WR", 31, 41),
        ("Marvin Harrison Jr", "WR", 6, 16),
        ("Tyler Warren", "TE", 1, 6),
    ],
    "Reid": [  # sheet total $150
        ("Kyle Pitts Sr", "TE", 6, 11),
        ("Tucker Kraft", "TE", 11, 21),
        ("Drake Maye", "QB", 20, 30),
        ("Chris Olave", "WR", 31, 41),
        ("Jalen Coker", "WR", 3, 13),
        ("Javonte Williams", "RB", 24, 34),
    ],
    "Evan": [  # sheet total $206
        ("Amon-Ra St Brown", "WR", 36, 41),
        ("Drake London", "WR", 26, 36),
        ("Saquon Barkley", "RB", 36, 46),
        ("George Pickens", "WR", 31, 41),
        ("Puka Nacua", "WR", 21, 31),
        ("Emeka Egbuka", "WR", 1, 11),
    ],
    "Sam": [  # sheet total $162 -- matches user-confirmed value exactly
        ("Garrett Wilson", "WR", 21, 31),
        ("Kenneth Walker III", "RB", 31, 36),   # +5 = franchise tag, user-confirmed
        ("Quentin Johnston", "WR", 1, 11),
        ("David Montgomery", "RB", 35, 45),
        ("Cam Skattebo", "RB", 18, 28),
        ("Jaxson Dart", "QB", 1, 11),
        # College-rights holds -- NOT veteran keepers, NOT auction eligible
        # until converted. User-confirmed explicitly.
        ("Isaiah Bond", "WR", None, None),
        ("Fernando Mendoza", "QB", None, None),
    ],
    "James": [  # sheet total $103
        ("Wan'Dale Robinson", "WR", 1, 11),
        ("Trevor Lawrence", "QB", 1, 11),
        ("Jordan Addison", "WR", 21, 31),
        ("Zay Flowers", "WR", 21, 31),
        ("Colston Loveland", "TE", 1, 6),
        ("Rico Dowdle", "RB", 3, 13),
    ],
    "Ryan J": [  # sheet total $136
        ("Brock Purdy", "QB", 5, 15),
        ("Michael Wilson", "WR", 1, 11),
        ("Oronde Gadsden", "TE", 1, 11),
        ("DeVon Achane", "RB", 16, 26),
        ("Ashton Jeanty", "RB", 1, 6),
        ("Ladd McConkey", "WR", 57, 67),
    ],
    "Jason": [  # sheet total $186
        ("CeeDee Lamb", "WR", 36, 46),
        ("Trey McBride", "TE", 21, 31),
        ("AJ Brown", "WR", 36, 46),
        ("Bijan Robinson", "RB", 16, 21),
        ("Jahmyr Gibbs", "RB", 21, 31),
        ("Luther Burden", "WR", 1, 11),
    ],
    "Travis": [  # sheet total $113 -- confirmed COMPLETE (6 priced keepers), contrary to prior assumption
        ("Jayden Reed", "WR", 16, 26),
        ("Malik Nabers", "WR", 6, 16),
        ("Woody Marks", "RB", 1, 11),
        ("Rhamondre Stevenson", "RB", 32, 42),
        ("Hunter Henry", "TE", 2, 12),
        ("Tetairoa McMillan", "WR", 1, 6),
        # College-rights holds -- NOT veteran keepers
        ("Denzel Boston", "WR", None, None),
        ("Ja'Kobi Lane", "WR", None, None),
    ],
    "CJ": [  # sheet total $136
        ("Tory Horton", "WR", 1, 11),
        ("Ja'Marr Chase", "WR", 36, 46),
        ("Chase Brown", "RB", 21, 31),
        ("Sam LaPorta", "TE", 21, 31),
        ("Parker Washington", "WR", 1, 11),
        ("Omarion Hampton", "RB", 1, 6),
    ],
    "Shane": [  # sheet total $76
        ("MarShawn Lloyd", "RB", 1, 11),
        ("Rome Odunze", "WR", 6, 16),
        ("Caleb Williams", "QB", 11, 16),
        ("Blake Corum", "RB", 1, 11),
        ("Kyle Monangai", "RB", 1, 11),
        ("Christian Watson", "WR", 1, 11),
        # College-rights hold -- NOT a veteran keeper
        ("Jadarian Price", "RB", None, None),
    ],
}

EXPECTED_TOTALS = {
    "Brandon": 221, "Coby": 126, "Brad": 119, "Reid": 150, "Evan": 206,
    "Sam": 162, "James": 103, "Ryan J": 136, "Jason": 186, "Travis": 113,
    "CJ": 136, "Shane": 76,
}


def main() -> None:
    keeper_rows = []
    for team, players in TEAMS.items():
        computed_total = sum(cost for _n, _p, _s, cost in players if cost is not None)
        expected = EXPECTED_TOTALS[team]
        status = "OK" if computed_total == expected else f"MISMATCH (computed {computed_total}, sheet {expected})"
        print(f"{team:<8} computed keeper spend: {computed_total:>4}  vs sheet total {expected:>4}  [{status}]")
        assert computed_total == expected, f"{team}: keeper spend mismatch"

        for player, pos, prior_salary, keeper_cost in players:
            is_hold = keeper_cost is None
            franchise_tag = (not is_hold) and (keeper_cost - prior_salary == 5)
            standard = (not is_hold) and (keeper_cost - prior_salary == 10)
            if not is_hold and not (franchise_tag or standard):
                raise ValueError(f"{team}/{player}: unexpected delta {keeper_cost - prior_salary}")
            keeper_rows.append({
                "season": 2026,
                "team_id": team,
                "team_name": team,
                "player_id": "",  # no stable ID source available -- see keeper_identity_issues.csv
                "player_name": player,
                "position": pos,
                "prior_salary": "" if prior_salary is None else prior_salary,
                "keeper_cost": "" if keeper_cost is None else keeper_cost,
                "franchise_tag": franchise_tag,
                "keeper_status": "COLLEGE_RIGHTS_HOLD" if is_hold else "CONFIRMED_KEEPER",
                "counts_as_keeper": not is_hold,
                "counts_as_active_roster": not is_hold,
                "auction_eligible": False,  # keepers AND college-rights holds are both excluded from the veteran auction
                "source": SOURCE,
                "source_date": SOURCE_DATE,
                "confidence": "HIGH" if team == "Sam" else "HIGH",
                "notes": (
                    "User-confirmed directly in addition to sheet" if team == "Sam"
                    else ("Sheet total reconciles exactly; tag inferred from +$5 delta" if not is_hold else "College-rights hold, n/a salary in sheet")
                ),
            })

    out_path = BASE_DIR / "data" / "keepers_2026_confirmed.csv"
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(keeper_rows[0].keys()))
        writer.writeheader()
        writer.writerows(keeper_rows)
    print(f"\nWrote {out_path} ({len(keeper_rows)} rows, {sum(1 for r in keeper_rows if r['counts_as_keeper'])} veteran keepers, "
          f"{sum(1 for r in keeper_rows if not r['counts_as_keeper'])} college-rights holds)")

    # Budget adjustments: only Sam's is currently documented (user-stated).
    # Other teams' cash trades, if any, are unknown -- NOT assumed zero in
    # confirmed mode; see current_architecture.md / phase 2 report.
    budget_rows = [
        {
            "season": 2026, "team_id": "Sam", "team_name": "Sam",
            "amount": -15, "reason": "Traded auction cash for Cam Skattebo",
            "counterparty": "UNKNOWN", "source": "user_direct_statement",
            "source_date": "2026-09-04", "confidence": "HIGH",
            "notes": "User-confirmed directly; counterparty team not specified",
        },
    ]
    adj_path = BASE_DIR / "data" / "team_budget_adjustments_2026.csv"
    with adj_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(budget_rows[0].keys()))
        writer.writeheader()
        writer.writerows(budget_rows)
    print(f"Wrote {adj_path} ({len(budget_rows)} rows)")


if __name__ == "__main__":
    main()
