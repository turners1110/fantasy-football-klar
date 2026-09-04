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


def compute_willingness(
    team: Team, player: Player, rng: np.random.Generator, draft_progress: float = 0.0,
    diagnostics: dict | None = None,
) -> float:
    """Max price this team is willing to raise a bid to, right now, for
    this player -- before the hard roster-slot budget cap is applied.

    diagnostics: PHASE 3C item 7 instrumentation, optional and additive
    (matches the existing bid_stats/unsold_log pattern -- every existing
    caller is unaffected). If given, it is filled in place with the
    named intermediate multiplier/value at each stage, keyed by stage
    name, so a caller can audit exactly how a final willingness figure
    was assembled without re-deriving it from scratch or guessing."""
    archetype = team.strategy
    private_val = get_private_value(team, player, rng)
    if diagnostics is not None:
        diagnostics["base_value"] = player.base_value
        diagnostics["private_value_after_noise"] = private_val
        diagnostics["noise_ratio"] = private_val / player.base_value if player.base_value else None

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
    if diagnostics is not None:
        diagnostics["is_star_candidate"] = is_star_candidate
        diagnostics["willingness_after_star_or_ceiling"] = willingness

    position_fit = _position_fit_multiplier(team, player, archetype)
    position_weight = archetype.position_weight.get(player.position, 1.0)
    willingness *= position_fit
    willingness *= position_weight
    if diagnostics is not None:
        diagnostics["position_fit_multiplier"] = position_fit
        diagnostics["position_weight_multiplier"] = position_weight

    # Tier-cliff panic: last or second-to-last player left in this tier.
    tier_cliff = player.tier_rank >= player.tier_size - 1
    if tier_cliff:
        willingness *= archetype.tier_aggression
    if diagnostics is not None:
        diagnostics["tier_aggression_applied"] = archetype.tier_aggression if tier_cliff else 1.0

    # Tilt: revenge-bidding after recent nomination losses at this position.
    tilt_applied = team.tilt > 0
    if tilt_applied:
        willingness *= archetype.tilt_boost
    if diagnostics is not None:
        diagnostics["tilt_boost_applied"] = archetype.tilt_boost if tilt_applied else 1.0
        diagnostics["willingness_after_tier_and_tilt"] = willingness

    # Early-draft premium: decaying multiplicative bump, not a floor, so it
    # scales *relevant* willingness up (a real $50 player bid more
    # aggressively at pick 5) rather than manufacturing demand for chaff
    # (a $1 player at 1.6x is still ~$1.60 -- negligible). Skipped for
    # Value Purist: their whole identity is a preset price immune to market
    # mood, which is exactly the discipline the OTHER archetypes are
    # meant to be overpaying against.
    #
    # PHASE 3C FIX: this used to run AFTER the star re-clamp below, which
    # defeated the very cap the re-clamp exists to enforce -- a stacked-
    # multiplier bug caught via item 7's bid-construction audit
    # (top_sale_bid_decomposition.csv): Jaylen Waddle, base_value $64,
    # star-ceiling-clamped to $160 (2.5x), then multiplied by the
    # early-draft premium (1.57x at draft_progress~0) to $251.56 -- 3.93x
    # base_value, well past the documented 2.5x hard cap. Moved above the
    # re-clamp so it is bounded by it like every other multiplier.
    if not archetype.strict_value_ceiling:
        premium = 1.0 + cfg.EARLY_DRAFT_PREMIUM_MAX * (1.0 - draft_progress)
        willingness *= premium
        if diagnostics is not None:
            diagnostics["early_draft_premium_multiplier"] = premium
    elif diagnostics is not None:
        diagnostics["early_draft_premium_multiplier"] = 1.0

    # Re-clamp star candidates AFTER every multiplier above (position-fit,
    # tier-aggression, tilt, AND early-draft premium -- see the premium
    # block's own comment for why this now runs last): applying any of
    # them on top of an already-capped star ceiling defeats the cap
    # (e.g. a $145 ceiling * 1.3 tier-aggression * 1.5 early-draft ~ $283)
    # -- caught in testing via a $58 WR simulating at a $197+ mean price
    # despite the 2.5x value cap above.
    if is_star_candidate and not archetype.strict_value_ceiling:
        willingness = min(willingness, player.base_value * cfg.STAR_MAX_VALUE_MULTIPLE)
        if diagnostics is not None:
            diagnostics["star_reclamp_applied"] = True

    if diagnostics is not None:
        diagnostics["final_willingness"] = willingness
        diagnostics["total_multiplier_vs_base_value"] = willingness / player.base_value if player.base_value else None

    # NOTE: no "spend the rest of my budget" pressure lives here. Two
    # earlier attempts at that (a hard cliff at slots_needed==1, then a
    # continuous fair-share floor applied to every player) either produced
    # a $286 bid on a ~$21 player or made teams bid $30+ on scrub backup
    # QBs. PHASE 2 removed the forced-final-slot rule entirely (a team's
    # last roster slot no longer costs its whole remaining budget) --
    # unspent cash at the end of a draft is now legal and expected;
    # roster COMPLETION (not cash exhaustion) is guaranteed instead by
    # mock_draft.feasibility.check_roster_completion_feasibility, which
    # blocks a purchase that would leave no legal path to fill every
    # required slot.

    return willingness
