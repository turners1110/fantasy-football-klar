"""Bridges auction_model.config into the constants the mock draft engine
needs, so the simulator can never silently drift from the real league
rules encoded there."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from auction_model import config as _cfg  # noqa: E402

NUM_TEAMS = _cfg.NUM_TEAMS
BUDGET_PER_TEAM = _cfg.BUDGET_PER_TEAM
MIN_PRICE = _cfg.MIN_PRICE
STARTING_LINEUP = _cfg.STARTING_LINEUP
FLEX_ELIGIBLE = {"RB", "WR", "TE"}

# Required roster size to fill via keepers + live auction. IR (2 spots) is
# optional and not required at auction -- see auction_model/config.py
# REQUIRED_AUCTION_ROSTER_SIZE / ACTIVE_ROSTER_SIZE.
REQUIRED_ROSTER_SIZE = getattr(_cfg, "REQUIRED_AUCTION_ROSTER_SIZE", 15)

TIER_SIZE = 4  # players per tier, per position -- drives "tier cliff" nomination logic

# "Star" eligibility (whatever-it-takes treatment) must be a GLOBAL top-N
# cutoff by real dollar value, not a per-position rank. Per-position tier==1
# looked like "best QB available" even when that QB was only worth ~$18 in
# a shallow 1-QB league, which let Anchor/Stars-and-Scrubs archetypes treat
# a cheap player as a star and pay up to 90% of their remaining budget on
# it -- a real bug caught in testing (a $18 QB simulated at a $257 mean
# price). ~2.5 genuine stars per team, league-wide.
GLOBAL_STAR_COUNT = 30

# RETIRED (phase 3D item 5): this 2.5x multiplicative star-ceiling cap is
# no longer used by mock_draft.valuation.compute_willingness, which now
# builds willingness additively from a bounded base_market_anchor +
# team_adjustment + behavior_adjustment (see auction_model/config.py's
# MAX_TOTAL_PREMIUM_OVER_ANCHOR / MAX_TOTAL_DISCOUNT_BELOW_ANCHOR for the
# replacement bound). Kept, unchanged, only because phase 3C's own
# regression tests and this file's own history reference the number it
# used to enforce -- do not reintroduce it into compute_willingness.
STAR_MAX_VALUE_MULTIPLE = 2.5

# Phase 3D item 5: bounded additive willingness model -- bridged straight
# from auction_model.config (see that module for the full design comment).
BASE_ANCHOR_WEIGHT_PUBLIC = _cfg.BASE_ANCHOR_WEIGHT_PUBLIC
BASE_ANCHOR_WEIGHT_HISTORICAL = _cfg.BASE_ANCHOR_WEIGHT_HISTORICAL
BASE_ANCHOR_WEIGHT_PROJECTION_NEUTRAL = _cfg.BASE_ANCHOR_WEIGHT_PROJECTION_NEUTRAL
MAX_ROSTER_FIT_ADJUSTMENT = _cfg.MAX_ROSTER_FIT_ADJUSTMENT
MAX_SCARCITY_ADJUSTMENT = _cfg.MAX_SCARCITY_ADJUSTMENT
MAX_TIER_ADJUSTMENT = _cfg.MAX_TIER_ADJUSTMENT
MAX_BUDGET_STATE_ADJUSTMENT = _cfg.MAX_BUDGET_STATE_ADJUSTMENT
MAX_FUTURE_ALTERNATIVES_ADJUSTMENT = _cfg.MAX_FUTURE_ALTERNATIVES_ADJUSTMENT
MAX_ARCHETYPE_ADJUSTMENT = _cfg.MAX_ARCHETYPE_ADJUSTMENT
MAX_NOISE_ADJUSTMENT = _cfg.MAX_NOISE_ADJUSTMENT
MAX_TOTAL_PREMIUM_OVER_ANCHOR = _cfg.MAX_TOTAL_PREMIUM_OVER_ANCHOR
MAX_TOTAL_DISCOUNT_BELOW_ANCHOR = _cfg.MAX_TOTAL_DISCOUNT_BELOW_ANCHOR

# Real auctions see EARLY picks go for a premium relative to true value --
# bidders are flush with cash and haven't recalibrated to the shrinking
# pool -- with discipline (and bargains) showing up late. First 100-sim
# run instead found organic bidding badly under-competitive throughout,
# with 62.6% of the ENTIRE league budget ($3,003 of $4,800) getting dumped
# into the forced-final-slot rule as a result ($250 avg on a $12 player).
# This decaying multiplier pushes willingness up early and fades to zero
# by the end of the draft, rather than uniformly loosening every ceiling
# (which would just re-inflate late picks too, defeating the tuning
# already done there).
EARLY_DRAFT_PREMIUM_MAX = 0.6  # up to +60% at draft_progress == 0.0
