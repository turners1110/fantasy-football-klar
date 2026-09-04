#!/usr/bin/env python3
"""Live MVP Part 8, Rehearsal 3: Sam wins two additional RBs early.
Runs through the REAL event path (auction_engine reducer/store) with the
real Phase 3G player pool, then verifies the RB-overload behavior end to
end (not just in isolated unit tests): remaining RB marginal values fall,
WR/TE relative values rise, complete roster paths adjust, undo/replay
still work correctly on top of a real, larger auction state.

Writes outputs/auction_rebuild/live_mvp/rehearsal_results.csv (appends
this rehearsal's row) and prints a full pass/fail summary.
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from auction_engine.auction_state import AuctionState, TeamState
from auction_engine.auction_state_store import AuctionStateStore
from auction_engine.auction_state_validation import validate
from auction_engine.live_values import compute_live_sam_values
from auction_engine.live_roster_paths import compute_live_roster_paths
from mock_draft.data import load_confirmed_pool_and_teams

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "live_mvp"


def log(msg):
    print(f"[rehearsal3] {msg}", flush=True)


def build_initial_state():
    players, teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    st = AuctionState(auction_id="rehearsal-3", rules_version="v1", model_version="live-mvp-v1", sam_team_id="Sam")
    for team_id, t in teams.items():
        roster = [{"player_id": n, "display_name": n, "position": p, "price": pr, "is_keeper": True, "projected_points": pts}
                  for n, p, pr, pts in t.roster]
        keeper_ids = {n for n, p, pr, pts in t.roster}
        st.teams[team_id] = TeamState(team_id=team_id, budget_remaining=t.budget_remaining, roster=roster, keeper_ids=keeper_ids)
    st.available_pool = {name: {"display_name": name, "position": p.position, "projected_points": p.projected_points,
                                 "base_value": p.base_value}
                          for name, p in players.items()}
    st.college_rights_excluded = {"Fernando Mendoza", "Isaiah Bond"}
    return st, players


def main():
    t_start = time.time()
    log("Building initial state from real Phase 3G pool...")
    initial_state, players = build_initial_state()
    store = AuctionStateStore(initial_state, log_path=Path("/tmp/rehearsal3_events.jsonl") if False else None)

    violations = validate(store.state)
    assert not violations, f"initial state illegal: {violations}"
    log(f"Initial state valid. Sam budget=${store.state.teams['Sam'].budget_remaining}")

    # Pick 2 real available RBs Sam will "win" early.
    rb_candidates = [n for n, p in players.items() if p.position == "RB" and n in store.state.available_pool][:2]
    other_sales = [n for n, p in players.items() if p.position in ("WR", "QB") and n not in rb_candidates][:6]

    def sam_remaining_pool():
        return {n: v for n, v in store.state.available_pool.items() if n != "" }

    def live_values_for_rb_and_wr():
        remaining = {n: v for n, v in store.state.available_pool.items()
                     if v["position"] in ("RB", "WR", "TE", "QB")}
        rows = compute_live_sam_values(store.state.teams["Sam"].roster, remaining)
        by_player = {r.player: r for r in rows}
        return by_player

    log("Recording BEFORE-overload live values snapshot...")
    before_rows = live_values_for_rb_and_wr()
    avg_rb_before = sum(r.marginal_value for r in before_rows.values() if r.position == "RB") / max(1, sum(1 for r in before_rows.values() if r.position == "RB"))
    avg_wr_before = sum(r.marginal_value for r in before_rows.values() if r.position == "WR") / max(1, sum(1 for r in before_rows.values() if r.position == "WR"))

    t0 = time.time()
    for name in rb_candidates:
        p = players[name]
        price = max(1.0, min(round(p.base_value), store.state.teams["Sam"].legal_max_bid))
        store.record("PLAYER_SOLD", {
            "player_id": name, "display_name": name, "position": p.position,
            "winning_owner": "Sam", "sale_price": float(price), "nominating_owner": "Brad",
            "projected_points": p.projected_points,
        })
        log(f"  Sam wins {name} (RB) at ${price}")
    for i, name in enumerate(other_sales):
        p = players[name]
        rival = list(store.state.teams.keys())[i % len(store.state.teams)]
        if rival == "Sam":
            rival = list(store.state.teams.keys())[(i + 1) % len(store.state.teams)]
        price = max(1.0, round(p.base_value * 0.5))
        store.record("PLAYER_SOLD", {
            "player_id": name, "display_name": name, "position": p.position,
            "winning_owner": rival, "sale_price": float(price), "nominating_owner": "Sam",
            "projected_points": p.projected_points,
        })
    t_sales = time.time() - t0
    log(f"Recorded {2 + len(other_sales)} sales through the real event path in {t_sales:.3f}s")

    violations = validate(store.state)
    assert not violations, f"state illegal after sales: {violations}"
    log("State remains legal after all sales.")

    log("Recording AFTER-overload live values snapshot...")
    after_rows = live_values_for_rb_and_wr()
    # Isolate the roster-composition effect from the confound of OTHER
    # unrelated sales shrinking the pool: compare only players present in
    # BOTH snapshots (identical identity set before and after).
    common_players = set(before_rows) & set(after_rows)
    common_rb = [p for p in common_players if before_rows[p].position == "RB"]
    common_wr = [p for p in common_players if before_rows[p].position == "WR"]
    avg_rb_before = sum(before_rows[p].marginal_value for p in common_rb) / max(1, len(common_rb))
    avg_rb_after = sum(after_rows[p].marginal_value for p in common_rb) / max(1, len(common_rb))
    avg_wr_before = sum(before_rows[p].marginal_value for p in common_wr) / max(1, len(common_wr))
    avg_wr_after = sum(after_rows[p].marginal_value for p in common_wr) / max(1, len(common_wr))

    rb_fell = avg_rb_after < avg_rb_before
    wr_rose_relative = (avg_wr_after - avg_rb_after) > (avg_wr_before - avg_rb_before)
    log(f"avg RB marginal value: before={avg_rb_before:.2f} after={avg_rb_after:.2f} (fell={rb_fell})")
    log(f"avg WR marginal value: before={avg_wr_before:.2f} after={avg_wr_after:.2f} (rose relative to RB={wr_rose_relative})")

    # roster paths still feasible after overload
    t1 = time.time()
    remaining_for_paths = {n: {"display_name": n, "position": v["position"], "projected_points": v["projected_points"],
                                "expected_price": max(1.0, v["base_value"]), "conservative_price": max(1.0, v["base_value"] * 1.15)}
                            for n, v in store.state.available_pool.items()}
    paths = compute_live_roster_paths(store.state.teams["Sam"], remaining_for_paths)
    t_paths = time.time() - t1
    paths_ok = all(r["status"] in ("OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL", "NO_OPEN_SLOTS") for r in paths.values())
    log(f"Roster paths recomputed after overload in {t_paths:.3f}s, all feasible/no-op: {paths_ok}")

    # undo works on top of a real, larger state
    pre_undo_seq = store.state.sequence_number
    store.undo_last()
    undo_ok = store.state.sequence_number != pre_undo_seq
    violations_after_undo = validate(store.state)
    log(f"Undo works on real state: {undo_ok}, still legal: {not violations_after_undo}")

    # replay/recovery works
    replayed = store.replay_from_log()
    replay_ok = replayed.to_dict() == store.state.to_dict()
    log(f"Replay matches live state: {replay_ok}")

    total_time = time.time() - t_start

    row = {
        "rehearsal": "3_rb_overload", "market_type": "Sam wins two additional RBs early",
        "sales_recorded": 2 + len(other_sales), "sale_recording_seconds_total": round(t_sales, 3),
        "avg_rb_marginal_value_before": round(avg_rb_before, 2), "avg_rb_marginal_value_after": round(avg_rb_after, 2),
        "rb_value_fell": rb_fell,
        "avg_wr_marginal_value_before": round(avg_wr_before, 2), "avg_wr_marginal_value_after": round(avg_wr_after, 2),
        "wr_rose_relative_to_rb": wr_rose_relative,
        "roster_paths_recompute_seconds": round(t_paths, 3), "roster_paths_feasible": paths_ok,
        "state_legal_after_sales": not violations, "undo_works": undo_ok,
        "state_legal_after_undo": not violations_after_undo, "replay_matches_live_state": replay_ok,
        "total_rehearsal_seconds": round(total_time, 3),
        "overall_pass": rb_fell and wr_rose_relative and paths_ok and not violations and undo_ok and replay_ok,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "rehearsal_results.csv"
    file_exists = out_path.exists()
    with out_path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not file_exists:
            w.writeheader()
        w.writerow(row)

    log(f"REHEARSAL 3 OVERALL PASS: {row['overall_pass']}")
    log(f"Total time: {total_time:.2f}s")
    return row


if __name__ == "__main__":
    main()
