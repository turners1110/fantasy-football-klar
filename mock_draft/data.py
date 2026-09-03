"""Load the real model's output into the mock draft engine's Player/Team
state. Reads from a snapshot directory (default: output_mock_draft_snapshot/,
a supplementary snapshot regenerated with the expanded FantasyPros-backed
pool -- see README) rather than mutating the canonical output/ the real
pipeline maintains.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import config_bridge as cfg
from .models import Player, Team
from .points import build_points_lookup, compute_fallback_ratio, points_for

BASE_DIR = Path(__file__).parent.parent


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
