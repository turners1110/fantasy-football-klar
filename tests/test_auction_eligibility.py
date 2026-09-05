"""Eligibility and college contamination tests."""

import pandas as pd
import pytest

from auction_model import auction_eligibility, config, data_pipeline, roster_optimizer


def test_jeremiyah_love_excluded():
    holdings = pd.DataFrame([{
        "owner": "Shane", "player": "Jeremiyah Love", "position": "RB",
        "college": "Notre Dame", "notes": "status unknown - verify",
    }])
    audit = pd.DataFrame([{
        "player": "Jeremiyah Love",
        "canonical_player_id": data_pipeline._normalize_name("Jeremiyah Love"),
        "position": "RB",
        "final_auction_status": auction_eligibility.COLLEGE_RIGHTS_HELD,
        "auction_eligible": False,
        "eligibility_reason": "college_holdings",
        "evidence_source": "college_holdings.csv",
        "verified_nfl_regular_season_debut": "",
        "confidence": 0.95,
        "warning": "college_rights_block",
        "nfl_team": "", "source_roster": "fantasypros", "source_status": "college",
        "league_veteran_status": False, "league_college_rights_status": True,
        "conversion_status": "", "verification_date": "2026-08-29",
    }])
    pool = pd.DataFrame([{
        "player": "Jeremiyah Love", "position": "RB",
        "projected_points": 202.4, "suggested_auction_price": 50,
        "keep_source": "not_prev_rostered",
    }])
    filtered = auction_eligibility.filter_veteran_auction_pool(pool, audit)
    assert filtered.empty


def test_college_player_never_selected():
    audit = pd.DataFrame([{
        "player": "Arch Manning",
        "canonical_player_id": data_pipeline._normalize_name("Arch Manning"),
        "final_auction_status": auction_eligibility.COLLEGE_RIGHTS_HELD,
        "auction_eligible": False,
    }])
    pool = pd.DataFrame([{
        "player": "Arch Manning", "position": "QB",
        "projected_points": 300, "suggested_auction_price": 1,
    }])
    assert auction_eligibility.filter_veteran_auction_pool(pool, audit).empty


def test_veteran_with_debut_eligible():
    rec = auction_eligibility.classify_player_eligibility(
        player="Josh Allen", position="QB", nfl_team="BUF",
        source_roster="fantasypros", on_historical=False, has_salary=False,
        will_keep=False, college_audit=None,
        debut_info={"games_played": 17, "nfl_team": "BUF", "season": 2025},
        fp_only=True,
    )
    assert rec["auction_eligible"] is True
    assert rec["final_auction_status"] == auction_eligibility.VETERAN_AUCTION_ELIGIBLE


def test_unknown_fp_only_excluded():
    rec = auction_eligibility.classify_player_eligibility(
        player="Mystery Player", position="WR", nfl_team="FA",
        source_roster="fantasypros", on_historical=False, has_salary=False,
        will_keep=False, college_audit=None, debut_info=None, fp_only=True,
    )
    assert rec["auction_eligible"] is False
    assert rec["final_auction_status"] == auction_eligibility.UNKNOWN_STATUS


def test_active_roster_config():
    # UPDATED (official commissioner data repair): 16-player roster
    # (9 starters + 7 bench), not 15.
    assert config.STARTING_ROSTER_SIZE + config.BENCH_SIZE == config.ACTIVE_ROSTER_SIZE
    assert config.ACTIVE_ROSTER_SIZE == 16
    assert config.IR_CAPACITY == 2
