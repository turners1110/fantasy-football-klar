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


def load_pool_and_teams(snapshot_dir: str | Path = BASE_DIR / "output_mock_draft_snapshot"):
    snapshot_dir = Path(snapshot_dir)
    prices = pd.read_csv(snapshot_dir / "veteran_auction_price_sheet.csv")
    prices = prices[prices["suggested_auction_price"].notna()].copy()
    prices = prices.rename(columns={"suggested_auction_price": "base_value"})
    prices = prices[["player", "position", "base_value"]]
    prices = build_tiers(prices)
    star_cutoff = prices["base_value"].sort_values(ascending=False).head(cfg.GLOBAL_STAR_COUNT).min()

    points_lookup = build_points_lookup()
    fallback_ratio = compute_fallback_ratio(prices, points_lookup)

    players = {}
    for _, row in prices.iterrows():
        pts, is_real = points_for(row["player"], points_lookup, fallback_ratio, row["base_value"])
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
            pts, _ = points_for(row["player"], points_lookup, fallback_ratio, row["salary_2025"] if pd.notna(row["salary_2025"]) else 0.0)
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
    prices = prices[prices["suggested_auction_price"].notna()].copy()
    prices = prices.rename(columns={"suggested_auction_price": "base_value"})
    prices = prices[["player", "position", "base_value"]]

    # Exclude every confirmed veteran keeper AND every college-rights hold
    # from the auction-eligible pool -- the one eligibility decision this
    # loader makes, independent of auction_model.auction_eligibility's own
    # (broader, nflverse/college-holdings-aware) classification, which
    # governs the real run_valuation.py price sheet instead. Both must
    # agree that keepers/holds are excluded; this loader enforces it
    # directly against the confirmed-keeper file as a hard guarantee for
    # the mock-draft pool specifically.
    excluded_names = set(confirmed_keepers["player_name"].map(normalize_name))
    prices["_key"] = prices["player"].map(normalize_name)
    contaminated = prices[prices["_key"].isin(excluded_names)]
    prices = prices[~prices["_key"].isin(excluded_names)].drop(columns=["_key"])

    prices = build_tiers(prices)
    star_cutoff = prices["base_value"].sort_values(ascending=False).head(cfg.GLOBAL_STAR_COUNT).min()

    points_lookup = build_points_lookup()
    fallback_ratio = compute_fallback_ratio(prices, points_lookup)

    players = {}
    for _, row in prices.iterrows():
        pts, is_real = points_for(row["player"], points_lookup, fallback_ratio, row["base_value"])
        players[row["player"]] = Player(
            name=row["player"], position=row["position"], base_value=float(row["base_value"]),
            tier=int(row["tier"]), tier_size=int(row["tier_size"]), tier_rank=int(row["tier_rank"]),
            is_star_eligible=bool(row["base_value"] >= star_cutoff),
            projected_points=pts, points_is_real=is_real,
        )

    teams: dict[str, Team] = {}
    budget_col = "primary_auction_budget" if budget_scenario == "primary" else "conversions_scenario_auction_budget"
    for _, state_row in team_states.iterrows():
        team_name = state_row["team_id"]
        team_keepers = confirmed_keepers[
            (confirmed_keepers["team_id"] == team_name) & (confirmed_keepers["counts_as_keeper"].astype(bool))
        ]
        roster = []
        for _, kr in team_keepers.iterrows():
            pts, _ = points_for(kr["player_name"], points_lookup, fallback_ratio, float(kr["keeper_cost"]))
            roster.append((kr["player_name"], kr["position"], float(kr["keeper_cost"]), pts))
        teams[team_name] = Team(
            name=team_name,
            budget_remaining=float(state_row[budget_col]),
            roster=roster,
        )

    return players, teams, {
        "excluded_count": len(excluded_names),
        "contaminated_in_price_sheet_before_filter": contaminated["player"].tolist(),
        "budget_scenario": budget_scenario,
    }
