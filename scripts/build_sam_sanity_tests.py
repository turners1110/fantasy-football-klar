#!/usr/bin/env python3
"""Phase 3A item 14: Sam-specific sanity tests. Using Sam's real 6
confirmed keepers (Garrett Wilson, Kenneth Walker III, Quentin Johnston,
David Montgomery, Cam Skattebo, Jaxson Dart) and her real $223 primary
budget, evaluate 8 required scenarios against the ACTUAL remaining pool,
explaining each result via legal-starting-lineup gain and opportunity
cost (marginal_dollar_value), not by fiat.

Sam's real roster is 1 QB (Dart), 3 RB (Walker/Montgomery/Skattebo -- one
more than the 2 required starters), 2 WR (meets the minimum exactly),
0 TE -- she cannot field a legal lineup at all without acquiring a TE.
That fact is load-bearing for several of these scenarios (a TE is a hard
NEED; QB/RB are not).

Writes outputs/auction_rebuild/phase3a/sam_sanity_tests.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from mock_draft.cash_value import marginal_dollar_value
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.legal_lineup import build_production_lineup, partial_lineup_value

OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "sam_sanity_tests.json"


def _add(roster, players_by_name, name, price):
    p = players_by_name[name]
    return roster + [(p.name, p.position, price, p.projected_points)]


def main() -> None:
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    sam = teams["Sam"]
    base_roster = list(sam.roster)
    base_value = partial_lineup_value(base_roster)
    base_lineup = build_production_lineup(base_roster)
    dollar_rate = marginal_dollar_value(sam, players)

    print(f"Sam base roster utility (partial_lineup_value): {base_value}")
    print(f"Sam base lineup legal? {base_lineup.lineup_is_legal} ({base_lineup.lineup_failure_reason})")
    print(f"Sam's current marginal dollar value: {dollar_rate:.4f} utility/$")

    scenarios = []

    def _record(label, roster, price_spent, explanation):
        value = partial_lineup_value(roster)
        lineup = build_production_lineup(roster)
        marginal = value - base_value
        opportunity_cost = price_spent * dollar_rate
        scenarios.append({
            "scenario": label,
            "price_spent": price_spent,
            "marginal_utility": round(marginal, 2),
            "opportunity_cost_of_spending": round(opportunity_cost, 2),
            "net_of_opportunity_cost": round(marginal - opportunity_cost, 2),
            "lineup_is_legal_after": lineup.lineup_is_legal,
            "lineup_failure_reason_after": lineup.lineup_failure_reason,
            "explanation": explanation,
        })

    # 1. No 2nd QB: the baseline itself -- Dart already fully occupies the
    # only starting QB slot; a 2nd QB (if never bought) contributes $0 by
    # definition. Recorded as the reference point for the QB scenarios below.
    _record(
        "no_2nd_qb_baseline", base_roster, 0.0,
        "Reference point: Dart is Sam's only rostered QB and already fills the single starting slot. "
        "Not buying a 2nd QB spends $0 and forgoes zero legal-lineup points (there is no 2nd QB slot to fill).",
    )

    # 2. Cheap backup QB.
    r = _add(base_roster, players, "Geno Smith", 1.0)
    _record(
        "cheap_backup_qb", r, 1.0,
        "A $1 backup QB never starts over Dart (273.1 pts) unless Dart is injured; scores only the "
        "backup_qb bench-tier weight (0.075) on its own points -- small, cheap emergency-bye-week value.",
    )

    # 3. Mid-tier backup QB.
    r = _add(base_roster, players, "Dak Prescott", 7.0)
    _record(
        "mid_tier_backup_qb", r, 7.0,
        "Similar to the cheap backup case -- still doesn't start over Dart, so still only earns the "
        "backup_qb bench weight on its own points, just at a higher (worse economics) price.",
    )

    # 4. Expensive QB (top of the board).
    r = _add(base_roster, players, "Josh Allen", 40.0)
    _record(
        "expensive_qb", r, 40.0,
        "Josh Allen (328.8 pts) DOES exceed Dart (273.1 pts) and would become Sam's starting QB, "
        "displacing Dart to bench -- a real starter upgrade, not just bench depth. Compare its "
        "marginal_utility net of opportunity cost against premium_wr/premium_te below at similar cost: "
        "item 14 predicts this should lose to a TE/WR upgrade UNLESS the QB swap margin is large, which "
        "the raw points gap (328.8 vs 273.1 = +55.7) suggests it may not, once bench-discounted Dart is credited.",
    )

    # 5. Premium WR (a real starting-caliber upgrade -- Sam's 2 WRs meet
    # the minimum exactly, so a 3rd WR pushes the weaker current starter
    # to FLEX/bench, not literally "the WR position" as a hard need).
    r = _add(base_roster, players, "Rashee Rice", 72.0)
    _record(
        "premium_wr", r, 72.0,
        "Rashee Rice (197.1 pts) exceeds both of Sam's current WRs. With only 7 total players on this "
        "roster, everyone still fits in a starting or FLEX slot at full value (bench discounting hasn't "
        "kicked in yet -- that only bites once her real 9-starter group is actually full), so Rice's "
        "marginal utility here equals his full raw point total: a genuine starting-lineup-points gain, "
        "not diluted by any bench tier.",
    )

    # 6. Premium TE -- Sam's single biggest structural NEED (0 rostered).
    r = _add(base_roster, players, "TJ Hockenson", 43.0)
    _record(
        "premium_te", r, 43.0,
        "Sam holds ZERO tight ends -- without one, her lineup is structurally illegal (MISSING_TE) no "
        "matter what else she does. This is the one scenario that fixes a hard legal-lineup requirement, "
        "not just an incremental upgrade, so it should show the largest marginal utility of any single "
        "scenario here.",
    )

    # 7. Another premium RB -- she already has 3 (one more than required).
    r = _add(base_roster, players, "Josh Jacobs", 126.0)
    _record(
        "another_premium_rb", r, 126.0,
        "Sam already rosters 3 RBs against a 2-RB starting requirement (Walker 217.7, Montgomery 176.5, "
        "Skattebo 186.4 pts) -- a 4th RB fills no open requirement (unlike the TE scenario). At this "
        "sparse roster size it still lands as a full-value FLEX starter rather than a discounted bench "
        "player, so compare its cost-adjusted (net_of_opportunity_cost / price) return against premium_te "
        "and premium_wr: expect the WORST per-dollar return of the three, since RB is Sam's one position "
        "with real existing depth, not a need.",
    )

    # 8. Two strong FLEX additions (a mid-tier WR + a mid-tier RB, both
    # genuinely flex-relevant given her starting slots are otherwise full).
    r = _add(base_roster, players, "Terry McLaurin", 57.0)
    r = _add(r, players, "Bucky Irving", 102.0)
    _record(
        "two_strong_flex_additions", r, 57.0 + 102.0,
        "Two strong FLEX-eligible adds (McLaurin WR 173.9 pts, Irving RB 175.8 pts) compete for Sam's "
        "3 FLEX slots alongside her existing depth (Johnston 125.4, Skattebo 186.4) -- real lineup value "
        "if either displaces a current FLEX occupant, tested jointly here rather than one at a time.",
    )

    out = {
        "sam_base_roster": [{"player": n, "position": p, "keeper_price": pr, "points": pts} for n, p, pr, pts in base_roster],
        "sam_starting_budget": sam.budget_remaining,
        "sam_base_partial_lineup_value": base_value,
        "sam_base_lineup_is_legal": base_lineup.lineup_is_legal,
        "sam_base_lineup_failure_reason": base_lineup.lineup_failure_reason,
        "sam_marginal_dollar_value": round(dollar_rate, 4),
        "scenarios": scenarios,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {OUT_PATH}\n")
    for s in scenarios:
        print(f"{s['scenario']:28s} price=${s['price_spent']:6.1f}  marginal_utility={s['marginal_utility']:8.2f}  "
              f"net_of_opp_cost={s['net_of_opportunity_cost']:8.2f}  legal_after={s['lineup_is_legal_after']}")


if __name__ == "__main__":
    main()
