"""Phase 4 Stage 1 tests: event replay, undo, correction, budget/roster
accounting, keeper/college-rights exclusion, minimum reserve, legal max
bid, position/FLEX needs, and event-log recovery."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from auction_engine.auction_events import AuctionEvent, make_player_sold_event
from auction_engine.auction_reducer import apply_event, replay, IllegalEventError
from auction_engine.auction_state import AuctionState, TeamState
from auction_engine.auction_state_store import AuctionStateStore
from auction_engine.auction_state_validation import validate, is_legal


def make_test_state() -> AuctionState:
    st = AuctionState(auction_id="test-2026", rules_version="v1", model_version="phase4-v1", sam_team_id="Sam")
    st.teams["Sam"] = TeamState(team_id="Sam", budget_remaining=223.0, roster=[
        {"player_id": "dart", "display_name": "Jaxson Dart", "position": "QB", "price": 11.0, "is_keeper": True},
    ], keeper_ids={"dart"})
    st.teams["Rival"] = TeamState(team_id="Rival", budget_remaining=200.0, roster=[])
    st.available_pool = {
        "allen": {"display_name": "Josh Allen", "position": "QB"},
        "rice": {"display_name": "Rashee Rice", "position": "WR"},
    }
    st.college_rights_excluded = {"mendoza"}
    return st


def test_player_sold_updates_budget_and_roster():
    st = make_test_state()
    ev = make_player_sold_event(1, "allen", "Josh Allen", "QB", "Sam", 22.0, "Rival")
    new_st = apply_event(st, ev)
    assert new_st.teams["Sam"].budget_remaining == pytest.approx(201.0)
    assert any(p["player_id"] == "allen" for p in new_st.teams["Sam"].roster)
    assert "allen" not in new_st.available_pool
    assert is_legal(new_st)


def test_duplicate_sale_rejected():
    st = make_test_state()
    ev = make_player_sold_event(1, "allen", "Josh Allen", "QB", "Sam", 22.0, "Rival")
    st2 = apply_event(st, ev)
    ev2 = make_player_sold_event(2, "allen", "Josh Allen", "QB", "Rival", 5.0, "Sam")
    with pytest.raises(IllegalEventError):
        apply_event(st2, ev2)


def test_keeper_cannot_be_sold_in_veteran_auction():
    st = make_test_state()
    ev = make_player_sold_event(1, "dart", "Jaxson Dart", "QB", "Rival", 15.0, "Sam")
    with pytest.raises(IllegalEventError):
        apply_event(st, ev)


def test_college_rights_player_cannot_be_sold():
    st = make_test_state()
    ev = make_player_sold_event(1, "mendoza", "Fernando Mendoza", "QB", "Sam", 1.0, "Sam")
    with pytest.raises(IllegalEventError):
        apply_event(st, ev)


def test_dart_and_mendoza_never_consume_auction_budget():
    """Direct regression test for the coordinator's addendum: Dart's
    keeper cost and Mendoza's college-rights status must never be
    modeled as veteran-auction spend."""
    st = make_test_state()
    assert st.teams["Sam"].budget_remaining == 223.0  # Dart's $11 already excluded from this pool
    assert "dart" in st.teams["Sam"].keeper_ids
    assert "mendoza" in st.college_rights_excluded
    assert "mendoza" not in st.available_pool  # never offered in the veteran auction


def test_sale_price_above_legal_max_bid_rejected():
    st = make_test_state()
    # Sam has 14 open slots (15 - 1 keeper), reserve = 13 * $1 = $13, legal max = 223-13 = $210
    ev = make_player_sold_event(1, "allen", "Josh Allen", "QB", "Sam", 211.0, "Rival")
    with pytest.raises(IllegalEventError):
        apply_event(st, ev)


def test_legal_max_bid_respects_slot_reserve():
    # UPDATED (official commissioner data repair): 16-player roster, not 15.
    st = make_test_state()
    team = st.teams["Sam"]
    assert team.open_slots == 15
    assert team.min_reserve == 14
    assert team.legal_max_bid == pytest.approx(209.0)


def test_replay_reproduces_saved_state_exactly():
    st = make_test_state()
    events = [
        make_player_sold_event(1, "allen", "Josh Allen", "QB", "Sam", 22.0, "Rival"),
        make_player_sold_event(2, "rice", "Rashee Rice", "WR", "Sam", 54.0, "Rival"),
    ]
    state1 = st
    for e in events:
        state1 = apply_event(state1, e)
    state2 = replay(st, events)
    assert state1.to_dict() == state2.to_dict()


def test_undo_restores_prior_state_exactly():
    store = AuctionStateStore(make_test_state())
    before = store.state.to_dict()
    store.record("PLAYER_SOLD", {
        "player_id": "allen", "display_name": "Josh Allen", "position": "QB",
        "winning_owner": "Sam", "sale_price": 22.0, "nominating_owner": "Rival",
    })
    assert store.state.to_dict() != before
    store.undo_last()
    after_undo = store.state.to_dict()
    # sequence_number and timestamp legitimately advance across an undo,
    # but every accounting field must match exactly
    assert after_undo["teams"] == before["teams"]
    assert after_undo["available_pool"] == before["available_pool"]
    assert after_undo["sold_players"] == before["sold_players"]


def test_sale_corrected_reverses_old_accounting_before_applying_new():
    store = AuctionStateStore(make_test_state())
    store.record("PLAYER_SOLD", {
        "player_id": "allen", "display_name": "Josh Allen", "position": "QB",
        "winning_owner": "Sam", "sale_price": 22.0, "nominating_owner": "Rival",
    })
    assert store.state.teams["Sam"].budget_remaining == pytest.approx(201.0)
    store.correct_sale("allen", "Josh Allen", "QB", "Rival", 30.0, "Sam")
    # Sam's budget must be fully restored, then Rival charged the corrected price
    assert store.state.teams["Sam"].budget_remaining == pytest.approx(223.0)
    assert store.state.teams["Rival"].budget_remaining == pytest.approx(170.0)
    assert any(p["player_id"] == "allen" for p in store.state.teams["Rival"].roster)
    assert not any(p["player_id"] == "allen" for p in store.state.teams["Sam"].roster)


def test_event_log_recovery_matches_live_state():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "events.jsonl"
        store = AuctionStateStore(make_test_state(), log_path=log_path)
        store.record("PLAYER_SOLD", {
            "player_id": "allen", "display_name": "Josh Allen", "position": "QB",
            "winning_owner": "Sam", "sale_price": 22.0, "nominating_owner": "Rival",
        })
        store.record("PLAYER_SOLD", {
            "player_id": "rice", "display_name": "Rashee Rice", "position": "WR",
            "winning_owner": "Rival", "sale_price": 40.0, "nominating_owner": "Sam",
        })
        live_state = store.state.to_dict()

        recovered = AuctionStateStore.recover(make_test_state(), log_path)
        assert recovered.state.to_dict() == live_state


def test_validate_flags_negative_budget():
    st = make_test_state()
    st.teams["Sam"].budget_remaining = -5.0
    violations = validate(st)
    assert any("negative budget" in v for v in violations)


def test_validate_flags_roster_over_fifteen():
    st = make_test_state()
    for i in range(20):
        st.teams["Sam"].roster.append({"player_id": f"filler_{i}", "display_name": f"Filler {i}",
                                        "position": "WR", "price": 1.0, "is_keeper": False})
    violations = validate(st)
    assert any("exceeds 15" in v for v in violations)


def test_position_and_flex_needs_computed_correctly():
    st = make_test_state()
    needs = st.teams["Sam"].legal_starting_needs()
    assert needs["QB"] == 0  # Dart already fills the 1 QB slot
    assert needs["RB"] == 2
    assert needs["WR"] == 2
    assert needs["TE"] == 1
    assert needs["FLEX"] == 3


def test_event_type_validation_rejects_unknown_type():
    with pytest.raises(ValueError):
        AuctionEvent(event_type="NOT_A_REAL_EVENT", sequence_number=1)


def test_multiple_teams_no_shared_mutable_roster_list():
    """Regression guard: TeamState.roster default_factory must not share
    a single list object across teams (a classic dataclass footgun)."""
    st = make_test_state()
    st.teams["Sam"].roster.append({"player_id": "x", "display_name": "X", "position": "WR", "price": 1.0, "is_keeper": False})
    assert not any(p["player_id"] == "x" for p in st.teams["Rival"].roster)
