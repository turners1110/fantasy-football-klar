"""Owner archetypes: one shared bidding/nomination engine, driven by a
per-archetype parameter table instead of bespoke code per personality.

Condensed from a longer real-world description of auction-drafter
psychology (stars-and-scrubs, balanced, value-purist, anchor, positional
extremist, tier controller, price enforcer, emotional). Every parameter
below is a deliberate simplification of that richer description into
something a bidding function can actually consume -- retune freely.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Archetype:
    name: str
    # Stars: how many players get "pay whatever it takes" treatment, and
    # what fraction of the CURRENT remaining budget that treatment can eat
    # into for a single player.
    max_stars: int
    star_ceiling_pct: float          # of remaining budget, for a "star" bid
    # Non-star players: hard cap as % of remaining budget per player.
    price_ceiling_pct: float
    # Bid multiplier applied when a candidate is the last or second-to-last
    # player remaining in their position tier (tier-cliff panic).
    tier_aggression: float
    # Rigid position allocation targets (None entries = no plan for that
    # position). Positional Extremist uses this; most archetypes leave it
    # empty (no rigid plan).
    position_targets: dict = field(default_factory=dict)
    # Value Purist: refuses to cross their own preset private value at all
    # (no "just this once" overpay), even for a nominally "star" player.
    strict_value_ceiling: bool = False
    # Valuation noise (relative stddev) and chance of a jump-bid (+$2..$5
    # instead of +$1) when raising -- both drive Emotional-Drafter-style
    # unpredictability without literally modeling team fandom.
    noise_std: float = 0.12
    jump_bid_prob: float = 0.05
    # "Tilt": after losing N nominations they were actively bidding on,
    # temporarily boost willingness-to-pay on the next player at that
    # position (revenge-bidder / panic-buyer behavior).
    tilt_after_losses: int = 3
    tilt_boost: float = 1.15
    # Continuous per-position willingness multiplier (default empty ->
    # 1.0 everywhere, so every hand-designed archetype above is unaffected).
    # Separate from position_targets (which does a discontinuous share
    # comparison) -- this exists so the evolutionary optimizer
    # (evolution.py) has a smooth, mutable per-position knob instead of
    # positional_extremist's hard-coded target-share logic.
    position_weight: dict = field(default_factory=dict)


ARCHETYPES: dict[str, Archetype] = {
    "stars_and_scrubs": Archetype(
        name="stars_and_scrubs",
        max_stars=4, star_ceiling_pct=0.90,
        price_ceiling_pct=0.08,
        tier_aggression=1.3,
        noise_std=0.10, jump_bid_prob=0.15,
    ),
    "balanced": Archetype(
        name="balanced",
        max_stars=0, star_ceiling_pct=0.0,
        price_ceiling_pct=0.17,
        tier_aggression=1.1,
        noise_std=0.08, jump_bid_prob=0.03,
    ),
    "value_purist": Archetype(
        name="value_purist",
        max_stars=0, star_ceiling_pct=0.0,
        price_ceiling_pct=0.25,
        tier_aggression=1.0,
        strict_value_ceiling=True,
        noise_std=0.15, jump_bid_prob=0.02,
    ),
    "anchor": Archetype(
        name="anchor",
        max_stars=1, star_ceiling_pct=0.70,
        price_ceiling_pct=0.15,
        tier_aggression=1.15,
        noise_std=0.10, jump_bid_prob=0.08,
    ),
    "positional_extremist_rb": Archetype(
        name="positional_extremist_rb",
        max_stars=2, star_ceiling_pct=0.60,
        price_ceiling_pct=0.20,
        tier_aggression=1.25,
        position_targets={"RB": 0.55, "WR": 0.30, "TE": 0.10, "QB": 0.05},
        noise_std=0.10, jump_bid_prob=0.10,
    ),
    "positional_extremist_wr": Archetype(
        name="positional_extremist_wr",
        max_stars=2, star_ceiling_pct=0.60,
        price_ceiling_pct=0.20,
        tier_aggression=1.25,
        position_targets={"WR": 0.55, "RB": 0.30, "TE": 0.10, "QB": 0.05},
        noise_std=0.10, jump_bid_prob=0.10,
    ),
    "tier_controller": Archetype(
        name="tier_controller",
        max_stars=1, star_ceiling_pct=0.50,
        price_ceiling_pct=0.16,
        tier_aggression=1.6,
        noise_std=0.09, jump_bid_prob=0.05,
    ),
    "price_enforcer": Archetype(
        name="price_enforcer",
        max_stars=0, star_ceiling_pct=0.0,
        price_ceiling_pct=0.14,
        tier_aggression=1.0,
        noise_std=0.06, jump_bid_prob=0.02,
    ),
    "emotional": Archetype(
        name="emotional",
        max_stars=2, star_ceiling_pct=0.75,
        price_ceiling_pct=0.20,
        tier_aggression=1.2,
        noise_std=0.28, jump_bid_prob=0.25,
        tilt_after_losses=2, tilt_boost=1.35,
    ),
}

ARCHETYPE_NAMES = list(ARCHETYPES.keys())
