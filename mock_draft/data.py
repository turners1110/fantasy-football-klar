"""Load the real model's output into the mock draft engine's Player/Team
state. Reads from a snapshot directory (default: output_mock_draft_snapshot/,
a supplementary snapshot regenerated with the expanded FantasyPros-backed
pool -- see README) rather than mutating the canonical output/ the real
pipeline maintains.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import sys

from . import config_bridge as cfg
from .models import Player, Team
from .points import build_points_lookup, compute_fallback_ratio, points_for

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))
from auction_model import config as auction_cfg  # noqa: E402
from auction_model import data_pipeline  # noqa: E402
from auction_model.auction_eligibility import build_confirmed_veteran_auction_pool  # noqa: E402
from auction_model.confirmed_keeper_pipeline import normalize_name  # noqa: E402


def build_tiers(pool: pd.DataFrame, tier_size: int = cfg.TIER_SIZE) -> pd.DataFrame:
    """Assign (tier, tier_size, tier_rank) per position, ranking by
    base_value descending, in fixed-size blocks. A lightweight stand-in for
    real scout tiers -- good enough to drive tier-cliff nomination/bidding
    behavior without depending on external tier labels."""
    pool = pool.copy()
    pool["tier"] = 0
    pool["tier_rank"] = 0
    pool["tier_size"] = 0
    for position, group in pool.groupby("position"):
        ranked = group.sort_values("base_value", ascending=False)
        n = len(ranked)
        for i, idx in enumerate(ranked.index):
            tier_num = i // tier_size + 1
            rank_in_tier = i % tier_size + 1
            this_tier_size = min(tier_size, n - (tier_num - 1) * tier_size)
            pool.loc[idx, "tier"] = tier_num
            pool.loc[idx, "tier_rank"] = rank_in_tier
            pool.loc[idx, "tier_size"] = this_tier_size
    return pool


class DuplicateCanonicalPlayerError(ValueError):
    """Raised when two rows in the price sheet normalize to the same
    canonical identity (V3 Part 3, minimal safety-net version). This is
    NOT the full canonical player-identity layer the spec describes
    (that would also cover keeper ingestion, protection, search, sale
    entry, corrections, undo/replay, and Monte Carlo aggregation) --
    it is a minimal, additive guard that refuses to build a pool
    containing a known-duplicate alias pair (e.g. Bill/Jacory
    Croskey-Merritt, Kenny/Kenneth Gainwell -- see
    outputs/auction_rebuild/live_v3/canonical_player_aliases.csv), so a
    simulation or live pool can never silently sell the same real person
    twice under two different display names."""


def _assert_no_canonical_duplicate_names(prices: pd.DataFrame) -> None:
    # Uses the SAME canonical_id function as the live sale-entry
    # duplicate check (live_auction_cli.py) and website search
    # (api_search) -- one identity layer (auction_engine.player_identity),
    # not three independently-maintained normalization implementations.
    from auction_engine.player_identity import canonical_id, CanonicalIdentityCollisionError
    seen: dict[str, str] = {}
    collisions = []
    for name in prices["player"]:
        key = canonical_id(name)
        if key in seen and seen[key] != name:
            collisions.append((seen[key], name))
        else:
            seen[key] = name
    if collisions:
        raise DuplicateCanonicalPlayerError(
            f"Duplicate canonical player identity detected in the price sheet -- refusing to "
            f"build the pool (this is exactly the alias-duplicate-sale bug class found reviewing "
            f"ten simulated drafts): {collisions}"
        )


def load_pool_and_teams(snapshot_dir: str | Path = BASE_DIR / "output_mock_draft_snapshot"):
    snapshot_dir = Path(snapshot_dir)
    prices = pd.read_csv(snapshot_dir / "veteran_auction_price_sheet.csv")
    _assert_no_canonical_duplicate_names(prices)
    prices = prices[prices["suggested_auction_price"].notna()].copy()
    prices = prices.rename(columns={"suggested_auction_price": "base_value"})
    prices = prices[["player", "position", "base_value"]]
    prices = build_tiers(prices)
    star_cutoff = prices["base_value"].sort_values(ascending=False).head(cfg.GLOBAL_STAR_COUNT).min()

    points_lookup = build_points_lookup()
    fallback_ratio = compute_fallback_ratio(prices, points_lookup)

    players = {}
    for _, row in prices.iterrows():
        pts, is_real = points_for(row["player"], points_lookup, fallback_ratio, row["base_value"], row["position"])
        players[row["player"]] = Player(
            name=row["player"], position=row["position"], base_value=float(row["base_value"]),
            tier=int(row["tier"]), tier_size=int(row["tier_size"]), tier_rank=int(row["tier_rank"]),
            is_star_eligible=bool(row["base_value"] >= star_cutoff),
            projected_points=pts, points_is_real=is_real,
        )

    keepers = pd.read_csv(snapshot_dir / "keepers_2026.csv")
    teams: dict[str, Team] = {}
    for team_name, group in keepers.groupby("team"):
        spent = float(group["keeper_price_2026"].fillna(0).sum())
        roster = []
        for _, row in group.iterrows():
            price = float(row["keeper_price_2026"]) if pd.notna(row["keeper_price_2026"]) else 0.0
            pts, _ = points_for(
                row["player"], points_lookup, fallback_ratio,
                row["salary_2025"] if pd.notna(row["salary_2025"]) else 0.0, row["position"],
            )
            roster.append((row["player"], row["position"], price, pts))
        teams[team_name] = Team(
            name=team_name,
            budget_remaining=round(cfg.BUDGET_PER_TEAM - spent, 2),
            roster=roster,
        )

    # Teams with zero keepers still need a Team object.
    known_teams = set(pd.read_csv(BASE_DIR / "data" / "historical_salaries_2025_raw.csv")["team"].unique())
    for team_name in known_teams - set(teams):
        teams[team_name] = Team(name=team_name, budget_remaining=float(cfg.BUDGET_PER_TEAM))

    return players, teams


def load_confirmed_pool_and_teams(
    snapshot_dir: str | Path = BASE_DIR / "output_mock_draft_snapshot",
    keepers_path: str | Path = BASE_DIR / "data" / "keepers_2026_confirmed.csv",
    team_states_path: str | Path = BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv",
    budget_scenario: str = "primary",
):
    """Confirmed-keeper-mode loader for phase 2 -- the one authoritative
    source for who's on which team, at what price, with what budget.

    KNOWN LIMITATION (documented, not silently glossed over): player
    `base_value` (dollar prices) still comes from
    output_mock_draft_snapshot/veteran_auction_price_sheet.csv, which was
    priced under the OLD fallback_neutral keeper set, not these confirmed
    keepers. Re-deriving suggested_auction_price under the real keeper-
    driven scarcity is a pricing-model concern outside phase 2's three
    goals (authoritative keeper/budget pipeline, legal-lineup fitness,
    forced-final-slot removal) -- tracked as required before any real
    price/strategy output is published. What THIS function guarantees
    correctly: pool composition (every confirmed keeper and college-
    rights hold is excluded from the auction-eligible pool) and starting
    budgets (from team_starting_states.csv, itself built from the
    confirmed keeper file + user-confirmed / sheet-reported budgets).

    budget_scenario: "primary" (SAM_PRIMARY_223) or "conversions" (SAM_CONVERSIONS_221).
    """
    if budget_scenario not in ("primary", "conversions"):
        raise ValueError(f"budget_scenario must be 'primary' or 'conversions', got {budget_scenario!r}")

    snapshot_dir = Path(snapshot_dir)
    confirmed_keepers = pd.read_csv(keepers_path)
    team_states = pd.read_csv(team_states_path)

    prices = pd.read_csv(snapshot_dir / "veteran_auction_price_sheet.csv")
    _assert_no_canonical_duplicate_names(prices)
    prices = prices[prices["suggested_auction_price"].notna()].copy()
    prices = prices.rename(columns={"suggested_auction_price": "base_value"})
    prices = prices[["player", "position", "base_value"]]

    # PHASE 2B FIX: this used to be a direct name-exclusion against the
    # confirmed-keeper file only -- phase 2's own documented gap, since it
    # meant this pool never received the real nflverse-debut-aware /
    # college-rights-aware classification auction_model.auction_eligibility
    # provides (a name match is identity resolution, not eligibility).
    # Now calls the SAME shared function run_valuation.py's confirmed mode
    # uses, so both production paths can never silently diverge on who is
    # auction-eligible. See
    # outputs/auction_rebuild/audit/eligibility_path_reconciliation.csv
    # for the required cross-check between the two paths.
    salaries, _ = data_pipeline.load_historical_salaries(BASE_DIR / "data" / "historical_salaries_2025_raw.csv")
    eligibility_roster = salaries.copy()
    eligibility_roster["will_keep"] = False
    keeper_norm = set(confirmed_keepers.loc[confirmed_keepers["counts_as_keeper"].astype(bool), "player_name"].map(normalize_name))
    eligibility_roster.loc[eligibility_roster["player"].map(normalize_name).isin(keeper_norm), "will_keep"] = True

    excluded_names = set(confirmed_keepers["player_name"].map(normalize_name))
    prices_before = prices["player"].map(normalize_name)
    contaminated = prices[prices_before.isin(excluded_names)]
    prices, eligibility_audit = build_confirmed_veteran_auction_pool(
        prices, salaries, confirmed_keepers, roster=eligibility_roster,
        # PHASE 3A: fp_only_fallback_eligible=True is no longer needed here.
        # Phase 2B's fallback approximated "probably a real active player"
        # from FantasyPros-rank presence alone (confidence 0.3) because
        # nflverse debut data was the ONLY active-player evidence source
        # wired in, and its file is absent in this environment. Phase 3A
        # added real evidence sources instead (data/actuals_2025.csv,
        # data/fantasy_data_last_year_clean.csv, data/projections_2026.csv
        # -- see auction_model.auction_eligibility._active_player_registry_evidence),
        # which correctly include real veterans like Mike Evans/Stefon
        # Diggs/Courtland Sutton via genuine roster/production evidence.
        # Both production paths now use the SAME strict default (False) --
        # the documented divergence this flag existed for is gone.
        fp_only_fallback_eligible=False,
    )
    prices = prices[["player", "position", "base_value"]]

    # Points are needed BEFORE base_value can be recomputed under the
    # phase 3D replacement methods (VBD needs points), so this now happens
    # ahead of build_tiers/star_cutoff (both of which depend on base_value).
    points_lookup = build_points_lookup()
    fallback_ratio = compute_fallback_ratio(prices, points_lookup)
    player_points: dict[str, tuple[float, bool]] = {}
    for _, row in prices.iterrows():
        player_points[row["player"]] = points_for(
            row["player"], points_lookup, fallback_ratio, row["base_value"], row["position"],
        )

    # PHASE 3D item 3: recompute base_value from the selected replacement
    # method (auction_model.config.REPLACEMENT_METHOD) instead of using
    # the snapshot's fixed-rank-derived suggested_auction_price as-is.
    # FIXED_RANK_LEGACY keeps the snapshot's own values unchanged (no
    # recompute -- this is exactly the pre-3D behavior, preserved as an
    # explicit opt-back-in). GREEDY_/EXACT_LEAGUEWIDE_ALLOCATION rebuild
    # VBD from that method's real replacement points, then redistribute
    # the SAME per-position dollar pool proportionally to (new VBD)^power,
    # so total league dollars are conserved -- only the SHAPE changes, not
    # the total. See auction_model.replacement_methods.
    if auction_cfg.REPLACEMENT_METHOD != auction_cfg.FIXED_RANK_LEGACY:
        from auction_model import replacement_methods
        team_keepers_for_solve = {
            row["team_id"]: [
                (kr["player_name"], kr["position"], player_points.get(kr["player_name"], (0.0, False))[0])
                for _, kr in confirmed_keepers[
                    (confirmed_keepers["team_id"] == row["team_id"]) & (confirmed_keepers["counts_as_keeper"].astype(bool))
                ].iterrows()
            ]
            for _, row in team_states.iterrows()
        }
        pool_points_for_solve = {
            name: (row["position"], player_points.get(name, (0.0, False))[0])
            for name, row in prices.set_index("player").iterrows()
        }
        prices["base_value"] = replacement_methods.recompute_base_value(
            prices, pool_points_for_solve, team_keepers_for_solve,
            method=auction_cfg.REPLACEMENT_METHOD,
        )

    prices = build_tiers(prices)
    star_cutoff = prices["base_value"].sort_values(ascending=False).head(cfg.GLOBAL_STAR_COUNT).min()

    players = {}
    for _, row in prices.iterrows():
        pts, is_real = player_points[row["player"]]
        players[row["player"]] = Player(
            name=row["player"], position=row["position"], base_value=float(row["base_value"]),
            tier=int(row["tier"]), tier_size=int(row["tier_size"]), tier_rank=int(row["tier_rank"]),
            is_star_eligible=bool(row["base_value"] >= star_cutoff),
            projected_points=pts, points_is_real=is_real,
        )

    # Phase 3D item 5/7/8: attach the public-anchor hierarchy and
    # keeper-removal-normalized historical anchor to every pool player, for
    # compute_willingness's base_market_anchor blend. Both are best-effort
    # lookups (see auction_model.public_anchor / historical_anchor's own
    # docstrings for exactly which players each source covers) -- a
    # missing value stays None, never a fabricated fallback number.
    from auction_model.public_anchor import build_public_anchor_hierarchy
    from auction_model.anchor_normalization import normalize_anchors_after_keeper_removal
    from auction_model.historical_anchor import build_historical_league_anchor

    anchor_df = normalize_anchors_after_keeper_removal(build_public_anchor_hierarchy(players)).set_index("player")
    live_budget_total = float(team_states["primary_auction_budget"].sum())
    hist_df = build_historical_league_anchor(players, live_budget_total).set_index("player")
    for name, player in players.items():
        if name in anchor_df.index and anchor_df.loc[name, "source"] != "NO_PUBLIC_ANCHOR_INTERNAL_NEUTRAL_VALUE":
            player.public_anchor_value = float(anchor_df.loc[name, "keeper_removed_anchor_primary"])
        if name in hist_df.index and bool(hist_df.loc[name, "matched"]):
            player.historical_anchor_value = float(hist_df.loc[name, "historical_anchor_value"])

    teams: dict[str, Team] = {}
    budget_col = "primary_auction_budget" if budget_scenario == "primary" else "conversions_scenario_auction_budget"
    for _, state_row in team_states.iterrows():
        team_name = state_row["team_id"]
        team_keepers = confirmed_keepers[
            (confirmed_keepers["team_id"] == team_name) & (confirmed_keepers["counts_as_keeper"].astype(bool))
        ]
        roster = []
        for _, kr in team_keepers.iterrows():
            pts, _ = points_for(kr["player_name"], points_lookup, fallback_ratio, float(kr["keeper_cost"]), kr["position"])
            roster.append((kr["player_name"], kr["position"], float(kr["keeper_cost"]), pts))
        teams[team_name] = Team(
            name=team_name,
            budget_remaining=float(state_row[budget_col]),
            roster=roster,
        )

    return players, teams, {
        "excluded_count": len(excluded_names),
        "contaminated_in_price_sheet_before_filter": contaminated["player"].tolist(),
        "eligibility_audit": eligibility_audit,
        "budget_scenario": budget_scenario,
    }
