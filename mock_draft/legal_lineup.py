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

PHASE 2 ADDITION (everything below `select_legal_lineup`/`LineupResult`
above this point is untouched -- audit_qb_arbitrage.py depends on it
producing bit-identical output for the preserved phase-1 audit trail):
`build_production_lineup` / `ProductionLineupResult` is the follow-on
"larger task" the docstring above flagged, wired into every production
fitness path (evolution.py, best_response.py) in place of
`Team.total_points`. `raw_all_rostered_points` is included on the result
ONLY as a diagnostic field for comparing against the old (bugged) metric.
Never use raw_all_rostered_points as fitness -- it reintroduces the exact
bug (crediting bench players at 4th-string positions with a full season
of points) this rebuild exists to remove.
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


# --------------------------------------------------------------------------
# PHASE 2 PRODUCTION VERSION -- used by every strategy-evaluation path
# (evolution.py, best_response.py). Field names and per-position bench
# weights below match the rebuild spec exactly; they are intentionally
# NOT the same shape as LineupResult above so that changing them can never
# silently perturb select_legal_lineup's phase-1 audit output.
# --------------------------------------------------------------------------

# ASSUMPTION (config switch, per the rebuild spec -- retune or replace with
# real weekly-projection-based marginal values once available). These
# weights are first-pass, hand-picked guesses reflecting "how often does a
# bench player actually get started via bye week / injury / matchup", NOT
# an empirically fitted result. Do not present them as validated.
#
# PHASE 3A RETUNE -- TRIED, THEN REVERTED (kept in history for the audit
# trail; see outputs/auction_rebuild/phase3a/ for the full writeup).
# phase 3A's unused-cash diagnostics
# (outputs/auction_rebuild/phase3a/unspent_cash_decomposition.csv) found
# 99.8% of all blocked bids were "zero/negative incremental utility"
# blocks, and the FIRST hypothesis was that third_qb=0.00 and
# other_legal_bench=0.05 gave 4th+ roster depth too little marginal
# value. Raising them (third_qb 0.00->0.03, other_legal_bench
# 0.05->0.10) was tried and measured to have ZERO effect on
# market_clearing_diagnostics.json (byte-identical output before/after).
# Root-caused instead to a real bug: mock_draft.auction._incremental_utility
# was calling build_production_lineup, whose total_roster_utility is
# hard-zeroed to 0 for ANY illegal (incomplete) roster -- so for a team
# that hadn't yet completed a full legal lineup (most teams, most of the
# draft), before=0 and after=0 regardless of bench weight, making the
# weight retune structurally unable to matter. Fixed by adding
# partial_lineup_value (below build_production_lineup in this file) for
# _incremental_utility's use instead. That fix alone took
# percentage_cash_spent from 26.77% to 96.47% over 40 seeds -- see
# market_clearing_diagnostics.json's before/after.
#
# The weight retune is REVERTED here (restored to the original phase 2B
# values) because: (1) it is no longer needed -- the real fix already
# resolves the unused-cash problem; (2) item 6 explicitly says "do not
# solve unused cash by inflating bench points," and this retune was
# exactly that; (3) third_qb=0.03 broke an established phase 2B backstop
# test (test_11_zero_utility_player_receives_no_bid_above_one_dollar)
# that a 3rd QB must never draw a bid above $1, and phase 3A's own item
# 15 explicitly requires "third QB produces zero depth value" as one of
# the 20 required tests.
PRODUCTION_BENCH_WEIGHTS = {
    "first_reserve_rb": 0.30,   # RB2-deep: plausible bye-week/injury starter
    "first_reserve_wr": 0.30,   # WR3-deep: same logic
    "second_reserve_rb": 0.15,  # RB3-deep: real but less-likely start
    "second_reserve_wr": 0.15,  # WR4-deep: same logic
    "backup_te": 0.10,          # TE streaming is common but low-value in this format
    "backup_qb": 0.075,         # 1-QB league: a backup QB starts only on bye/injury
    "third_qb": 0.00,           # zero depth value -- see item 15's explicit requirement
    "other_legal_bench": 0.05,  # deep bench: small option value, not fake season points
}


