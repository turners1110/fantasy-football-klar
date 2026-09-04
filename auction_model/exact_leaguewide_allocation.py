"""Phase 3D item 2: EXACT_LEAGUEWIDE_ALLOCATION -- a real mixed-integer
program (PuLP/CBC) that jointly assigns every auction-eligible player to
at most one team and one starter role across ALL 12 teams simultaneously,
respecting each team's real fixed keepers and roster rules, to measure
true positional replacement level and FLEX demand from football
demand alone (no auction price in the objective).

Distinguished explicitly from GREEDY_LEAGUEWIDE_ALLOCATION (phase 3C's
single-pass greedy fill, renamed from the earlier, inaccurate
"C_OPTIMIZATION_DERIVED" label -- a greedy heuristic is not an exact
optimization and must never be called one).

FORMULATION (disclosed simplification for tractability, not hidden):
role types are QB/RB/WR/TE/FLEX (5 types, not 9 numbered slots) --
legality only requires the right COUNT of each role type per team, never
which numbered slot, so numbering starter slots individually would add
size with no effect on the optimal value. Bench membership is NOT a
separate role variable: any rostered player who isn't a starter is
implicitly bench, contributing a single FLAT bench weight (this repo's
own PRODUCTION_BENCH_WEIGHTS["other_legal_bench"], not a fabricated
number) rather than the live auction engine's full rank-dependent tiered
bench weights -- a real, disclosed reduction from the live utility
scorer's fidelity, in exchange for a MUCH smaller (tractable) MIP; a
rank-dependent tiered bench objective would require sequencing
constraints that blow up the problem size for a marginal accuracy gain
on players who, by construction, only affect the BENCH share of the
objective. The auction pool is capped to the top N_POOL_CAP players per
position by points (default 60 -- comfortably above any plausible
12-team demand at any position) purely for solver tractability, matching
this repo's existing exact_roster_solver.py's own max_pool convention.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pandas as pd
import pulp

ROLE_TYPES = ("QB", "RB", "WR", "TE", "FLEX")
ROLE_ELIGIBLE_POSITIONS = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "FLEX": {"RB", "WR", "TE"},
}
REQUIRED_STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 3}
BENCH_WEIGHT = 0.10  # PRODUCTION_BENCH_WEIGHTS["other_legal_bench"], mock_draft/legal_lineup.py
ROSTER_SIZE = 15
N_POOL_CAP = 60  # per position, by points -- see module docstring
SOLVER_TIME_LIMIT_SECONDS = 600
# A flat bench weight applied to RAW points (not tiered/position-aware like
# mock_draft.legal_lineup.PRODUCTION_BENCH_WEIGHTS) reproduces the exact
# phase-1 bug this whole rebuild exists to fix: this league's QBs score far
# more raw points than RB/WR/TE (~69.6 vs ~7.6 pts/$, per the phase 1
# historical audit), so an UNCAPPED flat-weight objective hoards bench QB
# slots that provide zero real value in a 1-QB league. Caught directly:
# without this cap, the exact solve pushed QB replacement to rank 33 (101
# points) by stuffing extra bench QBs across the league. Capped here using
# the SAME historically-grounded PRIMARY_QB_CAP already established in
# mock_draft/feasibility.py (2), not a new or arbitrary assumption.
MAX_QB_PER_TEAM = 2


@dataclass
class LeaguewideAllocationResult:
    status: str
    objective_value: float
    assignments: pd.DataFrame  # columns: player, position, team, role ('BENCH' if no starter role), points
    replacement_by_position: dict[str, dict]  # {position: {rank, points}}
    flex_mix: dict[str, int]  # {position: count of FLEX slots filled}
    runtime_seconds: float
    warnings: list[str]


def solve_exact_leaguewide_allocation(
    pool_points: dict[str, tuple[str, float]],  # {player: (position, points)}
    team_keepers: dict[str, list[tuple[str, str, float]]],  # {team: [(player, position, points), ...]}
    n_pool_cap: int = N_POOL_CAP,
    time_limit: int = SOLVER_TIME_LIMIT_SECONDS,
) -> LeaguewideAllocationResult:
    t0 = time.time()
    warnings: list[str] = []
    teams = list(team_keepers.keys())

    # Cap the auction pool per position for tractability (see module docstring).
    by_pos: dict[str, list[tuple[str, float]]] = {"QB": [], "RB": [], "WR": [], "TE": []}
    for name, (pos, pts) in pool_points.items():
        if pos in by_pos:
            by_pos[pos].append((name, pts))
    capped_pool: dict[str, float] = {}
    for pos, entries in by_pos.items():
        entries.sort(key=lambda e: e[1], reverse=True)
        for name, pts in entries[:n_pool_cap]:
            capped_pool[name] = pts
    if len(capped_pool) < sum(n_pool_cap for _ in by_pos):
        pass  # pool smaller than cap at some position -- fine, just means no truncation happened there
    pool_position = {name: pos for name, (pos, _pts) in pool_points.items() if name in capped_pool}

    n_open_slots = {t: ROSTER_SIZE - len(keepers) for t, keepers in team_keepers.items()}
    if any(v < 0 for v in n_open_slots.values()):
        warnings.append("a team has more than 15 keepers -- infeasible by construction")

    prob = pulp.LpProblem("leaguewide_exact_allocation", pulp.LpMaximize)

    # x[p, t]: auction-pool player p joins team t.
    x = {(p, t): pulp.LpVariable(f"x_{p}_{t}", cat="Binary") for p in capped_pool for t in teams}
    # s[p, t, role]: player p (pool OR keeper) starts at role on team t.
    s: dict[tuple[str, str, str], pulp.LpVariable] = {}
    all_players_by_team: dict[str, list[tuple[str, str, float, bool]]] = {t: [] for t in teams}
    for t, keepers in team_keepers.items():
        for name, pos, pts in keepers:
            all_players_by_team[t].append((name, pos, pts, True))
    for name in capped_pool:
        pos = pool_position[name]
        for t in teams:
            all_players_by_team[t].append((name, pos, capped_pool[name], False))

    for t, entries in all_players_by_team.items():
        for name, pos, pts, is_keeper in entries:
            for role in ROLE_TYPES:
                if pos in ROLE_ELIGIBLE_POSITIONS[role]:
                    s[(name, t, role)] = pulp.LpVariable(f"s_{name}_{t}_{role}", cat="Binary")

    # Each pool player joins at most one team.
    for p in capped_pool:
        prob += pulp.lpSum(x[(p, t)] for t in teams) <= 1, f"one_team_{p}"

    # Each team ends with exactly 15 (keepers + assigned pool players).
    for t in teams:
        prob += pulp.lpSum(x[(p, t)] for p in capped_pool) == n_open_slots[t], f"roster_size_{t}"

    # A starter role requires actually being on that team's roster.
    for (name, t, role), var in s.items():
        is_keeper = any(kn == name for kn, _p, _pts in team_keepers[t])
        if not is_keeper:
            prob += var <= x[(name, t)], f"onroster_{name}_{t}_{role}"
        # else: keeper is always on-roster for their own team -- no upper-bound needed.

    # At most one starter role per (player, team).
    by_pt: dict[tuple[str, str], list] = {}
    for (name, t, role), var in s.items():
        by_pt.setdefault((name, t), []).append(var)
    for (_name, _t), vars_ in by_pt.items():
        prob += pulp.lpSum(vars_) <= 1

    # Exact starter/FLEX counts per team.
    for t in teams:
        for role, need in REQUIRED_STARTERS.items():
            role_vars = [var for (name, tt, r), var in s.items() if tt == t and r == role]
            prob += pulp.lpSum(role_vars) == need, f"fill_{role}_{t}"

    # Historically-grounded QB roster cap (see MAX_QB_PER_TEAM's docstring
    # note above) -- without this, the flat bench-weight objective hoards
    # bench QB slots since this league's QBs score far more raw points
    # than any other position, a real, measured pathology this cap exists
    # to prevent, not an arbitrary added rule.
    for t in teams:
        qb_on_roster = [
            (x[(name, t)] if not is_keeper else 1)
            for name, pos, _pts, is_keeper in all_players_by_team[t] if pos == "QB"
        ]
        prob += pulp.lpSum(qb_on_roster) <= MAX_QB_PER_TEAM, f"qb_cap_{t}"

    # Objective: full weight for starters, flat bench weight for the rest of each roster.
    starter_terms = []
    bench_terms = []
    for t, entries in all_players_by_team.items():
        for name, pos, pts, is_keeper in entries:
            on_roster = 1 if is_keeper else x[(name, t)]
            role_vars = [s[(name, t, role)] for role in ROLE_TYPES if (name, t, role) in s]
            starter_indicator = pulp.lpSum(role_vars)
            starter_terms.append(pts * starter_indicator)
            bench_terms.append(pts * BENCH_WEIGHT * (on_roster - starter_indicator))
    prob += pulp.lpSum(starter_terms) + pulp.lpSum(bench_terms)

    solved = False
    for solver in (pulp.HiGHS(msg=0, timeLimit=time_limit), pulp.PULP_CBC_CMD(msg=0, timeLimit=time_limit)):
        try:
            prob.solve(solver)
            solved = True
            break
        except Exception:
            continue
    if not solved:
        warnings.append("no usable MIP solver backend available (HiGHS and CBC both failed)")
    status = pulp.LpStatus[prob.status]
    if status == "Optimal":
        result_status = "OPTIMAL"
    elif status in ("Not Solved", "Undefined") and prob.objective.value() is not None:
        result_status = "FEASIBLE_NOT_PROVEN_OPTIMAL"
        warnings.append(f"solver hit time_limit={time_limit}s before proving optimality")
    else:
        result_status = "INFEASIBLE"
        warnings.append(f"pulp status: {status}")

    rows = []
    flex_mix = {"RB": 0, "WR": 0, "TE": 0}
    replacement_candidates: dict[str, list[float]] = {"QB": [], "RB": [], "WR": [], "TE": []}
    for t, entries in all_players_by_team.items():
        for name, pos, pts, is_keeper in entries:
            on_roster_val = 1 if is_keeper else (x[(name, t)].value() or 0)
            if on_roster_val < 0.5:
                continue
            role_assigned = "BENCH"
            for role in ROLE_TYPES:
                var = s.get((name, t, role))
                if var is not None and (var.value() or 0) > 0.5:
                    role_assigned = role
                    break
            rows.append({"player": name, "position": pos, "team": t, "role": role_assigned, "points": pts})
            replacement_candidates[pos].append(pts)
            if role_assigned == "FLEX":
                flex_mix[pos] += 1

    # Replacement level uses only players with REAL positive points. The
    # mandatory exactly-15-per-team roster fill (per the spec's own
    # requirement) forces some teams to roster zero-point filler when a
    # position's real (non-fallback-zero) depth runs out within the
    # capped candidate pool -- found directly in testing: without this
    # filter, RB/WR/TE replacement collapsed to exactly 0.0 points,
    # because enough $0-fallback players (players with no real
    # projection AND a $0 old-snapshot base_value, so
    # mock_draft.points.points_for's fallback imputation --
    # base_value*ratio -- also computes to exactly 0) got forced onto
    # rosters to satisfy the roster-size constraint. A $0-fallback player
    # is real roster filler, not a meaningful replacement-level
    # benchmark, so it is excluded from this calculation specifically
    # (it is NOT excluded from the roster-fill/objective itself).
    replacement_by_position = {}
    for pos, pts_list in replacement_candidates.items():
        positive_only = sorted((p for p in pts_list if p > 0), reverse=True)
        replacement_by_position[pos] = {
            "rank": len(positive_only),
            "points": positive_only[-1] if positive_only else None,
        }

    return LeaguewideAllocationResult(
        status=result_status,
        objective_value=prob.objective.value() or 0.0,
        assignments=pd.DataFrame(rows),
        replacement_by_position=replacement_by_position,
        flex_mix=flex_mix,
        runtime_seconds=round(time.time() - t0, 2),
        warnings=warnings,
    )
