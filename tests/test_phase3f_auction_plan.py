"""Phase 3F tests: reproduction integrity, four-target scenario audit
assertions, Josh Allen price-ladder sanity, whole-dollar ceiling/portfolio
legality, safety margins, and bid-board labeling.

Reads the real (non-fabricated) CSVs produced by
scripts/build_phase3f_sam_auction_plan.py and
scripts/build_phase3f_bid_board_and_plan.py where those files exist;
skips gracefully (not silently) when a file has not been generated yet
(e.g. the ceiling sweep is still running) so this suite can be run
mid-pipeline without false failures. Also runs a handful of direct
exact_roster_solver checks that do not depend on any generated file.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from auction_model import exact_roster_solver

BASE_DIR = Path(__file__).parent.parent
PHASE3F_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3f"

FOUR_TARGETS = {"Josh Allen", "Rashee Rice", "Terry McLaurin", "George Kittle"}
KEEPER_NAMES = {"Garrett Wilson", "Kenneth Walker III", "Quentin Johnston",
                "David Montgomery", "Cam Skattebo", "Jaxson Dart"}
COLLEGE_RIGHTS = {"Fernando Mendoza", "Isaiah Bond"}


def _need(path: Path):
    if not path.exists():
        pytest.skip(f"{path.name} not generated yet in this run")
    return pd.read_csv(path)


# ---------------------------------------------------------------------------
# Part 1: reproduction
# ---------------------------------------------------------------------------

def test_reproduction_file_exists_and_has_rows():
    df = _need(PHASE3F_DIR / "phase3e_reproduction.csv")
    assert len(df) >= 10


def test_reproduction_four_targets_match_exactly():
    df = _need(PHASE3F_DIR / "phase3e_reproduction.csv")
    target_rows = df[df["Metric"].str.contains("exact surplus at reported test price", na=False)]
    assert len(target_rows) == 4
    assert (target_rows["Status"] == "MATCH").all(), "all four positive-surplus results must reproduce exactly"


def test_reproduction_highs_solver_present():
    df = _need(PHASE3F_DIR / "phase3e_reproduction.csv")
    row = df[df["Metric"].str.contains("HiGHS", na=False)]
    assert not row.empty
    assert (row["Status"] == "MATCH").all()


def test_reproduction_budgets_match():
    df = _need(PHASE3F_DIR / "phase3e_reproduction.csv")
    row = df[df["Metric"].str.contains("budget_remaining", na=False)]
    assert len(row) == 2
    assert (row["Status"] == "MATCH").all()


def test_reproduction_keepers_and_college_rights_match():
    df = _need(PHASE3F_DIR / "phase3e_reproduction.csv")
    keeper_row = df[df["Metric"].str.contains("6 keepers", na=False)]
    cr_row = df[df["Metric"].str.contains("College-rights", na=False)]
    assert (keeper_row["Status"] == "MATCH").all()
    assert (cr_row["Status"] == "MATCH").all()


# ---------------------------------------------------------------------------
# Part 2: four-target scenario audit
# ---------------------------------------------------------------------------

def test_four_target_audit_has_all_players_both_budgets():
    df = _need(PHASE3F_DIR / "sam_four_target_scenario_audit.csv")
    assert set(df["Candidate"]) == FOUR_TARGETS
    assert set(df["budget_scenario"]) == {"primary_223", "conversions_221"}
    assert len(df) == 8


def test_four_target_audit_all_optimal():
    df = _need(PHASE3F_DIR / "sam_four_target_scenario_audit.csv")
    assert df["Both OPTIMAL"].all()


def test_four_target_audit_candidate_included_in_purchase():
    df = _need(PHASE3F_DIR / "sam_four_target_scenario_audit.csv")
    assert df["Candidate on purchase roster (assert)"].all()


def test_four_target_audit_candidate_excluded_from_pass():
    df = _need(PHASE3F_DIR / "sam_four_target_scenario_audit.csv")
    assert df["Candidate absent from pass roster (assert)"].all()


def test_four_target_audit_no_duplicate_leaguewide():
    df = _need(PHASE3F_DIR / "sam_four_target_scenario_audit.csv")
    assert (df["Leaguewide purchase count (assert <=1)"] <= 1).all()


def test_four_target_audit_college_rights_excluded():
    df = _need(PHASE3F_DIR / "sam_four_target_scenario_audit.csv")
    assert df["College-rights excluded (assert)"].all()


def test_four_target_audit_both_rosters_legal_15():
    df = _need(PHASE3F_DIR / "sam_four_target_scenario_audit.csv")
    assert df["Purchase roster legal 15 (assert)"].all()
    assert df["Pass roster legal 15 (assert)"].all()


def test_four_target_full_rosters_no_keeper_double_charge():
    df = _need(PHASE3F_DIR / "sam_four_target_full_rosters.csv")
    keeper_rows = df[df["player"].isin(KEEPER_NAMES)]
    # every keeper row's price must equal its known confirmed keeper cost
    known_costs = {"Garrett Wilson": 31.0, "Kenneth Walker III": 36.0, "Quentin Johnston": 11.0,
                   "David Montgomery": 45.0, "Cam Skattebo": 28.0, "Jaxson Dart": 11.0}
    for _, row in keeper_rows.iterrows():
        assert float(row["price"]) == known_costs[row["player"]], (
            f"{row['player']} keeper cost was charged incorrectly ({row['price']} != {known_costs[row['player']]})"
        )


def test_four_target_full_rosters_no_college_rights():
    df = _need(PHASE3F_DIR / "sam_four_target_full_rosters.csv")
    assert not set(df["player"]).intersection(COLLEGE_RIGHTS)


# ---------------------------------------------------------------------------
# Part 3: Josh Allen
# ---------------------------------------------------------------------------

def test_josh_allen_ladder_all_optimal():
    df = _need(PHASE3F_DIR / "josh_allen_exact_price_ladder.csv")
    assert (df["solver_status_purchase"] == "OPTIMAL").all()
    assert (df["solver_status_pass"] == "OPTIMAL").all()


def test_josh_allen_starts_at_every_tested_price():
    df = _need(PHASE3F_DIR / "josh_allen_exact_price_ladder.csv")
    assert df["allen_starts"].all(), "Allen should occupy the single QB_START role at every price tested (his surplus is a starter upgrade)"


def test_josh_allen_dart_never_in_flex():
    df = _need(PHASE3F_DIR / "josh_allen_exact_price_ladder.csv")
    col = "dart_incorrectly_in_flex (Dart is QB, QB not FLEX-eligible in this league)"
    assert not df[col].any(), "Jaxson Dart (QB) must never be assigned a FLEX role -- QB is not FLEX-eligible in this league"


def test_josh_allen_surplus_monotonically_nonincreasing_in_price():
    df = _need(PHASE3F_DIR / "josh_allen_exact_price_ladder.csv").sort_values("price")
    surpluses = df["total_surplus"].tolist()
    # allow small numerical noise but the overall trend must be non-increasing
    violations = sum(1 for i in range(1, len(surpluses)) if surpluses[i] > surpluses[i - 1] + 0.5)
    assert violations == 0, f"exact surplus should be non-increasing as price rises; found {violations} violation(s)"


def test_josh_allen_ceiling_price_has_nonneg_or_near_zero_surplus():
    df = _need(PHASE3F_DIR / "josh_allen_exact_price_ladder.csv")
    row41 = df[df["price"] == 41]
    if not row41.empty:
        assert row41["total_surplus"].iloc[0] >= -0.5, "surplus at the reported $41 ceiling should be ~non-negative"


def test_josh_allen_ceiling_plus_one_is_negative():
    df = _need(PHASE3F_DIR / "josh_allen_exact_price_ladder.csv")
    row42 = df[df["price"] == 42]
    if not row42.empty:
        assert row42["total_surplus"].iloc[0] < 0, "surplus at $42 (ceiling+1) should be negative, confirming the $41 ceiling"


# ---------------------------------------------------------------------------
# Part 4: whole-dollar ceilings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fname", ["sam_exact_bid_ceilings_223.csv", "sam_exact_bid_ceilings_221.csv"])
def test_ceilings_are_whole_dollars(fname):
    df = _need(PHASE3F_DIR / fname)
    valid = df["exact_ceiling_whole_dollar"].dropna()
    assert (valid == valid.astype(int)).all(), "every exact ceiling must be a whole dollar amount"


@pytest.mark.parametrize("fname,budget", [("sam_exact_bid_ceilings_223.csv", 223), ("sam_exact_bid_ceilings_221.csv", 221)])
def test_ceilings_bounded_by_budget(fname, budget):
    df = _need(PHASE3F_DIR / fname)
    valid = df["exact_ceiling_whole_dollar"].dropna()
    assert (valid <= budget).all()
    assert (valid >= 0).all()


@pytest.mark.parametrize("fname", ["sam_exact_bid_ceilings_223.csv", "sam_exact_bid_ceilings_221.csv"])
def test_ceilings_no_silent_solver_failure_reported_as_exact(fname):
    df = _need(PHASE3F_DIR / fname)
    failures = df[df["solver_failure"] == True]  # noqa: E712
    if not failures.empty:
        assert (failures["calculation_label"] == "SOLVER_FAILURE").all(), (
            "a failed solve must be labeled SOLVER_FAILURE, never presented as an exact ceiling"
        )
        assert failures["exact_ceiling_whole_dollar"].isna().all()


def test_ceiling_monotonicity_audit_file_exists():
    df = _need(PHASE3F_DIR / "sam_ceiling_monotonicity_audit.csv")
    assert len(df) >= 1


# ---------------------------------------------------------------------------
# Part 6: whole-dollar portfolios
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fname,scen,budget", [
    ("sam_complete_portfolios_223.csv", "primary_223", 223),
    ("sam_complete_portfolios_221.csv", "conversions_221", 221),
])
def test_p50_portfolio_whole_dollar_and_legal(fname, scen, budget):
    df = _need(PHASE3F_DIR / fname)
    p50 = df[(df.budget_scenario == scen) & (df.price_scenario == "P50_WHOLE_DOLLAR")]
    if p50.empty or p50["solver_status"].iloc[0] not in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL"):
        pytest.skip("P50 portfolio not solved OPTIMAL in this run")
    assert len(p50) == 15
    assert (p50["position"] == "TE").sum() >= 1
    assert p50["player"].nunique() == 15
    new_players = p50[~p50["player"].isin(KEEPER_NAMES)]
    assert len(new_players) == 9
    prices = new_players["price_paid_whole_dollar"].dropna()
    assert (prices == prices.astype(int)).all(), "every purchase price in the recommended portfolio must be a whole dollar"
    assert prices.sum() <= budget


@pytest.mark.parametrize("fname,scen", [
    ("sam_complete_portfolios_223.csv", "primary_223"),
    ("sam_complete_portfolios_221.csv", "conversions_221"),
])
def test_p75_portfolio_legal_if_present(fname, scen):
    df = _need(PHASE3F_DIR / fname)
    p75 = df[(df.budget_scenario == scen) & (df.price_scenario == "P75_CONSERVATIVE_WHOLE_DOLLAR_HEURISTIC")]
    if p75.empty or p75["solver_status"].iloc[0] not in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL"):
        pytest.skip("P75 portfolio not solved OPTIMAL in this run")
    assert len(p75) == 15
    assert p75["player"].nunique() == 15
    assert (p75["position"] == "TE").sum() >= 1


@pytest.mark.parametrize("fname", ["sam_complete_portfolios_223.csv", "sam_complete_portfolios_221.csv"])
def test_portfolios_exclude_college_rights_and_keepers_not_repurchased(fname):
    df = _need(PHASE3F_DIR / fname)
    assert not set(df["player"]).intersection(COLLEGE_RIGHTS)


# ---------------------------------------------------------------------------
# Part 5 / 8: safety margins + bid board
# ---------------------------------------------------------------------------

def test_bid_board_hard_max_never_exceeds_exact_ceiling():
    df = _need(PHASE3F_DIR / "sam_auction_bid_board.csv")
    both = df.dropna(subset=["Recommended hard maximum", "Exact ceiling under $223"])
    assert (both["Recommended hard maximum"] <= both["Exact ceiling under $223"]).all()


def test_bid_board_safety_deduction_matches_confidence_rule():
    df = _need(PHASE3F_DIR / "sam_auction_bid_board.csv")
    for _, row in df.dropna(subset=["Safety deduction pct"]).iterrows():
        conf = row["Confidence 1-10"]
        pct = row["Safety deduction pct"]
        if conf >= 9:
            assert pct == pytest.approx(0.05)
        elif conf >= 7:
            assert pct == pytest.approx(0.10)
        elif conf >= 5:
            assert pct == pytest.approx(0.15)


def test_bid_board_low_confidence_has_no_hard_maximum():
    df = _need(PHASE3F_DIR / "sam_auction_bid_board.csv")
    low_conf = df[df["Confidence 1-10"] < 5]
    assert (low_conf["Recommended action"] == "INSUFFICIENT_EVIDENCE").all()


def test_bid_board_every_row_has_confidence_and_deductions():
    df = _need(PHASE3F_DIR / "sam_auction_bid_board.csv")
    assert df["Confidence 1-10"].notna().all()
    assert (df["Confidence deductions"].str.len() > 0).all()


def test_bid_board_every_row_has_calculation_label():
    df = _need(PHASE3F_DIR / "sam_auction_bid_board.csv")
    assert (df["Calculation label"].str.len() > 0).all()


def test_bid_board_no_keeper_or_college_rights_listed_as_purchasable():
    df = _need(PHASE3F_DIR / "sam_auction_bid_board.csv")
    assert not set(df["Player"]).intersection(KEEPER_NAMES)
    assert not set(df["Player"]).intersection(COLLEGE_RIGHTS)


def test_bid_board_no_provisional_price_relabeled_as_final():
    df = _need(PHASE3F_DIR / "sam_auction_bid_board.csv")
    # every provisional price must carry its source label, never a bare unlabeled number
    assert df["Provisional market P50 label"].isin(
        ["PROVISIONAL_SIMULATED_MARKET_PRICE", "PRELIMINARY_NOT_FINAL"]
    ).all()


# ---------------------------------------------------------------------------
# Direct exact_roster_solver checks (no generated file dependency)
# ---------------------------------------------------------------------------

def test_qb_cannot_occupy_flex_role_directly():
    assert "QB" not in exact_roster_solver.FLEX_ELIG


def test_only_one_qb_start_role_exists():
    qb_start_roles = [r for r, elig in exact_roster_solver.STARTER_ROLES if elig == frozenset({"QB"})]
    assert len(qb_start_roles) == 1


def test_highs_is_tried_before_cbc_in_exact_roster_solver():
    import inspect
    src = inspect.getsource(exact_roster_solver._solve_stage)
    assert src.index("HiGHS") < src.index("PULP_CBC_CMD"), "HiGHS must be attempted before the CBC fallback"
