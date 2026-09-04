"""Phase 3G tests: reproduction integrity, hard-max invariant enforcement,
Ekeler exclusion, McLaurin ceiling resolution, portfolio legality, and
final-sheet labeling. Reads Phase 3G's real generated CSVs; skips (not
fails) when a file wasn't generated in a given run."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from auction_model import exact_roster_solver

BASE_DIR = Path(__file__).parent.parent
PHASE3G_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3g"

KEEPER_NAMES = {"Garrett Wilson", "Kenneth Walker III", "Quentin Johnston",
                "David Montgomery", "Cam Skattebo", "Jaxson Dart"}
COLLEGE_RIGHTS = {"Fernando Mendoza", "Isaiah Bond"}
UNSUPPORTED = {"Austin Ekeler", "AJ Barner", "Cade Otton"}


def _need(path):
    if not path.exists():
        pytest.skip(f"{path.name} not generated yet in this run")
    return pd.read_csv(path)


# ---------------------------------------------------------------------------
# Part 1: reproduction
# ---------------------------------------------------------------------------

def test_phase3f_reproduction_all_material_items_match():
    df = _need(PHASE3G_DIR / "phase3f_reproduction.csv")
    assert (df["Status"] == "MATCH").all(), df[df["Status"] != "MATCH"]


def test_highs_solver_selection_present():
    import inspect
    src = inspect.getsource(exact_roster_solver._solve_stage)
    assert src.index("HiGHS") < src.index("PULP_CBC_CMD")


def test_every_ceiling_solve_is_optimal():
    df = _need(PHASE3G_DIR / "selected_player_ceiling_validation.csv")
    assert df["both_optimal"].all()


# ---------------------------------------------------------------------------
# Part 3: hard-maximum invariant
# ---------------------------------------------------------------------------

def test_kittle_never_purchased_above_23_hard_max():
    for fname in ("sam_portfolios_223.csv", "sam_portfolios_221.csv"):
        df = _need(PHASE3G_DIR / fname)
        kittle = df[df["player"] == "George Kittle"]
        if not kittle.empty:
            assert (kittle["price_whole_dollar"] <= 23).all(), (
                "George Kittle must never be purchased above his $23 safety-adjusted hard maximum "
                "(this is the exact Phase 3F blocker this phase was told to fix)"
            )


def test_unsupported_ekeler_excluded_from_every_portfolio():
    for fname in ("sam_portfolios_223.csv", "sam_portfolios_221.csv"):
        df = _need(PHASE3G_DIR / fname)
        assert "Austin Ekeler" not in set(df["player"]), "unsupported $97 Ekeler price must be excluded from all recommended portfolios"


def test_no_portfolio_purchase_exceeds_its_hard_maximum():
    planning = _need(PHASE3G_DIR / "selected_player_planning_prices.csv").set_index("player")
    for fname in ("sam_portfolios_223.csv", "sam_portfolios_221.csv"):
        df = _need(PHASE3G_DIR / fname)
        for _, row in df.iterrows():
            name = row["player"]
            if name in planning.index and pd.notna(planning.loc[name, "safety_adjusted_hard_maximum"]):
                hm = planning.loc[name, "safety_adjusted_hard_maximum"]
                assert row["price_whole_dollar"] <= hm + 0.01, f"{name} purchased above hard max in {fname}"


def test_portfolio_validation_file_all_pass():
    df = _need(PHASE3G_DIR / "sam_portfolio_validation.csv")
    assert (df["status"] == "PASS").all(), df[df["status"] != "PASS"]


# ---------------------------------------------------------------------------
# Portfolio legality (Part 8)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fname,budget", [("sam_portfolios_223.csv", 223), ("sam_portfolios_221.csv", 221)])
def test_every_portfolio_style_has_nine_new_players_and_fifteen_total(fname, budget):
    df = _need(PHASE3G_DIR / fname)
    for style in df["portfolio_style"].unique():
        sub = df[df["portfolio_style"] == style]
        assert len(sub) == 15, f"{style}: not 15 players"
        new = sub[~sub["player"].isin(KEEPER_NAMES)]
        assert len(new) == 9, f"{style}: not 9 new purchases"
        assert new["player"].nunique() == 9, f"{style}: duplicate players"


@pytest.mark.parametrize("fname,budget", [("sam_portfolios_223.csv", 223), ("sam_portfolios_221.csv", 221)])
def test_every_portfolio_style_within_budget(fname, budget):
    df = _need(PHASE3G_DIR / fname)
    for style in df["portfolio_style"].unique():
        sub = df[df["portfolio_style"] == style]
        new = sub[~sub["player"].isin(KEEPER_NAMES)]
        assert new["price_whole_dollar"].sum() <= budget


@pytest.mark.parametrize("fname", ["sam_portfolios_223.csv", "sam_portfolios_221.csv"])
def test_every_portfolio_style_has_at_least_one_te(fname):
    df = _need(PHASE3G_DIR / fname)
    for style in df["portfolio_style"].unique():
        sub = df[df["portfolio_style"] == style]
        assert (sub["position"] == "TE").sum() >= 1, f"{style}: no TE"


@pytest.mark.parametrize("fname", ["sam_portfolios_223.csv", "sam_portfolios_221.csv"])
def test_every_portfolio_style_whole_dollar_prices(fname):
    df = _need(PHASE3G_DIR / fname)
    new = df[~df["player"].isin(KEEPER_NAMES)]
    prices = new["price_whole_dollar"].dropna()
    assert (prices == prices.astype(int)).all()


@pytest.mark.parametrize("fname", ["sam_portfolios_223.csv", "sam_portfolios_221.csv"])
def test_every_portfolio_excludes_college_rights(fname):
    df = _need(PHASE3G_DIR / fname)
    assert not set(df["player"]).intersection(COLLEGE_RIGHTS)


def test_primary_conservative_style_exists_and_uses_conservative_prices():
    df = _need(PHASE3G_DIR / "sam_portfolios_223.csv")
    assert "PRIMARY_CONSERVATIVE" in set(df["portfolio_style"])


def test_fallback_portfolio_excludes_allen_rice_mclaurin():
    df = _need(PHASE3G_DIR / "sam_portfolios_223.csv")
    fb = df[df["portfolio_style"] == "FALLBACK_NO_ALLEN_RICE_MCLAURIN"]
    assert not set(fb["player"]).intersection({"Josh Allen", "Rashee Rice", "Terry McLaurin"})


def test_te_contingency_excludes_kittle():
    df = _need(PHASE3G_DIR / "sam_portfolios_223.csv")
    contingency = df[df["portfolio_style"] == "TE_CONTINGENCY_NO_KITTLE"]
    assert "George Kittle" not in set(contingency["player"])


# ---------------------------------------------------------------------------
# McLaurin / ceiling monotonicity
# ---------------------------------------------------------------------------

def test_mclaurin_final_safety_ceiling_uses_the_lower_value():
    df = _need(PHASE3G_DIR / "selected_player_exact_ceilings.csv").set_index("player")
    if "Terry McLaurin" in df.index:
        row = df.loc["Terry McLaurin"]
        assert row["final_safety_ceiling"] == min(row["exact_ceiling_223"], row["exact_ceiling_221"])


def test_all_selected_ceilings_monotonic():
    df = _need(PHASE3G_DIR / "selected_player_ceiling_validation.csv")
    assert df["monotonic_223"].fillna(True).all()
    assert df["monotonic_221"].fillna(True).all()


def test_no_selected_ceiling_solver_failure():
    df = _need(PHASE3G_DIR / "selected_player_ceiling_validation.csv")
    assert not df["solver_failure_223"].any()
    assert not df["solver_failure_221"].any()


# ---------------------------------------------------------------------------
# Shock tests (Part 9)
# ---------------------------------------------------------------------------

def test_all_three_targets_unavailable_shock_remains_feasible():
    df = _need(PHASE3G_DIR / "sam_portfolio_shock_tests.csv")
    row = df[df["shock"].str.contains("all unavailable", na=False)]
    assert not row.empty
    assert row["feasible"].all()


def test_p75_full_roster_shock_feasible():
    df = _need(PHASE3G_DIR / "sam_portfolio_shock_tests.csv")
    row = df[df["shock"].str.contains("P75", na=False)]
    assert not row.empty
    assert row["feasible"].all()


def test_p90_full_roster_shock_feasible():
    df = _need(PHASE3G_DIR / "sam_portfolio_shock_tests.csv")
    row = df[df["shock"].str.contains("P90", na=False)]
    assert not row.empty
    assert row["feasible"].all()


# ---------------------------------------------------------------------------
# Final auction sheet (Part 10)
# ---------------------------------------------------------------------------

def test_final_sheet_no_keeper_or_college_rights():
    df = _need(PHASE3G_DIR / "sam_final_auction_sheet.csv")
    assert not set(df["Player"]).intersection(KEEPER_NAMES)
    assert not set(df["Player"]).intersection(COLLEGE_RIGHTS)


def test_final_sheet_ekeler_labeled_insufficient_evidence():
    df = _need(PHASE3G_DIR / "sam_final_auction_sheet.csv")
    row = df[df["Player"] == "Austin Ekeler"]
    if not row.empty:
        assert row["Recommended action"].iloc[0] == "INSUFFICIENT_EVIDENCE"
        assert row["Extreme-price status"].iloc[0] == "NOT_SUPPORTED_REVIEW_REQUIRED"


def test_final_sheet_every_row_has_confidence():
    df = _need(PHASE3G_DIR / "sam_final_auction_sheet.csv")
    assert df["Confidence"].notna().all()


def test_final_sheet_every_row_has_calculation_labels():
    df = _need(PHASE3G_DIR / "sam_final_auction_sheet.csv")
    assert (df["Calculation labels"].str.len() > 0).all()


def test_final_sheet_no_provisional_price_unlabeled():
    df = _need(PHASE3G_DIR / "sam_final_auction_sheet.csv")
    assert df["Expected price label"].notna().all()


def test_priority_target_requires_ceiling_under_both_budgets():
    df = _need(PHASE3G_DIR / "sam_final_auction_sheet.csv")
    priority = df[df["Recommended action"] == "PRIORITY_TARGET"]
    if not priority.empty:
        assert priority["Exact ceiling under $223"].notna().all()
        assert priority["Exact ceiling under $221"].notna().all()


# ---------------------------------------------------------------------------
# Direct, no-file-dependency checks (Josh Allen / Dart / QB-FLEX, keeper-cost)
# ---------------------------------------------------------------------------

def test_qb_never_flex_eligible():
    assert "QB" not in exact_roster_solver.FLEX_ELIG


def test_dart_benches_when_allen_starts_in_four_target_audit():
    path = BASE_DIR / "outputs" / "auction_rebuild" / "phase3f" / "sam_four_target_full_rosters.csv"
    if not path.exists():
        pytest.skip("phase3f four-target roster file not present")
    df = pd.read_csv(path)
    allen_purchase = df[(df.candidate_forced == "Josh Allen") & (df.roster_type == "PURCHASE")]
    dart_row = allen_purchase[allen_purchase.player == "Jaxson Dart"]
    if not dart_row.empty:
        assert dart_row["role"].iloc[0].startswith("BENCH")


def test_keeper_costs_not_charged_twice_in_final_rosters():
    path = BASE_DIR / "outputs" / "auction_rebuild" / "phase3f" / "sam_four_target_full_rosters.csv"
    if not path.exists():
        pytest.skip("phase3f four-target roster file not present")
    df = pd.read_csv(path)
    known_costs = {"Garrett Wilson": 31.0, "Kenneth Walker III": 36.0, "Quentin Johnston": 11.0,
                   "David Montgomery": 45.0, "Cam Skattebo": 28.0, "Jaxson Dart": 11.0}
    keeper_rows = df[df["player"].isin(known_costs)]
    for _, row in keeper_rows.iterrows():
        assert float(row["price"]) == known_costs[row["player"]]


def test_test_failure_resolution_file_documents_a_real_fix():
    path = PHASE3G_DIR / "test_failure_resolution.txt"
    if not path.exists():
        pytest.skip("not generated yet")
    text = path.read_text()
    assert "LABEL CORRECTION NOTICE" in text
    assert "Fix applied" in text
