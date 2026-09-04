"""Stage 1 required acceptance checks, as a single validate() function
returning a list of violation strings (empty list = state is legal)."""

from __future__ import annotations

from .auction_state import AuctionState


def validate(state: AuctionState) -> list[str]:
    violations = []

    for tid, team in state.teams.items():
        if team.budget_remaining < -1e-9:
            violations.append(f"{tid}: negative budget {team.budget_remaining}")
        if len(team.roster) > 15:
            violations.append(f"{tid}: {len(team.roster)} players exceeds 15")
        seen = set()
        for p in team.roster:
            if p["player_id"] in seen:
                violations.append(f"{tid}: duplicate player {p['player_id']} on same roster")
            seen.add(p["player_id"])

    # no duplicate sale leaguewide
    all_sold_ids = list(state.sold_players.keys())
    if len(all_sold_ids) != len(set(all_sold_ids)):
        violations.append("duplicate player_id present in sold_players")
    for pid in all_sold_ids:
        owners_with_player = [tid for tid, t in state.teams.items() if any(p["player_id"] == pid for p in t.roster)]
        if len(owners_with_player) > 1:
            violations.append(f"{pid} appears on more than one roster: {owners_with_player}")

    # no keeper sale
    for tid, team in state.teams.items():
        for kid in team.keeper_ids:
            if kid in state.sold_players and state.sold_players[kid]["winning_owner"] != tid:
                violations.append(f"keeper {kid} (of {tid}) was sold to a different team via the veteran auction")

    # no college-rights sale
    for cid in state.college_rights_excluded:
        if cid in state.sold_players:
            violations.append(f"college-rights player {cid} was sold in the veteran auction")

    # legal maximum bid respects open-slot reserves (never negative)
    for tid, team in state.teams.items():
        if team.legal_max_bid < 0:
            violations.append(f"{tid}: legal_max_bid is negative ({team.legal_max_bid})")

    return violations


def is_legal(state: AuctionState) -> bool:
    return len(validate(state)) == 0
