"""Per-team private valuation and bid-willingness, driven by the archetype
parameter table. This is where 'higher cash bids aggressively' and
'balance positions intelligently' actually get implemented.

PHASE 3D item 5: compute_willingness was rewritten from a multiplicative
model (private-value noise x position/tier/tilt/premium multipliers,
capped by a 2.5x star-ceiling override) to a fully additive,
dollar-denominated model:

    willingness = base_market_anchor + team_adjustment + behavior_adjustment

base_market_anchor blends PUBLIC_AUCTION_ANCHOR, HISTORICAL_LEAGUE_PRICE,
and the projection-based internal neutral value (player.base_value).
team_adjustment sums five independently-bounded components (roster fit,
scarcity, tier, budget state, future alternatives); behavior_adjustment
sums two (archetype, noise). The whole result is clipped to
[anchor - MAX_TOTAL_DISCOUNT_BELOW_ANCHOR, anchor + MAX_TOTAL_PREMIUM_OVER_ANCHOR]
-- see auction_model/config.py's Phase 3D item 5 section for the full
design rationale and every MAX_* bound. The old 2.5x star-ceiling
override (STAR_MAX_VALUE_MULTIPLE) has been removed from this function
entirely, not replaced with another hand-picked multiplier.
"""

from __future__ import annotations

import numpy as np

from . import config_bridge as cfg
from .archetypes import ARCHETYPES, Archetype
from .models import Player, Team


def get_private_value(team: Team, player: Player, rng: np.random.Generator) -> float:
    """Noisy perceived value, computed once per player per team per draft
    and cached -- represents 'what this owner privately thinks the player
    is worth.' Used by nomination.py's rival-interest ratio; NOT used by
    compute_willingness any more (see get_noise_adjustment for that)."""
    if player.name in team.private_value:
        return team.private_value[player.name]
    archetype = team.strategy
    noise = float(np.clip(rng.normal(1.0, archetype.noise_std), 0.5, 1.9))
    value = player.base_value * noise
    team.private_value[player.name] = value
    return value


def compute_base_market_anchor(player: Player) -> float:
    """Phase 3D item 5: weighted blend of PUBLIC_AUCTION_ANCHOR,
    HISTORICAL_LEAGUE_PRICE, and the projection-based internal neutral
    value (player.base_value), renormalized over whichever of the first
    two actually have coverage for this player (see Player's own
    docstring -- both are None, not a fabricated number, when their
    source has no data for this player). player.base_value is always
    populated (it is this simulator's own VBD output), so the blend can
    never fail; a player with zero external coverage just falls back to
    the projection-based component alone."""
    components = [(player.base_value, cfg.BASE_ANCHOR_WEIGHT_PROJECTION_NEUTRAL)]
    if player.public_anchor_value is not None:
        components.append((player.public_anchor_value, cfg.BASE_ANCHOR_WEIGHT_PUBLIC))
    if player.historical_anchor_value is not None:
        components.append((player.historical_anchor_value, cfg.BASE_ANCHOR_WEIGHT_HISTORICAL))
    total_weight = sum(w for _, w in components)
    return sum(v * w for v, w in components) / total_weight


def _roster_fit_adjustment(team: Team, player: Player, archetype: Archetype) -> float:
    """Positive when this team still needs more of this position (relative
    to the archetype's own rigid position_targets, when it has any),
    negative when already saturated. Zero for archetypes with no rigid
    plan for this position (most of them -- see Archetype.position_targets)."""
    target = archetype.position_targets.get(player.position)
    if target is None:
        return 0.0
    roster_size = max(1, len(team.roster))
    current_share = team.position_count(player.position) / roster_size
    need_gap = max(-1.0, min(1.0, target - current_share))
    return cfg.MAX_ROSTER_FIT_ADJUSTMENT * need_gap


# ASSUMPTION (disclosed): "plenty left" reference counts per position used
# to normalize the pool-wide scarcity signal below -- not derived from a
# formal model, but a reasonable order-of-magnitude given this league's own
# 12-team / 3-FLEX / no-K-DEF roster shape (see auction_model/config.py's
# STARTING_LINEUP + BENCH_DEMAND_PER_TEAM for the demand these reference
# thresholds are meant to roughly span).
_SCARCITY_REFERENCE_COUNT = {"QB": 15, "RB": 25, "WR": 30, "TE": 12}


def _scarcity_adjustment(player: Player, available: dict[str, Player] | None) -> float:
    """Pool-wide positional scarcity: how many players remain, right now,
    at this position across the ENTIRE live pool (not just this player's
    tier -- see _tier_adjustment for that). Zero when no live-pool context
    is available (e.g. a direct unit-test call) rather than a fabricated
    guess."""
    if available is None:
        return 0.0
    remaining = sum(1 for p in available.values() if p.position == player.position)
    reference = _SCARCITY_REFERENCE_COUNT.get(player.position, 20)
    signal = max(0.0, min(1.0, 1.0 - (remaining / reference)))
    return cfg.MAX_SCARCITY_ADJUSTMENT * signal


