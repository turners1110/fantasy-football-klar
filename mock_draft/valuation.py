"""Per-team private valuation and bid-willingness, driven by the archetype
parameter table. This is where 'higher cash bids aggressively' and
'balance positions intelligently' actually get implemented.
"""

from __future__ import annotations

import numpy as np

from . import config_bridge as cfg
from .archetypes import ARCHETYPES, Archetype
from .models import Player, Team


def get_private_value(team: Team, player: Player, rng: np.random.Generator) -> float:
    """Noisy perceived value, computed once per player per team per draft
    and cached -- represents 'what this owner privately thinks the player
    is worth,' the Value Purist's preset price among others."""
    if player.name in team.private_value:
        return team.private_value[player.name]
    archetype = team.strategy
    noise = float(np.clip(rng.normal(1.0, archetype.noise_std), 0.5, 1.9))
    value = player.base_value * noise
    team.private_value[player.name] = value
    return value


def _position_fit_multiplier(team: Team, player: Player, archetype: Archetype) -> float:
    target = archetype.position_targets.get(player.position)
    if target is None:
        return 1.0
    roster_size = max(1, len(team.roster))
    current_share = team.position_count(player.position) / roster_size
    return 1.3 if current_share < target else 0.4


def compute_willingness(team: Team, player: Player, rng: np.random.Generator, draft_progress: float = 0.0) -> float:
    """Max price this team is willing to raise a bid to, right now, for
    this player -- before the hard roster-slot budget cap is applied."""
    archetype = team.strategy
    private_val = get_private_value(team, player, rng)

    is_star_candidate = (
        team.stars_bought < archetype.max_stars and player.is_star_eligible
    )
    if is_star_candidate:
        # Bound the star premium as a MULTIPLE of the player's real value,
        # not just a fraction of the team's budget -- otherwise two
        # star-hunting archetypes colliding on the same "star" race toward
        # ~90% of a ~$400 budget regardless of whether that player is
        # really worth $57 or $150. Caught in testing: a $57 WR simulating
        # at a $292 median price (5x fair value) before this cap.
        budget_ceiling = team.budget_remaining * archetype.star_ceiling_pct
        # Anchored to the real base_value, NOT the noisy private_val --
        # otherwise per-team noise (up to 1.9x) compounds with this
        # multiplier and the effective cap slips well past 2.5x real value.
        value_ceiling = player.base_value * cfg.STAR_MAX_VALUE_MULTIPLE
        ceiling = min(budget_ceiling, value_ceiling)
        willingness = max(private_val, ceiling) if not archetype.strict_value_ceiling else private_val
    else:
        ceiling = team.budget_remaining * archetype.price_ceiling_pct
        willingness = private_val if archetype.strict_value_ceiling else min(private_val, max(ceiling, cfg.MIN_PRICE))

    willingness *= _position_fit_multiplier(team, player, archetype)
    willingness *= archetype.position_weight.get(player.position, 1.0)

    # Tier-cliff panic: last or second-to-last player left in this tier.
    if player.tier_rank >= player.tier_size - 1:
        willingness *= archetype.tier_aggression

    # Tilt: revenge-bidding after recent nomination losses at this position.
    if team.tilt > 0:
        willingness *= archetype.tilt_boost

    # Re-clamp star candidates AFTER the multipliers above: applying
    # position-fit/tier-aggression on top of an already-capped star
    # ceiling defeated the cap (e.g. a $145 ceiling * 1.3 position-fit *
    # 1.3 tier-aggression ~ $245) -- caught in testing via a $58 WR
    # simulating at a $197+ mean price despite the 2.5x value cap above.
    if is_star_candidate and not archetype.strict_value_ceiling:
        willingness = min(willingness, player.base_value * cfg.STAR_MAX_VALUE_MULTIPLE)

    # Early-draft premium: decaying multiplicative bump, not a floor, so it
    # scales *relevant* willingness up (a real $50 player bid more
    # aggressively at pick 5) rather than manufacturing demand for chaff
    # (a $1 player at 1.6x is still ~$1.60 -- negligible). Skipped for
    # Value Purist: their whole identity is a preset price immune to market
    # mood, which is exactly the discipline the OTHER archetypes are
    # meant to be overpaying against.
    if not archetype.strict_value_ceiling:
        premium = 1.0 + cfg.EARLY_DRAFT_PREMIUM_MAX * (1.0 - draft_progress)
        willingness *= premium

    # NOTE: no "spend the rest of my budget" pressure lives here. Two
    # earlier attempts at that (a hard cliff at slots_needed==1, then a
    # continuous fair-share floor applied to every player) either produced
    # a $286 bid on a ~$21 player or made teams bid $30+ on scrub backup
    # QBs. Guaranteeing "every dollar spent AND every roster spot filled"
    # is handled instead as an explicit deterministic rule in auction.py
    # (a team's final roster slot costs exactly whatever budget they have
    # left) -- English-auction bidding literally cannot force spend beyond
    # a winning bid when nobody contests a nomination, so this has to be a
    # rule layered on top, not an emergent willingness effect.

    return willingness
