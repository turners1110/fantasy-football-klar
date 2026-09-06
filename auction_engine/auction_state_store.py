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
        # ROOT-CAUSE FIX (undo-oscillation bug): this used to take
        # self.events[-1] unconditionally as "the last event to undo."
        # That is only correct for the FIRST undo call. After that,
        # self.events[-1] is the EVENT_UNDONE marker just appended by the
        # previous undo, not a real mutating event -- so a second undo
        # would "undo" that marker instead of walking one step further
        # back. Since replay() unconditionally skips every EVENT_UNDONE
        # event by type (see replay() below), skipping the marker's own
        # event_id in skip_event_ids had NO effect on the replayed state,
        # while the previously-undone real event was no longer in the
        # skip set at all -- so it silently came BACK. Net effect,
        # reproduced directly: 5 sales -> undo x3 walked sequence_number
        # 5 -> 4 -> 5 -> 5 (oscillating and then stuck) instead of
        # 5 -> 4 -> 3 -> 2, and the second undo actually re-added the
        # just-removed sale rather than removing an earlier one.
        #
        # Fix: track the full set of already-undone real-event ids, and
        # find the most recent event that is (a) not itself an
        # EVENT_UNDONE marker and (b) not already undone. Skip the
        # UNION of all previously-undone ids plus this new one, so N
        # consecutive undo calls always walk back exactly N real events,
        # never fewer and never oscillating.
        already_undone_ids = {
            e.payload.get("undone_event_id") for e in self.events if e.event_type == "EVENT_UNDONE"
        }
        undone = None
        for event in reversed(self.events):
            if event.event_type == "EVENT_UNDONE":
                continue
            if event.event_id in already_undone_ids:
                continue
            undone = event
            break
        if undone is None:
            raise ValueError("no events to undo")
        undo_event = AuctionEvent(
            event_type="EVENT_UNDONE", sequence_number=self._next_sequence(),
            payload={"undone_event_id": undone.event_id, "undone_event_type": undone.event_type},
        )
        self.events.append(undo_event)
        if self.log_path is not None:
            self._persist_event(undo_event)
        skip_event_ids = already_undone_ids | {undone.event_id}
        self.state = replay(self.initial_state, self.events, skip_event_ids=skip_event_ids)
        return self.state

    def correct_sale(self, player_id: str, display_name: str, position: str,
                      winning_owner: str, sale_price: float, nominating_owner: str | None = None,
                      projected_points: float | None = None) -> AuctionState:
        """GATE B FIX (V3 repair, Part 4): a correction used to recreate
        the player with ONLY name and position -- projected_points
        silently defaulted to 0.0 in the re-applied PLAYER_SOLD event,
        which would then corrupt every downstream lineup/valuation
        calculation for that player (a $0-point "ghost" version of a
        real starter). projected_points is now threaded through
        end-to-end so a correction preserves the same valuation-relevant
        metadata the original sale had."""
        event = self.record("SALE_CORRECTED", {
            "player_id": player_id, "display_name": display_name, "position": position,
            "winning_owner": winning_owner, "sale_price": sale_price, "nominating_owner": nominating_owner,
            "projected_points": projected_points,
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
