#!/usr/bin/env python3
"""Phase 3D item 16: Sam's exact preliminary bid board -- exact ILP
counterfactual solves (auction_model.exact_roster_solver) at each
actionable player's P25/P50/P75/P90 simulated price, using Sam's real
confirmed state.

Sam's real confirmed context (restated verbatim, must never drift):
  Keepers: Garrett Wilson $31 WR, Kenneth Walker III $36 RB,
           Quentin Johnston $11 WR, David Montgomery $45 RB,
           Cam Skattebo $28 RB, Jaxson Dart $11 QB.
  Primary auction budget: $223. Conversion scenario: $221.
  Current structural need: at least one TE.
  Primary improvement areas: WR, TE, FLEX.

ACTIONABLE PLAYER SCOPE (disclosed, matches item 14's own coverage list):
  every player with a simulated P50 >= $20, plus the top-20 WR, top-15 RB,
  top-15 TE, and top-10 QB by projected points among the live pool. The
  "without" (Sam passes) scenario does not depend on price, so it is
  solved ONCE per candidate, not once per price point -- a 4x reduction
  in solver calls with no loss of correctness.

Requires outputs/auction_rebuild/phase3d/price_distributions.csv (item 15)
to already exist.

Writes:
  outputs/auction_rebuild/phase3d/sam_preliminary_bid_board.csv
  (label: PRELIMINARY_NOT_FINAL)
"""

from __future__ import annotations

import multiprocessing as mp
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from auction_model import exact_roster_solver
from mock_draft.data import load_confirmed_pool_and_teams

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3d"
DIST_PATH = OUT_DIR / "price_distributions.csv"

SAM_CONFIRMED_KEEPERS = {
    "Garrett Wilson": 31, "Kenneth Walker III": 36, "Quentin Johnston": 11,
    "David Montgomery": 45, "Cam Skattebo": 28, "Jaxson Dart": 11,
}
SAM_PRIMARY_BUDGET = 223
SAM_CONVERSION_BUDGET = 221


def _pool_to_exact_df(pool: dict, exclude_names: set) -> pd.DataFrame:
    return pd.DataFrame([
        {"player": p.name, "position": p.position, "projected_points": p.projected_points,
         "suggested_auction_price": max(1.0, p.base_value)}
        for name, p in pool.items() if name not in exclude_names
    ])


def _keepers_to_exact_df(roster: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame([
        {"player": name, "position": pos, "projected_points": pts, "keeper_price_2026": price}
        for name, pos, price, pts in roster
    ])


def _evaluate_player(args):
    name, position, prices, players_dict, sam_roster, sam_budget = args
    pool_minus = {n: p for n, p in players_dict.items() if n != name}
    exact_df = _pool_to_exact_df(pool_minus, {name})
    slots_after = max(0, 15 - len(sam_roster))

    # "Without" scenario -- price-independent, solved once.
    exact_df_pass = _pool_to_exact_df(players_dict, {name})
    result_without = exact_roster_solver.solve_exact_roster(
        exact_df_pass, budget=sam_budget, n_auction_spots=slots_after,
        keepers=_keepers_to_exact_df(sam_roster),
    )

    row_results = []
    for pct_name, price in prices.items():
        if price is None:
            row_results.append({"percentile": pct_name, "price": None, "surplus": None, "status": "NO_PRICE"})
            continue
        candidate = players_dict[name]
        roster_with = sam_roster + [(name, position, price, candidate.projected_points)]
        result_with = exact_roster_solver.solve_exact_roster(
            exact_df, budget=max(0.0, sam_budget - price),
            n_auction_spots=max(0, slots_after - 1), keepers=_keepers_to_exact_df(roster_with),
        )
        surplus = (
            result_with.starting_points - result_without.starting_points
            if result_with.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL")
            and result_without.status in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL")
            else None
        )
        row_results.append({
            "percentile": pct_name, "price": price, "surplus": round(surplus, 2) if surplus is not None else None,
            "status": result_with.status,
        })
    return name, position, row_results, result_without.status


def main() -> None:
    if not DIST_PATH.exists():
        print(f"MISSING {DIST_PATH} -- run build_phase3d_price_distributions.py first.")
        sys.exit(1)

    t0 = time.time()
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    sam = teams["Sam"]

    dist = pd.read_csv(DIST_PATH)
    dist_live = dist[dist["player"].isin(players.keys())].copy()
    dist_live["p50_numeric"] = pd.to_numeric(dist_live["p50"], errors="coerce")

    def _numeric_or_none(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    p50_ge_20 = set(dist_live[dist_live["p50_numeric"].fillna(-1) >= 20]["player"])
    by_pos_top = set()
    for pos, n in (("WR", 20), ("RB", 15), ("TE", 15), ("QB", 10)):
        top = sorted((p for p in players.values() if p.position == pos), key=lambda p: p.projected_points, reverse=True)[:n]
        by_pos_top.update(p.name for p in top)

    actionable = (p50_ge_20 | by_pos_top) - set(SAM_CONFIRMED_KEEPERS.keys())
    print(f"Actionable players: {len(actionable)} (P50>=$20: {len(p50_ge_20)}, position-top lists: {len(by_pos_top)})")

    dist_lookup = dist_live.set_index("player")
    work = []
    for name in actionable:
        if name not in players:
            continue
        row = dist_lookup.loc[name] if name in dist_lookup.index else None
        prices = {}
        for pct in ("p25", "p50", "p75", "p90"):
            val = _numeric_or_none(row[pct]) if row is not None else None
            prices[pct] = val
        if all(v is None for v in prices.values()):
            continue
        work.append((name, players[name].position, prices, players, sam.roster, sam.budget_remaining))

    print(f"Running exact ILP solves for {len(work)} actionable players (parallelized)...")
    with mp.Pool(processes=min(4, mp.cpu_count())) as pool:
        results = pool.map(_evaluate_player, work)

    rows = []
    for name, position, pct_results, without_status in results:
        row = {
            "player": name, "position": position,
            "sam_primary_budget": SAM_PRIMARY_BUDGET, "sam_conversion_budget": SAM_CONVERSION_BUDGET,
            "solver_status_if_pass": without_status,
            "label": "PRELIMINARY_NOT_FINAL",
        }
        for pr in pct_results:
            row[f"{pr['percentile']}_price"] = pr["price"]
            row[f"{pr['percentile']}_exact_surplus"] = pr["surplus"]
            row[f"{pr['percentile']}_solver_status"] = pr["status"]
        rows.append(row)

    df = pd.DataFrame(rows)
    if "p50_exact_surplus" in df.columns:
        df = df.sort_values("p50_exact_surplus", ascending=False, na_position="last")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "sam_preliminary_bid_board.csv"
    df.to_csv(out_path, index=False)
    print(f"Wrote {out_path} ({len(df)} players) in {time.time() - t0:.1f}s -- label: PRELIMINARY_NOT_FINAL")
    print("Top 10 by P50 exact surplus:")
    for _, r in df.head(10).iterrows():
        print(f"  {r['player']:20s} ({r['position']}) P50=${r.get('p50_price')} surplus={r.get('p50_exact_surplus')}")


if __name__ == "__main__":
    main()
