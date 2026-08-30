"""Tests for college prospect debut detection and valuation."""

import pandas as pd
import pytest

from auction_model import college_prospects, config


@pytest.fixture
def sample_holdings():
    return pd.DataFrame([
        {"owner": "Sam", "player": "Isaiah Bond", "position": "WR", "college": "Texas",
         "notes": "CONFIRMED ALREADY DEBUTED - needs conversion"},
        {"owner": "Sam", "player": "Jojo Earle", "position": "WR", "college": "UNLV",
         "notes": "confirmed still in college"},
    ])


def test_classify_debuted_from_nflverse_games():
    nfl_match = {
        "exact_debut": {
            "nflverse_name": "Isaiah Bond", "games_played": 15,
            "nfl_team": "CLE", "season": 2025,
        },
    }
    row = pd.Series({"player": "Isaiah Bond", "notes": ""})
    status, reason, evidence = college_prospects.classify_prospect_status(row, nfl_match)
    assert status == "debuted_pending_conversion"
    assert "15" in evidence


def test_classify_still_college():
    status, _, _ = college_prospects.classify_prospect_status(
        pd.Series({"player": "Jojo Earle", "notes": "confirmed still in college"}),
        {},
    )
    assert status == "college"


def test_college_pick_table_has_36_picks():
    picks = college_prospects.build_college_pick_table()
    assert len(picks) == 36
    assert picks.iloc[0]["pick_number"] == 1
    assert picks.iloc[0]["original_team"] == "Sam"
    assert picks.iloc[0]["estimated_value"] > picks.iloc[-1]["estimated_value"]


def test_prospect_value_discounts_future_years():
    row = pd.Series({
        "position": "WR",
        "projected_nfl_draft_round": 1,
        "projected_nfl_draft_year": 2027,
        "projection_confidence": "high",
        "status": "college",
    })
    row_near = row.copy()
    row_near["projected_nfl_draft_year"] = 2026
    near = college_prospects.compute_prospect_value(row_near)
    far = college_prospects.compute_prospect_value(row)
    assert near["prospect_value_score"] > far["prospect_value_score"]


def test_conversion_fee_constant():
    assert config.COLLEGE_DEBUT_FEE == 1
