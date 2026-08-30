"""Iterative keeper-market equilibrium, counterfactual depleted alpha, tag optimization."""

from __future__ import annotations

import hashlib
import itertools
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config, keepers, valuation

_CACHE: dict[str, dict] = {}
_CACHE_STATS = {"hits": 0, "misses": 0}


@dataclass
class KeeperMarketResult:
    roster: pd.DataFrame
    inflation: dict
    iteration_log: pd.DataFrame
    converged: bool
    iterations: int
    cycle_detected: bool
    cache_hits: int
    cache_misses: int
    runtime_seconds: float


def _keeper_hash(df: pd.DataFrame) -> str:
    kept = df[df["will_keep"]][["team", "player", "tag_used"]].sort_values(["team", "player"])
    payload = kept.to_csv(index=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _price_market(
    full_pool: pd.DataFrame,
    roster: pd.DataFrame,
    blend_weight: float,
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    state_hash = _keeper_hash(roster)
    if state_hash in _CACHE:
        _CACHE_STATS["hits"] += 1
        cached = _CACHE[state_hash]
        return cached["inflation"], cached["priced_live"], cached["priced_hypo"]

    _CACHE_STATS["misses"] += 1
    inflation = keepers.inflation_summary(roster)
    keeper_cols = roster[["team", "player", "will_keep", "tag_used", "keeper_price_2026"]].copy()
    pool = full_pool.drop(columns=[c for c in ("will_keep", "tag_used", "keeper_price_2026") if c in full_pool.columns])
    pool = pool.merge(keeper_cols, on=["team", "player"], how="left")
    pool["will_keep"] = pool["will_keep"].fillna(False).astype(bool)
    pool["tag_used"] = pool["tag_used"].fillna(False).astype(bool)
    priced_live, priced_hypo = valuation.price_live_and_hypothetical(pool, inflation, blend_weight)
    _CACHE[state_hash] = {"inflation": inflation, "priced_live": priced_live, "priced_hypo": priced_hypo}
    return inflation, priced_live, priced_hypo


def _legacy_position_ratio(full_pool: pd.DataFrame, roster: pd.DataFrame, priced_live: pd.DataFrame, priced_hypo: pd.DataFrame) -> pd.Series:
    live = priced_live.set_index("player")["suggested_auction_price"]
    hypo = priced_hypo.set_index("player")["hypothetical_open_market_value"]
    kept_players = set(roster.loc[roster["will_keep"].astype(bool), "player"])
    ratios = {}
    for pos in ("QB", "RB", "WR", "TE"):
        mask = (full_pool["position"] == pos) & ~full_pool["player"].isin(kept_players)
        live_vals = live.reindex(full_pool.loc[mask, "player"]).dropna()
        hypo_vals = hypo.reindex(full_pool.loc[mask, "player"]).dropna()
        valid = (live_vals > 0) & (hypo_vals > 0)
        if valid.any():
            ratios[pos] = float((live_vals[valid] / hypo_vals[valid]).mean())
        else:
            ratios[pos] = 1.0
    roster_pos = roster.set_index(roster.index)["position"]
    return roster_pos.map(ratios).fillna(1.0)


def counterfactual_release_price(
    row_idx: int,
    full_pool: pd.DataFrame,
    roster: pd.DataFrame,
    blend_weight: float,
    use_exact: bool = True,
) -> tuple[float, str]:
    """Return (release_price, method_label)."""
    row = roster.loc[row_idx]
    if not use_exact and config.DEPLETED_ALPHA_COUNTERFACTUAL_MODE == "position_ratio_fallback":
        _, priced_live, priced_hypo = _price_market(full_pool, roster, blend_weight)
        ratio = _legacy_position_ratio(full_pool, roster, priced_live, priced_hypo).loc[row_idx]
        neutral = float(priced_hypo.loc[priced_hypo["player"] == row["player"], "hypothetical_open_market_value"].iloc[0])
        return neutral * float(ratio), "position_ratio_fallback"

    alt = roster.copy()
    alt.loc[row_idx, "will_keep"] = False
    alt.loc[row_idx, "tag_used"] = False
    alt = keepers.price_keepers(alt)
    _, priced_live, _ = _price_market(full_pool, alt, blend_weight)
    match = priced_live[priced_live["player"] == row["player"]]
    if match.empty:
        return 0.0, "PLAYER_COUNTERFACTUAL"
    price = float(match.iloc[0]["suggested_auction_price"])
    return price, "PLAYER_COUNTERFACTUAL"


def _standard_cost(row: pd.Series) -> float:
    if pd.isna(row["salary_2025"]):
        return np.nan
    return keepers.keeper_price(row["salary_2025"], False, bool(row.get("paul_rule_eligible", False)))


def _exact_counterfactual_mask(roster: pd.DataFrame) -> pd.Series:
    """Exact counterfactual for Sam, projected keepers, and borderline contracts."""
    mask = pd.Series(False, index=roster.index)
    if config.DEPLETED_ALPHA_COUNTERFACTUAL_MODE == "player_counterfactual":
        mask = roster["salary_2025"].notna()
        return mask

    # Fast mode: exact for high-impact players only
    mask |= roster["team"] == config.SAM_TEAM_NAME
    if "will_keep" in roster.columns:
        mask |= roster["will_keep"].astype(bool)
    if "neutral_alpha" in roster.columns:
        borderline = roster["neutral_alpha"].fillna(-999) > -15
        mask |= borderline & roster["salary_2025"].notna()
    mask &= roster["salary_2025"].notna()
    return mask


def compute_player_keeper_metrics(
    roster: pd.DataFrame,
    full_pool: pd.DataFrame,
    neutral_value: pd.Series,
    blend_weight: float,
    exact_mask: pd.Series | None = None,
) -> pd.DataFrame:
    out = roster.copy()
    out["neutral_value"] = neutral_value
    out["standard_keeper_cost"] = out.apply(_standard_cost, axis=1)
    out["neutral_alpha"] = out["neutral_value"] - out["standard_keeper_cost"]

    release_prices = []
    methods = []
    legacy = []

    batch_live = batch_hypo = None
    batch_ratios = None
    if exact_mask is not None and not exact_mask.any():
        _, batch_live, batch_hypo = _price_market(full_pool, roster, blend_weight)
        batch_ratios = _legacy_position_ratio(full_pool, roster, batch_live, batch_hypo)

    for idx, row in out.iterrows():
        exact = True if exact_mask is None else bool(exact_mask.loc[idx])
        if not exact and batch_ratios is not None:
            ratio = float(batch_ratios.loc[idx])
            match = batch_hypo[batch_hypo["player"] == row["player"]]
            neutral = float(match.iloc[0]["hypothetical_open_market_value"]) if len(match) else 0.0
            rp = neutral * ratio
            method = "position_ratio_fallback"
        else:
            rp, method = counterfactual_release_price(idx, full_pool, roster, blend_weight, use_exact=exact)
        release_prices.append(rp)
        methods.append(method)
        legacy.append(rp - out.loc[idx, "standard_keeper_cost"])

    out["counterfactual_release_price"] = release_prices
    out["depleted_alpha_method"] = methods
    out["approximate_depleted_alpha_legacy"] = legacy
    selected_cost = out.apply(
        lambda r: keepers.keeper_price(
            r["salary_2025"], bool(r.get("tag_used", False)), bool(r.get("paul_rule_eligible", False))
        ) if r.get("will_keep") and pd.notna(r["salary_2025"]) else r["standard_keeper_cost"],
        axis=1,
    )
    out["selected_keeper_cost"] = selected_cost
    out["depleted_market_alpha"] = out["counterfactual_release_price"] - out["selected_keeper_cost"]
    out["scarcity_premium"] = out["counterfactual_release_price"] - out["neutral_value"]
    return out


def _portfolio_value(team_df: pd.DataFrame, keep_indices: list, tag_idx: int | None) -> float:
    total = 0.0
    for idx in keep_indices:
        row = team_df.loc[idx]
        tagged = tag_idx is not None and idx == tag_idx
        cost = keepers.keeper_price(
            row["salary_2025"], tagged, bool(row.get("paul_rule_eligible", False))
        )
        total += float(row["counterfactual_release_price"]) - cost
    return total


def optimize_team_keeper_portfolio(
    team_df: pd.DataFrame,
    force_keep: set[int] | None = None,
    force_release: set[int] | None = None,
    tag_allowed: bool = True,
) -> tuple[list[int], int | None, float]:
    """Return (keeper_indices, tag_index_or_none, portfolio_depleted_value)."""
    force_keep = force_keep or set()
    force_release = force_release or set()
    candidates = team_df[
        ~team_df.index.isin(force_release) & team_df["salary_2025"].notna()
    ].copy()
    if candidates.empty:
        return [], None, 0.0

    indices = list(candidates.index)
    limit = config.MAX_KEEPERS_PER_TEAM
    if config.KEEPER_COUNT_IS_EXACT:
        min_keep = limit
    else:
        min_keep = len(force_keep)

    best_keep: list[int] = []
    best_tag: int | None = None
    best_val = -np.inf

    max_r = min(limit, len(indices))
    for r in range(min_keep, max_r + 1):
        for combo in itertools.combinations(indices, r):
            if not all(i in combo for i in force_keep):
                continue
            if config.KEEPER_COUNT_IS_EXACT and r != limit:
                continue
            if not config.KEEPER_COUNT_IS_EXACT:
                low_col = "depleted_alpha_low" if "depleted_alpha_low" in team_df.columns else "depleted_market_alpha"
                margin = config.KEEPER_DECISION_MARGIN
                vals = [team_df.loc[i, low_col] for i in combo]
                if any(v <= margin for v in vals):
                    continue
            tag_options: list[int | None] = [None]
            if tag_allowed and config.SCENARIO_TAG == "C" and config.FRANCHISE_TAGS_PER_TEAM >= 1:
                tag_options = [None] + list(combo)
            for tag_idx in tag_options:
                val = _portfolio_value(team_df, list(combo), tag_idx)
                if val > best_val:
                    best_val = val
                    best_keep = list(combo)
                    best_tag = tag_idx

    if best_val == -np.inf:
        return list(force_keep)[:limit], None, 0.0
    return best_keep, best_tag, best_val


def iterate_keeper_market(
    roster: pd.DataFrame,
    full_pool: pd.DataFrame,
    neutral_value: pd.Series,
    blend_weight: float,
    overrides: pd.DataFrame | None = None,
    max_iterations: int | None = None,
) -> KeeperMarketResult:
    t0 = time.time()
    _CACHE.clear()
    _CACHE_STATS["hits"] = 0
    _CACHE_STATS["misses"] = 0
    max_iter = max_iterations or config.MAX_KEEPER_MARKET_ITERATIONS

    working = roster.copy()
    working["will_keep"] = keepers.neutral_alpha_keep_flag(working, neutral_value)
    working["tag_used"] = False
    working = keepers.price_keepers(working)

    pending_changes: dict[tuple[str, str], bool] = {}

    force_keep: dict[tuple[str, str], bool] = {}
    force_release: dict[tuple[str, str], bool] = {}
    if overrides is not None and not overrides.empty:
        for _, ov in overrides.iterrows():
            key = (str(ov["team"]).strip(), str(ov["player"]).strip())
            if "force_keep" in ov and str(ov.get("force_keep", "")).lower() in {"true", "1", "yes", "y"}:
                force_keep[key] = True
            if "force_release" in ov and str(ov.get("force_release", "")).lower() in {"true", "1", "yes", "y"}:
                force_release[key] = True

    log_rows: list[dict] = []
    seen_hashes: list[str] = []
    converged = False
    cycle = False

    for iteration in range(1, max_iter + 1):
        exact_mask = _exact_counterfactual_mask(working)
        metrics = compute_player_keeper_metrics(
            working, full_pool, neutral_value, blend_weight, exact_mask=exact_mask
        )
        new_state = working.copy()
        new_state["will_keep"] = False
        new_state["tag_used"] = False

        for team, team_df in metrics.groupby("team"):
            fk = {idx for idx, row in team_df.iterrows() if (team, row["player"]) in force_keep}
            fr = {idx for idx, row in team_df.iterrows() if (team, row["player"]) in force_release}
            keep_idxs, tag_idx, _ = optimize_team_keeper_portfolio(team_df, fk, fr)
            for idx in keep_idxs:
                new_state.loc[idx, "will_keep"] = True
            if tag_idx is not None:
                new_state.loc[tag_idx, "tag_used"] = True

        new_state = keepers.price_keepers(new_state)

        if config.KEEPER_MARKET_UPDATE_METHOD == "DAMPED_CONFIRMATION":
            for idx, row in new_state.iterrows():
                key = (row["team"], row["player"])
                proposed = bool(new_state.loc[idx, "will_keep"])
                if key in pending_changes and pending_changes[key] == proposed:
                    working.loc[idx, "will_keep"] = proposed
                    working.loc[idx, "tag_used"] = new_state.loc[idx, "tag_used"]
                else:
                    pending_changes[key] = proposed
            working = keepers.price_keepers(working)
            state_hash = _keeper_hash(working)
        else:
            working = new_state
            state_hash = _keeper_hash(new_state)

        for idx, row in metrics.iterrows():
            prior_keep = working.loc[idx, "will_keep"]
            new_keep = new_state.loc[idx, "will_keep"]
            change = "unchanged"
            if prior_keep != new_keep:
                change = "added" if new_keep else "removed"
            log_rows.append({
                "iteration": iteration,
                "team": row["team"],
                "player": row["player"],
                "keeper_status": new_keep,
                "selected_keeper_cost": new_state.loc[idx, "keeper_price_2026"],
                "tag_status": new_state.loc[idx, "tag_used"],
                "neutral_alpha": row["neutral_alpha"],
                "depleted_market_alpha": row["depleted_market_alpha"],
                "change_from_prior_iteration": change,
                "change_reason": "portfolio_optimization",
                "market_state_hash": state_hash,
            })

        if state_hash in seen_hashes:
            converged = True
            if len(seen_hashes) > 1 and seen_hashes[-1] != state_hash:
                cycle = True
            break
        seen_hashes.append(state_hash)
        if config.KEEPER_MARKET_UPDATE_METHOD != "DAMPED_CONFIRMATION":
            if working["will_keep"].equals(new_state["will_keep"]) and working["tag_used"].equals(new_state["tag_used"]):
                converged = True
                break
            working = new_state
        elif working["will_keep"].equals(new_state["will_keep"]) and working["tag_used"].equals(new_state["tag_used"]):
            converged = True
            break

    final_metrics = compute_player_keeper_metrics(
        working, full_pool, neutral_value, blend_weight, exact_mask=_exact_counterfactual_mask(working)
    )
    inflation, _, _ = _price_market(full_pool, working, blend_weight)
    log_df = pd.DataFrame(log_rows)

    return KeeperMarketResult(
        roster=final_metrics,
        inflation=inflation,
        iteration_log=log_df,
        converged=converged,
        iterations=iteration,
        cycle_detected=cycle,
        cache_hits=_CACHE_STATS["hits"],
        cache_misses=_CACHE_STATS["misses"],
        runtime_seconds=time.time() - t0,
    )
