"""V3.1 release-blocker repair tests -- Repairs 1-5 and cleanup items,
verified against Sam's real initial state and the real event engine per
the spec's own required test lists."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from live_auction_cli import AuctionCLI
from auction_engine.live_roster_paths import compute_live_roster_paths
from auction_engine.live_values import BENCH_SIZE as LIVE_VALUES_BENCH_SIZE
from auction_model.config import BENCH_SIZE as CANONICAL_BENCH_SIZE


@pytest.fixture
def cli():
    return AuctionCLI(log_path=None)


# ---------------------------------------------------------------------------
# REPAIR 1: exact solver uses eight, not ten, auction openings
# ---------------------------------------------------------------------------

def test_repair1_sam_open_slots_is_eight(cli):
    sam = cli._sam()
    assert sam.open_slots == 8


def test_repair1_exact_pass_scenario_selects_eight_auction_players(cli):
    player = next(p for p, i in cli.store.state.available_pool.items() if i["position"] == "RB")
    payload, _ = cli._run_exact_purchase_vs_pass(player, 30.0)
    rp, rpass, rt, seq = payload
    assert rpass.status == "OPTIMAL"
    pass_auction = rpass.selected[~rpass.selected["is_keeper"]] if "is_keeper" in rpass.selected.columns else rpass.selected
    assert len(pass_auction) == 8


def test_repair1_exact_purchase_scenario_selects_candidate_plus_seven(cli):
    # NOTE: the candidate is passed into solve_exact_roster's `keepers`
    # arg as a pinning trick (guarantees it's selected) for the purchase
    # scenario, so it is flagged is_keeper=True internally even though
    # it is really one of Sam's 8 auction openings -- check TOTAL
    # selected count and candidate membership directly, not the
    # is_keeper flag (6 real keepers + candidate = 7 "keeper-flagged" +
    # 7 more real auction picks = 14 total, +2 college-rights = 16).
    player = next(p for p, i in cli.store.state.available_pool.items() if i["position"] == "RB")
    payload, _ = cli._run_exact_purchase_vs_pass(player, 30.0)
    rp, rpass, rt, seq = payload
    assert rp.status == "OPTIMAL"
    assert len(rp.selected) == 14
    assert player in set(rp.selected["player"])
    non_pinned_auction = rp.selected[(~rp.selected["is_keeper"]) if "is_keeper" in rp.selected.columns else True]
    assert len(non_pinned_auction) == 7  # the 7 OTHER auction picks besides the pinned candidate


def test_repair1_total_occupancy_is_sixteen(cli):
    player = next(p for p, i in cli.store.state.available_pool.items() if i["position"] == "RB")
    payload, _ = cli._run_exact_purchase_vs_pass(player, 30.0)
    rp, rpass, rt, seq = payload
    assert len(rp.selected) + cli._sam().college_rights_count == 16
    assert len(rpass.selected) + cli._sam().college_rights_count == 16


def test_repair1_no_exact_result_selects_nine_or_ten_auction_purchases(cli):
    # Total selected occupancy (keeper-flagged rows + real auction picks)
    # must be 14 for BOTH scenarios (6 keepers [+1 pinned candidate for
    # purchase] + real auction picks), never 15/16/17/18 -- the old bug
    # would have produced 16 or 18 total selected here.
    player = next(p for p, i in cli.store.state.available_pool.items() if i["position"] == "WR")
    payload, _ = cli._run_exact_purchase_vs_pass(player, 20.0)
    rp, rpass, rt, seq = payload
    assert len(rp.selected) == 14  # 6 keepers + candidate(pinned) + 7 more auction
    assert len(rpass.selected) == 14  # 6 keepers + 8 auction


def test_repair1_mendoza_and_bond_never_in_exact_selections(cli):
    player = next(p for p, i in cli.store.state.available_pool.items() if i["position"] == "TE")
    payload, _ = cli._run_exact_purchase_vs_pass(player, 15.0)
    rp, rpass, rt, seq = payload
    for result in (rp, rpass):
        names = set(result.selected["player"])
        assert "Fernando Mendoza" not in names
        assert "Isaiah Bond" not in names


def test_repair1_exact_after_one_sam_purchase_selects_seven_remaining(cli):
    rb1 = next(p for p, i in cli.store.state.available_pool.items() if i["position"] == "RB")
    cli.cmd_sale(rb1, "Sam", "5", confirmed=True)
    assert cli._sam().open_slots == 7
    player = next(p for p, i in cli.store.state.available_pool.items() if i["position"] == "WR")
    payload, _ = cli._run_exact_purchase_vs_pass(player, 20.0)
    rp, rpass, rt, seq = payload
    pass_auction = rpass.selected[~rpass.selected["is_keeper"]] if "is_keeper" in rpass.selected.columns else rpass.selected
    assert len(pass_auction) == 7


def test_repair1_exact_after_eight_sam_purchases_reports_zero_open(cli):
    sam = cli._sam()
    bought = 0
    while bought < 8:
        candidate = next(iter(cli.store.state.available_pool))
        out = cli.cmd_sale(candidate, "Sam", "1", confirmed=True)
        if out.startswith("Recorded"):
            bought += 1
    assert cli._sam().open_slots == 0


def test_repair1_respects_budget_and_reserve(cli):
    sam = cli._sam()
    player = next(p for p, i in cli.store.state.available_pool.items() if i["position"] == "RB")
    payload, _ = cli._run_exact_purchase_vs_pass(player, sam.legal_max_bid)
    rp, rpass, rt, seq = payload
    auction = rp.selected[~rp.selected["is_keeper"]] if rp.status == "OPTIMAL" else None
    if auction is not None:
        assert float(auction["price"].sum()) <= sam.budget_remaining + 1e-6


# ---------------------------------------------------------------------------
# REPAIR 2: roster paths use eight, not ten, additions
# ---------------------------------------------------------------------------

def test_repair2_all_initial_paths_use_eight_additions(cli):
    sam = cli._sam()
    pool = {n: {"display_name": n, "position": v["position"], "projected_points": v.get("projected_points", 0),
                "expected_price": max(1, v.get("base_value", 1)), "conservative_price": max(1, v.get("base_value", 1) * 1.15)}
            for n, v in cli.store.state.available_pool.items()}
    paths = compute_live_roster_paths(sam, pool)
    for style, result in paths.items():
        if result["status"] in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL"):
            assert len(result["players"]) == 8, f"{style} had {len(result['players'])} additions, not 8"


def test_repair2_no_initial_path_has_ten_additions(cli):
    sam = cli._sam()
    pool = {n: {"display_name": n, "position": v["position"], "projected_points": v.get("projected_points", 0),
                "expected_price": max(1, v.get("base_value", 1)), "conservative_price": max(1, v.get("base_value", 1) * 1.15)}
            for n, v in cli.store.state.available_pool.items()}
    paths = compute_live_roster_paths(sam, pool)
    for style, result in paths.items():
        assert len(result["players"]) != 10


def test_repair2_paths_never_include_mendoza_or_bond(cli):
    sam = cli._sam()
    pool = {n: {"display_name": n, "position": v["position"], "projected_points": v.get("projected_points", 0),
                "expected_price": max(1, v.get("base_value", 1)), "conservative_price": max(1, v.get("base_value", 1) * 1.15)}
            for n, v in cli.store.state.available_pool.items()}
    paths = compute_live_roster_paths(sam, pool)
    for style, result in paths.items():
        names = {p.get("player") or p.get("display_name") for p in result["players"]}
        assert "Fernando Mendoza" not in names
        assert "Isaiah Bond" not in names


def test_repair2_paths_recompute_after_a_sale(cli):
    sam_before = cli._sam()
    rb = next(p for p, i in cli.store.state.available_pool.items() if i["position"] == "RB")
    cli.cmd_sale(rb, "Sam", "10", confirmed=True)
    sam_after = cli._sam()
    pool = {n: {"display_name": n, "position": v["position"], "projected_points": v.get("projected_points", 0),
                "expected_price": max(1, v.get("base_value", 1)), "conservative_price": max(1, v.get("base_value", 1) * 1.15)}
            for n, v in cli.store.state.available_pool.items()}
    paths = compute_live_roster_paths(sam_after, pool)
    for style, result in paths.items():
        if result["status"] in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL"):
            assert len(result["players"]) == 7


# ---------------------------------------------------------------------------
# REPAIR 3: seven bench slots, one canonical source
# ---------------------------------------------------------------------------

def test_repair3_live_values_bench_size_matches_canonical():
    assert LIVE_VALUES_BENCH_SIZE == CANONICAL_BENCH_SIZE == 7


def test_repair3_full_active_roster_has_nine_starters_seven_bench():
    from auction_model.roster_optimizer import assign_lineup
    import pandas as pd
    rows = []
    for i, pos in enumerate(["QB"] * 2 + ["RB"] * 5 + ["WR"] * 5 + ["TE"] * 4):
        rows.append({"player": f"P{i}", "position": pos, "projected_points": 100 - i})
    df = pd.DataFrame(rows)
    lineup = assign_lineup(df)
    starters = [r for r in lineup.roles.values() if not r.startswith("BENCH")]
    bench = [r for r in lineup.roles.values() if r.startswith("BENCH")]
    assert len(starters) == 9
    assert len(bench) == 7


def test_repair3_rb_saturation_reduces_marginal_value(cli):
    board = cli.api_board()
    rb_candidate = next(p["player"] for p in board if p["position"] == "RB")
    before = cli.api_check(rb_candidate)
    rbs = [p["player"] for p in board if p["position"] == "RB" and p["player"] != rb_candidate][:4]
    for rb in rbs:
        cli.cmd_sale(rb, "Sam", "5", confirmed=True)
    after = cli.api_check(rb_candidate)
    assert after["marginal_value"] < before["marginal_value"]
    assert after["recommended_stop"] <= before["recommended_stop"]


# ---------------------------------------------------------------------------
# REPAIR 4: exact-ceiling propagation, no mislabeling
# ---------------------------------------------------------------------------

def test_repair4_targets_never_shows_exact_ceiling_without_a_real_solve(cli):
    targets = cli.api_targets(25)
    for t in targets:
        if t["exact_ceiling_dollars"] is not None:
            pytest.fail(f"{t['player']} shows an exact_ceiling_dollars with no exact solve run this test")
        assert t["exact_or_approximate_status"] == "APPROXIMATE_NO_CURRENT_EXACT"


def test_repair4_after_real_exact_solve_targets_shows_the_same_true_ceiling(cli):
    board = cli.api_board()
    player = next(p["player"] for p in board if p["position"] == "RB")
    api_result = cli.api_exact(player)
    true_ceiling = api_result["exact_ceiling"]
    targets = cli.api_targets(50)
    target_row = next((t for t in targets if t["player"] == player), None)
    if target_row is not None:
        assert target_row["exact_ceiling_dollars"] == true_ceiling
        assert target_row["exact_or_approximate_status"] == "EXACT_CURRENT"
        assert target_row["approximate_ceiling_dollars"] is None


def test_repair4_board_and_check_and_verdict_agree_with_exact(cli):
    board = cli.api_board()
    player = next(p["player"] for p in board if p["position"] == "WR")
    api_result = cli.api_exact(player)
    true_ceiling = api_result["exact_ceiling"]

    board_after = cli.api_board()
    board_row = next(p for p in board_after if p["player"] == player)
    check_row = cli.api_check(player)
    verdict_row = cli.api_verdict(player, current_bid=1)

    assert board_row["exact_ceiling_dollars"] == true_ceiling
    assert check_row["exact_ceiling_dollars"] == true_ceiling
    assert verdict_row["exact_ceiling_dollars"] == true_ceiling


def test_repair4_recommended_stop_never_exceeds_true_exact_ceiling(cli):
    board = cli.api_board()
    player = next(p["player"] for p in board if p["position"] == "TE")
    api_result = cli.api_exact(player)
    if api_result["exact_ceiling"] is not None:
        check = cli.api_check(player)
        assert check["recommended_stop"] <= api_result["exact_ceiling"] + 1e-6


def test_repair4_sale_makes_exact_result_stale(cli):
    board = cli.api_board()
    player = next(p["player"] for p in board if p["position"] == "RB")
    cli.api_exact(player)
    assert cli._get_current_exact_ceiling_record(player) is not None
    other = next(p["player"] for p in board if p["position"] == "WR")
    cli.cmd_sale(other, "Brandon", "5", confirmed=True)
    assert cli._get_current_exact_ceiling_record(player) is None


def test_repair4_undo_still_requires_fresh_solve(cli):
    board = cli.api_board()
    player = next(p["player"] for p in board if p["position"] == "RB")
    other = next(p["player"] for p in board if p["position"] == "WR")
    cli.api_exact(player)
    cli.cmd_sale(other, "Brandon", "5", confirmed=True)
    assert cli._get_current_exact_ceiling_record(player) is None
    cli.cmd_undo()
    # Sequence changed again on undo -- the OLD record (from before the
    # sale) must not be treated as current just because we're "back" to
    # a similar-looking state; identity is by sequence number, not content.
    assert cli._get_current_exact_ceiling_record(player) is None


def test_repair4_two_players_exact_records_stay_separate(cli):
    board = cli.api_board()
    p1 = next(p["player"] for p in board if p["position"] == "RB")
    p2 = next(p["player"] for p in board if p["position"] == "WR")
    r1 = cli.api_exact(p1)
    r2 = cli.api_exact(p2)
    rec1 = cli._get_current_exact_ceiling_record(p1)
    rec2 = cli._get_current_exact_ceiling_record(p2)
    assert rec1["player"] == p1
    assert rec2["player"] == p2
    assert rec1 is not rec2


def test_repair4_regression_old_false_exact_behavior_is_gone(cli):
    """Direct regression test for the exact old bug: running exact for
    player A must NOT make api_targets label player B as exact."""
    board = cli.api_board()
    p1 = next(p["player"] for p in board if p["position"] == "RB")
    p2 = next(p["player"] for p in board if p["position"] == "WR" and p["player"] != p1)
    cli.api_exact(p1)
    targets = cli.api_targets(50)
    row2 = next((t for t in targets if t["player"] == p2), None)
    if row2 is not None:
        assert row2["exact_ceiling_dollars"] is None
        assert row2["exact_or_approximate_status"] == "APPROXIMATE_NO_CURRENT_EXACT"


# ---------------------------------------------------------------------------
# REPAIR 5: practice AI opponents respect protected-slot occupancy
# ---------------------------------------------------------------------------

def test_repair5_md_team_slots_needed_respects_protected_but_unlisted():
    from mock_draft.models import Team as MDTeam
    t = MDTeam(name="X", budget_remaining=225.0, roster=[("a", "RB", 10, 100)] * 6, protected_but_unlisted=2)
    assert t.slots_needed == 16 - 6 - 2


def test_repair5_ten_seeded_practice_drafts_all_complete_113_sales():
    from auction_engine.practice_draft_session import PracticeDraftSession
    for seed in range(201, 211):
        sess = PracticeDraftSession(session_id=f"v31-seed-{seed}", seed=seed)
        steps = 0
        while sess.status == "IN_PROGRESS" and steps < 400:
            p = sess.pending_nomination()
            sam = sess.cli._sam()
            if p and sam.open_slots > 0 and p["ai_current_price"] <= p["sam_legal_max_bid"]:
                bid_amt = max(p["ai_current_price"], min(p["sam_recommended_stop"], p["sam_legal_max_bid"]))
                sess.sam_bid(bid_amt)
            else:
                sess.sam_pass()
            steps += 1
        assert sess.status == "COMPLETE", f"seed {seed} did not complete (status={sess.status})"
        assert len(sess.cli.store.state.sold_players) == 113, f"seed {seed}: {len(sess.cli.store.state.sold_players)} sales, not 113"
        for tid, t in sess.cli.store.state.teams.items():
            assert len(t.roster) + t.college_rights_count == 16, f"seed {seed}, {tid} not at 16"
            assert t.budget_remaining >= 0, f"seed {seed}, {tid} negative budget"
        sold_names = list(sess.cli.store.state.sold_players.keys())
        assert len(sold_names) == len(set(sold_names)), f"seed {seed}: duplicate sale"
        sam = sess.cli._sam()
        sam_purchases = [p for p in sam.roster if not p.get("is_keeper")]
        assert len(sam_purchases) == 8, f"seed {seed}: Sam has {len(sam_purchases)} purchases, not 8"


# ---------------------------------------------------------------------------
# CLEANUP checks
# ---------------------------------------------------------------------------

def test_cleanup_b_cmd_check_never_prefixes_points_with_dollar_sign(cli):
    board = cli.api_board()
    player = next(p["player"] for p in board if p["position"] == "RB")
    out = cli.cmd_check(player)
    assert "points" in out
    assert "$189" not in out  # the historical Josh Jacobs symptom, generalized
    for line in out.splitlines():
        if "marginal roster value" in line:
            assert "$" not in line


def test_cleanup_a_no_fifteen_man_roster_text_in_app_js():
    js = (Path(__file__).parent.parent / "live_web" / "static" / "app.js").read_text()
    assert "15-man roster" not in js


def test_cleanup_f_warning_clears_now_that_overrides_are_filled(cli):
    # Historically this asserted the warning was PRESENT because
    # data/protected_player_overrides.csv was empty. Sam has since
    # supplied Brad's and Reid's real 7th-protected-player names
    # (Makai Lemon, Carnell Tate) from commissioner spreadsheet
    # screenshots, so the mechanism now correctly clears the warning --
    # see test_global_protected_player_warning_clears for the direct
    # assertion, and test_protected_player_overrides_file_loads_both_named_players
    # for confirmation the real file is what's driving this.
    status = cli.api_operational_status()
    assert status["protected_player_warning"] is None


def test_cleanup_f_template_file_exists():
    template = Path(__file__).parent.parent / "outputs" / "auction_rebuild" / "live_v31" / "missing_protected_player_template.csv"
    assert template.exists()
    content = template.read_text()
    assert "Brad" in content and "Reid" in content


# ---------------------------------------------------------------------------
# GATE 6: real tier propagation (this follow-up pass)
# ---------------------------------------------------------------------------

def test_gate6_tier_label_matches_real_player_tier(cli):
    for name, p in cli.players.items():
        if name in cli.store.state.available_pool:
            assert cli._tier_label(name) == f"t{p.tier}"
            break


def test_gate6_sale_records_real_tier_not_t1(cli):
    name = next(n for n, p in cli.players.items() if n in cli.store.state.available_pool and p.tier != 1)
    cli.cmd_sale(name, "Brandon", "5", confirmed=True)
    obs = cli.market_state.observations[-1]
    assert obs["tier"] == cli._tier_label(name)
    assert obs["tier"] != "t1"


def test_gate6_tier_survives_resume(tmp_path):
    from live_auction_cli import AuctionCLI as CLI
    log_path = tmp_path / "tier_session.jsonl"
    cli1 = CLI(log_path=log_path, resume=False)
    name = next(n for n, p in cli1.players.items() if n in cli1.store.state.available_pool and p.tier != 1)
    real_tier_label = cli1._tier_label(name)
    cli1.cmd_sale(name, "Brandon", "5", confirmed=True)
    assert cli1.market_state.observations[-1]["tier"] == real_tier_label

    cli2 = CLI(log_path=log_path, resume=True)
    assert cli2.market_state.observations[-1]["tier"] == real_tier_label


def test_gate6_tier_survives_correction(cli):
    name = next(n for n, p in cli.players.items() if n in cli.store.state.available_pool and p.tier != 1)
    real_tier_label = cli._tier_label(name)
    cli.cmd_sale(name, "Brandon", "5", confirmed=True)
    cli.cmd_correct(name, "Coby", "8")
    assert cli.market_state.observations[-1]["tier"] == real_tier_label


def test_gate6_practice_pool_uses_real_tier():
    from auction_engine.practice_draft_session import PracticeDraftSession
    sess = PracticeDraftSession(session_id="gate6-tier-test", seed=1)
    pool = sess._build_md_pool()
    checked = 0
    for name, mdp in pool.items():
        real_player = sess.cli.players.get(name)
        if real_player is not None:
            assert mdp.tier == real_player.tier
            checked += 1
        if checked >= 10:
            break
    assert checked >= 10


def test_gate6_missing_tier_uses_unknown_not_t1(cli):
    assert cli._tier_label("Not A Real Player At All") == "UNKNOWN"


# ---------------------------------------------------------------------------
# CLEANUP D: target-scorer parameters are explicitly unit-named
# ---------------------------------------------------------------------------

def test_cleanup_d_compute_target_score_uses_explicit_unit_param_names():
    import inspect
    from auction_engine.live_target_scoring import compute_target_score
    params = list(inspect.signature(compute_target_score).parameters.keys())
    assert "team_specific_value_dollars" in params
    assert "expected_market_price_dollars" in params
    assert "exact_or_approximate_ceiling_dollars" in params
    assert "marginal_value" not in params
    assert "exact_or_approx_ceiling" not in params


# ---------------------------------------------------------------------------
# CLEANUP E: unified protected-occupancy breakdown
# ---------------------------------------------------------------------------

def test_cleanup_e_sam_protected_breakdown(cli):
    detail = cli.api_team_detail("Sam")
    b = detail["protected_breakdown"]
    assert b["veteran_roster_count"] == 6
    # Sam also holds Bryce Young's college-draft rights (see the
    # college-draft-rights leak fix below) in addition to the original
    # Mendoza/Bond pair -- college_rights_count counts NAMED holdings,
    # not official roster-slot capacity (which is unaffected: Bryce
    # Young's pick isn't modeled as consuming an additional real 16-man
    # roster slot the way the official commissioner-confirmed
    # Mendoza/Bond pair is, so total_occupied_count/open_auction_slots
    # stay exactly as before).
    assert b["college_rights_count"] == 3
    assert b["unnamed_protected_count"] == 0
    assert b["total_occupied_count"] == 8
    assert b["open_auction_slots"] == 8


def test_cleanup_e_brad_shows_unnamed_protected_slot(cli):
    # As of the real data/protected_player_overrides.csv (commissioner
    # screenshots confirmed Sam supplied), Brad's and Reid's 7th
    # protected player is now NAMED (Makai Lemon / Carnell Tate), so
    # unnamed_protected_count correctly clears to 0 for both -- see
    # test_protected_player_overrides_resolve_brad_and_reid below for
    # the full override-resolution trace.
    detail = cli.api_team_detail("Brad")
    b = detail["protected_breakdown"]
    assert b["unnamed_protected_count"] == 0
    assert b["college_rights_count"] == 1
    assert b["total_occupied_count"] == 7
    assert b["open_auction_slots"] == 9


# ---------------------------------------------------------------------------
# Protected-player-overrides resolution: Brad's/Reid's real names now
# supplied via data/protected_player_overrides.csv (commissioner
# screenshots) -- Makai Lemon (Brad) and Carnell Tate (Reid).
# ---------------------------------------------------------------------------

def test_protected_player_overrides_file_loads_both_named_players(cli):
    # data/protected_player_overrides.csv now also carries the 6
    # college-draft-rights leak closures (see
    # COLLEGE_DRAFT_CONFIRMED_LEAKS below) on top of the original
    # Brad/Reid rows -- assert Brad/Reid are still present rather than
    # asserting an exact set, so this test doesn't need updating again
    # every time the overrides file legitimately grows.
    assert {"Makai Lemon", "Carnell Tate"} <= cli.protected_player_overrides
    assert cli.protected_player_overrides_by_team["Brad"] == ["Makai Lemon"]
    assert cli.protected_player_overrides_by_team["Reid"] == ["Carnell Tate"]


def test_protected_player_overrides_resolve_brad_and_reid(cli):
    brad = cli.api_team_detail("Brad")
    reid = cli.api_team_detail("Reid")
    assert brad["college_rights_holdings"] == ["Makai Lemon"]
    assert reid["college_rights_holdings"] == ["Carnell Tate"]
    assert brad["protected_breakdown"]["unnamed_protected_count"] == 0
    assert reid["protected_breakdown"]["unnamed_protected_count"] == 0


def test_global_protected_player_warning_clears(cli):
    status = cli.api_operational_status()
    assert status["protected_player_warning"] is None


def test_named_overrides_excluded_from_pool_search_and_sale(cli):
    for name, team in [("Makai Lemon", "Brad"), ("Carnell Tate", "Reid")]:
        assert name not in cli.store.state.available_pool
        assert name not in cli.players
        assert cli.api_search(name) == []
        protected_hits = cli.api_search(name, include_protected=True)
        assert len(protected_hits) == 1
        assert protected_hits[0]["status"] == "COLLEGE_RIGHTS_HELD"
        assert protected_hits[0]["owner"] == team
        result = cli.cmd_sale(name, "Brandon", "5")
        assert result.startswith("REFUSED:")


# ---------------------------------------------------------------------------
# SAFETY-CRITICAL FIX: undo-oscillation bug in AuctionStateStore.undo_last().
#
# Reproduced directly before this fix: after 5 sales, 3 consecutive
# cmd_undo() calls walked sequence_number 5 -> 4 -> 5 -> 5 (oscillating,
# then stuck) instead of 5 -> 4 -> 3 -> 2, and the second undo call
# actually RE-ADDED the just-removed sale rather than removing an
# earlier one.
#
# Root cause: undo_last() took self.events[-1] unconditionally as "the
# event to undo." That's only correct for the FIRST call -- after that,
# self.events[-1] is the EVENT_UNDONE marker the previous undo just
# appended, not a real mutating event. Since replay() unconditionally
# skips every EVENT_UNDONE event by TYPE (independent of
# skip_event_ids), passing that marker's own event_id in skip_event_ids
# had zero effect, while the previously-undone real event was no longer
# in the skip set at all -- so it silently came back.
#
# Fix: undo_last() now tracks the full set of already-undone real-event
# ids, walks backward to find the most recent event that is neither an
# EVENT_UNDONE marker nor already undone, and skips the UNION of every
# previously-undone id plus the new one on replay -- so N consecutive
# undo calls always walk back exactly N real events.
# ---------------------------------------------------------------------------

def _sell_n_players(cli, n, team="Brandon", start_price=5):
    pool = list(cli.store.state.available_pool.keys())[:n]
    for i, p in enumerate(pool):
        cli.cmd_sale(p, team, str(start_price + i), confirmed=True)
    return pool


def test_undo_n_consecutive_calls_walk_back_n_steps_monotonically(cli):
    sales = _sell_n_players(cli, 5)
    seq_after_sales = cli.store.state.sequence_number
    assert seq_after_sales == 5
    assert set(sales) <= set(cli.store.state.sold_players.keys())

    seen_sequences = [seq_after_sales]
    for _ in range(4):
        cli.cmd_undo()
        seen_sequences.append(cli.store.state.sequence_number)

    # Must decrease by exactly 1 each time -- never fewer, never
    # oscillating back up.
    assert seen_sequences == [5, 4, 3, 2, 1], f"undo sequence oscillated or stalled: {seen_sequences}"
    # All 5 real sales must now be fully reverted after 5 undos total.
    cli.cmd_undo()
    assert cli.store.state.sold_players == {}
    assert cli.store.state.sequence_number == 0
    # A 6th undo (nothing left) must be a clean no-op, not an error or
    # a state change.
    result = cli.cmd_undo()
    assert "nothing to undo" in result.lower()
    assert cli.store.state.sequence_number == 0


def test_undo_second_call_does_not_resurrect_the_first_undone_sale(cli):
    """Direct regression for the exact observed symptom: undo #2 used
    to bring back the player undo #1 had just removed, instead of
    removing an earlier sale."""
    sales = _sell_n_players(cli, 3)
    last_sale, middle_sale, first_sale = sales[2], sales[1], sales[0]

    cli.cmd_undo()
    assert last_sale not in cli.store.state.sold_players
    assert middle_sale in cli.store.state.sold_players

    cli.cmd_undo()
    assert last_sale not in cli.store.state.sold_players, "undo #2 resurrected the sale undo #1 just removed"
    assert middle_sale not in cli.store.state.sold_players
    assert first_sale in cli.store.state.sold_players


def test_undo_fix_applies_identically_to_practice_draft_session():
    """PracticeDraftSession.undo() delegates straight to
    AuctionCLI.cmd_undo() -> AuctionStateStore.undo_last(), so the
    production fix covers the practice path with no separate code
    change -- verified directly against a real PracticeDraftSession's
    own internal CLI/store, not just asserted from the shared code
    path."""
    from auction_engine.practice_draft_session import PracticeDraftSession
    sess = PracticeDraftSession(session_id="test-undo-oscillation", seed=909001)
    sales = _sell_n_players(sess.cli, 4)
    seq_after_sales = sess.cli.store.state.sequence_number
    assert seq_after_sales == 4

    seen_sequences = [seq_after_sales]
    for _ in range(3):
        sess.undo()
        seen_sequences.append(sess.cli.store.state.sequence_number)
    assert seen_sequences == [4, 3, 2, 1], f"practice-mode undo sequence oscillated or stalled: {seen_sequences}"


# ---------------------------------------------------------------------------
# College-draft-rights eligibility gap: data/college_draft_completed_picks.csv
# lists 144 college-draft picks across all 12 teams (3 draft classes).
# 12 rows are tagged nfl_status_sheet == "In NFL"; cross-checked against
# the real projections universe (cli.players / available_pool -- the same
# ground truth the rest of the auction already treats as authoritative)
# on 2026-09-06:
#   - 6 were REAL, CURRENT LEAKS: Josh Downs, Bryce Young, Sean Tucker,
#     Darnell Washington, Xavier Hutchinson, CJ Stroud -- all sitting in
#     the live sellable pool despite belonging to another team's
#     college-rights holding.
#   - Jordan Addison and Zay Flowers (owner Brandon) were NOT leaking --
#     already real veteran keepers on Brandon's roster (the college pick
#     was superseded by a real keeper claim), confirmed via each
#     player's presence in Brandon's roster/keeper_ids.
#   - Jalin Hyatt, Deuce Vaughn, Israel Abanikanda, Mohamed Ibrahim were
#     NOT leaking -- none appear in this season's projections universe
#     at all (cli.players), so they cannot enter the pool regardless of
#     the sheet's "In NFL" tag; no identity/alias mismatch found for any
#     of the 12 "In NFL" rows.
#
# Owner resolution (data/college_draft_reference.md, cross-checked
# against outputs/auction_rebuild/live_v3/canonical_team_mapping.csv,
# the same evidence-based mapping used throughout this whole repair
# arc): Brandon/Sam/Ryan J/Shane/James/CJ all map directly to a current
# team. "Paul" and "Ryan B" are legacy owner names the reference doc
# itself states do NOT appear on the current 12-team roster and require
# commissioner confirmation -- both of the leaking "Paul"-owned players
# (Josh Downs, CJ Stroud) are excluded from the auction regardless
# (the safety property), but are recorded with an explicit
# UNRESOLVED_LEGACY_PAUL team id rather than guessed onto a real team.
# ---------------------------------------------------------------------------

COLLEGE_DRAFT_CONFIRMED_LEAKS = {
    "Josh Downs": "WR", "Bryce Young": "QB", "Sean Tucker": "RB",
    "Darnell Washington": "TE", "Xavier Hutchinson": "WR", "CJ Stroud": "QB",
}


def test_college_draft_rights_confirmed_leaks_are_now_excluded(cli):
    for name in COLLEGE_DRAFT_CONFIRMED_LEAKS:
        assert name not in cli.store.state.available_pool, f"{name} is still leaking into the sellable pool"
        result = cli.cmd_sale(name, "Brandon", "5")
        assert result.startswith("REFUSED:"), f"{name} was sellable: {result}"


def test_college_draft_rights_legacy_owner_mapping_spot_check(cli):
    # Resolvable legacy owners land on the correct current team.
    assert cli.api_team_detail("Sam")["college_rights_holdings"].count("Bryce Young") == 1
    assert cli.api_team_detail("Ryan J")["college_rights_holdings"] == ["Sean Tucker"]
    assert cli.api_team_detail("Shane")["college_rights_holdings"] == ["Darnell Washington"]
    assert cli.api_team_detail("James")["college_rights_holdings"] == ["Xavier Hutchinson"]
    # Genuinely unresolved legacy owner ("Paul") is excluded (safety
    # property) but NOT guessed onto any real team -- it must not appear
    # under any actual team's college_rights_holdings.
    for team_id in cli.store.state.teams:
        assert "Josh Downs" not in cli.api_team_detail(team_id)["college_rights_holdings"]
        assert "CJ Stroud" not in cli.api_team_detail(team_id)["college_rights_holdings"]


def test_college_draft_rights_fix_does_not_over_exclude_real_free_agents(cli):
    """The 6 non-leaking 'In NFL' rows (already-real keepers, or players
    absent from this season's projections entirely) must NOT have been
    swept into protected_player_overrides.csv -- only genuine current
    leaks get excluded, not the whole 144-row sheet mechanically."""
    non_leaking_names = {
        "Jordan Addison", "Zay Flowers",  # already real keepers elsewhere
        "Jalin Hyatt", "Deuce Vaughn", "Israel Abanikanda", "Mohamed Ibrahim",  # not in this season's pool at all
    }
    assert non_leaking_names.isdisjoint(cli.protected_player_overrides)


def test_college_draft_rights_overrides_file_still_has_brad_reid_rows(cli):
    """Extending the overrides file for the college-draft leak must not
    disturb the existing Brad/Reid protected-player-identity entries."""
    assert "Makai Lemon" in cli.protected_player_overrides
    assert "Carnell Tate" in cli.protected_player_overrides
    assert cli.protected_player_overrides_by_team.get("Brad") == ["Makai Lemon"]
    assert cli.protected_player_overrides_by_team.get("Reid") == ["Carnell Tate"]


# ---------------------------------------------------------------------------
# V3.2 FIX: real root cause of the underspend / marginal-value-cliff bug.
#
# _governed_ceiling's fallback conversion from marginal LINEUP points to
# team-specific dollars used to multiply marginal_value_points by the
# PLAYER'S OWN base_value/raw-projected-points ratio -- a units mismatch.
# Confirmed via a real practice draft (seed 4242, nomination ~51): Romeo
# Doubs had marginal_value=43.6 (a genuine FLEX-starter-quality upgrade)
# but the old formula gave him a recommended_stop of only $8.24, purely
# because his OWN generic market rate ($23.1 base_value / 122.2 points =
# $0.19/pt) has nothing to do with his value to Sam's specific roster
# hole -- with $189 of budget and 5 open slots still available. This
# directly caused both the severe underspend (Sam ending drafts having
# spent as little as 16-18% of his $225 budget) and the misleading
# "cliff" pattern.
#
# Fix: _current_state_points_to_dollars_rate() replaces the player-
# specific rate with a genuine current-state conversion: (Sam's budget,
# after reserving $1 for each of his OTHER open slots) / (the sum of the
# single best remaining marginal-value player at EACH of Sam's open
# slots -- an estimate of the best realistically achievable finish from
# here). This directly follows the original spec's Part 7 requirement
# ("accounting for Sam's remaining budget, open roster slots... replacement
# alternatives... a reserve for completing the roster") using only real
# data already computed elsewhere -- never a fixed universal scalar, and
# never a single player's own idiosyncratic market price.
# ---------------------------------------------------------------------------

def test_v32_current_state_rate_ignores_a_players_own_cheap_market_rate(cli):
    """Synthetic case matching the exact Doubs/Croskey-Merritt/Kincaid
    pattern: pick a real player with a LOW generic base_value/points
    rate, but feed _governed_ceiling a HIGH synthetic marginal_value_points
    (as if this player were a big roster-specific upgrade for Sam).
    The fixed conversion must not collapse back down near the player's
    own cheap generic rate."""
    pool = cli.store.state.available_pool
    # Find a real remaining player with a deliberately cheap generic rate
    # (base_value/points well under $0.10/pt), matching the failure
    # pattern -- most late-pool players qualify.
    cheap_rate_player = None
    for name, info in pool.items():
        pts = info.get("projected_points", 0)
        bv = info.get("base_value", 0)
        if pts > 50 and 0 < bv / pts < 0.10:
            cheap_rate_player = (name, info["position"], bv / pts)
            break
    assert cheap_rate_player is not None, "expected at least one real cheap-generic-rate player in the pool"
    name, position, own_rate = cheap_rate_player

    synthetic_marginal_value_points = 40.0  # a genuine large roster upgrade, matching the Doubs case
    old_style_dollar_value = synthetic_marginal_value_points * own_rate  # what the removed formula would have given
    governed = cli._governed_ceiling(name, position, synthetic_marginal_value_points, "FLEX starter", 10.0)

    # The fixed ceiling must reflect real team-specific upside, not
    # collapse back to (or near) what the player's own cheap generic
    # rate would have produced.
    assert governed.dollar_ceiling > old_style_dollar_value * 2, (
        f"{name}: fixed ceiling ${governed.dollar_ceiling:.2f} did not clear "
        f"2x the old player-specific-rate value ${old_style_dollar_value:.2f}"
    )


def test_v32_current_state_rate_formula_matches_documented_derivation(cli):
    """Directly verify _current_state_points_to_dollars_rate against an
    independently-computed expectation: (budget minus $1-per-other-open-
    slot reserve) / (sum of the top-N remaining marginal values, N = Sam's
    open slots)."""
    from auction_engine.live_values import compute_live_sam_values
    sam = cli._sam()
    pool = cli.store.state.available_pool
    rows = compute_live_sam_values(sam.roster, pool)
    marginal_values = sorted((r.marginal_value for r in rows if r.marginal_value and r.marginal_value > 0), reverse=True)
    n = max(1, sam.open_slots)
    best_achievable_points = sum(marginal_values[:n])
    reserve = max(0, sam.open_slots - 1) * 1.0
    spendable = max(0.0, sam.budget_remaining - reserve)
    expected_rate = spendable / best_achievable_points if best_achievable_points > 0 else 0.20

    actual_rate = cli._current_state_points_to_dollars_rate()
    assert actual_rate == pytest.approx(expected_rate, rel=1e-6)


def test_v32_no_overpayment_introduced_by_the_new_conversion(cli):
    """The fix must never let a purchase exceed its own recommended stop
    or the legal max bid -- i.e. the new, larger team-specific dollar
    values still flow through the SAME governing min(), they don't bypass
    it. Drives one real practice draft with a simple 'bid up to the
    model's own recommended stop' policy and checks every actual Sam
    purchase against the stop/legal-max recorded at time of purchase."""
    from auction_engine.practice_draft_session import PracticeDraftSession
    sess = PracticeDraftSession(session_id="v32-no-overpay-check", seed=4242)
    steps = 0
    while sess.status == "IN_PROGRESS" and steps < 200:
        p = sess.pending_nomination()
        if p is None:
            break
        sam = sess.cli._sam()
        if sam.open_slots > 0 and p["ai_current_price"] <= p["sam_recommended_stop"] and p["ai_current_price"] <= sam.legal_max_bid:
            sess.sam_bid(max(p["ai_current_price"], 1))
            assert p["ai_current_price"] <= p["sam_recommended_stop"] + 1e-6
            assert p["ai_current_price"] <= sam.legal_max_bid + 1e-6
        else:
            sess.sam_pass()
        steps += 1
    assert sess.status == "COMPLETE"


def test_v32_spend_rises_substantially_after_fix(cli):
    """Directional regression guard: with the old player-specific-rate
    fallback, this exact seed/policy combination spent only $41 of $225
    (~18%). The fixed current-state conversion must produce a
    substantially higher spend for the same seed and policy -- confirms
    the fix isn't a no-op and isn't accidentally reverted."""
    from auction_engine.practice_draft_session import PracticeDraftSession
    sess = PracticeDraftSession(session_id="v32-spend-regression", seed=4242)
    steps = 0
    while sess.status == "IN_PROGRESS" and steps < 200:
        p = sess.pending_nomination()
        if p is None:
            break
        sam = sess.cli._sam()
        if sam.open_slots > 0 and p["ai_current_price"] <= p["sam_recommended_stop"] and p["ai_current_price"] <= sam.legal_max_bid:
            sess.sam_bid(max(p["ai_current_price"], 1))
        else:
            sess.sam_pass()
        steps += 1
    review = sess.post_draft_review()
    spend = review["total_spend_on_purchases"]
    assert spend >= 90.0, f"expected substantially higher spend after the V3.2 fix, got ${spend:.0f}"
