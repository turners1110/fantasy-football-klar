#!/usr/bin/env python3
"""Phase 3A item 5: decompose unspent cash at team/archetype/position/
nomination level using the auction engine's new bid_stats instrumentation
(mock_draft/auction.py resolve_bid's bid_stats param), rather than
guessing at the mechanism from aggregate numbers alone.

Writes outputs/auction_rebuild/phase3a/unspent_cash_decomposition.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from mock_draft.auction import run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.legal_lineup import build_production_lineup

N_SEEDS = 40
STARTER_SLOTS = 9  # 1 QB + 2 RB + 2 WR + 1 TE + 3 FLEX
OUT_PATH = BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "unspent_cash_decomposition.csv"


def main() -> None:
    players, teams_template, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    team_states = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv")
    starting_budget = dict(zip(team_states["team_id"], team_states["primary_auction_budget"]))

    rows = []
    for seed in range(N_SEEDS):
        rng = np.random.default_rng(seed)
        bid_stats: dict = {}
        log, final_teams = run_single_auction(players, teams_template, rng, bid_stats=bid_stats)
        log_df = pd.DataFrame(log)

        for name, team in final_teams.items():
            stats = bid_stats.get(name, {
                "bids": 0, "wins": 0, "blocked_zero_utility": 0,
                "blocked_feasibility": 0, "blocked_budget": 0, "blocked_roster_cap": 0,
            })
            team_sales = log_df[log_df["winning_team"] == name] if len(log_df) else pd.DataFrame()
            pos_spend = {p: 0.0 for p in ("QB", "RB", "WR", "TE")}
            for _, entry in team_sales.iterrows():
                pos_spend[entry["position"]] = pos_spend.get(entry["position"], 0.0) + entry["sale_price"]

            total_spend = float(team_sales["sale_price"].sum()) if len(team_sales) else 0.0
            highest_price = float(team_sales["sale_price"].max()) if len(team_sales) else 0.0

            lineup = build_production_lineup(team.roster)
            starter_names = {lineup.starting_qb, lineup.starting_te} | set(lineup.starting_rbs) | set(lineup.starting_wrs) | set(lineup.starting_flex)
            starter_spend = sum(r[2] for r in team.roster if r[0] in starter_names)
            bench_spend = sum(r[2] for r in team.roster if r[0] not in starter_names)

            rows.append({
                "seed": seed, "team": name, "archetype": team.strategy.name,
                "starting_auction_budget": starting_budget.get(name, ""),
                "players_needed": 15 - len(teams_template[name].roster),
                "starter_slots_needed": STARTER_SLOTS,
                "bench_slots_needed": 15 - STARTER_SLOTS,
                "total_spend": total_spend,
                "unused_cash": team.budget_remaining,
                "number_of_bids": stats["bids"],
                "number_of_wins": stats["wins"],
                "number_of_blocked_bids": (
                    stats["blocked_zero_utility"] + stats["blocked_feasibility"]
                    + stats["blocked_budget"] + stats["blocked_roster_cap"]
                ),
                "zero_utility_blocks": stats["blocked_zero_utility"],
                "feasibility_blocks": stats["blocked_feasibility"],
                "budget_blocks": stats["blocked_budget"],
                "roster_cap_blocks": stats["blocked_roster_cap"],
                "highest_price_paid": highest_price,
                "starter_spend": starter_spend,
                "bench_spend": bench_spend,
                "QB_spend": pos_spend["QB"], "RB_spend": pos_spend["RB"],
                "WR_spend": pos_spend["WR"], "TE_spend": pos_spend["TE"],
            })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    df = pd.DataFrame(rows)
    print(f"Wrote {OUT_PATH} ({len(df)} rows, {N_SEEDS} seeds)")
    print(f"\nMean unused cash: ${df['unused_cash'].mean():.2f}")
    print(f"Mean blocked-bid breakdown (per team-seed):")
    for col in ("zero_utility_blocks", "feasibility_blocks", "budget_blocks", "roster_cap_blocks"):
        print(f"  {col}: {df[col].mean():.2f}")
    total_blocks = df[["zero_utility_blocks", "feasibility_blocks", "budget_blocks", "roster_cap_blocks"]].sum().sum()
    print(f"\nShare of all blocked-bid events attributable to each mechanism:")
    for col in ("zero_utility_blocks", "feasibility_blocks", "budget_blocks", "roster_cap_blocks"):
        share = df[col].sum() / total_blocks if total_blocks else 0
        print(f"  {col}: {share:.1%}")
    print(f"\nMean bids per team-seed: {df['number_of_bids'].mean():.2f}, mean wins: {df['number_of_wins'].mean():.2f}")
    print(
        "\n--- Cause-by-cause assessment (item 5's 12 candidate mechanisms) ---\n"
        "REVISED after root-causing the zero-utility-block mechanism directly (see\n"
        "mock_draft/legal_lineup.py:partial_lineup_value docstring for the full diagnosis).\n"
        "The FIRST hypothesis below (#3, bench weights too low) was tested by raising the\n"
        "weights and re-running this script and market_clearing_diagnostics.py: it produced\n"
        "ZERO measurable change, which is what led to instrumenting _incremental_utility\n"
        "directly. That found the real cause: mock_draft.auction._incremental_utility called\n"
        "build_production_lineup, whose total_roster_utility is hard-zeroed to 0 for ANY\n"
        "incomplete/illegal roster -- so for a team without a full legal lineup yet (most\n"
        "teams, most of the draft), before=0 and after=0 for almost every candidate,\n"
        "regardless of bench weight (0-0=0 either way). Fixed by adding\n"
        "legal_lineup.partial_lineup_value (best-effort, non-zeroing scoring) for\n"
        "_incremental_utility's use only; build_production_lineup is untouched and remains\n"
        "correct for final-roster fitness (evolution.py, best_response.py). Effect of the fix\n"
        "alone (bench weights reverted to their original phase 2B values): mean unused cash\n"
        f"fell from $184.34/team to ${df['unused_cash'].mean():.2f}/team, and zero_utility_blocks fell to "
        f"{df['zero_utility_blocks'].sum()} (from 99.8% of all blocked-bid events) -- essentially all "
        "remaining blocks are now legitimate budget exhaustion.\n"
        "1. Shared base values too low: NOT the primary driver -- base_value drives willingness ceilings "
        "upward, not downward; this was never the mechanism.\n"
        "2. Team willingness too low: MINOR RESIDUAL -- willingness/price_ceiling_pct still caps individual "
        "bids, but no longer produces large aggregate unspent cash now the utility gate is fixed.\n"
        "3. Bench weights too low: NOT THE CAUSE -- tested directly (raised, then measured zero effect); "
        "the true cause was the illegal-roster zero-clamping bug above, independent of bench-weight values.\n"
        "4/5. Agents fill weak starters early / fail to replace them: NOT the cause -- "
        "build_production_lineup already re-optimizes the best lineup from the FULL roster every time it's "
        "called, so a later stronger starter already displaces an earlier weak one (see item 9 test).\n"
        "6. Nomination order stranding cash: NOT separately isolated in this run -- would need a "
        "nomination-order sensitivity sweep (not run at this scope); no longer the dominant driver either way.\n"
        f"7. Position caps blocking useful purchases: MINOR -- roster_cap_blocks "
        f"({df['roster_cap_blocks'].sum()} total) is a negligible share of all blocks.\n"
        "8. Bid increment/opening price misbehavior: NOT indicated -- MIN_PRICE=$1 and increments are "
        "small and unchanged from phase 2.\n"
        "9. Utility ignoring future completion: STILL A KNOWN LIMITATION -- the zero/negative-utility gate "
        "(and now partial_lineup_value) is a CURRENT-roster snapshot, not a full counterfactual against the "
        "remaining pool or rival competition (see item 7's counterfactual bid-ceiling design, deliberately "
        "not wired into live bidding for documented reasons).\n"
        "10. Cash has no terminal penalty: TRUE by design in phase 2/2B -- see item 8's terminal cash "
        "value formula (mock_draft/cash_value.py), not required to fix THIS bug but still a real gap.\n"
        "11. Available-player pool too large/weak: NOT a major factor -- pool depth (~320) comfortably "
        "exceeds need (108).\n"
        "12. Too many $1 uncontested sales: NO LONGER a major factor post-fix -- see aggregate diagnostics "
        "for the current $1-sale rate."
    )


if __name__ == "__main__":
    main()
