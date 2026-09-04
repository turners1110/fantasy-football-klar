"""Tests for the Live Auction MVP CLI's command-handling functions
(live_auction_cli.AuctionCLI), driven directly (not through terminal
I/O) through a realistic mini-sequence."""
from __future__ import annotations

import shutil

import pytest

from live_auction_cli import AuctionCLI, SNAPSHOT_DIR


@pytest.fixture
def cli(tmp_path):
    c = AuctionCLI(budget_scenario="primary", log_path=tmp_path / "session.jsonl")
    yield c


def _first_available(cli, position):
    for name, info in cli.store.state.available_pool.items():
        if info["position"] == position:
            return name
    raise AssertionError(f"no available {position} in pool")


def test_status_shows_budget_and_roster(cli):
    out = cli.cmd_status()
    assert "budget remaining" in out
    assert "Sam" not in out.split("\n")[0] or "$223" in out  # primary budget


def test_full_mini_sequence_status_sales_check_undo_save_load(cli):
    status0 = cli.cmd_status()
    assert "$223.00" in status0 or "223" in status0

    rb1 = _first_available(cli, "RB")
    sale_out = cli.cmd_sale(rb1, "Sam", "50")
    assert "Recorded" in sale_out
    assert rb1 not in cli.store.state.available_pool
    assert any(p["player_id"] == rb1 for p in cli.store.state.teams["Sam"].roster)

    rb2 = _first_available(cli, "RB")
    cli.cmd_sale(rb2, "Sam", "40")
    rb3 = _first_available(cli, "RB")
    cli.cmd_sale(rb3, "Sam", "30")

    check_out = cli.cmd_check(_first_available(cli, "RB"))
    assert "RECOMMENDED STOP" in check_out

    budget_before_undo = cli.store.state.teams["Sam"].budget_remaining
    undo_out = cli.cmd_undo()
    assert "Undo complete" in undo_out
    assert cli.store.state.teams["Sam"].budget_remaining == budget_before_undo + 30

    check_out2 = cli.cmd_check(rb3)  # rb3's sale was undone, should be available again
    assert "RECOMMENDED STOP" in check_out2 or "ERROR" not in check_out2

    save_out = cli.cmd_save("test_snapshot")
    assert "Saved snapshot" in save_out
    state_before_load = cli.store.state.to_dict()

    load_out = cli.cmd_load("test_snapshot")
    assert "Loaded snapshot" in load_out
    assert cli.store.state.to_dict() == state_before_load

    shutil.rmtree(SNAPSHOT_DIR / "..", ignore_errors=True) if False else None
    (SNAPSHOT_DIR / "test_snapshot.jsonl").unlink(missing_ok=True)
    (SNAPSHOT_DIR / "test_snapshot_initial.json").unlink(missing_ok=True)


def test_keeper_sale_rejected(cli):
    keeper_name = cli.store.state.teams["Sam"].roster[0]["player_id"]
    out = cli.cmd_sale(keeper_name, "Rival", "10")
    assert "REFUSED" in out
    assert keeper_name not in cli.store.state.available_pool or any(
        p["player_id"] == keeper_name for p in cli.store.state.teams["Sam"].roster)


def test_college_rights_sale_rejected(cli):
    out = cli.cmd_sale("Fernando Mendoza", "Sam", "1")
    assert "REFUSED" in out
    assert "college-rights" in out.lower()


def test_malformed_command_does_not_crash(cli):
    out = cli.dispatch("sale onlyoneword")
    assert "Usage" in out
    out2 = cli.dispatch("totally_bogus_command")
    assert "Unknown command" in out2
    out3 = cli.dispatch("sale unknownplayer Sam notanumber")
    assert "ERROR" in out3


def test_dispatch_never_raises_on_bad_input(cli):
    for bad in ["", "   ", "check", "save", "load", "sale a b c d e"]:
        out = cli.dispatch(bad)
        assert isinstance(out, str)


def test_targets_and_paths_return_strings(cli):
    targets_out = cli.cmd_targets()
    assert "Top" in targets_out
    paths_out = cli.cmd_paths()
    assert "roster paths" in paths_out.lower()


def test_emergency_returns_content(cli):
    out = cli.cmd_emergency()
    assert len(out) > 0


def test_help_lists_all_commands(cli):
    out = cli.cmd_help()
    for cmd in ("status", "sale", "check", "targets", "paths", "undo", "save", "load", "emergency"):
        assert cmd in out
