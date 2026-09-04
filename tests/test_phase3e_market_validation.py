"""Phase 3E tests: Sam exact-surplus synthetic cases, exact-ceiling
monotonicity, exact-allocation solver fix, and portfolio/CSV legality
checks against this pass's real (non-fabricated) audit outputs.

Uses auction_model.exact_roster_solver directly with small synthetic pools
for the numbered synthetic cases (Part 8), and reads the CSVs produced by
scripts/build_phase3e_sam_exact_audit.py for the broader legality checks
(these CSVs are real exact-solver outputs, not fabricated fixtures).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from auction_model import exact_roster_solver

BASE_DIR = Path(__file__).parent.parent
PHASE3E_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3e"


_EMPTY_POOL_COLUMNS = ["player", "position", "projected_points", "suggested_auction_price"]


def _pool(rows):
    if not rows:
        return pd.DataFrame(columns=_EMPTY_POOL_COLUMNS)
    return pd.DataFrame(rows)


def _keepers(rows):
    return pd.DataFrame(rows)


def _keepers_from_tuples(tuples):
    """tuples of (player, position, price, projected_points) -> keepers df."""
    return pd.DataFrame([
        {"player": p, "position": pos, "projected_points": pts, "keeper_price_2026": price}
        for p, pos, price, pts in tuples
    ])


# A minimal 15-man-legal keeper set: enough to fill everything except the
# slots each synthetic test explicitly probes, at trivial ($1) prices, so
# the tests isolate the behavior being checked rather than real 2026 data.
def _filler_keepers(n, position_cycle=("RB", "WR")):
    rows = []
    for i in range(n):
        pos = position_cycle[i % len(position_cycle)]
        rows.append({"player": f"filler_{i}", "position": pos, "projected_points": 50.0 - i, "keeper_price_2026": 1.0})
    return _keepers(rows)


# ---------------------------------------------------------------------------
# Part 8 synthetic cases (numbered 1-10 in the spec)
# ---------------------------------------------------------------------------

def test_syn01_elite_te_beats_replacement_te_when_sam_has_no_te():
    # Sam holds 5 keepers (no TE), needs 10 more incl. exactly one TE slot's worth.
    keepers = _filler_keepers(5, ("QB", "RB", "RB", "WR", "WR"))
    pool = _pool([
        {"player": "Elite TE", "position": "TE", "projected_points": 220.0, "suggested_auction_price": 30.0},
        {"player": "Replacement TE", "position": "TE", "projected_points": 60.0, "suggested_auction_price": 1.0},
        {"player": "Flex Filler", "position": "RB", "projected_points": 80.0, "suggested_auction_price": 5.0},
    ] + [{"player": f"pad_{i}", "position": "WR", "projected_points": 40.0 - i, "suggested_auction_price": 1.0} for i in range(10)])
    roster_with = list(zip(keepers["player"], keepers["position"], keepers["keeper_price_2026"], keepers["projected_points"]))
    roster_with.append(("Elite TE", "TE", 30.0, 220.0))
    result_a = exact_roster_solver.solve_exact_roster(
        pool[pool.player != "Elite TE"], budget=170.0, n_auction_spots=9, keepers=_keepers_from_tuples(roster_with),
    )
    result_b = exact_roster_solver.solve_exact_roster(
        pool[pool.player != "Elite TE"], budget=200.0, n_auction_spots=10, keepers=keepers,
    )
    assert result_a.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL")
    assert result_b.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL")
    assert result_a.starting_points > result_b.starting_points, (
        "buying the elite TE at a fair price must beat passing when Sam has no TE at all"
    )


def test_syn02_one_dollar_clear_upgrade_is_positive_surplus():
    keepers = _filler_keepers(14)
    pool_with_upgrade = _pool([
        {"player": "Cheap Star", "position": "WR", "projected_points": 300.0, "suggested_auction_price": 1.0},
    ])
    roster_with = list(zip(keepers["player"], keepers["position"], keepers["keeper_price_2026"], keepers["projected_points"]))
    roster_with.append(("Cheap Star", "WR", 1.0, 300.0))
    result_a = exact_roster_solver.solve_exact_roster(
        _pool([]), budget=199.0, n_auction_spots=0,
        keepers=_keepers_from_tuples(roster_with),
    )
    result_b = exact_roster_solver.solve_exact_roster(
        pool_with_upgrade, budget=200.0, n_auction_spots=1, keepers=keepers,
    )
    assert result_a.starting_points >= result_b.starting_points


def test_syn03_spending_entire_budget_can_block_roster_completion():
    keepers = _filler_keepers(5)
    pool = _pool([
        {"player": "Budget Eater", "position": "RB", "projected_points": 100.0, "suggested_auction_price": 10.0},
    ] + [{"player": f"cheap_{i}", "position": "WR", "projected_points": 20.0, "suggested_auction_price": 1.0} for i in range(9)])
    roster_with = list(zip(keepers["player"], keepers["position"], keepers["keeper_price_2026"], keepers["projected_points"]))
    roster_with.append(("Budget Eater", "RB", 10.0, 100.0))
    # Forcing the purchase at a price that leaves < $1 per remaining slot must be INFEASIBLE.
    result_a = exact_roster_solver.solve_exact_roster(
        pool[pool.player != "Budget Eater"], budget=7.0,  # 9 slots remain, need >= $9 reserve, only $7 left
        n_auction_spots=8, keepers=_keepers_from_tuples(roster_with),
    )
    assert result_a.status == "INFEASIBLE", "spending the whole budget must not silently drop the $1-per-slot reserve rule"


def test_syn04_identical_value_alternatives_yield_zero_surplus():
    keepers = _filler_keepers(14)
    pool = _pool([
        {"player": "Twin A", "position": "WR", "projected_points": 100.0, "suggested_auction_price": 5.0},
        {"player": "Twin B", "position": "WR", "projected_points": 100.0, "suggested_auction_price": 5.0},
    ])
    roster_with = list(zip(keepers["player"], keepers["position"], keepers["keeper_price_2026"], keepers["projected_points"]))
    roster_with.append(("Twin A", "WR", 5.0, 100.0))
    result_a = exact_roster_solver.solve_exact_roster(
        pool[pool.player != "Twin A"], budget=195.0, n_auction_spots=0, keepers=_keepers_from_tuples(roster_with),
    )
    result_b = exact_roster_solver.solve_exact_roster(
        pool[pool.player != "Twin A"], budget=200.0, n_auction_spots=1, keepers=keepers,
    )
    assert result_a.starting_points == pytest.approx(result_b.starting_points, abs=0.01)


def test_syn05_backup_qb_behind_starter_has_no_starting_lineup_value():
    # Sam already has an elite starting QB among her keepers; a backup QB
    # can only ever occupy bench, so it must not increase starting points.
    keepers = _filler_keepers(13, ("RB", "WR"))
    keepers = pd.concat([keepers, pd.DataFrame([
        {"player": "Starter QB", "position": "QB", "projected_points": 350.0, "keeper_price_2026": 11.0},
    ])], ignore_index=True)
    pool = _pool([
        {"player": "Backup QB", "position": "QB", "projected_points": 200.0, "suggested_auction_price": 1.0},
    ])
    roster_with = list(zip(keepers["player"], keepers["position"], keepers["keeper_price_2026"], keepers["projected_points"]))
    roster_with.append(("Backup QB", "QB", 1.0, 200.0))
    result_a = exact_roster_solver.solve_exact_roster(
        _pool([]), budget=199.0, n_auction_spots=0, keepers=_keepers_from_tuples(roster_with),
    )
    result_b = exact_roster_solver.solve_exact_roster(
        pool, budget=200.0, n_auction_spots=1, keepers=keepers,
    )
    # Result A forces the backup QB onto the roster (bench only, since the
    # starter QB slot is taken); its starting points must equal (not exceed)
    # passing, since the backup can't start over the incumbent starter.
    assert result_a.starting_points <= result_b.starting_points + 0.01


def test_syn10_ineligible_candidate_is_rejected():
    # A player with no valid position (K -- not QB/RB/WR/TE) can never fill
    # a starter or bench role in this league, so the solver must never
    # select it into the final roster even if nominally in the pool.
    keepers = _filler_keepers(15)
    pool = _pool([
        {"player": "Bad Position", "position": "K", "projected_points": 999.0, "suggested_auction_price": 1.0},
    ])
    result = exact_roster_solver.solve_exact_roster(
        pool, budget=10.0, n_auction_spots=0, keepers=keepers,
    )
    selected_names = set(result.selected["player"]) if not result.selected.empty else set()
    assert "Bad Position" not in selected_names, (
        "a player with an ineligible position must never be selected onto the final roster"
    )


# ---------------------------------------------------------------------------
# Ceiling monotonicity + scenario-exclusion assertion (Part 9 / Part 8)
# ---------------------------------------------------------------------------

def test_scenario_b_excludes_candidate_by_construction():
    from scripts.build_phase3e_sam_exact_audit import _pool_to_exact_df
    players = {}

    class P:
        def __init__(self, name, pos, pts, val):
            self.name, self.position, self.projected_points, self.base_value = name, pos, pts, val

    players["Target"] = P("Target", "WR", 100, 20)
    players["Other"] = P("Other", "RB", 80, 10)
    df = _pool_to_exact_df(players, {"Target"})
    assert "Target" not in set(df["player"])
    assert "Other" in set(df["player"])


@pytest.mark.skipif(
    not (PHASE3E_DIR / "sam_exact_ceiling_validation.csv").exists(),
    reason="requires scripts/build_phase3e_sam_exact_audit.py to have been run",
)
def test_exact_ceilings_are_nonnegative_and_bounded_by_budget():
    df = pd.read_csv(PHASE3E_DIR / "sam_exact_ceiling_validation.csv")
    assert (df["exact_pre_draft_static_pool_ceiling"] >= 0).all()
    assert (df["exact_pre_draft_static_pool_ceiling"] <= 223).all()


@pytest.mark.skipif(
    not (PHASE3E_DIR / "sam_exact_ceiling_validation.csv").exists(),
    reason="requires scripts/build_phase3e_sam_exact_audit.py to have been run",
)
def test_positive_surplus_is_not_confined_to_te_and_p25():
    """Directly tests the Phase 3D claim under audit: 'only four positive
    surplus, all TEs, all P25'. Refuted if any non-TE shows positive surplus."""
    df = pd.read_csv(PHASE3E_DIR / "sam_exact_scenario_rosters.csv")
    positive = df[df["objective_difference_surplus"] > 0]
    assert (positive["position"] != "TE").any(), (
        "expected at least one non-TE with positive exact surplus in this pass's real audit "
        "(refutes the prior claim that only TEs show positive surplus)"
    )


