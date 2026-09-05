"""Pure reducer: apply_event(state, event) -> new AuctionState.

No hidden mutation outside this module's own copy-then-mutate-the-copy
pattern; the caller always gets a fresh AuctionState back. Undo is
implemented by re-deriving the initial state and replaying every event
in the log EXCEPT the undone one (append-only log, never edited in
place) -- this guarantees undo produces exactly the same state as if
the undone event had never happened, by construction, rather than by a
bespoke "reverse" function that could drift out of sync with apply_event.
"""

from __future__ import annotations

import copy

from .auction_events import AuctionEvent
from .auction_state import AuctionState, TeamState


class IllegalEventError(Exception):
    pass


def apply_event(state: AuctionState, event: AuctionEvent) -> AuctionState:
    new_state = copy.deepcopy(state)
    new_state.sequence_number = event.sequence_number
    new_state.latest_event_timestamp = event.timestamp
    p = event.payload

    if event.event_type == "AUCTION_STARTED":
        pass  # state already initialized by the caller before the first event

    elif event.event_type == "PLAYER_NOMINATED":
        new_state.nomination_number += 1
        new_state.current_nominating_team = p.get("nominating_owner")

    elif event.event_type == "BID_OBSERVED":
        pass  # informational only -- feeds owner learning (Stage 3), not state truth

    elif event.event_type == "PLAYER_SOLD":
        player_id = p["player_id"]
        winner = p["winning_owner"]
        price = float(p["sale_price"])

        if player_id in new_state.sold_players:
            raise IllegalEventError(f"duplicate sale: {player_id} already sold")
        if player_id in new_state.college_rights_excluded:
            raise IllegalEventError(f"{player_id} is a college-rights asset and cannot enter the veteran auction")
        team = new_state.teams.get(winner)
        if team is None:
            raise IllegalEventError(f"unknown team {winner!r}")
        if any(pl.get("player_id") == player_id for pl in team.roster):
            raise IllegalEventError(f"{player_id} already on {winner}'s roster")
        if any(player_id in t.keeper_ids for t in new_state.teams.values()):
            raise IllegalEventError(f"{player_id} is a keeper and cannot be sold in the veteran auction")
        # GATE B FIX (V3 repair): this cap used to check only
        # len(team.roster) against 16 -- but college-rights holds (and,
        # after the Gate A repair, Brad/Reid's unidentified 7th protected
        # player) occupy a real roster slot via college_rights_count
        # WITHOUT ever appearing in `roster` itself. A team with
        # college_rights_count=2 could previously buy a full 16-player
        # roster on top of those 2 slots, reaching 18 total protected
        # players -- 2 over the official 16-player cap. Fixed to count
        # both.
        if len(team.roster) + team.college_rights_count >= 16:
            raise IllegalEventError(
                f"{winner} already has 16 players "
                f"({len(team.roster)} rostered + {team.college_rights_count} protected-but-unlisted)"
            )
        # legal-max-bid check: price must not exceed what was legally biddable
        # at the moment of sale (budget minus reserve for every OTHER open slot)
        other_open_after = max(0, team.open_slots - 1)
        max_legal = team.budget_remaining - other_open_after
        if price > max_legal + 1e-9:
            raise IllegalEventError(
                f"sale price {price} for {player_id} exceeds {winner}'s legal max bid {max_legal} "
                f"at time of sale (budget {team.budget_remaining}, other open slots {other_open_after})"
            )
        if price < 0:
            raise IllegalEventError("sale price cannot be negative")

        team.budget_remaining = round(team.budget_remaining - price, 2)
        if team.budget_remaining < -1e-9:
            raise IllegalEventError(f"{winner} would have negative budget after this sale")
        team.roster.append({
            "player_id": player_id, "display_name": p["display_name"], "position": p["position"],
            "price": price, "is_keeper": False,
            "projected_points": p.get("projected_points", 0.0),  # optional; live_values needs this for lineup math
        })
        new_state.sold_players[player_id] = {
            "winning_owner": winner, "sale_price": price, "nominating_owner": p.get("nominating_owner"),
            "observed_bidders": p.get("observed_bidders", []), "highest_losing_bidder": p.get("highest_losing_bidder"),
            "sequence_number": event.sequence_number,
            # GATE B FIX (V3 repair): recorded so a later correction can
            # fall back to it if the correction call site doesn't supply
            # a fresher projected_points value -- see SALE_CORRECTED above.
            "projected_points": p.get("projected_points", 0.0),
        }
        new_state.available_pool.pop(player_id, None)

    elif event.event_type == "PLAYER_UNSOLD":
        player_id = p["player_id"]
        # stays in the available pool; no accounting change. Just informational.

    elif event.event_type == "SALE_CORRECTED":
        # Reverse the OLD sale's accounting fully, then apply the corrected result.
        player_id = p["player_id"]
        old = new_state.sold_players.get(player_id)
        if old is None:
            raise IllegalEventError(f"cannot correct a sale that was never recorded: {player_id}")
        old_team = new_state.teams[old["winning_owner"]]
        old_team.roster = [r for r in old_team.roster if r["player_id"] != player_id]
        old_team.budget_remaining = round(old_team.budget_remaining + old["sale_price"], 2)
        del new_state.sold_players[player_id]
        new_state.available_pool[player_id] = {"display_name": p["display_name"], "position": p["position"]}

        # Apply the corrected sale via the same legality path as a normal
        # sale. GATE B FIX (V3 repair, Part 4): projected_points is now
        # preserved from the correction payload (falling back to the OLD
        # sale record's own points if the caller didn't supply a fresher
        # value) -- a correction must never recreate the player as a
        # $0/0-point ghost of themselves.
        preserved_points = p.get("projected_points")
        if preserved_points is None:
            preserved_points = old.get("projected_points", 0.0)
        corrected_event = AuctionEvent(
            event_type="PLAYER_SOLD", sequence_number=event.sequence_number,
            payload={
                "player_id": player_id, "display_name": p["display_name"], "position": p["position"],
                "winning_owner": p["winning_owner"], "sale_price": p["sale_price"],
                "nominating_owner": p.get("nominating_owner"),
                "observed_bidders": p.get("observed_bidders", []), "highest_losing_bidder": p.get("highest_losing_bidder"),
                "projected_points": preserved_points,
            },
        )
        new_state = apply_event(new_state, corrected_event)

    elif event.event_type == "EVENT_UNDONE":
        pass  # handled at the log/replay level (auction_state_store.undo), not here

    elif event.event_type == "OWNER_BUDGET_CORRECTED":
        team = new_state.teams[p["team_id"]]
        team.budget_remaining = float(p["new_budget_remaining"])

    elif event.event_type == "OWNER_ROSTER_CORRECTED":
        team = new_state.teams[p["team_id"]]
        team.roster = p["new_roster"]

    elif event.event_type == "AUCTION_PAUSED":
        new_state.paused = True

    elif event.event_type == "AUCTION_RESUMED":
        new_state.paused = False

    return new_state


def replay(initial_state: AuctionState, events: list[AuctionEvent], skip_event_ids: set | None = None) -> AuctionState:
    """Replay every event in order onto a fresh copy of initial_state,
    skipping any event_id in skip_event_ids (used to implement undo:
    the caller marks one event's id to skip and replays everything else,
    which by construction reproduces exactly the state as if that event
    had never happened)."""
    skip_event_ids = skip_event_ids or set()
    state = copy.deepcopy(initial_state)
    for event in events:
        if event.event_id in skip_event_ids:
            continue
        if event.event_type == "EVENT_UNDONE":
            continue
        state = apply_event(state, event)
    return state
