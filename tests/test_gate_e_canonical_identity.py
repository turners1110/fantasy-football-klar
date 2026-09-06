"""V3 Gate E -- canonical player identity layer (auction_engine.player_identity),
wired into pool construction, live sale-entry duplicate refusal, and
website search -- verified through the real CLI/API paths, not just the
module in isolation."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from auction_engine.player_identity import (
    canonical_id, canonical_display_name, build_identity_table, CanonicalIdentityCollisionError,
)
from live_auction_cli import AuctionCLI


def test_known_alias_pair_shares_canonical_id():
    assert canonical_id("Bill Croskey-Merritt") == canonical_id("Jacory Croskey-Merritt")
    assert canonical_id("Kenneth Gainwell") == canonical_id("Kenny Gainwell")


def test_distinct_players_sharing_surname_do_not_collide():
    assert canonical_id("Braelon Allen") != canonical_id("Keenan Allen")
    assert canonical_id("A.J. Brown") != canonical_id("Hollywood Brown")


def test_canonical_display_name_resolves_known_alias():
    assert canonical_display_name("Bill Croskey-Merritt") == "Jacory Croskey-Merritt"
    assert canonical_display_name("Kenneth Gainwell") == "Kenny Gainwell"
    assert canonical_display_name("Josh Allen") == "Josh Allen"  # unchanged, not a known alias


def test_build_identity_table_raises_on_real_collision():
    with pytest.raises(CanonicalIdentityCollisionError):
        build_identity_table(["Josh Allen", "josh   allen"])


def test_build_identity_table_accepts_real_pool_with_no_collisions():
    table = build_identity_table(["Braelon Allen", "Keenan Allen", "Kaytron Allen", "Cyrus Allen"])
    assert len(table) == 4


# ---------------------------------------------------------------------------
# Wired into the real live sale-entry path (not just the module)
# ---------------------------------------------------------------------------

@pytest.fixture
def cli(tmp_path):
    return AuctionCLI(budget_scenario="primary", log_path=tmp_path / "session.jsonl")


def test_sale_refuses_alias_of_already_sold_canonical_player(cli, monkeypatch):
    # Simulate the historical bug directly: inject a duplicate-alias row
    # into the live pool (the real price sheet no longer has one, but
    # this proves the RUNTIME guard works independent of that fix, in
    # case a future re-import ever reintroduces one).
    real_info = cli.store.state.available_pool.get("Jacory Croskey-Merritt")
    if real_info is None:
        pytest.skip("Jacory Croskey-Merritt not in this pool snapshot")
    cli.store.state.available_pool["Bill Croskey-Merritt"] = dict(real_info)

    out1 = cli.cmd_sale("Jacory Croskey-Merritt", "Sam", "5", confirmed=True)
    assert out1.startswith("Recorded")

    out2 = cli.cmd_sale("Bill Croskey-Merritt", "Brandon", "5", confirmed=True)
    assert out2.startswith("REFUSED")
    assert "canonical identity" in out2.lower()
    # Only ONE of the two aliases may ever end up on a roster.
    assert not any(p["player_id"] == "Bill Croskey-Merritt" for p in cli.store.state.teams["Brandon"].roster)


def test_real_pool_has_no_alias_duplicates_left(cli):
    names = list(cli.store.state.available_pool.keys())
    table = build_identity_table(names)  # must not raise
    assert len(table) == len(names)


def test_search_dedupes_by_canonical_identity(cli):
    real_info = cli.store.state.available_pool.get("Jacory Croskey-Merritt")
    if real_info is None:
        pytest.skip("Jacory Croskey-Merritt not in this pool snapshot")
    cli.store.state.available_pool["Bill Croskey-Merritt"] = dict(real_info)
    results = cli.api_search("croskey")
    canonical_ids_seen = {canonical_id(r["player"]) for r in results}
    assert len(canonical_ids_seen) == len(results)  # no two results share a canonical identity
