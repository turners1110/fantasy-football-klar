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


def _build_model(
    pool: pd.DataFrame,
    budget: float,
    n_auction_spots: int,
    fix_starting: float | None = None,
    fix_bench: float | None = None,
    stage: int = 1,
) -> tuple[pulp.LpProblem, dict, dict]:
    prob = pulp.LpProblem("roster_exact", pulp.LpMaximize)
    players = list(pool.index)
    y = pulp.LpVariable.dicts("y", (players, ALL_ROLES), cat=pulp.LpBinary)

    for role in ALL_ROLES:
        prob += pulp.lpSum(y[i][role] for i in players) == 1, f"fill_{role}"

    for i in players:
        prob += pulp.lpSum(y[i][role] for role in ALL_ROLES) <= 1, f"one_role_{i}"

    # Keepers must be selected
    for i, row in pool.iterrows():
        if row.get("is_keeper"):
            prob += pulp.lpSum(y[i][role] for role in ALL_ROLES) == 1, f"keeper_{i}"

    # Exactly n_auction_spots from non-keepers
    prob += (
        pulp.lpSum(
            pulp.lpSum(y[i][role] for role in ALL_ROLES)
            for i, row in pool.iterrows() if not row.get("is_keeper")
        ) == n_auction_spots
    ), "auction_count"

    for i, row in pool.iterrows():
        pos = row["position"]
        for role in ALL_ROLES:
            if not _role_eligible(pos, role):
                prob += y[i][role] == 0, f"pos_{i}_{role}"

    prob += (
        pulp.lpSum(
            float(row["price"]) * pulp.lpSum(y[i][role] for role in ALL_ROLES)
            for i, row in pool.iterrows() if not row.get("is_keeper")
        ) <= budget
    ), "budget"

    qb_players = [i for i, row in pool.iterrows() if row["position"] == "QB"]
    if qb_players:
        prob += pulp.lpSum(y[i][role] for i in qb_players for role in ALL_ROLES) <= 2, "max_two_qb"

    start_expr = pulp.lpSum(
        float(row["projected_points"]) * y[i][role]
        for i, row in pool.iterrows() for role in STARTER_ROLE_NAMES
    )
    bench_expr = pulp.lpSum(
        float(row["projected_points"]) * y[i][role]
        for i, row in pool.iterrows() for role in BENCH_ROLES
    )
    spend_expr = pulp.lpSum(
        float(row["price"]) * pulp.lpSum(y[i][role] for role in ALL_ROLES)
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
        for role in ALL_ROLES:
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
) -> ExactSolveResult:
    """Lexicographic exact solve for keepers + auction filling 15 active spots."""
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
    if n_keepers + n_auction_spots != config.AUCTION_PURCHASE_REQUIREMENT:
        warnings.append(
            f"keeper+auction={n_keepers}+{n_auction_spots} != {config.AUCTION_PURCHASE_REQUIREMENT}"
        )

    players = list(pool.index)
    prob1, y1, expr1 = _build_model(pool, budget, n_auction_spots, stage=1)
    status1 = _solve_stage(prob1)
    if status1 == "INFEASIBLE":
        return ExactSolveResult(pd.DataFrame(), budget, 0.0, "INFEASIBLE", 0, 0, {}, ["stage1_infeasible"])
    if status1 == "ERROR":
        return ExactSolveResult(pd.DataFrame(), budget, 0.0, "ERROR", 0, 0, {}, ["solver_error"])

    fix_start = pulp.value(expr1["start"])
    prob2, y2, expr2 = _build_model(pool, budget, n_auction_spots, fix_starting=fix_start, stage=2)
    _solve_stage(prob2)
    fix_bench = pulp.value(expr2["bench"]) if pulp.LpStatus[prob2.status] == "Optimal" else 0.0

    prob3, y3, _ = _build_model(
        pool, budget, n_auction_spots, fix_starting=fix_start, fix_bench=fix_bench, stage=3,
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
