"""Phase 2 auction-rebuild test suite: keeper pipeline, legal-lineup
utility, auction completion without forced-final-slot spending, and
regression tests proving the phase-1 QB-arbitrage finding stays fixed.

Organized to match the rebuild spec's four required groups (keeper tests,
lineup tests, auction tests, regression tests). Not exactly one test per
spec line item -- several related assertions are grouped into one test
function where that's the natural unit -- but every required check listed
in the spec is exercised somewhere below.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from auction_model.confirmed_keeper_pipeline import (
    compute_identity_issues, compute_team_states, normalize_name, unresolved_duplicate_identities,
)
from mock_draft.auction import run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.legal_lineup import (
    FLEX_ELIGIBLE, PRODUCTION_BENCH_WEIGHTS, STARTING_LINEUP,
    build_production_lineup, select_legal_lineup,
)
from mock_draft.models import Team

DATA_DIR = BASE_DIR / "data"
KEEPERS_PATH = DATA_DIR / "keepers_2026_confirmed.csv"
ADJUSTMENTS_PATH = DATA_DIR / "team_budget_adjustments_2026.csv"


# ---------------------------------------------------------------------------
# Keeper tests (#1-12)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def confirmed_keepers() -> pd.DataFrame:
    return pd.read_csv(KEEPERS_PATH)


@pytest.fixture(scope="module")
def adjustments() -> pd.DataFrame:
    return pd.read_csv(ADJUSTMENTS_PATH)


@pytest.fixture(scope="module")
def team_states(confirmed_keepers, adjustments):
    state_rows, conflict_rows = compute_team_states(confirmed_keepers, adjustments)
    return {r["team_id"]: r for r in state_rows}, conflict_rows


REQUIRED_KEEPER_COLUMNS = {
    "season", "team_id", "team_name", "player_id", "player_name", "position",
    "prior_salary", "keeper_cost", "franchise_tag", "keeper_status", "counts_as_keeper",
    "counts_as_active_roster", "auction_eligible", "source", "source_date", "confidence", "notes",
}


def test_1_confirmed_keeper_file_has_required_schema(confirmed_keepers):
    assert REQUIRED_KEEPER_COLUMNS.issubset(confirmed_keepers.columns)


def test_2_sam_has_exactly_six_veteran_keepers(confirmed_keepers):
    sam = confirmed_keepers[confirmed_keepers["team_name"] == "Sam"]
    assert int(sam["counts_as_keeper"].sum()) == 6


def test_3_sam_keeper_spend_is_162(team_states):
    states, _ = team_states
    assert states["Sam"]["keeper_spend"] == 162


def test_4_kenneth_walker_iii_is_36_dollar_franchise_tag(confirmed_keepers):
    row = confirmed_keepers[confirmed_keepers["player_name"] == "Kenneth Walker III"].iloc[0]
    assert row["keeper_cost"] == 36
    assert bool(row["franchise_tag"]) is True


def test_5_sam_required_player_costs(confirmed_keepers):
    expected = {
        "Garrett Wilson": 31, "David Montgomery": 45, "Cam Skattebo": 28,
        "Jaxson Dart": 11, "Quentin Johnston": 11,
    }
    for name, cost in expected.items():
        row = confirmed_keepers[confirmed_keepers["player_name"] == name].iloc[0]
        assert row["keeper_cost"] == cost, name


def test_6_college_rights_holds_not_counted_as_keepers(confirmed_keepers):
    for name in ("Isaiah Bond", "Fernando Mendoza"):
        row = confirmed_keepers[confirmed_keepers["player_name"] == name].iloc[0]
        assert bool(row["counts_as_keeper"]) is False
        assert bool(row["auction_eligible"]) is False
    sam = confirmed_keepers[confirmed_keepers["team_name"] == "Sam"]
    assert int(sam["counts_as_keeper"].sum()) == 6  # holds don't inflate the 6-keeper count


def test_7_sam_budget_scenarios(team_states):
    states, _ = team_states
    assert states["Sam"]["primary_auction_budget"] == 223
    assert states["Sam"]["conversions_scenario_auction_budget"] == 221


def test_8_no_team_exceeds_six_veteran_keepers(confirmed_keepers):
    for team, group in confirmed_keepers.groupby("team_name"):
        assert int(group["counts_as_keeper"].sum()) <= 6, team


def test_9_required_identity_spot_checks_resolve_uniquely(confirmed_keepers):
    identity_rows = compute_identity_issues(confirmed_keepers)
    checks = {r["player_name"]: r for r in identity_rows if r["issue_type"] == "REQUIRED_IDENTITY_CHECK"}
    for name in ("Kenneth Walker III", "Quentin Johnston", "Jaxson Dart", "Cam Skattebo"):
        assert checks[name]["detail"] == "resolved OK"


def test_10_no_player_on_two_teams_and_no_unresolved_duplicates(confirmed_keepers):
    identity_rows = compute_identity_issues(confirmed_keepers)
    assert unresolved_duplicate_identities(identity_rows) == []


def test_11_duplicate_identity_is_detected_when_present():
    """A synthetic duplicate (same normalized name, two teams) must be
    caught, not silently accepted."""
    dup = pd.DataFrame([
        {"team_name": "A", "player_name": "Cam Skattebo", "counts_as_keeper": True, "keeper_cost": 10},
        {"team_name": "B", "player_name": "Cam Skattebo", "counts_as_keeper": True, "keeper_cost": 10},
    ])
    identity_rows = compute_identity_issues(dup)
    unresolved = unresolved_duplicate_identities(identity_rows)
    assert len(unresolved) >= 2  # a DUPLICATE_NORMALIZED_NAME row and a PLAYER_ON_MULTIPLE_TEAMS row


def test_12_negative_auction_budget_is_detected_when_present():
    """A synthetic team that overspends its $400 on keepers alone must
    compute a negative primary_auction_budget -- the run_valuation.py
    confirmed-mode stop condition depends on this being detectable."""
    keepers = pd.DataFrame([
        {"team_name": "Overspent", "player_name": "P1", "keeper_cost": 390, "counts_as_keeper": True},
        {"team_name": "Overspent", "player_name": "P2", "keeper_cost": 50, "counts_as_keeper": True},
    ])
    empty_adj = pd.DataFrame(columns=["team_name", "amount"])
    state_rows, _ = compute_team_states(keepers, empty_adj)
    assert state_rows[0]["primary_auction_budget"] < 0


def test_confirmed_mode_never_falls_back_to_neutral_alpha_pool():
    """load_confirmed_pool_and_teams excludes every confirmed keeper/hold
    by direct name match -- it never calls the old heuristic keep-flag
    logic (which would silently guess keepers instead of using confirmed
    data)."""
    players, teams, meta = load_confirmed_pool_and_teams(budget_scenario="primary")
    excluded_names = {normalize_name(n) for n in pd.read_csv(KEEPERS_PATH)["player_name"]}
    pool_names = {normalize_name(n) for n in players}
    assert excluded_names.isdisjoint(pool_names)
    assert meta["excluded_count"] == len(pd.read_csv(KEEPERS_PATH))


# ---------------------------------------------------------------------------
# Lineup tests (#13-25)
# ---------------------------------------------------------------------------

def _roster(*entries):
    """entries: (name, pos, price, points) tuples."""
    return list(entries)


VALID_ROSTER = _roster(
    ("QB1", "QB", 10, 300), ("QB2", "QB", 1, 50), ("QB3", "QB", 1, 20),
    ("RB1", "RB", 20, 250), ("RB2", "RB", 15, 200), ("RB3", "RB", 5, 100), ("RB4", "RB", 1, 40),
    ("WR1", "WR", 20, 240), ("WR2", "WR", 15, 190), ("WR3", "WR", 5, 90), ("WR4", "WR", 1, 30),
    ("TE1", "TE", 10, 150), ("TE2", "TE", 1, 50),
)


def test_13_valid_roster_produces_legal_lineup():
    r = build_production_lineup(VALID_ROSTER)
    assert r.lineup_is_legal is True
    assert r.lineup_failure_reason is None


def test_14_missing_qb_flagged():
    roster = [x for x in VALID_ROSTER if x[1] != "QB"]
    r = build_production_lineup(roster)
    assert r.lineup_is_legal is False
    assert r.lineup_failure_reason == "MISSING_QB"


def test_15_missing_second_rb_flagged():
    roster = [x for x in VALID_ROSTER if x[1] != "RB"] + [("RBonly", "RB", 1, 50)]
    r = build_production_lineup(roster)
    assert r.lineup_failure_reason == "MISSING_SECOND_RB"


def test_16_missing_second_wr_flagged():
    roster = [x for x in VALID_ROSTER if x[1] != "WR"] + [("WRonly", "WR", 1, 50)]
    r = build_production_lineup(roster)
    assert r.lineup_failure_reason == "MISSING_SECOND_WR"


def test_17_missing_te_flagged():
    roster = [x for x in VALID_ROSTER if x[1] != "TE"]
    r = build_production_lineup(roster)
    assert r.lineup_failure_reason == "MISSING_TE"


def test_18_missing_flex_depth_flagged():
    # Exactly meets QB/RB/WR/TE minimums with nothing left over for FLEX.
    roster = _roster(
        ("QB1", "QB", 1, 100),
        ("RB1", "RB", 1, 100), ("RB2", "RB", 1, 90),
        ("WR1", "WR", 1, 100), ("WR2", "WR", 1, 90),
        ("TE1", "TE", 1, 80),
    )
    r = build_production_lineup(roster)
    assert r.lineup_failure_reason == "MISSING_FLEX_DEPTH"


def test_19_duplicate_player_flagged():
    roster = VALID_ROSTER + [("QB1", "QB", 1, 5)]
    r = build_production_lineup(roster)
    assert r.lineup_is_legal is False
    assert "DUPLICATE_PLAYER" in r.lineup_failure_reason


def test_20_bench_weights_match_spec_exactly():
    assert PRODUCTION_BENCH_WEIGHTS == {
        "first_reserve_rb": 0.30, "first_reserve_wr": 0.30,
        "second_reserve_rb": 0.15, "second_reserve_wr": 0.15,
        "backup_te": 0.10, "backup_qb": 0.075, "third_qb": 0.00,
        "other_legal_bench": 0.05,
    }


def test_21_bench_contributions_use_correct_tiered_weights():
    r = build_production_lineup(VALID_ROSTER)
    by_player = {b["player"]: b for b in r.bench_players}
    assert by_player["RB4"]["bench_weight"] == 0.30   # first reserve RB
    assert by_player["WR4"]["bench_weight"] == 0.30   # first reserve WR
    assert by_player["QB2"]["bench_weight"] == 0.075  # backup QB
    assert by_player["QB3"]["bench_weight"] == 0.00   # third QB


def test_22_flex_starter_gets_full_value_not_discounted():
    r = build_production_lineup(VALID_ROSTER)
    # RB3 (100 pts) is the best remaining RB/WR/TE after required slots are
    # filled, so it should be a FLEX starter counted at full value.
    assert "RB3" in r.starting_flex
    assert r.starting_lineup_points == pytest.approx(
        300 + 250 + 200 + 240 + 190 + 150 + 100 + 90 + 50, abs=0.01
    )  # QB1 + RB1,RB2 + WR1,WR2 + TE1 + FLEX(RB3,WR3,TE2)


def test_23_bench_qb_count_correct():
    r = build_production_lineup(VALID_ROSTER)
    assert r.bench_qb_count == 2  # QB2, QB3


def test_24_total_utility_equals_starting_plus_bench():
    r = build_production_lineup(VALID_ROSTER)
    assert r.total_roster_utility == pytest.approx(r.starting_lineup_points + r.bench_option_value, abs=0.01)


def test_25_greedy_lineup_selection_matches_brute_force_optimum():
    """The rebuild spec requires proof the greedy fill is optimal, not
    just fast. Brute-force every legal combination for small random
    rosters and confirm greedy always finds the same max starting_lineup_points."""
    import itertools

    rng = np.random.default_rng(7)
    for trial in range(15):
        roster = []
        for i in range(3):
            roster.append((f"QB{i}", "QB", 1, float(rng.integers(1, 300))))
        for i in range(5):
            roster.append((f"RB{i}", "RB", 1, float(rng.integers(1, 300))))
        for i in range(5):
            roster.append((f"WR{i}", "WR", 1, float(rng.integers(1, 300))))
        for i in range(3):
            roster.append((f"TE{i}", "TE", 1, float(rng.integers(1, 300))))

        greedy = build_production_lineup(roster)
        assert greedy.lineup_is_legal

        by_pos = {"QB": [], "RB": [], "WR": [], "TE": []}
        for name, pos, _price, pts in roster:
            by_pos[pos].append((name, pts))

        best = 0.0
        for qb in by_pos["QB"]:
            for rb_pair in itertools.combinations(by_pos["RB"], 2):
                for wr_pair in itertools.combinations(by_pos["WR"], 2):
                    for te in by_pos["TE"]:
                        used = {qb[0]} | {n for n, _ in rb_pair} | {n for n, _ in wr_pair} | {te[0]}
                        flex_pool = [
                            (n, p) for pos in FLEX_ELIGIBLE for n, p in by_pos[pos] if n not in used
                        ]
                        for flex_triplet in itertools.combinations(flex_pool, 3):
                            total = (
                                qb[1] + sum(p for _, p in rb_pair) + sum(p for _, p in wr_pair)
                                + te[1] + sum(p for _, p in flex_triplet)
                            )
                            best = max(best, total)
        assert greedy.starting_lineup_points == pytest.approx(best, abs=0.01), f"trial {trial}"


def test_team_total_points_is_deprecated():
    """raw_all_rostered_points-equivalent Team.total_points must warn on
    every access -- it must never be silently reused as fitness."""
    team = Team(name="X", budget_remaining=0, roster=[(n, p, pr, pts) for n, p, pr, pts in VALID_ROSTER])
    with pytest.warns(DeprecationWarning):
        team.total_points


def test_old_select_legal_lineup_preserved_exactly_for_phase1_audit():
    """audit_qb_arbitrage.py depends on select_legal_lineup's exact
    phase-1 field shape (starting_QB, roster_legality, etc.) -- confirm
    it's untouched."""
    r = select_legal_lineup([(n, p, pr, pts) for n, p, pr, pts in VALID_ROSTER])
    assert r.roster_legality == "LEGAL"
    assert hasattr(r, "starting_QB") and hasattr(r, "starting_RB") and hasattr(r, "roster_legality")


