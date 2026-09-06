"""Exact mixed-integer roster optimizer using PuLP/CBC."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pulp

from . import config

FLEX_ELIG = frozenset({"RB", "WR", "TE"})

STARTER_ROLES: list[tuple[str, frozenset[str]]] = [
    ("QB_START", frozenset({"QB"})),
    ("RB_START_1", frozenset({"RB"})),
    ("RB_START_2", frozenset({"RB"})),
    ("WR_START_1", frozenset({"WR"})),
    ("WR_START_2", frozenset({"WR"})),
    ("TE_START", frozenset({"TE"})),
    ("FLEX_1", FLEX_ELIG),
    ("FLEX_2", FLEX_ELIG),
    ("FLEX_3", FLEX_ELIG),
]
BENCH_ROLES = [f"BENCH_{i}" for i in range(1, config.BENCH_SIZE + 1)]
ALL_ROLES = [r for r, _ in STARTER_ROLES] + BENCH_ROLES
STARTER_ROLE_NAMES = {r for r, _ in STARTER_ROLES}


@dataclass
class ExactSolveResult:
    selected: pd.DataFrame
    unused_cash: float
    spent: float
    status: str
    starting_points: float
    bench_points: float
    role_assignments: dict[str, str]
    warnings: list[str]


def _role_eligible(position: str, role: str) -> bool:
    pos = str(position).upper()
    if role in BENCH_ROLES:
        return pos in {"QB", "RB", "WR", "TE"}
    for rname, eligible in STARTER_ROLES:
        if rname == role:
            return pos in eligible
    return False


def _prepare_auction_pool(candidates: pd.DataFrame, exclude: set[str], max_pool: int = 100) -> pd.DataFrame:
    pool = candidates[
        (~candidates["player"].isin(exclude))
        & candidates["projected_points"].notna()
        & candidates["suggested_auction_price"].notna()
    ].copy()
    if pool.empty:
        return pool
    pool["price"] = pool["suggested_auction_price"].clip(lower=config.MIN_PRICE)
    pool["is_keeper"] = False
    pool = pool.dropna(subset=["projected_points", "price"])
    by_pts = pool.sort_values("projected_points", ascending=False).head(max_pool)
    by_cheap = pool.sort_values(["price", "projected_points"], ascending=[True, False]).head(50)
    return pd.concat([by_pts, by_cheap]).drop_duplicates("player").reset_index(drop=True)


def _build_combined_pool(
    auction_candidates: pd.DataFrame,
    keepers: pd.DataFrame,
    exclude: set[str],
) -> pd.DataFrame:
    parts = []
    if not keepers.empty:
        k = keepers.copy()
        k["price"] = k["keeper_price_2026"].fillna(k.get("suggested_auction_price", 0))
        k["is_keeper"] = True
        parts.append(k)
    auction = _prepare_auction_pool(auction_candidates, exclude)
    if not auction.empty:
        parts.append(auction)
    if not parts:
        return pd.DataFrame()
    combined = pd.concat(parts, ignore_index=True)
    combined = combined.dropna(subset=["projected_points", "price"])
    return combined.drop_duplicates("player", keep="first").reset_index(drop=True)


def _active_roles(protected_but_unlisted: int) -> list[str]:
    """V3.1 REPAIR 1: the roster-role model (STARTER_ROLES + BENCH_ROLES)
    was always built assuming every one of the 16 roles is filled by a
    real, selectable player -- true for every team with fully-named
    protected occupancy, but NOT true for Sam (2 college-rights bench
    slots) or Brad/Reid (1 unidentified protected slot each). Those
    slots are real and occupied, but the people in them are never in
    `keepers` or `auction_candidates` -- they cannot be assigned a role
    by this solver at all. Trimming `protected_but_unlisted` BENCH
    roles off the model (never starter roles -- a protected occupant
    never displaces a real starting-lineup requirement) makes the
    model's role count match the TRUE number of real, selectable
    occupants this solve is actually choosing among."""
    if protected_but_unlisted <= 0:
        return list(ALL_ROLES)
    trimmed_bench = BENCH_ROLES[: max(0, len(BENCH_ROLES) - protected_but_unlisted)]
    return [r for r, _ in STARTER_ROLES] + trimmed_bench


def _build_model(
    pool: pd.DataFrame,
    budget: float,
    n_auction_spots: int,
    fix_starting: float | None = None,
    fix_bench: float | None = None,
    stage: int = 1,
    protected_but_unlisted: int = 0,
) -> tuple[pulp.LpProblem, dict, dict]:
    prob = pulp.LpProblem("roster_exact", pulp.LpMaximize)
    players = list(pool.index)
    active_roles = _active_roles(protected_but_unlisted)
    active_bench_roles = [r for r in active_roles if r in BENCH_ROLES]
    y = pulp.LpVariable.dicts("y", (players, active_roles), cat=pulp.LpBinary)

    for role in active_roles:
        prob += pulp.lpSum(y[i][role] for i in players) == 1, f"fill_{role}"

    for i in players:
        prob += pulp.lpSum(y[i][role] for role in active_roles) <= 1, f"one_role_{i}"

    # Keepers must be selected
    for i, row in pool.iterrows():
        if row.get("is_keeper"):
            prob += pulp.lpSum(y[i][role] for role in active_roles) == 1, f"keeper_{i}"

    # Exactly n_auction_spots from non-keepers
    prob += (
        pulp.lpSum(
            pulp.lpSum(y[i][role] for role in active_roles)
            for i, row in pool.iterrows() if not row.get("is_keeper")
        ) == n_auction_spots
    ), "auction_count"

    for i, row in pool.iterrows():
        pos = row["position"]
        for role in active_roles:
            if not _role_eligible(pos, role):
                prob += y[i][role] == 0, f"pos_{i}_{role}"

    prob += (
        pulp.lpSum(
            float(row["price"]) * pulp.lpSum(y[i][role] for role in active_roles)
            for i, row in pool.iterrows() if not row.get("is_keeper")
        ) <= budget
    ), "budget"

    qb_players = [i for i, row in pool.iterrows() if row["position"] == "QB"]
    if qb_players:
        prob += pulp.lpSum(y[i][role] for i in qb_players for role in active_roles) <= 2, "max_two_qb"

    start_expr = pulp.lpSum(
        float(row["projected_points"]) * y[i][role]
        for i, row in pool.iterrows() for role in STARTER_ROLE_NAMES
    )
    bench_expr = pulp.lpSum(
        float(row["projected_points"]) * y[i][role]
        for i, row in pool.iterrows() for role in active_bench_roles
    )
    spend_expr = pulp.lpSum(
        float(row["price"]) * pulp.lpSum(y[i][role] for role in active_roles)
        for i, row in pool.iterrows() if not row.get("is_keeper")
    )

    if stage == 1:
        prob += start_expr
    elif stage == 2:
        prob += bench_expr
        if fix_starting is not None:
            prob += start_expr >= fix_starting - config.STARTING_POINT_TOLERANCE, "fix_start"
    else:
        prob += (budget - spend_expr) * 1000
        if fix_starting is not None:
            prob += start_expr >= fix_starting - config.STARTING_POINT_TOLERANCE, "fix_start"
        if fix_bench is not None:
            prob += bench_expr >= fix_bench - 0.01, "fix_bench"

    return prob, y, {"start": start_expr, "bench": bench_expr, "spend": spend_expr}


def _extract_solution(pool: pd.DataFrame, y: dict, players: list) -> tuple[pd.DataFrame, dict[str, str], float, float]:
    picked_rows = []
    roles: dict[str, str] = {}
    start_pts = bench_pts = 0.0
    for i in players:
        for role in y[i]:  # the roles actually modeled for this solve (see _active_roles)
            if pulp.value(y[i][role]) and pulp.value(y[i][role]) > 0.5:
                row = pool.loc[i].to_dict()
                row["lineup_role"] = role
                picked_rows.append(row)
                roles[row["player"]] = role
                pts = float(row["projected_points"])
                if role in STARTER_ROLE_NAMES:
                    start_pts += pts
                else:
                    bench_pts += pts
                break
    return pd.DataFrame(picked_rows), roles, round(start_pts, 2), round(bench_pts, 2)


def _solve_stage(prob: pulp.LpProblem) -> str:
    for solver in (pulp.HiGHS(msg=0), pulp.PULP_CBC_CMD(msg=0)):
        try:
            prob.solve(solver)
            break
        except Exception:
            continue
    status = pulp.LpStatus[prob.status]
    if status == "Optimal":
        return "OPTIMAL"
    if status == "Infeasible":
        return "INFEASIBLE"
    if status == "Not Solved":
        return "ERROR"
    return "FEASIBLE_NOT_PROVEN_OPTIMAL"


def solve_exact_roster(
    auction_candidates: pd.DataFrame,
    budget: float,
    n_auction_spots: int,
    keepers: pd.DataFrame | None = None,
    exclude: set[str] | None = None,
    protected_but_unlisted: int = 0,
) -> ExactSolveResult:
    """Lexicographic exact solve for keepers + auction filling active roster spots (config.ACTIVE_ROSTER_SIZE, 16).

    `protected_but_unlisted`: V3.1 REPAIR 1/2 -- the number of this
    team's official protected roster slots that are occupied by players
    who never appear in `keepers` and are never selectable from
    `auction_candidates` (Sam's Mendoza/Bond college-rights holds, or
    Brad/Reid's one unidentified protected slot). The solver still only
    ever optimizes over `keepers` (fixed) + `n_auction_spots` (selected)
    -- this parameter exists ONLY so the internal self-check below
    compares against the team's TRUE total occupancy target
    (keepers + protected_but_unlisted + n_auction_spots) instead of
    blindly assuming every team's protected occupancy is fully named.
    Before this fix, Sam's call site passed n_auction_spots=10 (derived
    from len(roster) instead of the canonical open_slots property)
    specifically because 6 keepers + 10 = 16 satisfied this check with
    the WRONG numbers -- the check's own success is why the bug went
    undetected. It is correctly caught now: 6 keepers + 8 real openings
    + 2 protected_but_unlisted = 16, the true total."""
    exclude = exclude or set()
    keepers = keepers if keepers is not None else pd.DataFrame()
    warnings: list[str] = []

    if n_auction_spots < 0:
        return ExactSolveResult(pd.DataFrame(), budget, 0.0, "INFEASIBLE", 0, 0, {}, ["negative_spots"])

    if n_auction_spots == 0 and keepers.empty:
        return ExactSolveResult(pd.DataFrame(), budget, 0.0, "INFEASIBLE", 0, 0, {}, ["empty_roster"])

    pool = _build_combined_pool(auction_candidates, keepers, exclude)
    if pool.empty:
        return ExactSolveResult(pd.DataFrame(), budget, 0.0, "INFEASIBLE", 0, 0, {}, ["empty_pool"])

    n_keepers = int(keepers["player"].nunique()) if not keepers.empty else 0
    total_occupancy = n_keepers + n_auction_spots + protected_but_unlisted
    if total_occupancy != config.AUCTION_PURCHASE_REQUIREMENT:
        warnings.append(
            f"keeper+auction+protected_but_unlisted={n_keepers}+{n_auction_spots}+{protected_but_unlisted} "
            f"={total_occupancy} != {config.AUCTION_PURCHASE_REQUIREMENT}"
        )

    players = list(pool.index)
    prob1, y1, expr1 = _build_model(pool, budget, n_auction_spots, stage=1, protected_but_unlisted=protected_but_unlisted)
    status1 = _solve_stage(prob1)
    if status1 == "INFEASIBLE":
        return ExactSolveResult(pd.DataFrame(), budget, 0.0, "INFEASIBLE", 0, 0, {}, ["stage1_infeasible"])
    if status1 == "ERROR":
        return ExactSolveResult(pd.DataFrame(), budget, 0.0, "ERROR", 0, 0, {}, ["solver_error"])

    fix_start = pulp.value(expr1["start"])
    prob2, y2, expr2 = _build_model(pool, budget, n_auction_spots, fix_starting=fix_start, stage=2, protected_but_unlisted=protected_but_unlisted)
    _solve_stage(prob2)
    fix_bench = pulp.value(expr2["bench"]) if pulp.LpStatus[prob2.status] == "Optimal" else 0.0

    prob3, y3, _ = _build_model(
        pool, budget, n_auction_spots, fix_starting=fix_start, fix_bench=fix_bench, stage=3,
        protected_but_unlisted=protected_but_unlisted,
    )
    status3 = _solve_stage(prob3)
    final_status = status3 if status3 in {"OPTIMAL", "FEASIBLE_NOT_PROVEN_OPTIMAL"} else status1

    df, roles, start_pts, bench_pts = _extract_solution(pool, y3, players)
    auction_df = df[~df.get("is_keeper", False).astype(bool)] if "is_keeper" in df.columns else df
    spent = float(auction_df["price"].sum()) if not auction_df.empty else 0.0

    return ExactSolveResult(
        selected=df,
        unused_cash=round(budget - spent, 2),
        spent=round(spent, 2),
        status=final_status,
        starting_points=start_pts,
        bench_points=bench_pts,
        role_assignments=roles,
        warnings=warnings,
    )


def solve_exact_auction_roster(
    candidates: pd.DataFrame,
    budget: float,
    n_spots: int,
    exclude: set[str] | None = None,
) -> ExactSolveResult:
    """Auction-only exact solve (no keepers)."""
    return solve_exact_roster(candidates, budget, n_spots, keepers=pd.DataFrame(), exclude=exclude)


def post_solve_assertions(result: ExactSolveResult, budget: float) -> list[str]:
    failures = []
    df = result.selected
    if len(df) != config.AUCTION_PURCHASE_REQUIREMENT:
        failures.append(f"roster_size: {len(df)} != {config.AUCTION_PURCHASE_REQUIREMENT}")
    if result.spent > budget + 0.01:
        failures.append(f"budget: spent {result.spent} > {budget}")
    if len(df["player"].unique()) != len(df):
        failures.append("duplicate_players")
    starters = [r for r in df.get("lineup_role", []) if str(r).startswith(("QB", "RB", "WR", "TE", "FLEX"))]
    if len(starters) != config.ACTIVE_STARTER_SLOTS:
        failures.append(f"starters: {len(starters)} != {config.ACTIVE_STARTER_SLOTS}")
    bench = [r for r in df.get("lineup_role", []) if str(r).startswith("BENCH")]
    if len(bench) != config.BENCH_SIZE:
        failures.append(f"bench: {len(bench)} != {config.BENCH_SIZE}")
    for _, row in df.iterrows():
        role = str(row.get("lineup_role", ""))
        if role.startswith("FLEX") and row["position"] not in FLEX_ELIG:
            failures.append(f"illegal_flex: {row['player']}")
    return failures
