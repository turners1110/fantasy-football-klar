"""Canonical AuctionState (Phase 4 Stage 1).

Every field the spec lists is present, either as a top-level attribute or
inside a per-team/per-player dict (TeamState / rather than one attribute
per team, which would not scale to 12 teams). Legal-max-bid, position
counts, etc. are DERIVED (computed on read, not stored) so they can never
drift out of sync with the underlying budgets/rosters -- the only stored
truth is what the event log actually asserts (budgets, rosters, sales).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

ROSTER_SIZE = 15
MIN_PRICE = 1
STARTING_LINEUP = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 3}
FLEX_ELIGIBLE = {"RB", "WR", "TE"}


@dataclass
class TeamState:
    team_id: str
    budget_remaining: float
    roster: list = field(default_factory=list)  # list of dicts: player_id, display_name, position, price, is_keeper
    keeper_ids: set = field(default_factory=set)

    @property
    def open_slots(self) -> int:
        return max(0, ROSTER_SIZE - len(self.roster))

    @property
    def min_reserve(self) -> float:
        """$1 reserved for every OTHER open slot besides the one being filled now."""
        return max(0, self.open_slots - 1) * MIN_PRICE

    @property
    def legal_max_bid(self) -> float:
        return max(0.0, self.budget_remaining - self.min_reserve)

    @property
    def position_counts(self) -> dict:
        counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
        for p in self.roster:
            counts[p["position"]] = counts.get(p["position"], 0) + 1
        return counts

    def legal_starting_needs(self) -> dict:
        """A cheap, non-exact approximation of remaining starter needs
        (greedy fill by position then FLEX) -- used for the fast live
        board, NOT a substitute for an exact_roster_solver call."""
        counts = self.position_counts
        needs = {}
        for pos, required in (("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1)):
            needs[pos] = max(0, required - counts.get(pos, 0))
        flex_fillable = sum(max(0, counts.get(pos, 0) - req) for pos, req in (("RB", 2), ("WR", 2), ("TE", 1)))
        needs["FLEX"] = max(0, STARTING_LINEUP["FLEX"] - flex_fillable)
        return needs


@dataclass
class AuctionState:
    auction_id: str
    rules_version: str
    model_version: str
    sequence_number: int = 0
    nomination_number: int = 0
    current_nominating_team: Optional[str] = None
    available_pool: dict = field(default_factory=dict)     # player_id -> {display_name, position, ...}
    sold_players: dict = field(default_factory=dict)       # player_id -> {winning_owner, sale_price, nominating_owner, ...}
    teams: dict = field(default_factory=dict)               # team_id -> TeamState
    college_rights_excluded: set = field(default_factory=set)
    sam_team_id: str = "Sam"
    paused: bool = False
    latest_event_timestamp: Optional[float] = None

    # ---- derived, read-only views (never stored, always recomputed) ----

    def team(self, team_id: str) -> TeamState:
        return self.teams[team_id]

    @property
    def sam(self) -> TeamState:
        return self.teams[self.sam_team_id]

    def all_legal_max_bids(self) -> dict:
        return {tid: t.legal_max_bid for tid, t in self.teams.items()}

    def remaining_league_cash(self) -> float:
        return sum(t.budget_remaining for t in self.teams.values())

    def remaining_league_roster_slots(self) -> int:
        return sum(t.open_slots for t in self.teams.values())

    def position_demand_by_team(self) -> dict:
        return {tid: t.legal_starting_needs() for tid, t in self.teams.items()}

    def total_demand_by_position(self) -> dict:
        totals = {"QB": 0, "RB": 0, "WR": 0, "TE": 0, "FLEX": 0}
        for needs in self.position_demand_by_team().values():
            for pos, n in needs.items():
                totals[pos] += n
        return totals

    def available_supply_by_position(self) -> dict:
        supply = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
        for p in self.available_pool.values():
            supply[p["position"]] = supply.get(p["position"], 0) + 1
        return supply

    def to_dict(self) -> dict:
        return {
            "auction_id": self.auction_id, "rules_version": self.rules_version,
            "model_version": self.model_version, "sequence_number": self.sequence_number,
            "nomination_number": self.nomination_number,
            "current_nominating_team": self.current_nominating_team,
            "available_pool": self.available_pool, "sold_players": self.sold_players,
            "teams": {
                tid: {"team_id": t.team_id, "budget_remaining": t.budget_remaining,
                      "roster": t.roster, "keeper_ids": sorted(t.keeper_ids)}
                for tid, t in self.teams.items()
            },
            "college_rights_excluded": sorted(self.college_rights_excluded),
            "sam_team_id": self.sam_team_id, "paused": self.paused,
            "latest_event_timestamp": self.latest_event_timestamp,
        }

    @staticmethod
    def from_dict(d: dict) -> "AuctionState":
        st = AuctionState(
            auction_id=d["auction_id"], rules_version=d["rules_version"], model_version=d["model_version"],
            sequence_number=d["sequence_number"], nomination_number=d["nomination_number"],
            current_nominating_team=d.get("current_nominating_team"),
            available_pool=d.get("available_pool", {}), sold_players=d.get("sold_players", {}),
            college_rights_excluded=set(d.get("college_rights_excluded", [])),
            sam_team_id=d.get("sam_team_id", "Sam"), paused=d.get("paused", False),
            latest_event_timestamp=d.get("latest_event_timestamp"),
        )
        for tid, t in d.get("teams", {}).items():
            st.teams[tid] = TeamState(
                team_id=t["team_id"], budget_remaining=t["budget_remaining"],
                roster=t.get("roster", []), keeper_ids=set(t.get("keeper_ids", [])),
            )
        return st
