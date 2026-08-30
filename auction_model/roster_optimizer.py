"""Joint Sam roster optimizer: keepers + auction fill + lineup assignment."""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config, data_pipeline, exact_roster_solver, keepers, market_engine

FLEX_ELIG = frozenset({"RB", "WR", "TE"})
STARTER_REQ = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 3}


@dataclass
class LineupResult:
    starting_points: float
    bench_points: float
    total_points: float
    starting_lineup: dict[str, str]
    bench: list[str]
    roles: dict[str, str]


@dataclass
class PortfolioResult:
    keeper_count: int
    keepers: list[str]
    tagged_player: str | None
    keeper_spend: float
    auction_budget: float
    auction_players: list[str]
    all_players: list[str]
    lineup: LineupResult
    unused_cash: float
    auction_spend: float
    objective_value: float
    solver_status: str
    warnings: list[str] = field(default_factory=list)
    cache_key: str = ""
    auction_eligibility_valid: bool = True
    greedy_starting_points: float | None = None
    exact_starting_points: float | None = None


@dataclass
class RosterDelta:
    players_added: list[str]
    players_removed: list[str]
    starting_lineup_changes: list[tuple[str, str, str]]  # role, old, new
    points_delta_by_position: dict[str, float]
    spend_delta_by_position: dict[str, float]
    total_starting_points_change: float
    total_bench_points_change: float
    unused_cash_change: float


_CACHE: dict[str, PortfolioResult] = {}
_DEBUG_ROWS: list[dict] = []
_ELIGIBILITY_AUDIT: pd.DataFrame | None = None


def set_eligibility_audit(audit: pd.DataFrame | None) -> None:
    global _ELIGIBILITY_AUDIT
    _ELIGIBILITY_AUDIT = audit


def clear_caches() -> None:
    _CACHE.clear()
    _DEBUG_ROWS.clear()


def debug_rows() -> pd.DataFrame:
    return pd.DataFrame(_DEBUG_ROWS)


def _cache_key(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:20]


