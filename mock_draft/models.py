"""Player/Team state for the mock draft engine."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config_bridge as cfg


@dataclass
class Player:
    name: str
    position: str
    base_value: float           # from the real model's suggested_auction_price
    tier: int                   # 1 = best tier at this position (position-local; drives tier-cliff logic)
    tier_size: int
    tier_rank: int               # rank within tier (1 = best in tier)
    is_star_eligible: bool = False  # global top-N by real dollar value -- see data.py


@dataclass
class Team:
    name: str
    budget_remaining: float
    roster: list = field(default_factory=list)   # list of (player_name, position, price)
    archetype: str = ""
    stars_bought: int = 0
    tilt: int = 0                 # decays each pick; >0 boosts willingness
    consecutive_losses: dict = field(default_factory=dict)  # position -> loss streak
    private_value: dict = field(default_factory=dict)  # player_name -> float, set once per run

    @property
    def slots_needed(self) -> int:
        return max(0, cfg.REQUIRED_ROSTER_SIZE - len(self.roster))

    @property
    def is_done(self) -> bool:
        return self.slots_needed <= 0

    def position_count(self, position: str) -> int:
        return sum(1 for _, p, _ in self.roster if p == position)

    def max_bid_cap(self) -> float:
        """Classic auction-bot invariant: reserve $1 for every OTHER
        remaining slot. On the final slot this equals the entire remaining
        budget (see README: this is a ceiling, not a forced floor)."""
        other_slots = max(0, self.slots_needed - 1)
        return max(cfg.MIN_PRICE, self.budget_remaining - cfg.MIN_PRICE * other_slots)
