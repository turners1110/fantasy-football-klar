#!/usr/bin/env python3
"""Phase 3D items 2-3: build the required exact-vs-greedy replacement
comparison outputs, and the player-level old-vs-new value comparison
after adopting EXACT_LEAGUEWIDE_ALLOCATION in production.

Writes:
  outputs/auction_rebuild/phase3d/exact_replacement_allocation.csv
  outputs/auction_rebuild/phase3d/greedy_exact_replacement_comparison.csv
  outputs/auction_rebuild/phase3d/player_value_method_comparison.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

from auction_model import config as auction_cfg
from auction_model import replacement_methods
from auction_model.exact_leaguewide_allocation import solve_exact_leaguewide_allocation
from mock_draft.data import load_confirmed_pool_and_teams

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3d"


def main() -> None:
    # Load under FIXED_RANK_LEGACY first to get the untouched OLD base_value.
    orig_method = auction_cfg.REPLACEMENT_METHOD
    auction_cfg.REPLACEMENT_METHOD = auction_cfg.FIXED_RANK_LEGACY
    players_legacy, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    auction_cfg.REPLACEMENT_METHOD = orig_method  # restored to EXACT_LEAGUEWIDE_ALLOCATION (production default)
    players_exact, _teams2, _ = load_confirmed_pool_and_teams(budget_scenario="primary")

    pool_points = {p.name: (p.position, p.projected_points) for p in players_legacy.values()}
    team_keepers = {name: [(n, p, pts) for n, p, pr, pts in t.roster] for name, t in teams.items()}

    exact_result = solve_exact_leaguewide_allocation(pool_points, team_keepers)
    greedy_result = replacement_methods.greedy_leaguewide_selection(pool_points, team_keepers)
    greedy_replacement = greedy_result["replacement"]
    greedy_selected = greedy_result["selected_players"]

    # --- exact_replacement_allocation.csv: the exact solve's own assignment table ---
    exact_path = OUT_DIR / "exact_replacement_allocation.csv"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    exact_result.assignments.to_csv(exact_path, index=False)
    print(f"Wrote {exact_path} ({len(exact_result.assignments)} rostered assignments, "
          f"status={exact_result.status}, runtime={exact_result.runtime_seconds}s)")

    # --- greedy_exact_replacement_comparison.csv ---
    # exact_result.assignments includes BOTH keepers (fixed, not a method
    # choice) and pool fills; greedy_selected is pool fills only. Restrict
    # the exact side to non-keeper pool assignments so this is a like-for-
    # like comparison of what each METHOD chose, not "exact roster incl.
    # keepers" vs "greedy pool fill only" (which would spuriously inflate
    # players_only_in_exact by the full keeper count).
    all_keeper_names = {name for keepers in team_keepers.values() for name, _pos, _pts in keepers}
    exact_selected_all = set(exact_result.assignments["player"]) if len(exact_result.assignments) else set()
    exact_selected = exact_selected_all - all_keeper_names
    only_greedy = greedy_selected - exact_selected
    only_exact = exact_selected - greedy_selected
    both = greedy_selected & exact_selected

    comparison_rows = [{
        "method": "GREEDY_LEAGUEWIDE_ALLOCATION",
        "replacement_QB": round(greedy_replacement.get("QB", 0.0), 2),
        "replacement_RB": round(greedy_replacement.get("RB", 0.0), 2),
        "replacement_WR": round(greedy_replacement.get("WR", 0.0), 2),
        "replacement_TE": round(greedy_replacement.get("TE", 0.0), 2),
        "n_selected": len(greedy_selected), "runtime_seconds": None,
    }, {
        "method": "EXACT_LEAGUEWIDE_ALLOCATION",
        "replacement_QB": round(exact_result.replacement_by_position.get("QB", {}).get("points") or 0.0, 2),
        "replacement_RB": round(exact_result.replacement_by_position.get("RB", {}).get("points") or 0.0, 2),
        "replacement_WR": round(exact_result.replacement_by_position.get("WR", {}).get("points") or 0.0, 2),
        "replacement_TE": round(exact_result.replacement_by_position.get("TE", {}).get("points") or 0.0, 2),
        "n_selected": len(exact_selected), "runtime_seconds": exact_result.runtime_seconds,
        # n_selected here is pool-fill assignments only (excludes keepers),
        # matching greedy's n_selected for a fair side-by-side.
    }, {
        "method": "COMPARISON_SUMMARY",
        "replacement_QB": None, "replacement_RB": None, "replacement_WR": None, "replacement_TE": None,
        "n_selected": None, "runtime_seconds": None,
        "players_only_in_greedy": len(only_greedy), "players_only_in_exact": len(only_exact),
        "players_in_both": len(both),
        "greedy_objective_approx": None,
        "exact_objective_value": exact_result.objective_value,
    }]

    comp_path = OUT_DIR / "greedy_exact_replacement_comparison.csv"
    fieldnames = ["method", "replacement_QB", "replacement_RB", "replacement_WR", "replacement_TE",
                  "n_selected", "runtime_seconds", "players_only_in_greedy", "players_only_in_exact",
                  "players_in_both", "greedy_objective_approx", "exact_objective_value"]
    with comp_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in comparison_rows:
            w.writerow({k: r.get(k) for k in fieldnames})
    print(f"Wrote {comp_path}: {len(only_greedy)} players only in greedy, {len(only_exact)} only in exact, "
          f"{len(both)} in both")

    # --- player_value_method_comparison.csv: old (legacy) vs new (exact) base_value ---
    rows = []
    for name, p_new in players_exact.items():
        p_old = players_legacy.get(name)
        rows.append({
            "player": name, "position": p_new.position,
            "old_base_value_FIXED_RANK_LEGACY": p_old.base_value if p_old else None,
            "new_base_value_EXACT_LEAGUEWIDE_ALLOCATION": p_new.base_value,
            "delta": round(p_new.base_value - (p_old.base_value if p_old else 0.0), 2),
        })
    pv_path = OUT_DIR / "player_value_method_comparison.csv"
    pd.DataFrame(rows).sort_values("delta", ascending=False).to_csv(pv_path, index=False)
    print(f"Wrote {pv_path} ({len(rows)} players)")


if __name__ == "__main__":
    main()
