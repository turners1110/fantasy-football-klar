"""Value-Based Drafting engine, blended with historical-salary anchoring.

Two signals feed every price:

1. **VBD dollars** -- projected points above this league's actual
   replacement level (see config.replacement_rank, which bakes in the
   2RB/2WR/TE/3FLEX-no-K/DEF roster math), converted to dollars. Only
   available for players with a projection supplied.
2. **Anchor dollars** -- last year's real league salary, carried forward
   and scaled by the keeper-driven inflation multiplier. Always available
   for anyone with a confirmed 2025 salary.

``blend_weight`` controls how much the final price trusts (1) vs (2). With
no projections file supplied, blend_weight is forced to 0 (pure historical
anchor) rather than fabricating point projections for real players.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


def compute_replacement_baseline(pool: pd.DataFrame, points_col: str = "projected_points") -> dict:
    """Replacement-level projected points per position, using this league's
    actual replacement rank (config.replacement_rank), not a generic one."""
    baseline = {}
    for position in ("QB", "RB", "WR", "TE"):
        rank = config.replacement_rank(position)
        pos_players = pool[pool["position"] == position].dropna(subset=[points_col])
        pos_players = pos_players.sort_values(points_col, ascending=False)
        if len(pos_players) >= rank and rank > 0:
            baseline[position] = float(pos_players.iloc[rank - 1][points_col])
        elif len(pos_players) > 0:
            baseline[position] = float(pos_players[points_col].min())
        else:
            baseline[position] = np.nan
    return baseline


def add_vbd_scores(pool: pd.DataFrame, points_col: str = "projected_points") -> pd.DataFrame:
    pool = pool.copy()
    if points_col not in pool.columns or pool[points_col].dropna().empty:
        pool["VBD_score"] = np.nan
        return pool
    baseline = compute_replacement_baseline(pool, points_col)
    pool["replacement_points"] = pool["position"].map(baseline)
    pool["VBD_score"] = pool[points_col] - pool["replacement_points"]
    pool.loc[pool["VBD_score"] < 0, "VBD_score"] = 0.0
    return pool


def compute_replacement_baseline_live(
    live_pool: pd.DataFrame,
    kept_count_by_position: dict,
    points_col: str = "projected_points",
) -> dict:
    """Replacement-level points per position for the LIVE (post-keeper)
    auction specifically -- not the full league's original roster math.

    config.replacement_rank(position) is "how many roster spots this
    position needs leaguewide in a normal full draft." If keepers already
    filled some of those spots at a given position, the live auction only
    needs to fill what's left -- e.g. if RB keepers are unusually deep this
    year (stacked, cheap RB keepers across many teams), replacement rank
    for the live RB pool should sit HIGHER (fewer open RB spots left to
    fight over among a still-large remaining RB pool -> RB prices should
    NOT spike) or LOWER (many teams still need RBs and few remain -> RB
    prices SHOULD spike) depending on the real remaining supply/demand
    balance, not a flat inflation multiplier applied the same to every
    position regardless of which ones got hollowed out.
    """
    baseline = {}
    for position in ("QB", "RB", "WR", "TE"):
        total_rank = config.replacement_rank(position)
        remaining_rank = max(total_rank - kept_count_by_position.get(position, 0), 1)
        pos_players = live_pool[live_pool["position"] == position].dropna(subset=[points_col])
        pos_players = pos_players.sort_values(points_col, ascending=False)
        if len(pos_players) >= remaining_rank:
            baseline[position] = float(pos_players.iloc[remaining_rank - 1][points_col])
        elif len(pos_players) > 0:
            baseline[position] = float(pos_players[points_col].min())
        else:
            baseline[position] = np.nan
    return baseline


def add_auction_vbd_scores(
    live_pool: pd.DataFrame,
    kept_count_by_position: dict,
    points_col: str = "projected_points",
) -> pd.DataFrame:
    """"Auction VBD" -- points above the LIVE, post-keeper replacement
    level (see compute_replacement_baseline_live). This is what actually
    drives live-auction dollars; "talent VBD" (add_vbd_scores, full-universe
    replacement level) stays untouched for rankings, keeper alpha, and the
    college draft board.
    """
    pool = live_pool.copy()
    if points_col not in pool.columns or pool[points_col].dropna().empty:
        pool["auction_VBD_score"] = np.nan
        return pool
    baseline = compute_replacement_baseline_live(pool, kept_count_by_position, points_col)
    pool["auction_replacement_points"] = pool["position"].map(baseline)
    pool["auction_VBD_score"] = pool[points_col] - pool["auction_replacement_points"]
    pool.loc[pool["auction_VBD_score"] < 0, "auction_VBD_score"] = 0.0
    return pool


def apply_tier_shrinkage(
    pool: pd.DataFrame,
    shrinkage_pct: float,
    vbd_col: str = "VBD_score",
) -> pd.DataFrame:
    """Real auctions price in tiers, not a smooth curve -- the room bids the
    top few players in a tier up to near-identical prices, then the price
    cliffs once that tier is exhausted. But fully replacing a player's
    VBD_score with their tier's group average (v1's `apply_tier_flattening`)
    erases real skill gaps between the best and worst player in a broad
    tier, and lets a single analyst tier-boundary call create an outsized
    price cliff between two similar players. Shrink partway instead:

        adjusted = shrinkage_pct * tier_mean + (1 - shrinkage_pct) * individual

    shrinkage_pct=1.0 reproduces full flattening; 0.0 is pure individual
    scoring (no tiering effect at all). Picked empirically via the
    same-season backtest (see backtest_2025.py) rather than assumed.
    Players with no `fp_tier` match keep their individual score untouched.
    """
    pool = pool.copy()
    if "fp_tier" not in pool.columns or vbd_col not in pool.columns:
        return pool
    has_tier = pool["fp_tier"].notna() & pool[vbd_col].notna()
    if not has_tier.any():
        return pool
    tier_means = pool.loc[has_tier].groupby(["position", "fp_tier"])[vbd_col].transform("mean")
    individual = pool.loc[has_tier, vbd_col]
    pool.loc[has_tier, vbd_col] = shrinkage_pct * tier_means + (1 - shrinkage_pct) * individual
    return pool


def _proportional_dollars(values: pd.Series, budget: float) -> pd.Series:
    """Distribute `budget` proportionally to positive values; zero/NaN -> 0."""
    positive = values.clip(lower=0).fillna(0)
    total = positive.sum()
    if total <= 0:
        return pd.Series(0.0, index=values.index)
    return positive / total * budget


def price_pool(
    pool: pd.DataFrame,
    remaining_budget: float,
    inflation_multiplier: float,
    blend_weight: float,
    points_col: str = "projected_points",
    n_open_roster_spots: int | None = None,
) -> pd.DataFrame:
    """Compute suggested_auction_price for every non-keeper player.

    blend_weight: 0.0 = pure historical-salary anchor (no projections
    trusted), 1.0 = pure VBD-from-projections. Forced to 0 automatically
    for any player missing a projection, and forced to 0 league-wide if no
    projections were supplied at all.

    If ``pool`` already has a VBD_score column, it's used as-is instead of
    being recomputed here -- lets a caller compute VBD/replacement level
    against a different (larger) reference pool than the one being priced.
    """
    if "VBD_score" not in pool.columns:
        pool = add_vbd_scores(pool, points_col)
    else:
        pool = pool.copy()

    has_projection = pool[points_col].notna() if points_col in pool.columns else pd.Series(False, index=pool.index)

    # v4 Part 8: reliability-weighted anchor, not a binary has-anchor gate.
    # origin_confidence (data_pipeline.classify_salary_origin /
    # fill_anchor_fallback) is a continuous [0,1] trust score per
    # config.SALARY_ORIGIN_RELIABILITY -- e.g. a real recent auction price
    # keeps full weight, a flat-$1 administrative assignment gets ~0, a
    # keeper-escalation-derived salary gets partial weight. Falls back to
    # 1.0 (fully trusted) only if the pool predates this field entirely.
    if "origin_confidence" in pool.columns:
        anchor_reliability = pool["origin_confidence"].fillna(0.0).clip(lower=0.0, upper=1.0)
    else:
        anchor_reliability = pd.Series(1.0, index=pool.index)
    has_anchor = pool["has_confirmed_salary"] & (anchor_reliability > 0)

    # v2 fix (Priority 5), extended for v4: effective_weight RENORMALIZES
    # to whichever signal(s) a player actually has, weighted by anchor
    # reliability rather than a binary gate. A player with a projection and
    # a fully-trusted anchor gets the normal blend_weight/1-blend_weight
    # split; one with a projection and a LOW-reliability anchor has that
    # anchor's pull proportionally discounted back toward pure projection;
    # one with no anchor at all collapses to 100% projection (v2's fix --
    # v1 silently spent 40% of weight on a phantom $0 anchor).
    anchor_component_weight = (1 - blend_weight) * anchor_reliability
    proj_component_weight = pd.Series(0.0, index=pool.index)
    proj_component_weight[has_projection] = blend_weight
    total_weight = proj_component_weight + anchor_component_weight.where(has_anchor, 0.0)
    effective_weight = pd.Series(0.0, index=pool.index)
    can_renormalize = total_weight > 0
    effective_weight[can_renormalize] = (
        proj_component_weight[can_renormalize] / total_weight[can_renormalize]
    )
    # Pure-projection case (no anchor at all): 100% VBD, matching v2.
    effective_weight[has_projection & ~has_anchor] = 1.0

    # Raise to a convexity power before splitting the budget -- see
    # config.VBD_DOLLAR_POWER for why a straight linear split undervalues
    # stars. Only POSITIVE VBD competes (v4 Part 5/7: negative-VBD players
    # get none of the surplus split, not a small negative-power sliver).
    vbd_dollars = _proportional_dollars(
        pool["VBD_score"].clip(lower=0) ** config.VBD_DOLLAR_POWER, remaining_budget
    )

    anchor_raw = pool["salary_2025"] * inflation_multiplier
    anchor_dollars = _proportional_dollars(
        anchor_raw.where(has_anchor, other=np.nan), remaining_budget
    )

    blended = effective_weight * vbd_dollars + (1 - effective_weight) * anchor_dollars

    # Players with neither a confirmed historical salary nor a projection
    # cannot be priced responsibly -- leave null rather than guessing.
    unpriceable = (~pool["has_confirmed_salary"]) & (~has_projection)
    blended[unpriceable] = np.nan

    pool["suggested_auction_price_raw"] = blended
    pool["blend_weight_used"] = effective_weight
    pool["anchor_reliability_used"] = anchor_reliability

    # Reserve $1 x (every player who will be priced) off the top BEFORE
    # running the curve, then add it back after -- guarantees
    # sum(final prices) reconciles exactly to remaining_budget instead of
    # risking overshoot/undershoot once MIN_PRICE floor clipping kicks in
    # for a large share of the pool. Every DRAFTED player gets at least $1;
    # "reserve the floor, curve the surplus, add the floor back" is exact
    # where "curve the whole budget, then clip to floor" is not.
    #
    # v4 Part 7/9: the floor is reserved for the players actually MODELED
    # to fill the league's open auction roster spots -- not every player
    # with any signal at all. Pass n_open_roster_spots (leaguewide open
    # spots after keepers) to restrict the priced pool to the top N by raw
    # value; everyone else is undrafted and priced at exactly $0, not a
    # guaranteed $1 they were never going to actually cost. Omitting
    # n_open_roster_spots preserves the old "price everyone with signal"
    # behavior (used by the hypothetical/neutral full-market pass, where
    # there's no fixed roster-spot constraint to apply).
    has_raw = pool["suggested_auction_price_raw"].notna()
    if n_open_roster_spots is not None:
        ranked = pool.loc[has_raw, "suggested_auction_price_raw"].sort_values(ascending=False)
        drafted_index = ranked.index[: max(n_open_roster_spots, 0)]
        priceable = pd.Series(False, index=pool.index)
        priceable[drafted_index] = True
    else:
        priceable = has_raw

    n_priceable = int(priceable.sum())
    floor_reserve = n_priceable * config.MIN_PRICE
    surplus_budget = max(remaining_budget - floor_reserve, 0.0)

    current_sum = pool.loc[priceable, "suggested_auction_price_raw"].clip(lower=0).sum()
    if current_sum > 0 and surplus_budget > 0:
        scale = surplus_budget / current_sum
    else:
        scale = 0.0
    raw_prices = (
        config.MIN_PRICE
        + pool.loc[priceable, "suggested_auction_price_raw"].clip(lower=0) * scale
    )

    # No price ceiling (v4 Part 7 -- MAX_PRICE is None by default). If a
    # caller has explicitly set config.MAX_PRICE, water-fill any surplus
    # lost to that cap back to uncapped players so the total still
    # reconciles, exactly as v2/v3 did when the ceiling was the default.
    if config.MAX_PRICE is not None:
        final_prices = raw_prices.clip(lower=config.MIN_PRICE, upper=config.MAX_PRICE)
        for _ in range(10):
            shortfall = surplus_budget + n_priceable * config.MIN_PRICE - final_prices.sum()
            if abs(shortfall) < 0.5:
                break
            uncapped = final_prices < config.MAX_PRICE
            if not uncapped.any():
                break
            addable_weight = raw_prices[uncapped].clip(lower=0)
            if addable_weight.sum() <= 0:
                bump = pd.Series(shortfall / uncapped.sum(), index=final_prices.index[uncapped])
            else:
                bump = addable_weight / addable_weight.sum() * shortfall
            final_prices.loc[uncapped] = (final_prices[uncapped] + bump).clip(
                lower=config.MIN_PRICE, upper=config.MAX_PRICE
            )
    else:
        final_prices = raw_prices.clip(lower=config.MIN_PRICE)

    # v4 Part 9: deterministic largest-remainder rounding, not plain
    # round() -- guarantees integer prices sum to EXACTLY the target
    # (floor_reserve + surplus_budget = remaining_budget), not "close."
    target_total = round(floor_reserve + surplus_budget)
    rounded = _largest_remainder_round(final_prices, target_total)

    # NaN = truly no data at all (unpriceable -- neither anchor nor
    # projection); $0 = had real signal but didn't rank into the modeled
    # open-roster-spot pool ("undrafted"). Keeping these distinct matters
    # for the data-quality report (run_sanity_checks) -- collapsing both
    # to $0 would silently hide players with zero pricing signal.
    pool["suggested_auction_price"] = np.where(has_raw, 0.0, np.nan)
    pool.loc[priceable, "suggested_auction_price"] = rounded

    return pool.drop(columns=["suggested_auction_price_raw"])


def _largest_remainder_round(values: pd.Series, target_total: int) -> pd.Series:
    """Round every value to a whole dollar such that the sum is EXACTLY
    target_total (the largest-remainder / Hamilton apportionment method) --
    deterministic given identical inputs (ties broken by index order).
    """
    if len(values) == 0:
        return values
    floors = np.floor(values)
    remainders = values - floors
    deficit = int(target_total - floors.sum())
    result = floors.copy()
    if deficit > 0:
        top_up = remainders.sort_values(ascending=False).index[:deficit]
        result.loc[top_up] += 1
    elif deficit < 0:
        # Extremely rare (only if a min-price floor forced a total above
        # target) -- take back from the smallest remainders first, never
        # below config.MIN_PRICE.
        candidates = result[result > config.MIN_PRICE]
        take_from = remainders.loc[candidates.index].sort_values().index[: -deficit]
        result.loc[take_from] -= 1
    return result


def price_neutral_value(
    full_pool: pd.DataFrame,
    blend_weight: float,
    points_col: str = "projected_points",
    tier_shrinkage_pct: float | None = None,
) -> pd.DataFrame:
    """v4 Part 3: neutral (talent-VBD) value computed BEFORE any keeper
    decision exists -- doesn't need or use a `will_keep` column at all,
    since talent VBD is defined against the full universe regardless of
    who ends up kept. This is what neutral_alpha_keep_flag (keepers.py)
    needs to actually SELECT keepers by alpha, instead of the retired
    salary-band heuristic. Returns every player with
    `hypothetical_open_market_value` -- same neutral-value definition
    price_live_and_hypothetical uses later for the real keeper-alpha
    output, just available earlier in the pipeline.
    """
    if tier_shrinkage_pct is None:
        tier_shrinkage_pct = config.TIER_SHRINKAGE_PCT
    scored = add_vbd_scores(full_pool.copy(), points_col)
    scored = apply_tier_shrinkage(scored, tier_shrinkage_pct, vbd_col="VBD_score")
    priced = price_pool(
        scored,
        remaining_budget=config.TOTAL_LEAGUE_BUDGET,
        inflation_multiplier=1.0,
        blend_weight=blend_weight,
        points_col=points_col,
        n_open_roster_spots=None,
    ).rename(columns={"suggested_auction_price": "hypothetical_open_market_value"})
    return priced


def price_live_and_hypothetical(
    full_pool: pd.DataFrame,
    inflation: dict,
    blend_weight: float,
    points_col: str = "projected_points",
    tier_shrinkage_pct: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Shared by run_valuation.py and draft_ui/engine.py.

    ``full_pool`` must contain EVERY rostered/draftable player -- keepers
    included, already merged with projections.

    Two different questions, two different VBD calculations, two different
    pools to divide dollars across:

    1. `priced_live` -- the real live-auction board. Priced using
       **auction VBD**: points above the LIVE, post-keeper replacement
       level (add_auction_vbd_scores) -- so a position that got hollowed
       out by keepers (e.g. several cheap RB keepers stacked across teams)
       correctly prices its remaining players higher than a flat inflation
       multiplier would catch, and a position keepers barely touched
       doesn't get an inflation bump it didn't earn. Tier shrinkage is
       applied to this auction VBD. Only non-kept players are actually up
       for bid, and only the real leftover budget is actually in play, so
       dollars are split across just that smaller real pool.
    2. `priced_hypothetical` -- every player (kept included) priced using
       **talent VBD**: points above the full league's real replacement
       level (add_vbd_scores), independent of this year's specific keeper
       cuts. "What would this player cost at a normal open auction,
       independent of whether they're actually kept." Used for the roster
       board's keeper-surplus (alpha) comparison and the college draft
       board -- a kept player needs a stable value too, and it shouldn't
       move just because some OTHER position got hollowed out this year.
    """
    if tier_shrinkage_pct is None:
        tier_shrinkage_pct = config.TIER_SHRINKAGE_PCT

    full_scored = add_vbd_scores(full_pool.copy(), points_col)

    # Only a kept player who's actually a real difference-maker (their own
    # talent_VBD clears the FULL-universe replacement bar) should count as
    # removing a unit of remaining demand. This league's keeper heuristic
    # band ($15-45 salary) catches plenty of merely-decent players, not
    # just studs -- treating every one of those 1-for-1 the same as an
    # elite keeper over-collapses the live replacement-level bar (e.g. QB
    # demand was halving from 14 to 7 before this fix), flooring far more
    # of the live pool at $1 than is real.
    real_keepers = full_scored["will_keep"].astype(bool) & (full_scored["VBD_score"] > 0)
    kept_count_by_position = full_scored.loc[real_keepers, "position"].value_counts().to_dict()

    live_pool = full_scored[~full_scored["will_keep"].astype(bool)].copy()
    live_pool = live_pool.rename(columns={"VBD_score": "talent_VBD_score"})
    live_pool = add_auction_vbd_scores(live_pool, kept_count_by_position, points_col)
    live_pool["VBD_score"] = live_pool["auction_VBD_score"]
    live_pool = apply_tier_shrinkage(live_pool, tier_shrinkage_pct, vbd_col="VBD_score")

    # v4 Part 7: only the players actually modeled to fill this league's
    # remaining open auction roster spots get a real ($1+) price; everyone
    # else in the live pool is undrafted (priced $0). n_keepers from the
    # SAME inflation summary that produced remaining_budget, so both stay
    # consistent with each other.
    n_open_roster_spots = max(
        config.NUM_TEAMS * config.TOTAL_ROSTER_SPOTS_PER_TEAM - inflation.get("n_keepers", 0), 0
    )

    priced_live = price_pool(
        live_pool,
        remaining_budget=inflation["remaining_budget"],
        inflation_multiplier=inflation["inflation_multiplier"],
        blend_weight=blend_weight,
        points_col=points_col,
        n_open_roster_spots=n_open_roster_spots,
    )

    hypothetical_pool = apply_tier_shrinkage(full_scored, tier_shrinkage_pct, vbd_col="VBD_score")
    priced_hypothetical = price_pool(
        hypothetical_pool,
        remaining_budget=config.TOTAL_LEAGUE_BUDGET,
        inflation_multiplier=1.0,
        blend_weight=blend_weight,
        points_col=points_col,
    ).rename(columns={"suggested_auction_price": "hypothetical_open_market_value"})

    priced_live = priced_live.merge(
        priced_hypothetical[["player", "hypothetical_open_market_value"]], on="player", how="left",
    )

    return priced_live, priced_hypothetical


