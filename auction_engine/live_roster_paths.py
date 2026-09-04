"""Live MVP Part 5: complete-roster paths, rebuilt from the CURRENT
AuctionState (Sam's actual current roster/budget/available pool) rather
than the frozen pre-draft pool. Reuses auction_model.exact_roster_solver
(HiGHS-backed, OPTIMAL-only) exactly as Phase 3G did -- no new solver.
"""

from __future__ import annotations

import math

import pandas as pd

from auction_model import exact_roster_solver

PATH_STYLES = ("HIGHEST_PROJECTED_POINTS", "CONSERVATIVE_PRICES", "BALANCED_ROSTER", "PREMIUM_WR", "VALUE_AND_DEPTH")


def _keepers_df(roster: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {"player": p["player_id"], "position": p["position"], "projected_points": p.get("projected_points", 0.0),
         "keeper_price_2026": p["price"]}
        for p in roster
    ])


def compute_live_roster_paths(
    sam_team, available_players: dict[str, dict], hard_maxes: dict[str, float] | None = None,
) -> dict[str, dict]:
    """sam_team: auction_engine.auction_state.TeamState for Sam.
    available_players: {player_id: {"display_name","position","projected_points","expected_price","conservative_price"}}
    hard_maxes: optional {player_id: safety_adjusted_hard_maximum} -- prices are capped at this before solving,
    same structural enforcement pattern as Phase 3G (never checked only after the solve).
    Returns {style: {"status", "players": [...], "starting_points", "spend", "unused_cash"}}.
    """
    hard_maxes = hard_maxes or {}
    n_auction_spots = max(0, 15 - len(sam_team.roster))
    keepers_df = _keepers_df(sam_team.roster)
    results = {}

    def build_pool(price_key: str, markup: float, exclude: set, position_bias: dict | None = None):
        rows = []
        for pid, info in available_players.items():
            if pid in exclude:
                continue
            base = info.get(price_key) or info.get("expected_price") or 1.0
            price = math.ceil(base * markup)
            if position_bias:
                price = math.ceil(price * position_bias.get(info["position"], 1.0))
            if pid in hard_maxes and hard_maxes[pid] is not None:
                price = min(price, hard_maxes[pid])
            rows.append({"player": pid, "position": info["position"], "projected_points": info["projected_points"],
                         "suggested_auction_price": float(max(1, price))})
        return pd.DataFrame(rows)

    style_configs = {
        "HIGHEST_PROJECTED_POINTS": ("expected_price", 1.0, set(), None),
        "CONSERVATIVE_PRICES": ("conservative_price", 1.0, set(), None),
        "BALANCED_ROSTER": ("expected_price", 1.05, set(), None),
        "PREMIUM_WR": ("expected_price", 1.0, set(), {"WR": 1.0, "RB": 1.15, "TE": 1.1, "QB": 1.1}),
        "VALUE_AND_DEPTH": ("expected_price", 0.95, set(), None),
    }

    for style, (price_key, markup, exclude, bias) in style_configs.items():
        pool_df = build_pool(price_key, markup, exclude, bias)
        if pool_df.empty or n_auction_spots == 0:
            results[style] = {"status": "NO_OPEN_SLOTS" if n_auction_spots == 0 else "EMPTY_POOL", "players": [],
                              "starting_points": None, "spend": 0, "unused_cash": sam_team.budget_remaining}
            continue
        result = exact_roster_solver.solve_exact_roster(
            pool_df, budget=sam_team.budget_remaining, n_auction_spots=n_auction_spots, keepers=keepers_df,
        )
        if result.status not in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL"):
            results[style] = {"status": result.status, "players": [], "starting_points": None,
                              "spend": 0, "unused_cash": sam_team.budget_remaining}
            continue
        new_picks = result.selected[~result.selected["player"].isin({p["player_id"] for p in sam_team.roster})]
        results[style] = {
            "status": result.status,
            "players": [{"player": r["player"], "position": r["position"], "price": int(round(r["price"]))}
                        for _, r in new_picks.iterrows()],
            "starting_points": result.starting_points, "bench_points": result.bench_points,
            "spend": int(round(new_picks["price"].sum())), "unused_cash": result.unused_cash,
        }
    return results
