"""V3 Part 3 -- canonical player identity layer.

Single source of truth for "what real person does this display name
refer to." Used by: pool construction (mock_draft/data.py), live sale
recording / duplicate-sale refusal (live_auction_cli.py), and website
search (live_auction_cli.py::api_search). Reuses
auction_model.confirmed_keeper_pipeline.normalize_name rather than
reimplementing name normalization a second time.

Display names stay the primary key everywhere else in this codebase
(the roster/board/search UI, the event log, saved state) -- this module
does not introduce a second player-ID field threaded through every
data structure (that would be a much larger, riskier rewrite of the
event-sourced state format used by save/load/replay/undo). Instead it
provides one function, `canonical_id`, that any code needing to compare
two display names for "same real person" can call, plus an explicit,
audited alias-override table for the confirmed cases -- so identity
comparison never happens ad hoc or via bare lowercase string equality
in more than one place.
"""
from __future__ import annotations

from auction_model.confirmed_keeper_pipeline import normalize_name

# Explicit, human-confirmed alias overrides -- the ONLY safe general
# mechanism for real alias detection this codebase has (see
# outputs/auction_rebuild/official_repair_v1/player_identity_audit.md
# for why a blind normalized-name/last-name match is NOT safe: distinct
# real players routinely share a surname). Both confirmed pairs' thin
# duplicate rows were also removed directly from
# output_mock_draft_snapshot/veteran_auction_price_sheet.csv; this table
# additionally protects any OTHER code path (a stale cached file, a
# future re-import) that might still reference the alias spelling.
ALIAS_OVERRIDES: dict[str, str] = {
    "Bill Croskey-Merritt": "Jacory Croskey-Merritt",
    "Kenneth Gainwell": "Kenny Gainwell",
}


def canonical_id(display_name: str) -> str:
    """The stable identity key for a player: canonicalize any known
    alias to its real name FIRST, then normalize. Two display names
    refer to the same real person iff canonical_id(a) == canonical_id(b)."""
    canonical_display = ALIAS_OVERRIDES.get(display_name, display_name)
    return normalize_name(canonical_display)


def canonical_display_name(display_name: str) -> str:
    """The preferred display name for whatever real person `display_name`
    refers to (resolves a known alias to its canonical spelling; returns
    the input unchanged if it is not a known alias)."""
    return ALIAS_OVERRIDES.get(display_name, display_name)


class CanonicalIdentityCollisionError(ValueError):
    """Raised when two DIFFERENT display names in a single pool/roster
    resolve to the same canonical_id -- either an alias pair that should
    have been merged, or (much more likely, per the recon pass's own
    finding) a genuine bug in ALIAS_OVERRIDES pointing two unrelated
    real players at the same canonical key. Either way, this must never
    be silently ignored."""


def build_identity_table(display_names) -> dict[str, list[str]]:
    """Returns {canonical_id: [display_name, ...]} for a collection of
    display names. Raises CanonicalIdentityCollisionError if any
    canonical_id maps to more than one DISTINCT display name that is not
    itself a known alias pair for that same canonical id (i.e. genuinely
    unexpected collisions, not a name appearing twice verbatim)."""
    table: dict[str, list[str]] = {}
    for name in display_names:
        cid = canonical_id(name)
        table.setdefault(cid, [])
        if name not in table[cid]:
            table[cid].append(name)
    collisions = {cid: names for cid, names in table.items() if len(names) > 1}
    if collisions:
        raise CanonicalIdentityCollisionError(
            f"Canonical ID collisions detected (refusing to proceed): {collisions}"
        )
    return table