# ---------------------------------------------------------------------------
# Auction tests (#26-37)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def confirmed_pool_and_teams():
    return load_confirmed_pool_and_teams(budget_scenario="primary")


def _run_auctions(players, teams_template, seeds):
    results = []
    for seed in seeds:
        rng = np.random.default_rng(seed)
        log, final_teams = run_single_auction(players, teams_template, rng)
        results.append((log, final_teams))
    return results


def test_26_every_team_ends_with_fifteen_players(confirmed_pool_and_teams):
    players, teams, _ = confirmed_pool_and_teams
    for log, final_teams in _run_auctions(players, teams, range(5)):
        for name, team in final_teams.items():
            assert len(team.roster) == 15, name


def test_27_no_negative_budgets(confirmed_pool_and_teams):
    players, teams, _ = confirmed_pool_and_teams
    for log, final_teams in _run_auctions(players, teams, range(5)):
        for name, team in final_teams.items():
            assert team.budget_remaining >= 0, name


def test_28_forced_final_slot_always_false(confirmed_pool_and_teams):
    players, teams, _ = confirmed_pool_and_teams
    for log, _ in _run_auctions(players, teams, range(3)):
        assert all(entry["forced_final_slot"] is False for entry in log)


def test_29_every_sale_is_organic(confirmed_pool_and_teams):
    players, teams, _ = confirmed_pool_and_teams
    for log, _ in _run_auctions(players, teams, range(3)):
        assert all(entry["sale_is_organic"] is True for entry in log)


