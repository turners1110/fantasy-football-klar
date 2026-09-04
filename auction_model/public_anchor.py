"""Phase 3D item 7: public-anchor hierarchy.

Resolves ONE public-auction anchor value per player by walking a disclosed
priority order, highest-confidence source first, and recording exactly
which source/tier resolved every player -- never silently blending sources
or presenting a rank-derived guess as an observed auction cost.

Priority order (highest to lowest):
  1. human_imported_file    -- a manually-provided, verified snapshot of
     real current-year public auction values. NOT AVAILABLE in this
     environment (disclosed, not fabricated -- no such file has been
     provided).
  2. direct_snapshot        -- a live scrape/API pull from a public auction
     value tracker. NOT AVAILABLE: phase 3C confirmed WebFetch returns
     EGRESS_BLOCKED for every external auction-value site tried
     (rotoalpha.com, fantasynerds.com, rotowire.com; espn.com as a control
     also blocked), so no direct snapshot can be retrieved here.
  3. partial_websearch_values -- outputs/auction_rebuild/phase3c/
     public_value_normalization.csv: 8 players, WebSearch-synthesized
     consensus (not a verified single-source scrape), MODERATE confidence,
     already normalized to this league's budget format.
  4. fantasypros_rank_tier_conversion -- output/fantasypros_rank_valuations.csv:
     ~390 players, ECR rank/tier converted to a dollar share of this
     league's own remaining live-auction budget (build_fantasypros_valuations.py).
     Built on FantasyPros' GENERIC redraft consensus -- no keeper-stripping,
     no $10/$5 keeper-bump inflation dynamic for this specific league --
     so ordering/spacing is a real public signal but the DOLLAR LEVEL is a
     lower-confidence proxy. LOW-MODERATE confidence, explicitly disclosed
     as rank-derived, never as an observed auction price.
  5. internal_neutral_value -- player.base_value, this simulator's own
     VBD-derived dollar value (auction_model.labels.TEAM_SPECIFIC_VALUE's
     leaguewide-neutral cousin, not a public anchor at all). Lowest
     priority, used only when nothing else covers a player. NEVER to be
     reported as a PUBLIC_AUCTION_ANCHOR -- flagged with its own source
     label so a reader can never mistake it for a real external estimate.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent.parent

SOURCE_NO_PUBLIC_ANCHOR = "NO_PUBLIC_ANCHOR_INTERNAL_NEUTRAL_VALUE"

RETRIEVAL_DATES = {
    "PARTIAL_WEBSEARCH_VALUES": "2026-09-03",  # phase 3C WebSearch run date
    "FANTASYPROS_RANK_TIER_CONVERSION": None,  # unknown -- FantasyPros file's own pull date not recorded upstream
}


def _load_websearch_anchors() -> pd.DataFrame:
    """BUG FOUND in phase 3D (this item): phase 3C's own
    normalized_open_market_value column rescaled its tiny 6-7-player
    WebSearch sample as if THOSE FEW PLAYERS ALONE should absorb the
    entire live pool's discretionary cash (scale = discretionary_cash /
    doubled_total, where doubled_total summed only the matched sample --
    see build_phase3c_public_value_import.py's own `scale` line). That
    produced absurd values (Rashee Rice: $711.83, more than 3x this
    league's per-team budget) once wired into a real willingness
    calculation and caught directly here. NOT fixed by rewriting the
    historical phase3c CSV (preserved as-is per this project's audit-trail
    policy) -- instead this loader uses raw_public_value directly, doubled
    the same way the phase-3C script itself doubled it to move from the
    WebSearch source's stated $200 budget to this league's $400 gross
    budget (build_phase3c_public_value_import.py's own `doubled` variable),
    WITHOUT reapplying the broken discretionary-cash rescale on top."""
    path = BASE_DIR / "outputs" / "auction_rebuild" / "phase3c" / "public_value_normalization.csv"
    if not path.exists():
        return pd.DataFrame(columns=["player", "position", "normalized_value", "raw_value", "source_settings", "confidence"])
    df = pd.read_csv(path)
    df = df.rename(columns={"raw_public_value": "raw_value"})
    df["normalized_value"] = (df["raw_value"] * 2.0).round(2)
    return df


def _load_fantasypros_anchors() -> pd.DataFrame:
    path = BASE_DIR / "output" / "fantasypros_rank_valuations.csv"
    if not path.exists():
        return pd.DataFrame(columns=["player", "position", "price_fp_rank_curve_REFERENCE_ONLY"])
    df = pd.read_csv(path)
    # Two rows share a name (Isaiah Williams) -- keep the better-ranked
    # (lower fp_overall_rank) row deterministically, same dedup pattern
    # used for the phase-3C FantasyPros lookup duplicate-index bug.
    df = df.sort_values("fp_overall_rank").drop_duplicates("player", keep="first")
    return df


def build_public_anchor_hierarchy(players: dict) -> pd.DataFrame:
    """players: {name: Player} (mock_draft.models.Player, has .position and
    .base_value). Returns one row per player with the source-record schema
    required by item 7."""
    websearch = _load_websearch_anchors().set_index("player")
    fantasypros = _load_fantasypros_anchors().set_index("player")

    rows = []
    for name, player in players.items():
        if name in websearch.index:
            r = websearch.loc[name]
            rows.append({
                "player": name, "position": player.position,
                "source": "PARTIAL_WEBSEARCH_VALUES", "source_type": "PUBLIC_AUCTION_ANCHOR",
                "raw_value": r["raw_value"], "source_budget": 200,
                "source_scoring": "unknown (WebSearch-synthesized consensus, not a single verifiable site)",
                "source_roster": "unknown -- assumed standard redraft, no keepers",
                "normalized_value": round(float(r["normalized_value"]), 2),
                "coverage_quality": "PARTIAL_POOL_ONLY (8 players)",
                "confidence": r.get("confidence", "MODERATE"),
                "retrieval_date": RETRIEVAL_DATES["PARTIAL_WEBSEARCH_VALUES"],
                "source_url": None,
            })
        elif name in fantasypros.index:
            r = fantasypros.loc[name]
            rows.append({
                "player": name, "position": player.position,
                "source": "FANTASYPROS_RANK_TIER_CONVERSION", "source_type": "PUBLIC_AUCTION_ANCHOR",
                "raw_value": int(r["fp_overall_rank"]), "source_budget": "this league's own remaining budget (rescaled)",
                "source_scoring": "FantasyPros generic PPR-style ECR, not this league's exact scoring",
                "source_roster": "FantasyPros generic redraft consensus -- NO keeper-stripping, no keeper-bump inflation",
                "normalized_value": round(float(r["price_fp_rank_curve_REFERENCE_ONLY"]), 2),
                "coverage_quality": "BROAD_POOL (~390 players)",
                "confidence": "LOW-MODERATE -- rank-derived, not an observed price; generic consensus ordering",
                "retrieval_date": RETRIEVAL_DATES["FANTASYPROS_RANK_TIER_CONVERSION"],
                "source_url": None,
            })
        else:
            rows.append({
                "player": name, "position": player.position,
                "source": SOURCE_NO_PUBLIC_ANCHOR, "source_type": "TEAM_SPECIFIC_VALUE",
                "raw_value": player.base_value, "source_budget": None, "source_scoring": None, "source_roster": None,
                "normalized_value": round(float(player.base_value), 2),
                "coverage_quality": "NO_PUBLIC_COVERAGE",
                "confidence": "N/A -- this simulator's own internal neutral value, not a public estimate",
                "retrieval_date": None,
                "source_url": None,
            })
    return pd.DataFrame(rows)
