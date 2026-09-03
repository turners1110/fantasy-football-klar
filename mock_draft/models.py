"""Player/Team state for the mock draft engine."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

from . import config_bridge as cfg
from .archetypes import ARCHETYPES, Archetype


@dataclass
class Player:
    name: str
    position: str
    base_value: float           # from the real model's suggested_auction_price
    tier: int                   # 1 = best tier at this position (position-local; drives tier-cliff logic)
    tier_size: int
    tier_rank: int               # rank within tier (1 = best in tier)
    is_star_eligible: bool = False  # global top-N by real dollar value -- see data.py
    projected_points: float = 0.0   # the optimization objective -- see points.py
    points_is_real: bool = True     # False = imputed from base_value, no real projection found


@dataclass
class Team:
    name: str
    budget_remaining: float
    roster: list = field(default_factory=list)   # list of (player_name, position, price, points)
    archetype: str = ""
    # Set by the evolutionary optimizer (evolution.py) to drive bidding
    # from an evolved genome instead of a named archetype. When set, this
    # takes priority over `archetype` everywhere -- named-archetype runs
    # never set this, so their behavior is completely unaffected.
    custom_strategy: Archetype | None = None
    stars_bought: int = 0
    tilt: int = 0                 # decays each pick; >0 boosts willingness
    consecutive_losses: dict = field(default_factory=dict)  # position -> loss streak
    private_value: dict = field(default_factory=dict)  # player_name -> float, set once per run

    @property
    def strategy(self) -> Archetype:
        return self.custom_strategy if self.custom_strategy is not None else ARCHETYPES[self.archetype]

    @property
    def slots_needed(self) -> int:
        return max(0, cfg.REQUIRED_ROSTER_SIZE - len(self.roster))

    @property
    def is_done(self) -> bool:
        return self.slots_needed <= 0

    def position_count(self, position: str) -> int:
        return sum(1 for _, p, *_ in self.roster if p == position)

    @property
    def total_points(self) -> float:
        """PHASE 2: DEPRECATED as a fitness/strategy-selection signal. This
        sums every rostered player's points equally, including bench
        players who can never actually start (see
        outputs/auction_rebuild/audit/current_architecture.md and
        mock_draft/legal_lineup.py) -- the exact bug that credited a
        1-QB-league roster with 4.5 QBs' worth of points. Every strategy-
        evaluation path (evolution.py, best_response.py) now uses
        legal_lineup.build_production_lineup(...).total_roster_utility
        instead. Kept only as a raw diagnostic figure; do not use it to
        rank or select strategies."""
        warnings.warn(
            "Team.total_points is the pre-phase-2 naive all-rostered-points "
            "metric, deprecated as a fitness/strategy-selection signal -- "
            "use legal_lineup.build_production_lineup(team.roster)."
            "total_roster_utility instead. See mock_draft/legal_lineup.py.",
            DeprecationWarning,
            stacklevel=2,
        )
        return sum(entry[3] for entry in self.roster)

    def max_bid_cap(self) -> float:
        """Classic auction-bot invariant: reserve $1 for every OTHER
        remaining slot. On the final slot this equals the entire remaining
        budget (see README: this is a ceiling, not a forced floor)."""
        other_slots = max(0, self.slots_needed - 1)
        return max(cfg.MIN_PRICE, self.budget_remaining - cfg.MIN_PRICE * other_slots)