def test_30_every_sale_has_valid_bid_fields(confirmed_pool_and_teams):
    players, teams, _ = confirmed_pool_and_teams
    for log, _ in _run_auctions(players, teams, range(3)):
        for entry in log:
            assert entry["bidder_count"] >= 1
            assert entry["sale_price"] >= 1.0
            assert entry["second_highest_bid"] <= entry["sale_price"]


def test_31_max_bid_cap_reserves_one_dollar_per_remaining_slot():
    team = Team(name="X", budget_remaining=50, roster=[("P", "RB", 10, 100)] * 1)
    # slots_needed depends on cfg.REQUIRED_ROSTER_SIZE via len(roster); build directly:
    team.roster = [(f"P{i}", "RB", 1, 1) for i in range(10)]  # 10 rostered -> 5 slots needed (15-10)
    cap = team.max_bid_cap()
    # cap = budget - MIN_PRICE * (slots_needed - 1)
    from mock_draft import config_bridge as cfg
    expected = max(cfg.MIN_PRICE, team.budget_remaining - cfg.MIN_PRICE * max(0, team.slots_needed - 1))
    assert cap == expected


def test_32_unspent_cash_is_legal_and_observed(confirmed_pool_and_teams):
    players, teams, _ = confirmed_pool_and_teams
    any_leftover = False
    for log, final_teams in _run_auctions(players, teams, range(8)):
        if any(t.budget_remaining > 0 for t in final_teams.values()):
            any_leftover = True
    assert any_leftover, "expected at least one team to end with unspent cash across 8 seeds"