def _tier_adjustment(player: Player, archetype: Archetype) -> float:
    """Within-tier cliff urgency: highest for the last player left in
    their own value tier, scaled by how much THIS archetype's own
    tier_aggression (an existing, already-declared parameter) says it
    cares about tier cliffs at all -- not a new hand-picked number."""
    if player.tier_size <= 1:
        return 0.0
    cliff_signal = (player.tier_rank - 1) / (player.tier_size - 1)
    # ARCHETYPES' own tier_aggression values span roughly 1.0-1.3 -- used
    # here only to scale how much weight this archetype puts on cliffs,
    # not introduced as a new constant.
    archetype_weight = max(0.0, min(1.0, (archetype.tier_aggression - 1.0) / 0.3))
    return cfg.MAX_TIER_ADJUSTMENT * cliff_signal * archetype_weight


def _budget_state_adjustment(team: Team) -> float:
    """Positive when this team is cash-rich relative to its own remaining
    roster needs (can afford to pay up), negative when cash-poor relative
    to needs (must be conservative) -- compared against the league-average
    budget-per-slot pace, using only this team's own state."""
    if team.slots_needed <= 0:
        return 0.0
    budget_per_slot = team.budget_remaining / team.slots_needed
    reference_per_slot = cfg.BUDGET_PER_TEAM / max(1, cfg.REQUIRED_ROSTER_SIZE)
    signal = max(-1.0, min(1.0, (budget_per_slot - reference_per_slot) / reference_per_slot))
    return cfg.MAX_BUDGET_STATE_ADJUSTMENT * signal


def _future_alternatives_adjustment(player: Player, available: dict[str, Player] | None) -> float:
    """Negative (discount) when close substitutes for this player remain
    available -- no need to pay up now if a similar-value player at the
    same position is still on the board. Zero with no live-pool context."""
    if available is None:
        return 0.0
    tolerance = max(5.0, player.base_value * 0.15)
    similar = [
        p for p in available.values()
        if p.position == player.position and p.name != player.name
        and abs(p.base_value - player.base_value) <= tolerance
    ]
    signal = max(-1.0, min(1.0, 1.0 - len(similar) / 5.0))
    return cfg.MAX_FUTURE_ALTERNATIVES_ADJUSTMENT * signal


_ARCHETYPE_AGGRESSION_BOUNDS: tuple[float, float] | None = None


def _archetype_aggression_bounds() -> tuple[float, float]:
    """Min/max of every DECLARED archetype's own price_ceiling_pct (+
    star_ceiling_pct for star-hunters) -- computed once from the existing
    ARCHETYPES table so the aggression signal below is DERIVED, not a new
    hand-picked scale, and works identically for a custom/evolved
    Archetype instance not present in ARCHETYPES at all."""
    global _ARCHETYPE_AGGRESSION_BOUNDS
    if _ARCHETYPE_AGGRESSION_BOUNDS is None:
        raw = [
            a.price_ceiling_pct + (a.star_ceiling_pct if a.max_stars > 0 else 0.0)
            for a in ARCHETYPES.values()
        ]
        _ARCHETYPE_AGGRESSION_BOUNDS = (min(raw), max(raw))
    return _ARCHETYPE_AGGRESSION_BOUNDS


def _archetype_adjustment(archetype: Archetype) -> float:
    lo, hi = _archetype_aggression_bounds()
    raw = archetype.price_ceiling_pct + (archetype.star_ceiling_pct if archetype.max_stars > 0 else 0.0)
    spread = hi - lo if hi > lo else 1.0
    signal = max(-1.0, min(1.0, 2.0 * (raw - lo) / spread - 1.0))
    return cfg.MAX_ARCHETYPE_ADJUSTMENT * signal


def get_noise_adjustment(team: Team, player: Player, rng: np.random.Generator) -> float:
    """Bounded dollar noise, cached once per (team, player) per draft --
    replaces the old multiplicative private-value noise inside
    compute_willingness (get_private_value above is unchanged and still
    used by nomination.py, which reads it as a ratio, not a dollar
    amount)."""
    if player.name in team.noise_adjustment_cache:
        return team.noise_adjustment_cache[player.name]
    archetype = team.strategy
    z = float(np.clip(rng.normal(0.0, 1.0), -2.5, 2.5)) / 2.5
    # Scaled by this archetype's own noise_std (already-declared,
    # 0.08-0.15 across ARCHETYPES) relative to a fixed 0.12 reference, so
    # a "more emotional" archetype gets proportionally more dollar noise.
    noise = cfg.MAX_NOISE_ADJUSTMENT * z * (archetype.noise_std / 0.12)
    team.noise_adjustment_cache[player.name] = noise
    return noise