def run_sanity_checks(pool: pd.DataFrame, remaining_budget: float) -> dict:
    """Return the sanity-check report described in the spec.

    v4: $0 means "undrafted -- not modeled to fill an open roster spot"
    (Part 7), distinct from NaN ("no data to price at all"). Both are
    reported, but neither pollutes the drafted-player checks (out-of-range,
    large-move-vs-2025-salary).
    """
    drafted = pool[pool["suggested_auction_price"].notna() & (pool["suggested_auction_price"] > 0)]
    total_priced = float(drafted["suggested_auction_price"].sum())
    tolerance = remaining_budget * config.BUDGET_TOLERANCE
    budget_ok = abs(total_priced - remaining_budget) <= tolerance

    below_floor = drafted[drafted["suggested_auction_price"] < config.MIN_PRICE]
    if config.MAX_PRICE is not None:
        above_ceiling = drafted[drafted["suggested_auction_price"] > config.MAX_PRICE]
    else:
        above_ceiling = drafted.iloc[0:0]
    out_of_range = pd.concat([below_floor, above_ceiling])

    comparable = drafted[drafted["has_confirmed_salary"]].copy()
    comparable = comparable[comparable["salary_2025"] > 0]
    ratio = comparable["suggested_auction_price"] / comparable["salary_2025"]
    large_moves = comparable[
        (ratio >= config.LARGE_MOVE_MULTIPLE) | (ratio <= 1 / config.LARGE_MOVE_MULTIPLE)
    ][["team", "player", "position", "salary_2025", "suggested_auction_price"]]

    unpriced = pool[pool["suggested_auction_price"].isna()][["team", "player", "position", "notes"]]
    undrafted = pool[pool["suggested_auction_price"] == 0][["team", "player", "position"]]

    return {
        "remaining_budget": round(remaining_budget, 2),
        "total_priced": round(total_priced, 2),
        "budget_within_tolerance": bool(budget_ok),
        "n_out_of_range": int(len(out_of_range)),
        "n_large_moves_vs_2025_salary": int(len(large_moves)),
        "large_moves": large_moves,
        "n_unpriced_no_data": int(len(unpriced)),
        "unpriced_no_data": unpriced,
        "n_undrafted": int(len(undrafted)),
    }