def test_33_no_duplicate_players_on_any_roster(confirmed_pool_and_teams):
    players, teams, _ = confirmed_pool_and_teams
    for log, final_teams in _run_auctions(players, teams, range(5)):
        for name, team in final_teams.items():
            names = [r[0] for r in team.roster]
            assert len(names) == len(set(names)), name


def test_34_no_roster_exceeds_fifteen_players(confirmed_pool_and_teams):
    players, teams, _ = confirmed_pool_and_teams
    for log, final_teams in _run_auctions(players, teams, range(5)):
        for name, team in final_teams.items():
            assert len(team.roster) <= 15, name


def test_35_pick_count_matches_total_required_slots(confirmed_pool_and_teams):
    players, teams, _ = confirmed_pool_and_teams
    total_slots = sum(t.slots_needed for t in teams.values())
    for log, final_teams in _run_auctions(players, teams, range(5)):
        assert len(log) == total_slots


def test_36_auction_does_not_crash_across_many_seeds(confirmed_pool_and_teams):
    players, teams, _ = confirmed_pool_and_teams
    for seed in range(20):
        rng = np.random.default_rng(seed)
        log, final_teams = run_single_auction(players, teams, rng)
        assert len(log) > 0


def test_37_sale_price_never_exceeds_winning_teams_pre_sale_budget(confirmed_pool_and_teams):
    players, teams, _ = confirmed_pool_and_teams
    for log, _ in _run_auctions(players, teams, range(3)):
        for entry in log:
            assert entry["sale_price"] <= entry["budget_before"] + 1e-6


