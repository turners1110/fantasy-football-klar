"""Live MVP Part 2: dynamic, roster-aware Sam marginal-value engine.

FAST PATH (this module): a pure-Python greedy lineup optimizer (no MIP
solver) that computes Sam's best legal starting lineup from whatever
players are actually on his roster right now, with vs. without a
candidate. This is NOT a fixed position multiplier -- the position
effect emerges entirely from lineup competition: once Sam's RB slots and
FLEX are already claimed by better RBs, an additional RB's marginal
value collapses to the bench weight because he has nowhere left to
start. This is deliberately a fast APPROXIMATION (greedy, not globally
exact) -- see live_ceilings.py for the exact HiGHS-backed shortlist
refresh required by the spec for the nominated player / top targets /
tier-cliff candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BENCH_WEIGHT = 0.15  # matches auction_model.config.BENCH_POINT_WEIGHT
STARTER_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
FLEX_SLOTS = 3
FLEX_ELIGIBLE = {"RB", "WR", "TE"}
BENCH_SIZE = 6


def greedy_best_lineup(players: list[dict]) -> tuple[float, float, dict]:
    """players: list of {"player_id"/"name", "position", "projected_points"}.
    Returns (starting_points, bench_points, role_by_player_id)."""
    by_pos: dict[str, list[dict]] = {"QB": [], "RB": [], "WR": [], "TE": []}
    for p in players:
        pos = p["position"]
        if pos in by_pos:
            by_pos[pos].append(p)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda p: -p["projected_points"])

    roles: dict[str, str] = {}
    start_pts = 0.0
    leftover: list[tuple[dict, str]] = []  # (player, position) not used for a required slot

    for pos, n_required in STARTER_SLOTS.items():
        pool = by_pos.get(pos, [])
        for i, p in enumerate(pool):
            key = p.get("player_id", p.get("name"))
            if i < n_required:
                roles[key] = f"{pos}_START_{i+1}"
                start_pts += p["projected_points"]
            else:
                leftover.append((p, pos))

    # FLEX: fill from leftover RB/WR/TE by points descending
    flex_candidates = sorted(
        [(p, pos) for p, pos in leftover if pos in FLEX_ELIGIBLE],
        key=lambda t: -t[0]["projected_points"],
    )
    flex_filled = 0
    flex_ids = set()
    for p, pos in flex_candidates:
        if flex_filled >= FLEX_SLOTS:
            break
        key = p.get("player_id", p.get("name"))
        roles[key] = f"FLEX_{flex_filled+1}"
        start_pts += p["projected_points"]
        flex_ids.add(key)
        flex_filled += 1

    bench_pts = 0.0
    for p, pos in leftover:
        key = p.get("player_id", p.get("name"))
        if key in flex_ids:
            continue
        roles[key] = "BENCH"
        bench_pts += p["projected_points"]

    return round(start_pts, 3), round(bench_pts, 3), roles


@dataclass
class LiveValueRow:
    player: str
    position: str
    projected_points: float
    marginal_starting_points: float
    marginal_bench_points: float
    marginal_value: float  # starting + BENCH_WEIGHT*bench
    expected_role: str
    displaced_player: str | None
    calculation_method: str = "APPROXIMATE_LIVE_ROSTER_VALUE"


def compute_live_sam_values(sam_roster: list[dict], remaining_players: dict[str, dict],
                             bench_weight: float = BENCH_WEIGHT) -> list[LiveValueRow]:
    """sam_roster: Sam's CURRENT roster (list of {player_id/name, position,
    projected_points}) -- open slots simply contribute nothing, per the
    spec's own comparison definition ("current best projected starting
    lineup ... vs best ... after adding the player").
    remaining_players: {player_id: {"display_name", "position", "projected_points"}}
    """
    base_start, base_bench, base_roles = greedy_best_lineup(sam_roster)
    base_value = base_start + bench_weight * base_bench

    rows = []
    for pid, info in remaining_players.items():
        candidate = {"player_id": pid, "position": info["position"], "projected_points": info["projected_points"]}
        with_candidate = sam_roster + [candidate]
        start_w, bench_w, roles_w = greedy_best_lineup(with_candidate)
        value_w = start_w + bench_weight * bench_w

        role = roles_w.get(pid, "BENCH")
        if role.startswith("BENCH"):
            expected_role = "bench depth"
        elif role.startswith("FLEX"):
            expected_role = "FLEX starter"
        else:
            expected_role = "required starter"

        # displaced player: someone who was a starter/flex in base_roles but
        # is bench (or gone) in roles_w
        displaced = None
        base_starters = {k for k, v in base_roles.items() if not v.startswith("BENCH")}
        new_starters = {k for k, v in roles_w.items() if not v.startswith("BENCH") and k != pid}
        pushed_out = base_starters - new_starters
        if pushed_out:
            displaced = sorted(pushed_out)[0]

        rows.append(LiveValueRow(
            player=info.get("display_name", pid), position=info["position"],
            projected_points=info["projected_points"],
            marginal_starting_points=round(start_w - base_start, 3),
            marginal_bench_points=round(bench_w - base_bench, 3),
            marginal_value=round(value_w - base_value, 3),
            expected_role=expected_role, displaced_player=displaced,
        ))
    return rows