def compute_willingness(
    team: Team, player: Player, rng: np.random.Generator, draft_progress: float = 0.0,
    diagnostics: dict | None = None, available: dict[str, Player] | None = None,
) -> float:
    """Max price this team is willing to raise a bid to, right now, for
    this player -- before the hard roster-slot budget cap is applied.

    available: optional {name: Player} of the currently-remaining live
    pool (resolve_bid already has this in scope) -- feeds the pool-wide
    scarcity and future-alternatives components. None (the default) zeros
    both rather than fabricating a signal with no live-pool context, so
    every existing direct call to compute_willingness (e.g. in tests)
    keeps working unchanged.

    diagnostics: optional, filled in place with every named additive
    component so a caller can audit exactly how a final willingness
    figure was assembled (mirrors the phase 3C instrumentation pattern --
    additive, zero behavior change when omitted)."""
    archetype = team.strategy

    base_market_anchor = compute_base_market_anchor(player)

    roster_fit = _roster_fit_adjustment(team, player, archetype)
    scarcity = _scarcity_adjustment(player, available)
    tier = _tier_adjustment(player, archetype)
    budget_state = _budget_state_adjustment(team)
    future_alternatives = _future_alternatives_adjustment(player, available)
    team_adjustment = roster_fit + scarcity + tier + budget_state + future_alternatives

    archetype_adj = _archetype_adjustment(archetype)
    noise_adj = get_noise_adjustment(team, player, rng)
    behavior_adjustment = archetype_adj + noise_adj

    # Tilt (revenge-bidding after recent nomination losses) folds into
    # behavior_adjustment as its own bounded add-on, reusing the existing
    # tilt/tilt_boost signals rather than a new mechanism.
    tilt_applied = team.tilt > 0
    tilt_adjustment = cfg.MAX_ARCHETYPE_ADJUSTMENT * (archetype.tilt_boost - 1.0) if tilt_applied else 0.0
    behavior_adjustment += tilt_adjustment

    # Early-draft premium (bidders flush with cash early, tightening up
    # late) -- a bounded, decaying add-on, skipped for Value Purist (their
    # whole identity is a preset price immune to market mood).
    if not archetype.strict_value_ceiling:
        early_draft_adjustment = cfg.MAX_ARCHETYPE_ADJUSTMENT * cfg.EARLY_DRAFT_PREMIUM_MAX * (1.0 - draft_progress)
    else:
        early_draft_adjustment = 0.0
    behavior_adjustment += early_draft_adjustment

    raw_willingness = base_market_anchor + team_adjustment + behavior_adjustment

    # Single overall bound relative to the anchor -- replaces the old
    # star-ceiling override with one uniform rule that applies to every
    # player alike, not a special case for "star" candidates.
    lower_bound = base_market_anchor - cfg.MAX_TOTAL_DISCOUNT_BELOW_ANCHOR
    upper_bound = base_market_anchor + cfg.MAX_TOTAL_PREMIUM_OVER_ANCHOR
    willingness = max(lower_bound, min(raw_willingness, upper_bound))
    willingness = max(willingness, cfg.MIN_PRICE)

    # Value Purist: refuses to cross their own anchor at all (no "just
    # this once" overpay) -- same discipline the old strict_value_ceiling
    # branch enforced, expressed additively: no positive adjustment ever
    # pushes them above their own base_market_anchor.
    if archetype.strict_value_ceiling:
        willingness = min(willingness, base_market_anchor)
        willingness = max(willingness, cfg.MIN_PRICE)

    if diagnostics is not None:
        diagnostics["base_market_anchor"] = base_market_anchor
        diagnostics["roster_fit_adjustment"] = roster_fit
        diagnostics["scarcity_adjustment"] = scarcity
        diagnostics["tier_adjustment"] = tier
        diagnostics["budget_state_adjustment"] = budget_state
        diagnostics["future_alternatives_adjustment"] = future_alternatives
        diagnostics["team_adjustment"] = team_adjustment
        diagnostics["archetype_adjustment"] = archetype_adj
        diagnostics["noise_adjustment"] = noise_adj
        diagnostics["tilt_adjustment"] = tilt_adjustment
        diagnostics["early_draft_adjustment"] = early_draft_adjustment
        diagnostics["behavior_adjustment"] = behavior_adjustment
        diagnostics["raw_willingness_before_bound"] = raw_willingness
        diagnostics["lower_bound"] = lower_bound
        diagnostics["upper_bound"] = upper_bound
        diagnostics["final_willingness"] = willingness
        diagnostics["total_premium_over_anchor"] = willingness - base_market_anchor
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
