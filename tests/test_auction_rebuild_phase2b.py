"""Phase 2B test suite: positional-feasibility gate, zero-utility bid
gate, position caps, shared eligibility integration, and budget
reconciliation. This is the corrective round after phase 2 was reopened
-- the tests below specifically target the failure modes the reopening
cited: the MISSING_TE/MISSING_QB illegal rosters, the five-QB roster, and
mock_draft/data.py bypassing the real eligibility classifier.
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

from auction_model import auction_eligibility as ae
from auction_model import data_pipeline
from mock_draft.archetypes import ARCHETYPE_NAMES
from mock_draft.auction import resolve_bid, run_single_auction
from mock_draft.data import load_confirmed_pool_and_teams
from mock_draft.feasibility import (
    DEFAULT_POSITION_MAX, check_roster_completion_feasibility, position_urgency,
)
from mock_draft.legal_lineup import build_production_lineup, partial_lineup_value
from mock_draft.models import Player, Team

DATA_DIR = BASE_DIR / "data"


def _roster(*entries):
    return list(entries)


def _pool(entries):
    """entries: iterable of (name, position) -> Player dict."""
    return {
        name: Player(name=name, position=pos, base_value=5.0, tier=1, tier_size=4, tier_rank=1, projected_points=50.0)
        for name, pos in entries
    }


def _rich_pool(extra=()):
    """A realistically deep pool (plenty of every position) so a test can
    isolate ONE specific feasibility question without the pool itself
    running thin on an unrelated position -- real auctions have hundreds
    of players available, not the 1-2 a narrowly-scoped test might list."""
    entries = [(f"depthQB{i}", "QB") for i in range(5)] + [(f"depthRB{i}", "RB") for i in range(10)]
    entries += [(f"depthWR{i}", "WR") for i in range(10)] + [(f"depthTE{i}", "TE") for i in range(5)]
    pool = _pool(entries)
    pool.update(_pool(extra))
    return pool


# ---------------------------------------------------------------------------
# 1-7: feasibility preserves a path to each required slot / cash reserve
# ---------------------------------------------------------------------------

def test_01_candidate_purchase_preserves_path_to_one_qb():
    roster = _roster(("RB1", "RB", 1, 10), ("RB2", "RB", 1, 10), ("WR1", "WR", 1, 10), ("WR2", "WR", 1, 10), ("TE1", "TE", 1, 10))
    pool = _rich_pool([("WR3", "WR")])
    # 10 slots left; buying a 2nd WR now (no QB secured yet) must remain
    # feasible only if a QB is still reachable given remaining slots/pool.
    feas = check_roster_completion_feasibility(roster, 300, 10, pool, candidate_player=pool["WR3"], candidate_price=1)
    assert feas.is_feasible  # plenty of slots/pool left, QB still reachable
    # But with only 1 slot left and no QB yet, buying WR3 must be blocked.
    feas2 = check_roster_completion_feasibility(roster, 300, 1, pool, candidate_player=pool["WR3"], candidate_price=1)
    assert not feas2.is_feasible
    assert feas2.failure_reason == "POSITIONAL_INFEASIBILITY"


def test_02_candidate_purchase_preserves_path_to_one_te():
    roster = _roster(*[(f"WR{i}", "WR", 1, 10) for i in range(10)], ("QB1", "QB", 1, 10), *[(f"RB{i}", "RB", 1, 10) for i in range(3)])
    pool = _pool([("TE1", "TE"), ("WR11", "WR")])
    feas = check_roster_completion_feasibility(roster, 50, 1, pool, candidate_player=pool["WR11"], candidate_price=1)
    assert not feas.is_feasible
    assert feas.failure_reason == "POSITIONAL_INFEASIBILITY"
    feas_te = check_roster_completion_feasibility(roster, 50, 1, pool, candidate_player=pool["TE1"], candidate_price=1)
    assert feas_te.is_feasible


def test_03_candidate_purchase_preserves_two_rb():
    roster = _roster(("RB1", "RB", 1, 10), ("QB1", "QB", 1, 10), ("WR1", "WR", 1, 10), ("WR2", "WR", 1, 10), ("TE1", "TE", 1, 10))
    pool = _pool([("WR3", "WR")])  # no RB left in the pool at all
    feas = check_roster_completion_feasibility(roster, 50, 2, pool, candidate_player=pool["WR3"], candidate_price=1)
    assert not feas.is_feasible  # only 1 RB rostered, none left in pool, still need 1 more


def test_04_candidate_purchase_preserves_two_wr():
    roster = _roster(("WR1", "WR", 1, 10), ("QB1", "QB", 1, 10), ("RB1", "RB", 1, 10), ("RB2", "RB", 1, 10), ("TE1", "TE", 1, 10))
    pool = _pool([("RB3", "RB")])  # no WR left in the pool
    feas = check_roster_completion_feasibility(roster, 50, 2, pool, candidate_player=pool["RB3"], candidate_price=1)
    assert not feas.is_feasible


def test_05_candidate_purchase_preserves_three_flex():
    # Exactly meets QB/RB/WR/TE minimums, one slot left, one FLEX still needed.
    roster = _roster(("QB1", "QB", 1, 10), ("RB1", "RB", 1, 10), ("RB2", "RB", 1, 10),
                      ("WR1", "WR", 1, 10), ("WR2", "WR", 1, 10), ("TE1", "TE", 1, 10),
                      ("F1", "RB", 1, 10), ("F2", "WR", 1, 10))
    pool = _pool([("K1", "QB"), ("F3", "RB")])  # a 2nd QB would waste the last FLEX-eligible slot
    feas_bad = check_roster_completion_feasibility(roster, 50, 1, pool, candidate_player=pool["K1"], candidate_price=1)
    assert not feas_bad.is_feasible
    feas_ok = check_roster_completion_feasibility(roster, 50, 1, pool, candidate_player=pool["F3"], candidate_price=1)
    assert feas_ok.is_feasible


def test_06_candidate_purchase_preserves_fifteen_total_roster_spots():
    roster = _roster(*[(f"P{i}", "RB", 1, 10) for i in range(15)])
    pool = _pool([("Extra", "RB")])
    feas = check_roster_completion_feasibility(roster, 50, 0, pool, candidate_player=pool["Extra"], candidate_price=1)
    assert not feas.is_feasible
    assert feas.failure_reason == "ROSTER_FULL"


def test_07_candidate_purchase_preserves_minimum_cash_reserve():
    roster = _roster(("QB1", "QB", 1, 10), ("RB1", "RB", 1, 10), ("RB2", "RB", 1, 10),
                      ("WR1", "WR", 1, 10), ("WR2", "WR", 1, 10), ("TE1", "TE", 1, 10))
    pool = _rich_pool([("F1", "RB")])
    # 3 slots left after buying F1 -- needs $1 each remaining = $3 minimum.
    feas_ok = check_roster_completion_feasibility(roster, 4, 4, pool, candidate_player=pool["F1"], candidate_price=1)
    assert feas_ok.is_feasible
    feas_bad = check_roster_completion_feasibility(roster, 2, 4, pool, candidate_player=pool["F1"], candidate_price=1)
    assert not feas_bad.is_feasible
    assert feas_bad.failure_reason == "INSUFFICIENT_RESERVE"


# ---------------------------------------------------------------------------
# 8-9: position urgency / endgame slot restriction
# ---------------------------------------------------------------------------

def test_08_position_urgency_blocks_an_optional_player():
    # 2 slots left, still need QB + TE -- per the spec's own worked example.
    roster = _roster(*[(f"WR{i}", "WR", 1, 10) for i in range(6)], ("RB1", "RB", 1, 10), ("RB2", "RB", 1, 10),
                      ("RB3", "RB", 1, 10), ("RB4", "RB", 1, 10), ("RB5", "RB", 1, 10))
    urgency = position_urgency(roster, remaining_slots=2)
    assert urgency["qb_deficit"] == 1
    assert urgency["te_deficit"] == 1
    assert urgency["position_deadline"] == 0  # no more optional purchases allowed
    pool = _pool([("WR7", "WR"), ("QB1", "QB")])
    feas_wr = check_roster_completion_feasibility(roster, 50, 2, pool, candidate_player=pool["WR7"], candidate_price=1)
    assert not feas_wr.is_feasible


def test_09_last_open_slot_accepts_only_the_missing_required_position():
    roster = _roster(("QB1", "QB", 1, 10), ("RB1", "RB", 1, 10), ("RB2", "RB", 1, 10),
                      ("WR1", "WR", 1, 10), ("WR2", "WR", 1, 10),
                      *[(f"F{i}", "RB", 1, 10) for i in range(9)])
    pool = _pool([("TE1", "TE"), ("RB99", "RB")])
    feas_te = check_roster_completion_feasibility(roster, 5, 1, pool, candidate_player=pool["TE1"], candidate_price=1)
    assert feas_te.is_feasible
    feas_rb = check_roster_completion_feasibility(roster, 5, 1, pool, candidate_player=pool["RB99"], candidate_price=1)
    assert not feas_rb.is_feasible


# ---------------------------------------------------------------------------
# 10-11: zero/negative incremental utility gate
# ---------------------------------------------------------------------------

def test_10_third_quarterback_has_zero_incremental_utility():
    """PHASE 3A FIX: the original version of this test used a 2-player
    (QB-only) roster, which is ILLEGAL both before and after adding QB3
    (missing RB/WR/TE either way) -- so build_production_lineup returned
    total_roster_utility=0 on both sides for the WRONG reason (the
    illegal-roster zero-clamp phase 3A found and fixed in
    mock_draft.legal_lineup.partial_lineup_value / mock_draft.auction.
    _incremental_utility), not because third_qb's bench weight is 0. That
    made this test a false positive: it would have kept passing even if
    third_qb had a large positive weight, as long as the roster stayed
    illegal. Rebuilt on a FULLY LEGAL roster (1 starting QB, a backup QB,
    and a complete starting lineup) so the QB2->QB3 comparison actually
    isolates PRODUCTION_BENCH_WEIGHTS["third_qb"] == 0.00 (see item 15's
    explicit "third QB produces zero depth value" requirement -- the
    bench-weight retune that would have contradicted this was tried and
    reverted, see legal_lineup.py's PRODUCTION_BENCH_WEIGHTS comment)."""
    roster = _roster(
        ("QB1", "QB", 1, 300), ("QB2", "QB", 1, 100),
        ("RB1", "RB", 1, 200), ("RB2", "RB", 1, 190),
        ("WR1", "WR", 1, 180), ("WR2", "WR", 1, 170), ("TE1", "TE", 1, 150),
        ("RB3", "RB", 1, 140), ("WR3", "WR", 1, 130), ("TE2", "TE", 1, 120),
    )
    assert build_production_lineup(roster).lineup_is_legal is True

    before = build_production_lineup(roster).total_roster_utility
    after = build_production_lineup(roster + [("QB3", "QB", 1, 20)]).total_roster_utility
    assert after - before == pytest.approx(0.0, abs=1e-6)

    # Same assertion against partial_lineup_value -- the function the live
    # bid gate (mock_draft.auction._incremental_utility) actually calls.
    before_partial = partial_lineup_value(roster)
    after_partial = partial_lineup_value(roster + [("QB3", "QB", 1, 20)])
    assert after_partial - before_partial == pytest.approx(0.0, abs=1e-6)


def test_11_zero_utility_player_receives_no_bid_above_one_dollar():
    """Integration-level: resolve_bid must never let a team raise above
    $1 for a candidate with zero/negative incremental utility, even if
    that team's raw willingness (base-value driven) would be much higher.
    BOTH teams here already have 2 QBs (a legal starter + a legitimately
    valuable backup) so a 3rd QB is zero-utility for either -- a fresh
    empty-roster team would have genuine positive interest in a first QB,
    which would confound this specific test."""
    base_roster = [
        ("QB1", "QB", 1, 300), ("QB2", "QB", 1, 100),
        ("RB1", "RB", 1, 200), ("RB2", "RB", 1, 190),
        ("WR1", "WR", 1, 180), ("WR2", "WR", 1, 170), ("TE1", "TE", 1, 150),
    ]
    team_a = Team(name="A", budget_remaining=400, roster=list(base_roster))
    team_a.archetype = ARCHETYPE_NAMES[0]
    team_b = Team(name="B", budget_remaining=400, roster=[(n, p, pr, pts) for n, p, pr, pts in base_roster])
    team_b.archetype = ARCHETYPE_NAMES[1] if len(ARCHETYPE_NAMES) > 1 else ARCHETYPE_NAMES[0]
    teams = {"A": team_a, "B": team_b}
    candidate = Player(name="QB3", position="QB", base_value=50.0, tier=1, tier_size=4, tier_rank=1, projected_points=20.0)
    available = _rich_pool()
    available["QB3"] = candidate
    rng = np.random.default_rng(0)
    sale = resolve_bid(candidate, "A", teams, rng, draft_progress=0.5, available=available)
    # Neither team has any incremental utility for a 3rd QB; one of them
    # ends up the default winner uncontested at $1, but neither ever bids
    # above it.
    assert sale["price"] is not None
    assert sale["price"] <= 1.0 + 1e-9


# ---------------------------------------------------------------------------
# 12-14: position caps
# ---------------------------------------------------------------------------

def test_12_qb_maximum_holds_when_enabled():
    roster = _roster(*[(f"QB{i}", "QB", 1, 10) for i in range(DEFAULT_POSITION_MAX["QB"])])
    pool = _pool([("QBextra", "QB")])
    feas = check_roster_completion_feasibility(roster, 50, 5, pool, candidate_player=pool["QBextra"], candidate_price=1, position_max=DEFAULT_POSITION_MAX)
    assert not feas.is_feasible
    assert feas.failure_reason == "POSITION_CAP_EXCEEDED"


def test_13_te_maximum_holds_when_enabled():
    roster = _roster(*[(f"TE{i}", "TE", 1, 10) for i in range(DEFAULT_POSITION_MAX["TE"])])
    pool = _pool([("TEextra", "TE")])
    feas = check_roster_completion_feasibility(roster, 50, 5, pool, candidate_player=pool["TEextra"], candidate_price=1, position_max=DEFAULT_POSITION_MAX)
    assert not feas.is_feasible
    assert feas.failure_reason == "POSITION_CAP_EXCEEDED"


def test_14_position_caps_remain_configurable():
    roster = _roster(("QB1", "QB", 1, 10), ("QB2", "QB", 1, 10))
    pool = _rich_pool([("QB3", "QB")])
    # Disabled (None): no cap applied.
    feas_disabled = check_roster_completion_feasibility(roster, 50, 10, pool, candidate_player=pool["QB3"], candidate_price=1, position_max=None)
    assert feas_disabled.is_feasible
    # Custom cap of 2: blocked.
    feas_custom = check_roster_completion_feasibility(roster, 50, 10, pool, candidate_player=pool["QB3"], candidate_price=1, position_max={"QB": 2})
    assert not feas_custom.is_feasible


# ---------------------------------------------------------------------------
# 15-17: eligibility integration
# ---------------------------------------------------------------------------

def test_15_full_eligibility_classifier_runs_in_mock_draft_data():
    players, teams, meta = load_confirmed_pool_and_teams(budget_scenario="primary")
    assert "eligibility_audit" in meta
    audit = meta["eligibility_audit"]
    assert "final_auction_status" in audit.columns
    assert "auction_eligible" in audit.columns
    # confirmed keepers must be excluded
    for name in ("Kenneth Walker III", "Cam Skattebo", "Garrett Wilson"):
        assert name not in players
    # college-rights holds excluded
    for name in ("Isaiah Bond", "Fernando Mendoza"):
        assert name not in players
    # a real veteran free agent (not on any of this league's rosters, no
    # nflverse data available in this environment) must still be included
    # via the documented fp_only_fallback_eligible policy
    assert "Mike Evans" in players or "Stefon Diggs" in players


def test_16_valuation_and_mock_draft_eligibility_agree_except_documented_divergence():
    recon_path = BASE_DIR / "outputs" / "auction_rebuild" / "audit" / "eligibility_path_reconciliation.csv"
    assert recon_path.exists(), "run scripts/build_eligibility_path_reconciliation.py first"
    recon = pd.read_csv(recon_path)
    if len(recon):
        assert recon["difference_explained"].all(), "unexplained eligibility differences between the two production paths"


def test_17_ambiguous_identity_stops_confirmed_mode():
    from auction_model.confirmed_keeper_pipeline import compute_identity_issues, unresolved_duplicate_identities
    dup = pd.DataFrame([
        {"team_name": "A", "player_name": "Cam Skattebo"},
        {"team_name": "B", "player_name": "Cam Skattebo"},
    ])
    issues = compute_identity_issues(dup)
    assert unresolved_duplicate_identities(issues)


# ---------------------------------------------------------------------------
# 18-20: budget reconciliation
# ---------------------------------------------------------------------------

def test_18_internal_cash_transfers_net_to_zero_or_gap_is_named():
    adjustments = pd.read_csv(DATA_DIR / "team_budget_adjustments_2026.csv")
    net = adjustments["amount"].sum()
    recon_path = BASE_DIR / "outputs" / "auction_rebuild" / "audit" / "team_budget_reconciliation.csv"
    assert recon_path.exists(), "run scripts/build_team_budget_reconciliation.py first"
    if net != 0:
        # Not silently netted to zero -- the reconciliation script must
        # name this exact gap explicitly (it does, in its printed report
        # and the Sam row's notes column).
        recon = pd.read_csv(recon_path)
        sam_row = recon[recon["team"] == "Sam"].iloc[0]
        assert "cash_sent" in recon.columns and float(sam_row["cash_sent"]) == 15.0


def test_19_budget_adjustment_applies_once():
    keepers = pd.read_csv(DATA_DIR / "keepers_2026_confirmed.csv")
    adjustments = pd.read_csv(DATA_DIR / "team_budget_adjustments_2026.csv")
    from auction_model.confirmed_keeper_pipeline import compute_team_states
    state_rows, _ = compute_team_states(keepers, adjustments)
    sam = next(r for r in state_rows if r["team_id"] == "Sam")
    # Sam's -$15 must be reflected exactly once (223, not 208 or 238).
    assert sam["primary_auction_budget"] == 223


def test_20_sam_primary_budget_equals_223_and_conversion_equals_221():
    keepers = pd.read_csv(DATA_DIR / "keepers_2026_confirmed.csv")
    adjustments = pd.read_csv(DATA_DIR / "team_budget_adjustments_2026.csv")
    from auction_model.confirmed_keeper_pipeline import compute_team_states
    state_rows, _ = compute_team_states(keepers, adjustments)
    sam = next(r for r in state_rows if r["team_id"] == "Sam")
    assert 400 - 162 - 15 == 223 == sam["primary_auction_budget"]
    assert 223 - 1 - 1 == 221 == sam["conversions_scenario_auction_budget"]


# ---------------------------------------------------------------------------
# 21-24: full auction legality
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def confirmed_pool_and_teams():
    return load_confirmed_pool_and_teams(budget_scenario="primary")


def _roster_is_legal(team: Team) -> bool:
    counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    names = set()
    for name, pos, _price, _pts in team.roster:
        counts[pos] = counts.get(pos, 0) + 1
        names.add(name)
    return (
        len(team.roster) == 15 and len(names) == 15
        and counts["QB"] >= 1 and counts["RB"] >= 2 and counts["WR"] >= 2 and counts["TE"] >= 1
        and team.budget_remaining >= -1e-6
    )


def test_21_full_auction_produces_fifteen_player_rosters(confirmed_pool_and_teams):
    players, teams, _ = confirmed_pool_and_teams
    rng = np.random.default_rng(1)
    _, final_teams = run_single_auction(players, teams, rng)
    for team in final_teams.values():
        assert len(team.roster) == 15


def test_22_full_auction_produces_legal_lineups(confirmed_pool_and_teams):
    players, teams, _ = confirmed_pool_and_teams
    rng = np.random.default_rng(1)
    _, final_teams = run_single_auction(players, teams, rng)
    for team in final_teams.values():
        assert _roster_is_legal(team), team.roster


def test_23_two_hundred_seeded_auctions_remain_legal(confirmed_pool_and_teams):
    """Fast sanity subset (25 seeds) for the regular test suite -- the
    full 200-seed compliance run (required by the phase-2B spec) is
    scripts/run_phase2b_200_seed_simulation.py, run separately since it's
    too slow for a routine test-suite pass. See
    outputs/auction_rebuild/phase2b/phase2b_summary.json for those results."""
    players, teams, _ = confirmed_pool_and_teams
    failures = []
    n_seeds = 25
    for seed in range(n_seeds):
        rng = np.random.default_rng(seed)
        _, final_teams = run_single_auction(players, teams, rng)
        for name, team in final_teams.items():
            if not _roster_is_legal(team):
                failures.append((seed, name))
    legal_rate = 1 - len(failures) / (n_seeds * len(teams))
    assert legal_rate >= 0.99, f"legal roster rate {legal_rate:.4f}, failures: {failures[:10]}"


def test_24_no_forced_final_slot_field_in_new_sales(confirmed_pool_and_teams):
    players, teams, _ = confirmed_pool_and_teams
    rng = np.random.default_rng(2)
    log, _ = run_single_auction(players, teams, rng)
    assert all(entry["forced_final_slot"] is False for entry in log)


# ---------------------------------------------------------------------------
# 25: exact rerun commands
# ---------------------------------------------------------------------------

def test_25_exact_rerun_commands_execute_successfully():
    import subprocess
    result = subprocess.run(
        [sys.executable, str(BASE_DIR / "scripts" / "build_team_states.py")],
        cwd=str(BASE_DIR), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "All required assertions PASSED" in result.stdout
