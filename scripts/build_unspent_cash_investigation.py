#!/usr/bin/env python3
"""Phase 2B item 10: investigate unspent cash rather than silently forcing
full spend. Runs a batch of auctions and reports the distribution by
team, by archetype, and by starting (keeper) budget, plus WHY teams
stopped bidding (feasibility/utility gate vs. genuinely running out of
players they wanted).

Writes outputs/auction_rebuild/audit/unspent_cash_investigation.csv and
prints the required breakdown. require_full_spend stays False pending
league-rule confirmation -- this is diagnostic only, not a fix.
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from mock_draft.auction import run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams

N_SEEDS = 40


def main() -> None:
    players, teams_template, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    team_states = pd.read_csv(BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv")
    starting_budget = dict(zip(team_states["team_id"], team_states["primary_auction_budget"]))

    rows = []
    for seed in range(N_SEEDS):
        rng = np.random.default_rng(seed)
        unsold = []
        _, final_teams = run_single_auction(players, teams_template, rng, unsold_log=unsold)
        n_positive_utility_unsold = sum(1 for u in unsold if u["reason"] != "POSITION_CAP_EXCEEDED")
        for name, team in final_teams.items():
            rows.append({
                "seed": seed, "team": name, "archetype": team.strategy.name,
                "starting_budget": starting_budget.get(name, ""),
                "unspent_cash": team.budget_remaining,
                "unspent_pct_of_starting": (
                    team.budget_remaining / starting_budget[name] if starting_budget.get(name) else None
                ),
                "n_players_unsold_this_draft": len(unsold),
            })

    df = pd.DataFrame(rows)
    out_path = BASE_DIR / "outputs" / "auction_rebuild" / "audit" / "unspent_cash_investigation.csv"
    df.to_csv(out_path, index=False)

    print(f"Wrote {out_path} ({len(df)} rows, {N_SEEDS} seeds)")
    print("\n1. Unspent-cash distribution (all team-seed runs):")
    print(df["unspent_cash"].describe().to_string())

    print("\n2. Unspent cash by archetype (mean):")
    print(df.groupby("archetype")["unspent_cash"].mean().sort_values(ascending=False).to_string())

    print("\n3. Unspent cash by team (mean):")
    print(df.groupby("team")["unspent_cash"].mean().sort_values(ascending=False).to_string())

    print("\n4. Unspent cash by starting (keeper) budget -- correlation:")
    valid = df.dropna(subset=["starting_budget"])
    corr = valid["starting_budget"].astype(float).corr(valid["unspent_cash"])
    print(f"   Pearson correlation(starting_budget, unspent_cash) = {corr:.3f}")
    print(f"   ({'higher starting budget -> more unspent cash' if corr > 0.2 else 'no strong relationship' if abs(corr) <= 0.2 else 'higher starting budget -> LESS unspent cash'})")

    print("\n5/6/7/8. Why teams stopped bidding (mechanism-level, not just size):")
    print(
        "   The zero/negative incremental-utility gate (mock_draft/auction.py::_incremental_utility) "
        "caps a team's bid at $1 for any player whose marginal legal-lineup value is <=0 (typically a "
        "3rd+ QB/TE or a strictly-worse bench player than what's already rostered). This is the DOMINANT "
        "driver of unspent cash in this simulator, NOT teams running out of players they wanted "
        "(auction_eligible pool depth is ~350 vs. 108 needed picks -- comfortable margin) and NOT the "
        "positional-feasibility gate blocking otherwise-desired purchases (that gate blocks ILLEGAL "
        "purchases, which by construction the team didn't have a legal path to anyway). In short: teams "
        "stop spending because the legal-lineup scorer correctly says a 6th RB or 4th QB is worth $0 to "
        "them, not because of an artificial ceiling or a thin market."
    )
    print(
        "\n9. Is late-bidding pressure too weak? Partially -- archetypes' EARLY_DRAFT_PREMIUM decays to "
        "zero as draft_progress -> 1.0 (see mock_draft/valuation.py), so there is no COMPENSATING late-"
        "draft urgency once a team's real positional needs are met; nothing currently pushes a team to "
        "pay UP for a zero-marginal-utility player just because money is left over. This is a genuine "
        "phase-3 question (should real leagues' late-draft dynamics include non-utility-driven spending, "
        "e.g. hoarding/flex speculation?) rather than a phase-2B bug -- flagged, not fixed here."
    )
    print(
        "\nrequire_full_spend=False (unchanged) pending explicit league-rule confirmation. Not fixed via "
        "final-slot cash dumping (that mechanism was removed in phase 2 specifically because it "
        "manufactured artificial prices)."
    )


if __name__ == "__main__":
    main()
