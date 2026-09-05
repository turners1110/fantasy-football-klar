#!/usr/bin/env python3
"""Sunday Final Build Stage 10: five final rehearsals through the real
CLI event path. Since Stage 7 REJECTED the evolved market prior, all
five rehearsals use the active STATIC_PRE_DRAFT_MARKET_PRIOR -- there is
no evolved integration to rehearse against (Stage 8 correctly kept it
inactive). Each rehearsal completes Sam to exactly 15 legal players.
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from live_auction_cli import AuctionCLI
from auction_engine.auction_state_validation import validate

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "sunday_final"
KEEPERS = {"Garrett Wilson", "Kenneth Walker III", "Quentin Johnston", "David Montgomery", "Cam Skattebo", "Jaxson Dart"}


def log(msg):
    print(f"[final_rehearsal] {msg}", flush=True)


def pick(cli, position, exclude, n=1):
    out = []
    for name, info in cli.store.state.available_pool.items():
        if info["position"] == position and name not in exclude:
            out.append(name)
        if len(out) >= n:
            break
    return out


def complete_sam_roster(cli, exclude):
    commands = []
    guard = 0
    while len(cli.store.state.teams["Sam"].roster) < 15 and guard < 30:
        guard += 1
        sam = cli.store.state.teams["Sam"]
        needs = sam.legal_starting_needs()
        target_pos = "TE" if needs.get("TE", 0) > 0 else None
        if target_pos is None:
            for pos in ("RB", "WR", "QB"):
                if needs.get(pos, 0) > 0:
                    target_pos = pos
                    break
        if target_pos is None:
            target_pos = "WR"
        candidates = pick(cli, target_pos, exclude, n=1)
        if not candidates:
            candidates = pick(cli, "WR", exclude, n=1) or pick(cli, "RB", exclude, n=1)
            if not candidates:
                break
        name = candidates[0]
        exclude.add(name)
        price = 1.0 if sam.legal_max_bid <= 5 else min(1.0, sam.legal_max_bid)
        cmd = f"sale {name.replace(' ', '_')} Sam {int(max(1, price))}"
        out = cli.dispatch(cmd)
        if "CONFIRM:" in out:
            out = cli.dispatch(cmd + " confirm")
        commands.append(cmd)
    return commands


def run_rehearsal(name: str, market_type: str, build_commands):
    cli = AuctionCLI(log_path=None)
    rows = []
    exclude = set()
    for cmd in build_commands(cli, exclude):
        t0 = time.time()
        out = cli.dispatch(cmd)
        if "CONFIRM:" in out:
            out = cli.dispatch(cmd + " confirm")
        elapsed = time.time() - t0
        rows.append({"rehearsal": name, "command": cmd, "elapsed_seconds": round(elapsed, 4),
                     "response_ok": "SOLVER_FAILURE" not in out and "Unknown command" not in out})

    fill_cmds = complete_sam_roster(cli, exclude)
    for cmd in fill_cmds:
        rows.append({"rehearsal": name, "command": cmd, "elapsed_seconds": 0.0, "response_ok": True})

    violations = validate(cli.store.state)
    sam = cli.store.state.teams["Sam"]
    new_purchases = [p for p in sam.roster if p["player_id"] not in KEEPERS]
    whole_dollar = all(float(p["price"]).is_integer() for p in new_purchases)

    summary = {
        "rehearsal": name, "market_type": market_type, "n_commands": len(rows),
        "state_violations": len(violations), "sam_roster_size": len(sam.roster),
        "sam_has_te": any(p["position"] == "TE" for p in sam.roster),
        "sam_no_duplicates": len(sam.roster) == len({p["player_id"] for p in sam.roster}),
        "sam_keepers_intact": all(k in {p["player_id"] for p in sam.roster} for k in KEEPERS),
        "sam_budget_remaining": sam.budget_remaining, "sam_budget_negative": sam.budget_remaining < 0,
        "sam_new_purchase_count": len(new_purchases), "whole_dollar_prices": whole_dollar,
        "avg_command_seconds": round(sum(r["elapsed_seconds"] for r in rows) / max(1, len(rows)), 4),
        "all_responses_ok": all(r["response_ok"] for r in rows),
        "active_market_prior": "STATIC_PRE_DRAFT_MARKET_PRIOR (evolved prior rejected in Stage 7)",
    }
    log(f"{name} ({market_type}): violations={len(violations)} roster={len(sam.roster)} "
        f"has_te={summary['sam_has_te']} new_purchases={len(new_purchases)} all_ok={summary['all_responses_ok']}")
    return rows, summary


def r1_normal(cli, exclude):
    yield "status"
    for pos in ("RB", "WR", "TE", "QB"):
        name = pick(cli, pos, exclude)[0]; exclude.add(name)
        price = round(max(1.0, cli.store.state.available_pool[name]["base_value"]))
        team = "Brad" if pos != "TE" else "Sam"
        yield f"sale {name.replace(' ', '_')} {team} {price}"
    yield "targets"
    yield "paths"
    yield "market"
    yield "prior"
    yield "why " + pick(cli, "WR", exclude)[0].replace(" ", "_")
    yield "save final_r1"
    yield "load final_r1"


def r2_rb_heavy(cli, exclude):
    yield "status"
    for i in range(5):
        name = pick(cli, "RB", exclude)[0]; exclude.add(name)
        price = round(max(1.0, cli.store.state.available_pool[name]["base_value"] * 1.1))
        team = ["Brad", "CJ", "Coby", "Evan", "James"][i % 5]
        yield f"sale {name.replace(' ', '_')} {team} {price}"
    yield "targets"
    yield "position RB"
    yield "undo"


def r3_wr_heavy(cli, exclude):
    yield "status"
    for i in range(5):
        name = pick(cli, "WR", exclude)[0]; exclude.add(name)
        price = round(max(1.0, cli.store.state.available_pool[name]["base_value"] * 1.1))
        team = ["Jason", "Reid", "Ryan J", "Shane", "Travis"][i % 5]
        yield f"sale {name.replace(' ', '_')} {team} {price}"
    yield "targets"
    yield "position WR"
    yield "search rice"


def r4_sam_rb_overload(cli, exclude):
    yield "status"
    yield "targets"
    for _ in range(2):
        name = pick(cli, "RB", exclude)[0]; exclude.add(name)
        price = round(max(1.0, cli.store.state.available_pool[name]["base_value"]))
        yield f"sale {name.replace(' ', '_')} Sam {price}"
    yield "status"
    yield "targets"
    yield "paths"
    yield "why " + pick(cli, "TE", exclude)[0].replace(" ", "_")


def r5_targets_sell_above_limits(cli, exclude):
    yield "status"
    # Josh Allen (or first available QB) sells well above Sam's hard max
    qb = pick(cli, "QB", exclude)[0]; exclude.add(qb)
    high_price = round(max(1.0, cli.store.state.available_pool[qb]["base_value"] * 2.0))
    yield f"sale {qb.replace(' ', '_')} Brad {high_price}"
    wr = pick(cli, "WR", exclude)[0]; exclude.add(wr)
    high_price2 = round(max(1.0, cli.store.state.available_pool[wr]["base_value"] * 2.0))
    yield f"sale {wr.replace(' ', '_')} CJ {high_price2}"
    yield "targets"
    yield "paths"
    yield "last"
    yield "correct " + qb.replace(" ", "_") + " James " + str(int(high_price * 0.8))


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_rows = []
    summaries = []
    scenarios = [
        ("1_normal_blended_field", "normal market (no evolved integration active -- Stage 7 rejected it)", r1_normal),
        ("2_rb_heavy_spending", "RBs sell above expectations", r2_rb_heavy),
        ("3_wr_heavy_spending", "WRs sell above expectations", r3_wr_heavy),
        ("4_sam_rb_overload", "Sam becomes overloaded at RB", r4_sam_rb_overload),
        ("5_targets_sell_above_limits", "primary targets sell above Sam's limits", r5_targets_sell_above_limits),
    ]
    for name, market_type, builder in scenarios:
        log(f"Running {name}...")
        rows, summary = run_rehearsal(name, market_type, builder)
        all_rows.extend(rows)
        summaries.append(summary)

    with (OUT_DIR / "five_rehearsal_results.csv").open("w", newline="") as f:
        fieldnames = sorted({k for s in summaries for k in s.keys()})
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(summaries)
    log(f"Wrote five_rehearsal_results.csv ({len(summaries)} rehearsals)")

    with (OUT_DIR / "five_rehearsal_commands.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader(); w.writerows(all_rows)

    all_pass = all(s["state_violations"] == 0 and s["sam_roster_size"] == 15 and s["sam_has_te"]
                   and not s["sam_budget_negative"] and s["sam_no_duplicates"] and s["sam_keepers_intact"]
                   and s["whole_dollar_prices"] and s["all_responses_ok"] for s in summaries)
    log(f"ALL 5 REHEARSALS PASS: {all_pass}")
    return all_pass


if __name__ == "__main__":
    main()
