"""V3 Part 3 (minimal safety-net version) -- canonical player identity
duplicate-detection guard.

This does NOT implement the full canonical-identity layer the spec
describes (typed IDs threaded through keeper ingestion, protection,
search, sale entry, corrections, undo/replay, Monte Carlo aggregation).
It implements the minimal, additive fix that closes the confirmed real
bug: the price sheet used to contain two rows for the same real person
(Bill/Jacory Croskey-Merritt, Kenny/Kenneth Gainwell), and nothing
detected or prevented that. See
outputs/auction_rebuild/live_v3/canonical_player_aliases.csv for the
full evidence and action taken.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from mock_draft.data import DuplicateCanonicalPlayerError, _assert_no_canonical_duplicate_names, load_confirmed_pool_and_teams


def test_duplicate_normalized_name_is_rejected():
    df = pd.DataFrame({"player": ["Josh Allen", "josh   allen"], "position": ["QB", "QB"]})
    with pytest.raises(DuplicateCanonicalPlayerError):
        _assert_no_canonical_duplicate_names(df)


def test_distinct_players_with_shared_surname_are_not_flagged():
    # Regression guard against over-eager matching: distinct real people
    # sharing a surname (confirmed as a major false-positive risk in the
    # prior recon pass's blind last-name scan) must NOT be flagged.
    df = pd.DataFrame({
        "player": ["Braelon Allen", "Keenan Allen", "Kaytron Allen", "Cyrus Allen"],
        "position": ["RB", "WR", "RB", "WR"],
    })
    _assert_no_canonical_duplicate_names(df)  # must not raise


def test_real_pool_no_longer_contains_alias_duplicates():
    players, teams, _ = load_confirmed_pool_and_teams()
    names = set(players.keys())
    # The thin duplicate rows are gone; only the real, populated rows remain.
    assert "Bill Croskey-Merritt" not in names
    assert "Kenneth Gainwell" not in names
    assert "Jacory Croskey-Merritt" in names
    assert "Kenny Gainwell" in names


def test_real_pool_loads_without_raising_duplicate_error():
    # The guard is wired into the real loader -- confirms it does not
    # false-positive against the real ~339-player pool.
    players, teams, _ = load_confirmed_pool_and_teams()
    assert len(players) > 300