def partial_lineup_value(
    roster: list[tuple[str, str, float, float]],
    bench_weights: dict | None = None,
) -> float:
    """Best-effort roster utility for an INCOMPLETE, mid-draft roster --
    used ONLY by mock_draft.auction._incremental_utility's bid-time
    marginal-utility gate. Never use this for final-roster fitness
    (evolution.py, best_response.py); build_production_lineup's hard
    "0 if illegal" contract is correct there -- an unfinished final roster
    really did lose, and should score 0.

    PHASE 3A BUG FIX (item 5): build_production_lineup returns
    total_roster_utility=0 for ANY illegal roster (e.g. missing a 2nd RB),
    with no partial credit for real value already rostered. Reusing it
    inside _incremental_utility meant that for any team not yet holding a
    full legal lineup -- i.e. most teams for most of a live auction --
    BOTH "roster alone" and "roster + candidate" scored 0 whenever the
    candidate didn't singlehandedly complete the lineup, so
    after-before == 0 regardless of how good the candidate was. Measured
    directly: a 200-point RB added to a QB-only roster (still illegal,
    still missing a 2nd RB) reported exactly $0 of marginal value. This,
    not PRODUCTION_BENCH_WEIGHTS, was the dominant cause of the 83-99.8%
    "zero incremental utility" block rate found in
    outputs/auction_rebuild/phase3a/unspent_cash_decomposition.csv --
    confirmed by direct instrumentation: ~83% of zero/negative-utility
    calls were "..._ILLEGAL_AFTER" (both sides hard-zeroed), not an
    actual zero-weight bench tier. Retuning bench weights alone could
    never have fixed this, since 0 - 0 == 0 regardless of weight.

    Scores whatever starting slots CAN be filled from the roster as-is
    (a missing slot simply contributes 0 points, never blocks the whole
    score to 0), then scores every leftover player with the same tiered
    bench weights build_production_lineup uses. Monotonic non-decreasing
    in roster size: adding a player can only add a starting slot or a
    bench slot, never remove value already counted, so before <= after
    always -- exactly the property a subtraction-based marginal-utility
    gate needs.
    """
    weights = bench_weights or PRODUCTION_BENCH_WEIGHTS
    by_pos: dict[str, list[tuple[str, float]]] = {"QB": [], "RB": [], "WR": [], "TE": []}
    other: list[tuple[str, str, float]] = []
    for name, pos, _price, pts in roster:
        if pos in by_pos:
            by_pos[pos].append((name, pts))
        else:
            other.append((name, pos, pts))
    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: x[1], reverse=True)

    starting_qb = by_pos["QB"][: STARTING_LINEUP["QB"]]
    starting_rb = by_pos["RB"][: STARTING_LINEUP["RB"]]
    starting_wr = by_pos["WR"][: STARTING_LINEUP["WR"]]
    starting_te = by_pos["TE"][: STARTING_LINEUP["TE"]]
    used_names = {n for n, _ in starting_qb + starting_rb + starting_wr + starting_te}

    flex_pool = [(name, pts) for pos in FLEX_ELIGIBLE for name, pts in by_pos[pos] if name not in used_names]
    flex_pool.sort(key=lambda x: x[1], reverse=True)
    starting_flex = flex_pool[:FLEX_SLOTS]
    used_names |= {n for n, _ in starting_flex}

    starting_points = sum(p for _, p in starting_qb + starting_rb + starting_wr + starting_te + starting_flex)

    bench = [(name, pos, pts) for name, pos, _price, pts in roster if name not in used_names]
    bench_rb = sorted([b for b in bench if b[1] == "RB"], key=lambda x: x[2], reverse=True)
    bench_wr = sorted([b for b in bench if b[1] == "WR"], key=lambda x: x[2], reverse=True)
    bench_te = sorted([b for b in bench if b[1] == "TE"], key=lambda x: x[2], reverse=True)
    bench_qb = sorted([b for b in bench if b[1] == "QB"], key=lambda x: x[2], reverse=True)
    bench_other = [b for b in bench if b[1] not in ("RB", "WR", "TE", "QB")]

    def _tiered(items, tiers) -> float:
        total = 0.0
        for i, (_n, _p, pts) in enumerate(items):
            key = tiers[i] if i < len(tiers) else tiers[-1]
            total += pts * weights[key]
        return total

    bench_value = (
        _tiered(bench_rb, ["first_reserve_rb", "second_reserve_rb", "other_legal_bench"])
        + _tiered(bench_wr, ["first_reserve_wr", "second_reserve_wr", "other_legal_bench"])
        + _tiered(bench_te, ["backup_te", "other_legal_bench"])
        + _tiered(bench_qb, ["backup_qb", "third_qb"])
        + sum(pts * weights["other_legal_bench"] for _n, _p, pts in bench_other)
    )
    return round(starting_points + bench_value, 2)


