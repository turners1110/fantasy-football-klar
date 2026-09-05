"""V2.1 Part 6 -- minimal guided Practice Mode scenarios.

Builds a fresh, fully isolated `AuctionCLI` instance seeded for one of
three practice scenarios. Practice instances are ordinary AuctionCLI
objects with their own store/log/market-state/exact-cache -- they never
touch the production instance's state, event log, or files. Seeding
happens by mutating the freshly-built in-memory initial state BEFORE any
event is recorded (sequence_number stays 0), which is only safe here
because this module is practice-only and is never imported by any
production code path (live_auction_cli.py does not import this file).
"""
from __future__ import annotations

from pathlib import Path

SCENARIOS = ("normal", "rb_expensive", "sam_rb_overload")

PRACTICE_LOG_DIR = Path(__file__).parent.parent / "outputs" / "auction_rebuild" / "live_web_v21" / "practice"


def _practice_log_path() -> Path:
    return PRACTICE_LOG_DIR / "practice_session.jsonl"


def build_practice_cli(scenario: str):
    """Returns (cli, proof) where proof is a dict of before/after data for
    scenarios that must visibly prove something (RB overload); {} otherwise."""
    from live_auction_cli import AuctionCLI  # local import: avoid import cycle

    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown practice scenario: {scenario!r} (must be one of {SCENARIOS})")

    cli = AuctionCLI(log_path=_practice_log_path())
    proof: dict = {}

    if scenario == "normal":
        pass  # mixed field, unmodified -- the ordinary confirmed pool/teams

    elif scenario == "rb_expensive":
        # Synthetic, disclosed distortion: RB base_value inflated 1.4x
        # league-wide to simulate an unusually RB-hungry practice room.
        pool = cli.store.state.available_pool
        for name, info in pool.items():
            if info["position"] == "RB":
                info["base_value"] = round(info["base_value"] * 1.4, 2)

    elif scenario == "sam_rb_overload":
        from auction_engine.live_values import compute_live_sam_values

        sam = cli.store.state.teams["Sam"]
        pool = cli.store.state.available_pool

        def snapshot(pool_view):
            rb_pool = {n: v for n, v in pool_view.items() if v["position"] == "RB"}
            wr_pool = {n: v for n, v in pool_view.items() if v["position"] in ("WR", "TE")}
            rb_rows = compute_live_sam_values(sam.roster, rb_pool)
            wr_rows = compute_live_sam_values(sam.roster, wr_pool)
            rb_avg = sum(r.marginal_value for r in rb_rows) / len(rb_rows) if rb_rows else 0.0
            wr_avg = sum(r.marginal_value for r in wr_rows) / len(wr_rows) if wr_rows else 0.0
            return rb_avg, wr_avg

        before_rb_avg, before_wr_avg = snapshot(pool)

        # Load Sam up with 3 extra RBs at a discounted practice price,
        # simulating a roster that already overshot at the position,
        # while staying within Sam's real starting budget so the rest of
        # the practice draft remains legally playable.
        rb_candidates = sorted(
            (v for v in pool.values() if v["position"] == "RB"),
            key=lambda v: v["base_value"], reverse=True,
        )[:3]
        added = []
        for info in rb_candidates:
            price = round(min(info["base_value"] * 0.5, 35.0), 2)
            sam.roster.append({
                "player_id": info["display_name"], "display_name": info["display_name"],
                "position": "RB", "price": price, "is_keeper": False,
                "projected_points": info["projected_points"],
            })
            sam.budget_remaining -= price
            added.append(info["display_name"])
            del pool[info["display_name"]]

        after_rb_avg, after_wr_avg = snapshot(pool)

        proof = {
            "added_rbs": added,
            "rb_marginal_value_before": round(before_rb_avg, 2),
            "rb_marginal_value_after": round(after_rb_avg, 2),
            "wr_te_marginal_value_before": round(before_wr_avg, 2),
            "wr_te_marginal_value_after": round(after_wr_avg, 2),
            "rb_value_declined": after_rb_avg < before_rb_avg,
            "wr_te_relative_priority_rose": (after_wr_avg - after_rb_avg) > (before_wr_avg - before_rb_avg),
        }

    return cli, proof
