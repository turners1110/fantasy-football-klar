"""Legal-lineup-aware roster scoring -- built specifically to audit whether
the prior evolutionary result ("overweight QB") survives a fitness function
that respects this league's actual 1QB/2RB/2WR/1TE/3FLEX starting lineup,
instead of summing projected points across all 15 rostered players
(which credits bench QBs/RBs/etc. with a full season of points they can
never actually score in this lineup).

This is a MINIMAL implementation for the audit in
outputs/auction_rebuild/audit/ -- it selects the best legal starting
lineup and reports bench separately. It does not yet implement the full
weighted-bench / weekly-projection fitness function described in the
rebuild spec (section 7); that is a larger follow-on task.
"""

from __future__ import annotations

from dataclasses import dataclass

STARTING_LINEUP = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
FLEX_SLOTS = 3
FLEX_ELIGIBLE = ("RB", "WR", "TE")

# ASSUMPTION (ships as a config switch per the rebuild instructions --
# retune or replace with weekly-projection-based weights once real weekly
# data is wired in): bench discount weights for the season-approximation
# fallback fitness. These are FIRST-PASS guesses, not backtested.
BENCH_WEIGHTS = {
    "first_reserve_rb_wr": 0.30,
    "second_reserve_rb_wr": 0.15,
    "backup_qb": 0.075,
    "third_qb": 0.0,
    "backup_te": 0.10,
    "remaining_bench": 0.05,
}


@dataclass
class LineupResult:
    starting_lineup_points: float
    bench_option_value: float
    total_roster_utility: float
    starting_QB: str | None
    starting_RB: list
    starting_WR: list
    starting_TE: str | None
    starting_FLEX: list
    bench_QB_count: int
    bench_points_included: float
    roster_legality: str  # "LEGAL" or a reason string


def select_legal_lineup(roster: list[tuple[str, str, float, float]]) -> LineupResult:
    """roster: list of (player_name, position, price, projected_points).
    Selects the best legal 1QB/2RB/2WR/1TE/3FLEX lineup by projected
    points, then scores bench per BENCH_WEIGHTS. No player double-counted.
    """
    by_pos: dict[str, list[tuple[str, float]]] = {"QB": [], "RB": [], "WR": [], "TE": []}
    for name, pos, _price, pts in roster:
        if pos not in by_pos:
            return LineupResult(0, 0, 0, None, [], None, [], 0, 0, f"ILLEGAL_POSITION:{pos}")
        by_pos[pos].append((name, pts))

    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: x[1], reverse=True)

    def _illegal(reason: str) -> LineupResult:
        return LineupResult(
            starting_lineup_points=0, bench_option_value=0, total_roster_utility=0,
            starting_QB=None, starting_RB=[], starting_WR=[], starting_TE=None, starting_FLEX=[],
            bench_QB_count=len(by_pos["QB"]), bench_points_included=0, roster_legality=reason,
        )

    if len(by_pos["QB"]) < STARTING_LINEUP["QB"]:
        return _illegal("MISSING_QB")
    if len(by_pos["RB"]) < STARTING_LINEUP["RB"]:
        return _illegal("MISSING_RB")
    if len(by_pos["WR"]) < STARTING_LINEUP["WR"]:
        return _illegal("MISSING_WR")
    if len(by_pos["TE"]) < STARTING_LINEUP["TE"]:
        return _illegal("MISSING_TE")

    starting_qb = by_pos["QB"][0]
    starting_rb = by_pos["RB"][: STARTING_LINEUP["RB"]]
    starting_wr = by_pos["WR"][: STARTING_LINEUP["WR"]]
    starting_te = by_pos["TE"][: STARTING_LINEUP["TE"]]

    used_names = {starting_qb[0]} | {n for n, _ in starting_rb} | {n for n, _ in starting_wr} | {n for n, _ in starting_te}

    flex_pool = [
        (name, pts) for pos in FLEX_ELIGIBLE for name, pts in by_pos[pos] if name not in used_names
    ]
    flex_pool.sort(key=lambda x: x[1], reverse=True)
    if len(flex_pool) < FLEX_SLOTS:
        return _illegal("MISSING_FLEX_DEPTH")
    starting_flex = flex_pool[:FLEX_SLOTS]
    used_names |= {n for n, _ in starting_flex}

    starting_lineup_points = (
        starting_qb[1]
        + sum(p for _, p in starting_rb)
        + sum(p for _, p in starting_wr)
        + sum(p for _, p in starting_te)
        + sum(p for _, p in starting_flex)
    )

    # Bench scoring: rank remaining RB/WR by points for reserve tiers;
    # remaining QB gets backup/third-QB weight; remaining TE gets backup
    # weight; everything else gets the flat remaining-bench weight.
    bench = [(name, pos, pts) for name, pos, _price, pts in roster if name not in used_names]
    bench_rb_wr = sorted([b for b in bench if b[1] in ("RB", "WR")], key=lambda x: x[2], reverse=True)
    bench_qb = sorted([b for b in bench if b[1] == "QB"], key=lambda x: x[2], reverse=True)
    bench_te = sorted([b for b in bench if b[1] == "TE"], key=lambda x: x[2], reverse=True)

    bench_value = 0.0
    for i, (_n, _p, pts) in enumerate(bench_rb_wr):
        w = BENCH_WEIGHTS["first_reserve_rb_wr"] if i == 0 else (
            BENCH_WEIGHTS["second_reserve_rb_wr"] if i == 1 else BENCH_WEIGHTS["remaining_bench"]
        )
        bench_value += pts * w
    for i, (_n, _p, pts) in enumerate(bench_qb):
        w = BENCH_WEIGHTS["backup_qb"] if i == 0 else BENCH_WEIGHTS["third_qb"]
        bench_value += pts * w
    for i, (_n, _p, pts) in enumerate(bench_te):
        w = BENCH_WEIGHTS["backup_te"] if i == 0 else BENCH_WEIGHTS["remaining_bench"]
        bench_value += pts * w

    return LineupResult(
        starting_lineup_points=round(starting_lineup_points, 2),
        bench_option_value=round(bench_value, 2),
        total_roster_utility=round(starting_lineup_points + bench_value, 2),
        starting_QB=starting_qb[0],
        starting_RB=[n for n, _ in starting_rb],
        starting_WR=[n for n, _ in starting_wr],
        starting_TE=starting_te[0][0],
        starting_FLEX=[n for n, _ in starting_flex],
        bench_QB_count=len(by_pos["QB"]) - 1,
        bench_points_included=round(bench_value, 2),
        roster_legality="LEGAL",
    )