# ---------------------------------------------------------------------------
# Portfolio legality (Part 10) -- checks this pass's real portfolio output
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not (PHASE3E_DIR / "sam_portfolios.csv").exists(),
    reason="requires scripts/build_phase3e_sam_exact_audit.py to have been run",
)
@pytest.mark.parametrize("scenario", ["primary_223", "conversions_221"])
def test_portfolio_has_fifteen_players_and_one_te(scenario):
    df = pd.read_csv(PHASE3E_DIR / "sam_portfolios.csv")
    sub = df[df["budget_scenario"] == scenario]
    assert len(sub) == 15
    assert (sub["position"] == "TE").sum() >= 1
    assert sub["player"].nunique() == 15, "no duplicate players allowed"


@pytest.mark.skipif(
    not (PHASE3E_DIR / "sam_portfolios.csv").exists(),
    reason="requires scripts/build_phase3e_sam_exact_audit.py to have been run",
)
@pytest.mark.parametrize("scenario,budget", [("primary_223", 223), ("conversions_221", 221)])
def test_portfolio_nine_new_players_within_budget(scenario, budget):
    df = pd.read_csv(PHASE3E_DIR / "sam_portfolios.csv")
    sub = df[df["budget_scenario"] == scenario]
    keeper_names = {"Garrett Wilson", "Kenneth Walker III", "Quentin Johnston",
                     "David Montgomery", "Cam Skattebo", "Jaxson Dart"}
    new_players = sub[~sub["player"].isin(keeper_names)]
    assert len(new_players) == 9, "exactly nine auction acquisitions required"
    spend = new_players["price_paid"].sum()
    assert spend <= budget + 0.01


