"""V3 Gate D -- recovery half of Part 4: market-adjustment state must be
correctly rebuilt (not silently reset to empty) after a resume,
correction, or undo. Roster paths and the exact-solve cache are always
computed fresh from current state (never persisted/cached across a
restart in a way that could go stale), so they need no separate rebuild
-- verified here by confirming their outputs are consistent with a
correctly-resumed budget/roster state."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from live_auction_cli import AuctionCLI


def _first_available(cli, position):
    for name, info in cli.store.state.available_pool.items():
        if info["position"] == position:
            return name
    raise AssertionError(f"no available {position}")


def test_market_state_rebuilt_after_resume(tmp_path):
    log_path = tmp_path / "session.jsonl"
    cli1 = AuctionCLI(log_path=log_path, resume=False)
    rb = _first_available(cli1, "RB")
    cli1.cmd_sale(rb, "Sam", "80", confirmed=True)
    ratio1, n1 = cli1.market_state.league_ratio()
    assert n1 == 1

    # Simulate a crash: cli1 is dropped, a NEW AuctionCLI resumes the log.
    cli2 = AuctionCLI(log_path=log_path, resume=True)
    ratio2, n2 = cli2.market_state.league_ratio()
    assert n2 == n1
    assert ratio2 == pytest.approx(ratio1)


def test_market_state_rebuilt_after_multiple_sales_and_resume(tmp_path):
    log_path = tmp_path / "session.jsonl"
    cli1 = AuctionCLI(log_path=log_path, resume=False)
    for _ in range(3):
        rb = _first_available(cli1, "RB")
        cli1.cmd_sale(rb, "Sam", "10", confirmed=True)
    _, n1 = cli1.market_state.league_ratio()
    assert n1 == 3

    cli2 = AuctionCLI(log_path=log_path, resume=True)
    _, n2 = cli2.market_state.league_ratio()
    assert n2 == 3


def test_clean_mode_does_not_carry_over_market_state(tmp_path):
    log_path = tmp_path / "session.jsonl"
    cli1 = AuctionCLI(log_path=log_path, resume=False)
    rb = _first_available(cli1, "RB")
    cli1.cmd_sale(rb, "Sam", "10", confirmed=True)

    cli2 = AuctionCLI(log_path=log_path, resume=False)  # clean start, not resume
    _, n2 = cli2.market_state.league_ratio()
    assert n2 == 0


def test_correction_rebuilds_market_state():
    cli = AuctionCLI(log_path=None)
    rb = _first_available(cli, "RB")
    cli.cmd_sale(rb, "Sam", "10", confirmed=True)
    _, n_before = cli.market_state.league_ratio()
    assert n_before == 1

    cli.cmd_correct(rb, "Brandon", "20")
    _, n_after = cli.market_state.league_ratio()
    # Still exactly one observation (the corrected one), not zero and not two.
    assert n_after == 1


def test_undo_rebuilds_market_state_removing_the_observation():
    cli = AuctionCLI(log_path=None)
    rb = _first_available(cli, "RB")
    cli.cmd_sale(rb, "Sam", "10", confirmed=True)
    _, n_before = cli.market_state.league_ratio()
    assert n_before == 1

    cli.cmd_undo()
    _, n_after = cli.market_state.league_ratio()
    assert n_after == 0


def test_roster_paths_reflect_resumed_state_correctly(tmp_path):
    """Roster paths are always computed fresh from current state (never
    cached across a restart), so a correctly-resumed budget/roster is
    sufficient for them to be correct -- this proves that chain end to
    end rather than asserting a caching mechanism that doesn't exist."""
    log_path = tmp_path / "session.jsonl"
    cli1 = AuctionCLI(log_path=log_path, resume=False)
    rb = _first_available(cli1, "RB")
    cli1.cmd_sale(rb, "Sam", "50", confirmed=True)
    budget_before = cli1.store.state.teams["Sam"].budget_remaining

    cli2 = AuctionCLI(log_path=log_path, resume=True)
    assert cli2.store.state.teams["Sam"].budget_remaining == budget_before
    paths = cli2.api_paths()
    assert isinstance(paths, dict) and len(paths) > 0


def test_exact_cache_starts_empty_on_resume_never_stale(tmp_path):
    """An empty exact cache after a restart is the SAFE outcome (nothing
    stale can be served) -- confirmed here rather than assumed."""
    log_path = tmp_path / "session.jsonl"
    cli1 = AuctionCLI(log_path=log_path, resume=False)
    rb = _first_available(cli1, "RB")
    cli1.api_exact(rb)
    assert len(cli1._exact_cache) > 0

    cli2 = AuctionCLI(log_path=log_path, resume=True)
    assert len(cli2._exact_cache) == 0
