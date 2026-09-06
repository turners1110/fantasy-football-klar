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


def test_cleanup_f_warning_present_when_overrides_empty(cli):
    status = cli.api_operational_status()
    assert status["protected_player_warning"] is not None
    assert "BRAD AND REID" in status["protected_player_warning"]


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
    assert b["college_rights_count"] == 2
    assert b["unnamed_protected_count"] == 0
    assert b["total_occupied_count"] == 8
    assert b["open_auction_slots"] == 8


def test_cleanup_e_brad_shows_unnamed_protected_slot(cli):
    detail = cli.api_team_detail("Brad")
    b = detail["protected_breakdown"]
    assert b["unnamed_protected_count"] == 1
    assert b["total_occupied_count"] == 7
    assert b["open_auction_slots"] == 9