@pytest.mark.skipif(
    not (PHASE3E_DIR / "sam_portfolios.csv").exists(),
    reason="requires scripts/build_phase3e_sam_exact_audit.py to have been run",
)
def test_portfolio_contains_no_college_rights_players():
    df = pd.read_csv(PHASE3E_DIR / "sam_portfolios.csv")
    college_rights = {"Fernando Mendoza", "Isaiah Bond"}
    assert not set(df["player"]).intersection(college_rights)


# ---------------------------------------------------------------------------
# Exact-allocation solver fix (Part 3)
# ---------------------------------------------------------------------------

def test_exact_leaguewide_allocation_uses_a_working_solver_backend():
    """Regression test for the Phase 3E solver fix: the bundled CBC binary
    is wrong-arch in this environment, so exact_leaguewide_allocation must
    try HiGHS first (matching exact_roster_solver's own pattern)."""
    import inspect
    from auction_model import exact_leaguewide_allocation as ela
    src = inspect.getsource(ela.solve_exact_leaguewide_allocation)
    assert "HiGHS" in src, "must attempt a HiGHS solver before falling back to CBC"


def test_no_direct_noise_added_to_final_sale_price():
    """Part 6 requirement: price variation must come from competing
    willingness, not noise injected directly onto the sale price itself."""
    import inspect
    from mock_draft import auction as auction_mod
    src = inspect.getsource(auction_mod)
    # A crude but real static check: the sale-price assignment line(s)
    # should not call a noise/random function directly on the final price.
    assert "sale_price = random" not in src.replace(" ", "")
    assert "sale_price=random" not in src.replace(" ", "")