# ---------------------------------------------------------------------------
# Regression tests (#38-42)
# ---------------------------------------------------------------------------

PRIOR_ROSTERS_CSV = BASE_DIR / "outputs" / "auction_rebuild" / "audit" / "prior_winning_genome_rosters.csv"
PRIOR_DECOMP_CSV = BASE_DIR / "outputs" / "auction_rebuild" / "audit" / "prior_qb_arbitrage_decomposition.csv"


@pytest.mark.skipif(not PRIOR_DECOMP_CSV.exists(), reason="phase-1 preserved audit artifact not present")
def test_38_old_winning_genome_no_longer_credited_for_all_qb_points():
    decomp = pd.read_csv(PRIOR_DECOMP_CSV)
    avg_old = decomp["qb_points_under_old_objective"].mean()
    avg_new = decomp["qb_points_under_new_objective"].mean()
    assert avg_new < avg_old * 0.25  # ~82% reduction observed in phase 1; assert it stays dramatically lower


@pytest.mark.skipif(not PRIOR_DECOMP_CSV.exists(), reason="phase-1 preserved audit artifact not present")
def test_39_corrected_utility_below_old_score():
    decomp = pd.read_csv(PRIOR_DECOMP_CSV)
    assert (decomp["new_legal_lineup_total_utility"] < decomp["old_total_roster_score_sum_all_15"]).all()


@pytest.mark.skipif(not PRIOR_ROSTERS_CSV.exists(), reason="phase-1 preserved audit artifact not present")
def test_40_illegal_prior_rosters_remain_flagged():
    rosters = pd.read_csv(PRIOR_ROSTERS_CSV)
    illegal_count = 0
    for match_id, group in rosters.groupby("match"):
        roster = list(zip(group["player"], group["position"], group["price"], group["projected_points"]))
        result = select_legal_lineup(roster)
        if result.roster_legality != "LEGAL":
            illegal_count += 1
    assert illegal_count > 0, "expected at least some of the 40 audited rosters to remain illegal"


def test_41_plus_813_result_marked_retracted():
    strategy_md = (BASE_DIR / "mock_draft" / "STRATEGY.md").read_text()
    assert "RETRACTED" in strategy_md
    assert "+813" in strategy_md and "retracted" in strategy_md.lower()


def test_42_strategy_md_no_longer_presents_qb_overweighting_as_validated():
    strategy_md = (BASE_DIR / "mock_draft" / "STRATEGY.md").read_text()
    assert "RETRACTED PENDING REVALIDATION" in strategy_md
    assert "not validated findings" in strategy_md.lower() or "not remain approved" in strategy_md.lower() or "no strategic recommendation" in strategy_md.lower()
