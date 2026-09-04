"""Persistence: an append-only JSON-lines event log plus periodic
snapshots for fast recovery, and an AuctionStateStore that wraps
auction_reducer to give callers a simple record/undo/replay API without
touching the reducer directly.
"""

from __future__ import annotations

import json
from pathlib import Path

from .auction_events import AuctionEvent
from .auction_reducer import apply_event, replay
from .auction_state import AuctionState


class AuctionStateStore:
    def __init__(self, initial_state: AuctionState, log_path: Path | None = None, snapshot_every: int = 10):
        self.initial_state = initial_state
        self.events: list[AuctionEvent] = []
        self.state = initial_state
        self.log_path = log_path
        self.snapshot_every = snapshot_every
        self._snapshots: dict[int, AuctionState] = {0: initial_state}

    def _next_sequence(self) -> int:
        return len(self.events) + 1

    def record(self, event_type: str, payload: dict) -> AuctionEvent:
        event = AuctionEvent(event_type=event_type, sequence_number=self._next_sequence(), payload=payload)
        new_state = apply_event(self.state, event)  # raises IllegalEventError if invalid -- log stays clean
        self.events.append(event)
        self.state = new_state
        if self.log_path is not None:
            self._persist_event(event)
        if len(self.events) % self.snapshot_every == 0:
            self._snapshots[len(self.events)] = new_state
        return event

    def undo_last(self) -> AuctionState:
        if not self.events:
            raise ValueError("no events to undo")
        undone = self.events[-1]
        undo_event = AuctionEvent(
            event_type="EVENT_UNDONE", sequence_number=self._next_sequence(),
            payload={"undone_event_id": undone.event_id, "undone_event_type": undone.event_type},
        )
        self.events.append(undo_event)
        if self.log_path is not None:
            self._persist_event(undo_event)
        self.state = replay(self.initial_state, self.events, skip_event_ids={undone.event_id})
        return self.state

    def correct_sale(self, player_id: str, display_name: str, position: str,
                      winning_owner: str, sale_price: float, nominating_owner: str | None = None) -> AuctionState:
        event = self.record("SALE_CORRECTED", {
            "player_id": player_id, "display_name": display_name, "position": position,
            "winning_owner": winning_owner, "sale_price": sale_price, "nominating_owner": nominating_owner,
        })
        return self.state

    def replay_from_log(self) -> AuctionState:
        """Rebuild state from self.events from scratch -- used to verify
        'replaying the same event log must produce the same state'."""
        skip = {e.payload.get("undone_event_id") for e in self.events if e.event_type == "EVENT_UNDONE"}
        return replay(self.initial_state, self.events, skip_event_ids=skip)

    # ---- persistence ----

    def _persist_event(self, event: AuctionEvent) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as f:
            f.write(json.dumps(event.to_dict()) + "\n")

    @staticmethod
    def load_events(log_path: Path) -> list[AuctionEvent]:
        if not log_path.exists():
            return []
        events = []
        with log_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(AuctionEvent.from_dict(json.loads(line)))
        return events

    @classmethod
    def recover(cls, initial_state: AuctionState, log_path: Path) -> "AuctionStateStore":
        """Rebuild a store (and its current state) entirely from a persisted
        event log -- the recovery-from-restart path Stage 7 requires."""
        store = cls(initial_state, log_path=None)  # don't re-append while replaying
        events = cls.load_events(log_path)
        skip = {e.payload.get("undone_event_id") for e in events if e.event_type == "EVENT_UNDONE"}
        store.events = events
        store.state = replay(initial_state, events, skip_event_ids=skip)
        store.log_path = log_path
        return store
