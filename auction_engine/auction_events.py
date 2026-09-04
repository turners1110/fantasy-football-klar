"""Append-only AuctionEvent log: the required event types from Phase 4
Stage 1, each a frozen dataclass with a `to_dict`/`from_dict` pair so the
log can be persisted as JSON lines and replayed exactly."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

EVENT_TYPES = (
    "AUCTION_STARTED",
    "PLAYER_NOMINATED",
    "BID_OBSERVED",
    "PLAYER_SOLD",
    "PLAYER_UNSOLD",
    "SALE_CORRECTED",
    "EVENT_UNDONE",
    "OWNER_BUDGET_CORRECTED",
    "OWNER_ROSTER_CORRECTED",
    "AUCTION_PAUSED",
    "AUCTION_RESUMED",
)


@dataclass(frozen=True)
class AuctionEvent:
    event_type: str
    sequence_number: int
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    payload: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type {self.event_type!r}; must be one of {EVENT_TYPES}")

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "AuctionEvent":
        return AuctionEvent(
            event_type=d["event_type"], sequence_number=d["sequence_number"],
            event_id=d.get("event_id", str(uuid.uuid4())), timestamp=d.get("timestamp", time.time()),
            payload=d.get("payload", {}),
        )


def make_player_sold_event(
    sequence_number: int, player_id: str, display_name: str, position: str,
    winning_owner: str, sale_price: float, nominating_owner: str,
    observed_bidders: Optional[list] = None, highest_losing_bidder: Optional[dict] = None,
) -> AuctionEvent:
    """PLAYER_SOLD required fields per spec Part (Stage 1): player identifier;
    display name; position; winning owner; sale price; nominating owner;
    observed bidders (if entered); highest losing bidder (if entered); timestamp."""
    return AuctionEvent(
        event_type="PLAYER_SOLD", sequence_number=sequence_number,
        payload={
            "player_id": player_id, "display_name": display_name, "position": position,
            "winning_owner": winning_owner, "sale_price": float(sale_price),
            "nominating_owner": nominating_owner,
            "observed_bidders": observed_bidders or [],
            "highest_losing_bidder": highest_losing_bidder,
        },
    )
