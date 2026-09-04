#!/usr/bin/env python3
"""Phase 3A item 4: audit every 2025 QB by acquisition origin, so the mock
draft's QB position cap is set from actual auction behavior, not from one
final-roster snapshot (phase 2B's QB=4 default, based on Coby's roster
having 4 QBs at draft's end -- which does not prove any of them was
COMPETITIVELY PURCHASED at auction rather than kept, tagged, or added
after the fact).

HONEST DATA-QUALITY LIMITATION (checked, not assumed): this repo has no
draft-day transaction log, bid history, or acquisition-method field
anywhere (search performed: no file matches *draft*log*, *auction*log*,
*transaction*, *bid*history*). data/historical_salaries_2025_raw.csv is a
final-roster salary SNAPSHOT with a `notes` field (tagged/on-IR/no-salary)
and a derived `salary_origin` classifier (auction_model/data_pipeline.py)
that can only confirm KEEPER_ESCALATION (via the +$5/+$10 delta rule) --
it cannot distinguish a competitive-auction win from a post-auction
addition for any other row. This audit reports that honestly rather than
inventing acquisition-method certainty the source data doesn't contain.

Writes outputs/auction_rebuild/phase3a/historical_qb_roster_audit.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from auction_model import data_pipeline

OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "historical_qb_roster_audit.csv"


def classify_origin(row) -> tuple[str, str]:
    """Returns (origin, evidence). Only KEEPER is ever confirmed by this
    source; everything else is an explicitly-labeled inference or UNKNOWN."""
    if bool(row.get("is_tagged_2025")):
        return "KEEPER", "is_tagged_2025=True (franchise tag salary escalation confirmed by delta rule)"
    if row["salary_origin"] == "KEEPER_ESCALATION_CONFIRMED":
        return "KEEPER", "salary_origin=KEEPER_ESCALATION_CONFIRMED (standard +$10 escalation delta confirmed)"
    if "no salary on record" in str(row.get("notes", "")):
        return "UNKNOWN", "no salary_2025 value at all -- could be a late add, a data gap, or an admin placeholder; no evidence either way"
    if bool(row.get("paul_rule_eligible")):
        return "UNKNOWN", "paul_rule_eligible=True but not independently verified -- Paul Rule keeps prior salary, doesn't itself prove auction origin"
    if row["salary_2025"] == 1.0:
        return "UNKNOWN_DOLLAR_ONE", (
            "$1 salary -- ambiguous: a genuine uncontested competitive-auction winner also sells for $1 in "
            "this league's own rules, so this is NOT evidence of an administrative assignment by itself, but "
            "there is also no positive evidence it WAS a competitive bid rather than a post-auction add."
        )
    return "UNKNOWN_NON_DOLLAR_ONE", (
        f"${row['salary_2025']:.0f} salary, no keeper-escalation marker -- plausibly consistent with a "
        f"competitive auction result (an unadorned dollar salary in a keeper league is usually last "
        f"season's paid price), but NOT independently confirmed by any acquisition-method record in this repo."
    )


def main() -> None:
    salaries, _ = data_pipeline.load_historical_salaries(BASE_DIR / "data" / "historical_salaries_2025_raw.csv")
    qbs = salaries[salaries["position"] == "QB"].copy()

    rows = []
    for _, row in qbs.iterrows():
        origin, evidence = classify_origin(row)
        rows.append({
            "team": row["team"], "player": row["player"],
            "salary_2025": row["salary_2025"],
            "acquisition_origin": origin, "evidence": evidence,
            "is_dollar_one": bool(pd.notna(row["salary_2025"]) and row["salary_2025"] == 1.0),
            "notes": row.get("notes", ""),
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    n_per_team = qbs.groupby("team").size()
    n_teams_1qb = int((n_per_team == 1).sum())
    n_teams_2qb = int((n_per_team == 2).sum())
    n_teams_3qb = int((n_per_team == 3).sum())
    n_teams_4qb = int((n_per_team >= 4).sum())
    n_confirmed_keeper = sum(1 for r in rows if r["acquisition_origin"] == "KEEPER")
    n_dollar_one = sum(1 for r in rows if r["is_dollar_one"])
    n_unknown_nondollar = sum(1 for r in rows if r["acquisition_origin"] == "UNKNOWN_NON_DOLLAR_ONE")

    print(f"Wrote {OUT_PATH} ({len(rows)} QB rows)")
    print(f"\n--- Required questions (answered from available evidence, limitations stated) ---")
    print(f"1-3/4. Teams with exactly 1/2/3/4+ 2025 QB roster spots: "
          f"1={n_teams_1qb}, 2={n_teams_2qb}, 3={n_teams_3qb}, 4+={n_teams_4qb}")
    print(f"   NOTE: this counts ROSTER SPOTS, not confirmed AUCTION PURCHASES -- this repo has no "
          f"acquisition-method record, so 'bought via competitive auction' cannot be directly answered "
          f"for any row except the {n_confirmed_keeper} confirmed KEEPER row(s) (which are explicitly NOT "
          f"auction purchases).")
    print(f"4. Did anyone competitively buy four? NOT CONFIRMED by any record in this repo. After "
          f"deduplicating a known duplicate salary row (data_pipeline.load_historical_salaries -- see phase "
          f"2B's historical_roster_position_counts.py bug, which had inflated one team's QB count to 4), the "
          f"real 2025 maximum is {n_per_team.max() if len(n_per_team) else 0} QB roster spots on a single team, "
          f"and even that top count has no acquisition-method evidence establishing any spot as a "
          f"competitive-auction win specifically.")
    print(f"5. Prices paid for QB2/QB3/QB4 (by team, sorted): see the CSV -- no acquisition-method tag "
          f"exists to isolate 'the auction price for QB2' as a distinct, confirmed figure.")
    print(f"6. Present before/after the auction? UNKNOWN for all non-keeper rows -- no draft-day timestamp "
          f"or transaction log exists in this repo (searched for one; none found).")
    print(f"7. Were any $1 records administrative? UNKNOWN -- {n_dollar_one} of {len(rows)} QB rows are $1. "
          f"A $1 result is NOT itself evidence of an administrative assignment in this league (uncontested "
          f"competitive nominations also settle at $1 under this league's own rules) -- but it is also not "
          f"positive evidence of a competitive bid. Genuinely indeterminate from this source.")
    print(f"\n{n_unknown_nondollar} of {len(rows)} QB rows are UNKNOWN_NON_DOLLAR_ONE (a real salary, no "
          f"keeper marker) -- the closest this data gets to 'plausibly auction-acquired,' but explicitly "
          f"unconfirmed.")
    print(
        "\nCONCLUSION: no row in this repo proves a team COMPETITIVELY PURCHASED a 2nd, 3rd, or 4th QB at "
        "auction. Per the phase-3A instruction 'do not use four as the primary default without proof of "
        "four auction-acquired quarterbacks,' the QB cap defaults are corrected: "
        f"primary=2, stress-test=3, historical-observed={n_per_team.max() if len(n_per_team) else 3} "
        "(phase 2B's primary=4 default was itself based on a duplicate-row data bug -- the real historical "
        "max, after deduplication, is 3, not 4 -- see mock_draft/feasibility.py's PRIMARY_QB_CAP/"
        "STRESS_TEST_QB_CAP/HISTORICAL_OBSERVED_QB_CAP constants)."
    )


if __name__ == "__main__":
    main()