@dataclass
class ProductionLineupResult:
    starting_lineup_points: float
    bench_option_value: float
    total_roster_utility: float
    lineup_is_legal: bool
    lineup_failure_reason: str | None
    starting_qb: str | None
    starting_rbs: list
    starting_wrs: list
    starting_te: str | None
    starting_flex: list
    bench_players: list  # [{player, position, points, bench_weight, bench_contribution}, ...]
    bench_qb_count: int
    # DIAGNOSTIC ONLY. The exact metric phase 1 proved invalid (credits
    # every rostered player as if they started, e.g. a 4th-string QB
    # counted for a full season of points). Never use this as fitness --
    # see module docstring.
    raw_all_rostered_points: float


def build_production_lineup(
    roster: list[tuple[str, str, float, float]],
    bench_weights: dict | None = None,
) -> ProductionLineupResult:
    """roster: list of (player_name, position, price, projected_points).

    Selects the legal 1QB/2RB/2WR/1TE/3FLEX starting lineup that maximizes
    starting_lineup_points. This is done by a greedy-by-points fill (best
    QB; best 2 RB / 2 WR / 1 TE; best remaining 3 RB/WR/TE for FLEX) rather
    than brute-force search over all combinations -- per the rebuild spec's
    "do not select starters through a greedy sort unless tests prove
    equivalence," tests/test_legal_lineup.py brute-forces small rosters and
    asserts this greedy fill always matches the true optimum. It is optimal
    here specifically because every FLEX-eligible position (RB/WR/TE) draws
    from one shared point-maximizing pool once its own minimum is met --
    an exchange argument shows no swap between a required slot and FLEX (or
    between FLEX occupants) can ever increase the total, so "fill minimums
    with the best at each position, then fill FLEX with whoever's left
    over, ranked purely by points" already IS the max-points legal lineup.
    A player fills exactly one slot; duplicates are rejected outright.
    """
    weights = bench_weights or PRODUCTION_BENCH_WEIGHTS
    raw_all_rostered_points = round(sum(pts for _n, _p, _pr, pts in roster), 2)

    names_seen = [name for name, _p, _pr, _pts in roster]
    if len(names_seen) != len(set(names_seen)):
        dupes = sorted({n for n in names_seen if names_seen.count(n) > 1})
        return ProductionLineupResult(
            0, 0, 0, False, f"DUPLICATE_PLAYER:{','.join(dupes)}",
            None, [], [], None, [], [], 0, raw_all_rostered_points,
        )

    by_pos: dict[str, list[tuple[str, float]]] = {"QB": [], "RB": [], "WR": [], "TE": []}
    for name, pos, _price, pts in roster:
        if pos not in by_pos:
            return ProductionLineupResult(
                0, 0, 0, False, f"ILLEGAL_POSITION:{pos}",
                None, [], [], None, [], [], 0, raw_all_rostered_points,
            )
        by_pos[pos].append((name, pts))
    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: x[1], reverse=True)

    def _illegal(reason: str) -> ProductionLineupResult:
        return ProductionLineupResult(
            0, 0, 0, False, reason, None, [], [], None, [],
            [], len(by_pos["QB"]), raw_all_rostered_points,
        )

    if len(by_pos["QB"]) < STARTING_LINEUP["QB"]:
        return _illegal("MISSING_QB")
    if len(by_pos["RB"]) < 1:
        return _illegal("MISSING_RB")
    if len(by_pos["RB"]) < STARTING_LINEUP["RB"]:
        return _illegal("MISSING_SECOND_RB")
    if len(by_pos["WR"]) < 1:
        return _illegal("MISSING_WR")
    if len(by_pos["WR"]) < STARTING_LINEUP["WR"]:
        return _illegal("MISSING_SECOND_WR")
    if len(by_pos["TE"]) < STARTING_LINEUP["TE"]:
        return _illegal("MISSING_TE")

    starting_qb = by_pos["QB"][0]
    starting_rb = by_pos["RB"][: STARTING_LINEUP["RB"]]
    starting_wr = by_pos["WR"][: STARTING_LINEUP["WR"]]
    starting_te = by_pos["TE"][: STARTING_LINEUP["TE"]]
    used_names = {starting_qb[0]} | {n for n, _ in starting_rb} | {n for n, _ in starting_wr} | {n for n, _ in starting_te}

    flex_pool = [(name, pts) for pos in FLEX_ELIGIBLE for name, pts in by_pos[pos] if name not in used_names]
    flex_pool.sort(key=lambda x: x[1], reverse=True)
    if len(flex_pool) < FLEX_SLOTS:
        return _illegal("MISSING_FLEX_DEPTH")
    starting_flex = flex_pool[:FLEX_SLOTS]
    used_names |= {n for n, _ in starting_flex}

    starting_lineup_points = (
        starting_qb[1] + sum(p for _, p in starting_rb) + sum(p for _, p in starting_wr)
        + sum(p for _, p in starting_te) + sum(p for _, p in starting_flex)
    )

    bench = [(name, pos, pts) for name, pos, _price, pts in roster if name not in used_names]
    bench_rb = sorted([b for b in bench if b[1] == "RB"], key=lambda x: x[2], reverse=True)
    bench_wr = sorted([b for b in bench if b[1] == "WR"], key=lambda x: x[2], reverse=True)
    bench_te = sorted([b for b in bench if b[1] == "TE"], key=lambda x: x[2], reverse=True)
    bench_qb = sorted([b for b in bench if b[1] == "QB"], key=lambda x: x[2], reverse=True)
    bench_other = [b for b in bench if b[1] not in ("RB", "WR", "TE", "QB")]

    bench_players = []

    def _tag(items, tiers):
        for i, (name, pos, pts) in enumerate(items):
            weight_key = tiers[i] if i < len(tiers) else tiers[-1]
            w = weights[weight_key]
            bench_players.append({
                "player": name, "position": pos, "points": pts,
                "bench_weight": w, "bench_contribution": round(pts * w, 2),
            })

    _tag(bench_rb, ["first_reserve_rb", "second_reserve_rb", "other_legal_bench"])
    _tag(bench_wr, ["first_reserve_wr", "second_reserve_wr", "other_legal_bench"])
    _tag(bench_te, ["backup_te", "other_legal_bench"])
    _tag(bench_qb, ["backup_qb", "third_qb"])
    for name, pos, pts in bench_other:
        w = weights["other_legal_bench"]
        bench_players.append({
            "player": name, "position": pos, "points": pts,
            "bench_weight": w, "bench_contribution": round(pts * w, 2),
        })

    bench_value = round(sum(b["bench_contribution"] for b in bench_players), 2)

    return ProductionLineupResult(
        starting_lineup_points=round(starting_lineup_points, 2),
        bench_option_value=bench_value,
        total_roster_utility=round(starting_lineup_points + bench_value, 2),
        lineup_is_legal=True,
        lineup_failure_reason=None,
        starting_qb=starting_qb[0],
        starting_rbs=[n for n, _ in starting_rb],
        starting_wrs=[n for n, _ in starting_wr],
        starting_te=starting_te[0][0],
        starting_flex=[n for n, _ in starting_flex],
        bench_players=bench_players,
        bench_qb_count=len(bench_qb),
        raw_all_rostered_points=raw_all_rostered_points,
    )
