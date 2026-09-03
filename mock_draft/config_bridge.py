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

# Even for a genuine star, cap willingness at this multiple of the
# player's own (noisy) private value -- prevents two star-hunting
# archetypes from bidding a real $57 player up to $290+ just because both
# have ~90%-of-budget star ceilings. 2.5x still allows a real, meaningful
# "pay up for the stud" premium.
STAR_MAX_VALUE_MULTIPLE = 2.5