def assign_lineup(players: pd.DataFrame) -> LineupResult:
    """Best starting lineup + bench from a player set (exact assignment)."""
    df = players.dropna(subset=["projected_points"]).copy()
    df = df.sort_values("projected_points", ascending=False)
    if df.empty:
        return LineupResult(0, 0, 0, {}, [], {})

    used: set[str] = set()
    roles: dict[str, str] = {}
    start_names: dict[str, str] = {}
    start_pts = 0.0

    for pos, n in [("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1)]:
        pos_df = df[(df["position"] == pos) & (~df["player"].isin(used))]
        for i, (_, row) in enumerate(pos_df.head(n).iterrows()):
            used.add(row["player"])
            role = f"{pos}_START_{i+1}" if n > 1 else f"{pos}_START"
            roles[row["player"]] = role
            start_names[role] = row["player"]
            start_pts += float(row["projected_points"])

    flex_df = df[(df["position"].isin(FLEX_ELIG)) & (~df["player"].isin(used))]
    for i, (_, row) in enumerate(flex_df.head(3).iterrows()):
        used.add(row["player"])
        role = f"FLEX_{i+1}"
        roles[row["player"]] = role
        start_names[role] = row["player"]
        start_pts += float(row["projected_points"])

    bench = [p for p in df["player"] if p not in used]
    bench_pts = float(df[df["player"].isin(bench)]["projected_points"].sum())
    for i, p in enumerate(bench):
        roles[p] = f"BENCH_{i+1}"

    total = start_pts + bench_pts
    return LineupResult(
        round(start_pts, 2), round(bench_pts, 2), round(total, 2),
        start_names, bench, roles,
    )


def _objective(lineup: LineupResult, unused_cash: float, keeper_spend: float) -> float:
    return round(
        lineup.starting_points
        + config.ROSTER_BENCH_WEIGHT * lineup.bench_points
        + config.ROSTER_UNUSED_CASH_WEIGHT * unused_cash
        - 0.005 * keeper_spend,
        4,
    )


def _player_row(name: str, position: str, pts: float, price: float, source: str) -> dict:
    return {
        "player": name, "position": position,
        "projected_points": pts, "suggested_auction_price": price,
        "acquisition_type": source,
    }


def solve_auction_roster_greedy(
    candidates: pd.DataFrame,
    budget: float,
    n_spots: int,
    exclude: set[str] | None = None,
) -> tuple[pd.DataFrame, float, float]:
    """Select ``n_spots`` auction players maximizing lineup objective under budget."""
    exclude = exclude or set()
    if n_spots <= 0:
        return pd.DataFrame(), budget, 0.0

    pool = candidates[
        (~candidates["player"].isin(exclude))
        & candidates["projected_points"].notna()
        & candidates["suggested_auction_price"].notna()
    ].copy()
    if pool.empty:
        return pd.DataFrame(), budget, 0.0

    pool["price"] = pool["suggested_auction_price"].clip(lower=config.MIN_PRICE)
    # Include cheap depth — top-by-points alone cannot fill 15 spots under $400
    by_pts = pool.sort_values("projected_points", ascending=False).head(80)
    by_cheap = pool.sort_values(["price", "projected_points"], ascending=[True, False]).head(80)
    pool = pd.concat([by_pts, by_cheap]).drop_duplicates("player")

    picked: list[dict] = []
    spent = 0.0
    pos_counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0}
    names_picked: set[str] = set()

    def can_add(row, reserve_remaining: bool = True) -> bool:
        price = float(row["price"])
        slots_after = n_spots - len(picked) - 1
        min_needed = (slots_after * config.MIN_PRICE) if reserve_remaining else 0
        if spent + price + min_needed > budget:
            return False
        pos = row["position"]
        if pos == "QB" and pos_counts["QB"] >= 2:
            return False
        return True

    mins = [("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1)]
    for pos, need in mins:
        pos_pool = pool[(pool["position"] == pos) & (~pool["player"].isin(names_picked))]
        pos_pool = pos_pool.sort_values("projected_points", ascending=False)
        for _, row in pos_pool.iterrows():
            if pos_counts[pos] >= need or len(picked) >= n_spots:
                break
            if can_add(row):
                picked.append(row.to_dict())
                names_picked.add(row["player"])
                spent += float(row["price"])
                pos_counts[pos] += 1

    remaining = pool[~pool["player"].isin(names_picked)].sort_values("projected_points", ascending=False)
    for _, row in remaining.iterrows():
        if len(picked) >= n_spots:
            break
        if can_add(row):
            picked.append(row.to_dict())
            names_picked.add(row["player"])
            spent += float(row["price"])
            pos_counts[str(row["position"])] = pos_counts.get(str(row["position"]), 0) + 1

    # Fill any remaining spots with cheapest eligible players ($1 minimum bids)
    cheap = pool[~pool["player"].isin(names_picked)].sort_values(
        ["price", "projected_points"], ascending=[True, False]
    )
    for _, row in cheap.iterrows():
        if len(picked) >= n_spots:
            break
        if can_add(row, reserve_remaining=False):
            picked.append(row.to_dict())
            names_picked.add(row["player"])
            spent += float(row["price"])

    if picked:
        best_df = pd.DataFrame(picked)
        spent = float(best_df["price"].sum())
        unused = budget - spent
        return best_df, unused, spent

    return pd.DataFrame(), budget, 0.0


def solve_auction_roster(
    candidates: pd.DataFrame,
    budget: float,
    n_spots: int,
    exclude: set[str] | None = None,
    keepers_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, float, float, str, dict[str, str]]:
    """Exact roster solve; returns (auction_picks, unused, spent, status, roles)."""
    if config.FINAL_SOLVER_MODE == "greedy_diagnostic":
        df, unused, spent = solve_auction_roster_greedy(candidates, budget, n_spots, exclude)
        return df, unused, spent, "GREEDY_APPROXIMATION", {}

    exact = exact_roster_solver.solve_exact_roster(
        candidates, budget, n_spots, keepers=keepers_df or pd.DataFrame(), exclude=exclude,
    )
    if exact.status == "INFEASIBLE" and n_spots > 0:
        return pd.DataFrame(), budget, 0.0, "INFEASIBLE", {}

    if exact.selected.empty:
        return pd.DataFrame(), budget, 0.0, exact.status, {}

    is_k = exact.selected.get("is_keeper", pd.Series(False, index=exact.selected.index))
    auction_only = exact.selected[~is_k.astype(bool)] if is_k.any() else exact.selected
    return auction_only, exact.unused_cash, exact.spent, exact.status, exact.role_assignments


def _sam_candidates(roster: pd.DataFrame, team: str) -> pd.DataFrame:
    return roster[(roster["team"] == team) & roster["salary_2025"].notna()].copy()


def _viable_keeper_indices(candidates: pd.DataFrame, max_candidates: int = 10) -> list:
    """Limit combinatorial search to top keeper candidates by value signal."""
    df = candidates.copy()
    if "depleted_market_alpha" in df.columns:
        df = df.sort_values("depleted_market_alpha", ascending=False, na_position="last")
    elif "projected_points" in df.columns:
        df = df.sort_values("projected_points", ascending=False, na_position="last")
    else:
        df = df.sort_values("salary_2025")
    return list(df.head(max_candidates).index)


def _build_owned_df(
    keeper_rows: pd.DataFrame,
    auction_rows: pd.DataFrame,
) -> pd.DataFrame:
    parts = []
    if not keeper_rows.empty:
        k = keeper_rows.copy()
        k["acquisition_type"] = "keeper"
        k["suggested_auction_price"] = k["keeper_price_2026"]
        parts.append(k)
    if not auction_rows.empty:
        a = auction_rows.copy()
        a["acquisition_type"] = "auction"
        parts.append(a)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True)
    for col in ("player", "position", "projected_points", "suggested_auction_price"):
        if col not in out.columns:
            out[col] = np.nan
    return out


def evaluate_portfolio(
    team: str,
    keeper_names: list[str],
    tag_name: str | None,
    roster: pd.DataFrame,
    auction_pool: pd.DataFrame,
    scenario_id: str = "base",
    forced_context: str = "",
) -> PortfolioResult:
    """Evaluate one keeper portfolio + optimized auction fill."""
    k_count = len(keeper_names)
    team_roster = roster[roster["team"] == team].copy()
    tag_name = tag_name if tag_name in keeper_names else None

    alt = roster.copy()
    alt["will_keep"] = False
    alt["tag_used"] = False
    for _, row in team_roster.iterrows():
        if row["player"] in keeper_names:
            idx = row.name
            alt.loc[idx, "will_keep"] = True
            if row["player"] == tag_name:
                alt.loc[idx, "tag_used"] = True
    alt = keepers.price_keepers(alt)

    team_keepers = alt[(alt["team"] == team) & alt["will_keep"].astype(bool)].copy()
    if not team_keepers.empty:
        proj_lookup = auction_pool.drop_duplicates("player").set_index("player")["projected_points"]
        for idx, row in team_keepers.iterrows():
            if pd.isna(row.get("projected_points")):
                p = proj_lookup.get(row["player"])
                if pd.notna(p):
                    team_keepers.at[idx, "projected_points"] = p
                elif pd.notna(row.get("salary_2025")):
                    team_keepers.at[idx, "projected_points"] = 50.0  # conservative fallback for DATA_BLOCKED
    keeper_spend = float(team_keepers["keeper_price_2026"].sum())
    if keeper_spend > config.BUDGET_PER_TEAM:
        return PortfolioResult(
            k_count, keeper_names, tag_name, keeper_spend, 0, [], [], [],
            LineupResult(0, 0, 0, {}, [], {}), 0, 0, -1e9, "infeasible_budget", ["keeper spend exceeds $400"],
        )

    auction_budget = config.BUDGET_PER_TEAM - keeper_spend
    n_spots = config.AUCTION_PURCHASE_REQUIREMENT - len(team_keepers)
    kept = set(team_keepers["player"])

    cache_payload = {
        "scenario": scenario_id, "context": forced_context,
        "keepers": sorted(keeper_names), "tag": tag_name,
        "budget": auction_budget, "n_spots": n_spots,
        "pool_hash": _cache_key({"n": len(auction_pool)}),
    }
    ck = _cache_key(cache_payload)
    if ck in _CACHE:
        return _CACHE[ck]

    exact_result = exact_roster_solver.solve_exact_roster(
        auction_pool, auction_budget, n_spots, keepers=team_keepers, exclude=kept,
    ) if config.FINAL_SOLVER_MODE != "greedy_diagnostic" else None

    if config.FINAL_SOLVER_MODE == "greedy_diagnostic":
        auction_picks, unused, auction_spend = solve_auction_roster_greedy(
            auction_pool, auction_budget, n_spots, exclude=kept,
        )
        status, role_map = "GREEDY_APPROXIMATION", {}
    elif exact_result and exact_result.status != "INFEASIBLE":
        is_k = exact_result.selected.get("is_keeper", pd.Series(False, index=exact_result.selected.index))
        auction_picks = exact_result.selected[~is_k.astype(bool)] if not exact_result.selected.empty else pd.DataFrame()
        unused, auction_spend, status, role_map = (
            exact_result.unused_cash, exact_result.spent, exact_result.status, exact_result.role_assignments,
        )
    else:
        auction_picks, unused, auction_spend, status, role_map = (
            pd.DataFrame(), auction_budget, 0.0, "INFEASIBLE", {},
        )

    greedy_picks, _, _ = solve_auction_roster_greedy(auction_pool, auction_budget, n_spots, exclude=kept)
    greedy_owned = _build_owned_df(team_keepers, greedy_picks)
    greedy_lineup = assign_lineup(greedy_owned) if not greedy_owned.empty else LineupResult(0, 0, 0, {}, [], {})

    owned = _build_owned_df(team_keepers, auction_picks)
    warnings: list[str] = []
    eligibility_valid = True

    if _ELIGIBILITY_AUDIT is not None and not owned.empty:
        from . import auction_eligibility
        ok, bad = auction_eligibility.assert_roster_eligibility(owned["player"].tolist(), _ELIGIBILITY_AUDIT)
        if not ok:
            eligibility_valid = False
            warnings.extend(bad)
            status = "ERROR"

    if len(owned) < config.AUCTION_PURCHASE_REQUIREMENT and n_spots > 0:
        if status not in {"ERROR", "INFEASIBLE"}:
            status = "partial_roster"
        warnings.append(f"filled {len(owned)}/{config.AUCTION_PURCHASE_REQUIREMENT} active spots")

    if exact_result and status == "OPTIMAL":
        assertion_failures = exact_roster_solver.post_solve_assertions(exact_result, auction_budget)
        if assertion_failures:
            warnings.extend(assertion_failures)
            status = "ERROR"
            eligibility_valid = False

    lineup = assign_lineup(owned)
    if exact_result and exact_result.status == "OPTIMAL" and role_map:
        start_names = {r: p for p, r in role_map.items() if not str(r).startswith("BENCH")}
        bench_names = [p for p, r in role_map.items() if str(r).startswith("BENCH")]
        lineup = LineupResult(
            exact_result.starting_points, exact_result.bench_points,
            exact_result.starting_points + exact_result.bench_points,
            start_names, bench_names, role_map,
        )

    obj = _objective(lineup, unused, keeper_spend)

    result = PortfolioResult(
        keeper_count=k_count,
        keepers=list(keeper_names),
        tagged_player=tag_name,
        keeper_spend=round(keeper_spend, 2),
        auction_budget=round(auction_budget, 2),
        auction_players=auction_picks["player"].tolist() if not auction_picks.empty else [],
        all_players=owned["player"].tolist() if not owned.empty else list(keeper_names),
        lineup=lineup,
        unused_cash=round(unused, 2),
        auction_spend=round(auction_spend, 2),
        objective_value=obj,
        solver_status=status,
        warnings=warnings,
        cache_key=ck,
        auction_eligibility_valid=eligibility_valid,
        greedy_starting_points=greedy_lineup.starting_points,
        exact_starting_points=lineup.starting_points,
    )
    _CACHE[ck] = result

    _DEBUG_ROWS.append({
        "scenario_id": scenario_id,
        "forced_context": forced_context,
        "keeper_set": keeper_names,
        "tag": tag_name,
        "keeper_spend": keeper_spend,
        "auction_budget": auction_budget,
        "open_slots": n_spots,
        "auction_players": result.auction_players,
        "starting_lineup": result.lineup.starting_lineup,
        "bench": result.lineup.bench,
        "starting_points": result.lineup.starting_points,
        "bench_points": result.lineup.bench_points,
        "unused_cash": unused,
        "solver_status": status,
        "cache_key": ck,
        "cache_hit": False,
    })
    return result


def solve_portfolios_0_to_6(
    team: str,
    roster: pd.DataFrame,
    auction_pool: pd.DataFrame,
    max_keepers: int | None = None,
) -> list[PortfolioResult]:
    """Jointly solve Sam portfolios for keeper counts 0..6."""
    max_keepers = max_keepers or config.MAX_KEEPERS_PER_TEAM
    candidates = _sam_candidates(roster, team)
    indices = _viable_keeper_indices(candidates)
    # Always include any forced players from full candidate list
    all_indices = list(candidates.index)
    results: list[PortfolioResult] = []

    for k in range(0, min(max_keepers, len(indices)) + 1):
        best: PortfolioResult | None = None
        for combo in itertools.combinations(indices, k):
            names = [candidates.loc[i, "player"] for i in combo]
            res = evaluate_portfolio(team, names, None, roster, auction_pool, f"portfolio_{k}")
            if best is None or res.objective_value > best.objective_value:
                best = res
        if best:
            results.append(best)
    return results


def compute_roster_delta(keep: PortfolioResult, release: PortfolioResult) -> RosterDelta:
    keep_set = set(keep.all_players)
    rel_set = set(release.all_players)
    added = sorted(rel_set - keep_set)
    removed = sorted(keep_set - rel_set)

    sl_changes = []
    for role in set(keep.lineup.starting_lineup) | set(release.lineup.starting_lineup):
        old = keep.lineup.starting_lineup.get(role, "")
        new = release.lineup.starting_lineup.get(role, "")
        if old != new:
            sl_changes.append((role, old, new))

    return RosterDelta(
        players_added=added,
        players_removed=removed,
        starting_lineup_changes=sl_changes,
        points_delta_by_position={},
        spend_delta_by_position={},
        total_starting_points_change=round(
            keep.lineup.starting_points - release.lineup.starting_points, 2
        ),
        total_bench_points_change=round(
            keep.lineup.bench_points - release.lineup.bench_points, 2
        ),
        unused_cash_change=round(keep.unused_cash - release.unused_cash, 2),
    )


def compare_keep_vs_release(
    team: str,
    player_name: str,
    roster: pd.DataFrame,
    auction_pool: pd.DataFrame,
    scenario_id: str = "keep_vs_release",
) -> dict:
    """Compare optimal portfolios forcing keep vs forcing release of one player."""
    candidates = _sam_candidates(roster, team)
    if player_name not in set(candidates["player"]):
        return {"player": player_name, "error": "not on roster"}

    # Best portfolio with player kept (any count)
    candidates = _sam_candidates(roster, team)
    viable = _viable_keeper_indices(candidates)
    player_idx = candidates[candidates["player"] == player_name].index[0]

    best_keep: PortfolioResult | None = None
    others = [i for i in viable if i != player_idx]
    for k in range(1, min(config.MAX_KEEPERS_PER_TEAM, len(others) + 1)):
        extra = k - 1
        if extra == 0:
            combos = [()]
        else:
            combos = itertools.combinations(others, extra)
        for combo in combos:
            idxs = [player_idx] + list(combo)
            names = [candidates.loc[i, "player"] for i in idxs]
            res = evaluate_portfolio(
                team, names, None, roster, auction_pool,
                scenario_id, f"force_keep_{player_name}",
            )
            if best_keep is None or res.objective_value > best_keep.objective_value:
                best_keep = res

    best_rel: PortfolioResult | None = None
    rel_pool = [i for i in viable if i != player_idx]
    for k in range(0, min(config.MAX_KEEPERS_PER_TEAM, len(rel_pool)) + 1):
        for combo in itertools.combinations(rel_pool, k):
            names = [candidates.loc[i, "player"] for i in combo]
            res = evaluate_portfolio(
                team, names, None, roster, auction_pool,
                scenario_id, f"force_release_{player_name}",
            )
            if best_rel is None or res.objective_value > best_rel.objective_value:
                best_rel = res

    if best_keep is None or best_rel is None:
        return {"player": player_name, "error": "could not solve"}

    delta = compute_roster_delta(best_keep, best_rel)
    row = candidates[candidates["player"] == player_name].iloc[0]
    std_cost = keepers.keeper_price(
        row["salary_2025"], False, bool(row.get("paul_rule_eligible", False))
    )

    roster_gain = best_keep.objective_value - best_rel.objective_value

    return {
        "player": player_name,
        "standard_keeper_cost": std_cost,
        "projected_starting_points_if_kept": best_keep.lineup.starting_points,
        "projected_starting_points_if_released": best_rel.lineup.starting_points,
        "projected_bench_points_if_kept": best_keep.lineup.bench_points,
        "projected_bench_points_if_released": best_rel.lineup.bench_points,
        "points_gained_from_keep": round(
            best_keep.lineup.starting_points - best_rel.lineup.starting_points, 2
        ),
        "roster_value_gained_from_keep": round(roster_gain, 2),
        "final_keep_score": round(roster_gain, 2),
        "auction_budget_if_kept": best_keep.auction_budget,
        "auction_budget_if_released": best_rel.auction_budget,
        "unused_cash_if_kept": best_keep.unused_cash,
        "unused_cash_if_released": best_rel.unused_cash,
        "best_roster_if_kept": best_keep.all_players,
        "best_roster_if_released": best_rel.all_players,
        "keepers_if_kept": best_keep.keepers,
        "keepers_if_released": best_rel.keepers,
        "players_added_if_released": delta.players_added,
        "players_removed_if_released": delta.players_removed,
        "starting_lineup_changes": delta.starting_lineup_changes,
        "keep_cache_key": best_keep.cache_key,
        "release_cache_key": best_rel.cache_key,
    }


def portfolios_to_dataframe(results: list[PortfolioResult]) -> pd.DataFrame:
    rows = []
    for r in results:
        rows.append({
            "keeper_count": r.keeper_count,
            "selected_keepers": ", ".join(r.keepers),
            "tagged_player": r.tagged_player or "",
            "keeper_spend": r.keeper_spend,
            "auction_budget": r.auction_budget,
            "open_active_roster_spots": config.AUCTION_PURCHASE_REQUIREMENT - r.keeper_count,
            "auction_players": ", ".join(r.auction_players),
            "starting_lineup": str(r.lineup.starting_lineup),
            "bench": ", ".join(r.lineup.bench),
            "starting_points": r.lineup.starting_points,
            "bench_points": r.lineup.bench_points,
            "unused_cash": r.unused_cash,
            "objective_value": r.objective_value,
            "solver_status": r.solver_status,
            "warnings": "; ".join(r.warnings),
        })
    return pd.DataFrame(rows)
