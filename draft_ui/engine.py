"""Live draft engine: loads the same baseline valuation run_valuation.py
produces, then keeps recomputing live inflation and a personal
"my_team target price" as picks are logged during the actual auction.

Reuses auction_model directly -- this is not a second pricing model, it's
the same price_yahoo_forward-equivalent baseline, kept live.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from auction_model import config, data_pipeline, keepers, valuation

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"

MY_TEAM = "Sam"

# ASSUMPTION: how aggressively my_target_price tapers once a position's
# starters + fair flex share are covered for my own roster. Same spirit as
# the ASSUMPTION constants in auction_model/config.py -- retune freely.
NEED_TAPER_RATE = 0.25
NEED_FLOOR = 0.35


def load_baseline() -> dict:
    """Run the same pipeline run_valuation.py uses and return an in-memory
    baseline: available pool (priced), my pre-existing roster (keepers),
    and the starting live-auction budget pool."""
    salaries, _log = data_pipeline.load_historical_salaries(
        DATA_DIR / "historical_salaries_2025_raw.csv"
    )
    overrides = data_pipeline.load_optional_csv(DATA_DIR / "keeper_overrides.csv")
    with_keepers = keepers.apply_keeper_overrides(salaries, overrides)
    with_keepers = keepers.price_keepers(with_keepers)
    inflation = keepers.inflation_summary(with_keepers)

    pool = with_keepers[~with_keepers["will_keep"]].copy()

    fp_rankings = data_pipeline.load_fantasypros_rankings(
        BASE_DIR / "FantasyPros_2026_Draft_ALL_Rankings.csv"
    )
    pool = data_pipeline.expand_pool_with_full_universe(pool, fp_rankings)

    projections = data_pipeline.load_optional_csv(DATA_DIR / "projections_2026.csv")
    pool["projected_points"] = pd.NA
    if projections is not None:
        proj = projections.copy()
        proj["_key"] = proj["player"].astype(str).str.strip().str.lower()
        pool["_key"] = pool["player"].astype(str).str.strip().str.lower()
        lookup = proj.set_index("_key")["projected_points"].to_dict()
        pool["projected_points"] = pool["_key"].map(lookup)
        pool["projected_points"] = pd.to_numeric(pool["projected_points"], errors="coerce")
        pool = pool.drop(columns=["_key"])

    priced = valuation.price_pool(
        pool,
        remaining_budget=inflation["remaining_budget"],
        inflation_multiplier=inflation["inflation_multiplier"],
        blend_weight=0.6 if projections is not None else 0.0,
    )

    available = {}
    for _, row in priced.iterrows():
        if pd.isna(row["suggested_auction_price"]):
            continue
        key = data_pipeline._normalize_name(row["player"])
        available[key] = {
            "player": row["player"],
            "position": row["position"],
            "nfl_team": row.get("nfl_team", "") or "",
            "baseline_price": float(row["suggested_auction_price"]),
        }

    my_roster = []
    my_spent = 0.0
    kept = with_keepers[with_keepers["will_keep"]]
    for _, row in kept[kept["team"] == MY_TEAM].iterrows():
        price = float(row["keeper_price_2026"]) if pd.notna(row["keeper_price_2026"]) else 0.0
        my_roster.append({
            "player": row["player"],
            "position": row["position"],
            "price": price,
            "source": "keeper",
        })
        my_spent += price

    return {
        "my_team": MY_TEAM,
        "my_remaining_budget": round(config.BUDGET_PER_TEAM - my_spent, 2),
        "my_roster": my_roster,
        "available": available,
        "draft_log": [],
        "remaining_league_budget": round(inflation["remaining_budget"], 2),
        "remaining_baseline_value": round(
            sum(p["baseline_price"] for p in available.values()), 2
        ),
        "live_inflation_multiplier": 1.0,
    }


def _need_multiplier(position: str, my_roster: list[dict]) -> float:
    if position not in ("QB", "RB", "WR", "TE"):
        return 1.0

    have = sum(1 for p in my_roster if p["position"] == position)
    starter_slots = config.STARTING_LINEUP.get(position, 0)
    if have < starter_slots:
        return 1.0

    flex_slots_total = config.STARTING_LINEUP.get("FLEX", 0)
    flex_credit_target = flex_slots_total * config.FLEX_SHARE.get(position, 0.0)
    flex_filled = have - starter_slots
    if flex_filled < flex_credit_target:
        return 1.0

    bench_depth = flex_filled - flex_credit_target
    return max(NEED_FLOOR, 1.0 - NEED_TAPER_RATE * bench_depth)


def recompute(state: dict) -> dict:
    """Recompute live inflation + recommended_live + my_target_price for
    every player still available. Mutates and returns `state`."""
    available = state["available"]
    remaining_league_budget = state["remaining_league_budget"]
    remaining_baseline_value = sum(p["baseline_price"] for p in available.values())
    state["remaining_baseline_value"] = round(remaining_baseline_value, 2)

    if remaining_baseline_value > 0:
        live_mult = remaining_league_budget / remaining_baseline_value
    else:
        live_mult = 1.0
    state["live_inflation_multiplier"] = round(live_mult, 4)

    for entry in available.values():
        recommended_live = float(
            np.clip(entry["baseline_price"] * live_mult, config.MIN_PRICE, config.MAX_PRICE)
        )
        entry["recommended_live"] = round(recommended_live, 0)
        mult = _need_multiplier(entry["position"], state["my_roster"])
        entry["need_multiplier"] = round(mult, 3)
        entry["my_target_price"] = round(
            float(np.clip(recommended_live * mult, config.MIN_PRICE, config.MAX_PRICE)), 0
        )

    return state


def apply_pick(state: dict, player_key: str, price: float, is_me: bool) -> dict:
    entry = state["available"].pop(player_key, None)
    if entry is None:
        raise KeyError(f"Player key {player_key!r} not found in available pool.")

    state["remaining_league_budget"] = round(state["remaining_league_budget"] - price, 2)

    if is_me:
        state["my_roster"].append({
            "player": entry["player"],
            "position": entry["position"],
            "price": price,
            "source": "draft",
        })
        state["my_remaining_budget"] = round(state["my_remaining_budget"] - price, 2)

    state["draft_log"].append({
        "player": entry["player"],
        "position": entry["position"],
        "nfl_team": entry["nfl_team"],
        "price": price,
        "is_me": is_me,
        "recommended_at_time": entry.get("recommended_live", entry["baseline_price"]),
        "baseline_price": entry["baseline_price"],
        "_key": player_key,
        "_entry": entry,  # stashed for undo
    })

    recompute(state)
    return state


def undo_last(state: dict) -> dict:
    if not state["draft_log"]:
        return state
    last = state["draft_log"].pop()

    state["remaining_league_budget"] = round(state["remaining_league_budget"] + last["price"], 2)
    if last["is_me"]:
        for i, p in enumerate(state["my_roster"]):
            if p["player"] == last["player"] and p["source"] == "draft":
                del state["my_roster"][i]
                break
        state["my_remaining_budget"] = round(state["my_remaining_budget"] + last["price"], 2)

    entry = last["_entry"]
    entry.pop("recommended_live", None)
    entry.pop("my_target_price", None)
    entry.pop("need_multiplier", None)
    state["available"][last["_key"]] = entry

    recompute(state)
    return state
