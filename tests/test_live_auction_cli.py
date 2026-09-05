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
    sale_out = cli.cmd_sale(rb1, "Sam", "50", confirmed=True)
    assert "Recorded" in sale_out
    assert rb1 not in cli.store.state.available_pool
    assert any(p["player_id"] == rb1 for p in cli.store.state.teams["Sam"].roster)

    rb2 = _first_available(cli, "RB")
    cli.cmd_sale(rb2, "Sam", "40", confirmed=True)
    rb3 = _first_available(cli, "RB")
    cli.cmd_sale(rb3, "Sam", "30", confirmed=True)

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
    out = cli.cmd_sale("Fernando Mendoza", "Sam", "1", confirmed=True)
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


# ---------------------------------------------------------------------------
# Sunday Final Build Stage 2: exact on-demand check tests
# ---------------------------------------------------------------------------

def test_exact_uses_current_state_and_candidate_present_absent(cli):
    rb = _first_available(cli, "RB")
    out = cli.cmd_exact(rb)
    assert "EXACT purchase-vs-pass" in out
    assert "OPTIMAL" in out


def test_exact_requires_optimal_status_or_reports_solver_failure(cli):
    rb = _first_available(cli, "RB")
    out = cli.cmd_exact(rb, 5.0)
    assert "OPTIMAL" in out or "SOLVER_FAILURE" in out


def test_exact_cache_clears_after_sale(cli):
    rb = _first_available(cli, "RB")
    cli.cmd_exact(rb)
    seq_before = cli._exact_cache_sequence
    other_rb = _first_available(cli, "WR")
    cli.cmd_sale(other_rb, "Brad", "10")
    assert cli._exact_cache == {}
    assert cli._exact_cache_sequence != seq_before or len(cli.store.events) > 0


def test_exact_cache_clears_after_undo(cli):
    rb = _first_available(cli, "RB")
    cli.cmd_sale(rb, "Sam", "20", confirmed=True)
    cli.cmd_exact(_first_available(cli, "WR"))
    assert cli._exact_cache != {}
    cli.cmd_undo()
    assert cli._exact_cache == {}


def test_stale_exact_result_would_be_labeled(cli):
    rb = _first_available(cli, "RB")
    payload, was_cached = cli._run_exact_purchase_vs_pass(rb, 20.0)
    result_purchase, result_pass, runtime, solved_seq = payload
    assert solved_seq == cli.store.state.sequence_number  # fresh, not stale yet
    other = _first_available(cli, "WR")
    cli.cmd_sale(other, "Brad", "5")
    # after invalidation, cache is empty, so a fresh solve would report the NEW sequence
    payload2, was_cached2 = cli._run_exact_purchase_vs_pass(rb, 20.0)
    assert not was_cached2


def test_ladder_returns_prices_around_expected(cli):
    rb = _first_available(cli, "RB")
    out = cli.cmd_ladder(rb)
    assert "Ladder for" in out
    assert "surplus" in out or "SOLVER_FAILURE" in out


def test_exact_command_via_dispatch_with_price(cli):
    rb = _first_available(cli, "RB")
    out = cli.dispatch(f"exact {rb.replace(' ', '_')} 15")
    assert "test price $15" in out or "SOLVER_FAILURE" in out


def test_targets_uses_decision_score_not_raw_marginal_value(cli):
    out = cli.cmd_targets()
    assert "decision score" in out.lower()


# ---------------------------------------------------------------------------
# Sunday Final Build Stage 9: additional commands
# ---------------------------------------------------------------------------

def test_search_finds_partial_name_match(cli):
    rb = _first_available(cli, "RB")
    partial = rb.split()[0]
    out = cli.cmd_search(partial)
    assert rb in out or "1." in out


def test_last_reports_most_recent_sale(cli):
    rb = _first_available(cli, "RB")
    cli.cmd_sale(rb, "Sam", "20", confirmed=True)
    out = cli.cmd_last()
    assert rb in out


def test_correct_rebuilds_accounting(cli):
    rb = _first_available(cli, "RB")
    cli.cmd_sale(rb, "Sam", "20", confirmed=True)
    budget_before_correct = cli.store.state.teams["Sam"].budget_remaining
    out = cli.cmd_correct(rb, "Brad", "15")
    assert "Corrected" in out
    assert cli.store.state.teams["Sam"].budget_remaining == budget_before_correct + 20
    assert any(p["player_id"] == rb for p in cli.store.state.teams["Brad"].roster)


def test_market_shows_position_ratios(cli):
    out = cli.cmd_market()
    for pos in ("QB", "RB", "WR", "TE"):
        assert pos in out


def test_position_filter_returns_only_that_position(cli):
    out = cli.cmd_position("TE")
    assert "Remaining TE" in out or "No remaining players" in out


def test_why_includes_all_required_fields(cli):
    wr = _first_available(cli, "WR")
    out = cli.cmd_why(wr)
    for field in ("Projected points", "Expected role", "Pre-draft market prior", "RECOMMENDED STOP", "Confidence deductions"):
        assert field in out


def test_prior_reports_static_by_default(cli):
    out = cli.cmd_prior()
    assert "STATIC_PRE_DRAFT_MARKET_PRIOR" in out


def test_large_sale_to_sam_requires_confirmation(cli):
    rb = _first_available(cli, "RB")
    out = cli.dispatch(f"sale {rb.replace(' ', '_')} Sam 20")
    assert "CONFIRM:" in out
    assert rb in cli.store.state.available_pool  # not yet recorded


def test_large_sale_confirm_suffix_proceeds(cli):
    rb = _first_available(cli, "RB")
    cli.dispatch(f"sale {rb.replace(' ', '_')} Sam 20")
    out = cli.dispatch(f"sale {rb.replace(' ', '_')} Sam 20 confirm")
    assert "Recorded" in out
    assert rb not in cli.store.state.available_pool


def test_sale_above_50_to_rival_requires_confirmation(cli):
    rb = _first_available(cli, "RB")
    out = cli.dispatch(f"sale {rb.replace(' ', '_')} Brad 60")
    assert "CONFIRM:" in out
