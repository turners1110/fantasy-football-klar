"""V3 Gate A -- official commissioner data must reconcile EXACTLY for
all 12 teams, in both the data-build layer (team_starting_states.csv)
and the live engine (AuctionCLI's actual TeamState objects), not just
for Sam.

Official commissioner table transcribed from
outputs/auction_rebuild/official_repair_v1/commissioner_data_transcription.csv.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from live_auction_cli import AuctionCLI
from auction_model.confirmed_keeper_pipeline import OFFICIAL_PROTECTED_COUNT, OFFICIAL_STARTING_BUDGET

TEAM_STATES_PATH = Path(__file__).parent.parent / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv"

OFFICIAL_KEEPER_SALARY = {
    "Brandon": 221, "Brad": 119, "Travis": 113, "Coby": 126, "Shane": 76,
    "James": 103, "CJ": 136, "Ryan J": 136, "Jason": 186, "Evan": 206,
    "Sam": 162, "Reid": 150,
}
OFFICIAL_REMAINING_BUDGET = {
    "Brandon": 184, "Brad": 281, "Travis": 307, "Coby": 274, "Shane": 324,
    "James": 297, "CJ": 264, "Ryan J": 257, "Jason": 209, "Evan": 184,
    "Sam": 225, "Reid": 260,
}


@pytest.fixture(scope="module")
def team_states():
    return pd.read_csv(TEAM_STATES_PATH)


def test_all_12_official_teams_present(team_states):
    assert len(team_states) == 12
    assert set(team_states["team_id"]) == set(OFFICIAL_PROTECTED_COUNT.keys())


@pytest.mark.parametrize("team_id", list(OFFICIAL_KEEPER_SALARY.keys()))
def test_keeper_salary_matches_commissioner_table(team_states, team_id):
    row = team_states[team_states["team_id"] == team_id].iloc[0]
    assert row["keeper_spend"] == OFFICIAL_KEEPER_SALARY[team_id]


@pytest.mark.parametrize("team_id", list(OFFICIAL_REMAINING_BUDGET.keys()))
def test_remaining_budget_matches_commissioner_table(team_states, team_id):
    row = team_states[team_states["team_id"] == team_id].iloc[0]
    assert row["primary_auction_budget"] == OFFICIAL_REMAINING_BUDGET[team_id]


@pytest.mark.parametrize("team_id", list(OFFICIAL_STARTING_BUDGET.keys()))
def test_starting_budget_matches_commissioner_table(team_states, team_id):
    row = team_states[team_states["team_id"] == team_id].iloc[0]
    assert row["official_starting_budget"] == OFFICIAL_STARTING_BUDGET[team_id]


@pytest.mark.parametrize("team_id", list(OFFICIAL_PROTECTED_COUNT.keys()))
def test_protected_count_matches_commissioner_table(team_states, team_id):
    row = team_states[team_states["team_id"] == team_id].iloc[0]
    assert row["official_protected_count"] == OFFICIAL_PROTECTED_COUNT[team_id]


def test_official_league_totals_reconcile_exactly(team_states):
    assert team_states["official_starting_budget"].sum() == 4800
    assert team_states["keeper_spend"].sum() == 1734
    assert team_states["primary_auction_budget"].sum() == 3066
    assert team_states["official_protected_count"].sum() == 79
    assert (16 - team_states["official_protected_count"]).sum() == 113


def test_each_team_remaining_equals_starting_minus_keeper_salary(team_states):
    for _, row in team_states.iterrows():
        assert abs(row["official_starting_budget"] - row["keeper_spend"] - row["primary_auction_budget"]) < 0.01


# ---------------------------------------------------------------------------
# Live engine reconciliation (not just the CSV) -- the actual running
# AuctionCLI state every recommendation is computed against.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cli():
    return AuctionCLI(log_path=None)


def test_live_engine_total_protected_is_79(cli):
    total = sum(len(t.roster) + t.college_rights_count for t in cli.store.state.teams.values())
    assert total == 79


def test_live_engine_total_open_slots_is_113(cli):
    total = sum(t.open_slots for t in cli.store.state.teams.values())
    assert total == 113


def test_live_engine_sam_official_state(cli):
    sam = cli.store.state.teams["Sam"]
    assert sam.budget_remaining == 225.0
    assert sam.open_slots == 8
    assert sam.legal_max_bid == 218.0


@pytest.mark.parametrize("team_id,expected_open", [
    ("Brad", 9), ("Reid", 9), ("Shane", 9),
    ("Travis", 8), ("Sam", 8),
    ("Brandon", 10), ("CJ", 10), ("Coby", 10), ("Evan", 10), ("James", 10), ("Jason", 10), ("Ryan J", 10),
])
def test_live_engine_per_team_open_slots(cli, team_id, expected_open):
    assert cli.store.state.teams[team_id].open_slots == expected_open
