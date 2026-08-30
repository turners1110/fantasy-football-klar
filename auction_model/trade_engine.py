"""Pre-lock trade opportunity engine (Level 1 screen + Level 2 simulation)."""

from __future__ import annotations

import pandas as pd

from . import config, keeper_market


def level1_trade_screen(
    roster: pd.DataFrame,
    sam_team: str | None = None,
) -> pd.DataFrame:
    """Fast screen of trade targets for Sam."""
    sam_team = sam_team or config.SAM_TEAM_NAME
    sam = roster[roster["team"] == sam_team].copy()
    others = roster[roster["team"] != sam_team].copy()

    sam_keepers = sam[sam["will_keep"]].sort_values("depleted_market_alpha", ascending=False)
    sam_limit = config.MAX_KEEPERS_PER_TEAM
    sam_rank = {idx: i + 1 for i, idx in enumerate(sam_keepers.index)}

    rows = []
    for team, team_df in others.groupby("team"):
        team_keepers = team_df[team_df["will_keep"]].sort_values("depleted_market_alpha", ascending=False)
        keeper_rank = {idx: i + 1 for i, idx in enumerate(team_keepers.index)}
        positive = team_df[team_df["depleted_market_alpha"] > 0].sort_values(
            "depleted_market_alpha", ascending=False
        )
        squeeze = max(len(positive) - sam_limit, 0)

        for idx, row in team_df.iterrows():
            rank = keeper_rank.get(idx, 999)
            expected_keep = rank <= sam_limit
            sam_gain = float(row["depleted_market_alpha"]) if row["depleted_market_alpha"] > 0 else 0.0
            owner_loss = float(row["depleted_market_alpha"]) if expected_keep else 0.0
            available = (not expected_keep) and sam_gain > 0 and owner_loss <= sam_gain * 0.5
            rows.append({
                "target_team": team,
                "target_player": row["player"],
                "position": row["position"],
                "keeper_cost": row.get("selected_keeper_cost", row.get("standard_keeper_cost")),
                "neutral_value": row.get("neutral_value"),
                "counterfactual_release_price": row.get("counterfactual_release_price"),
                "depleted_alpha_for_sam": sam_gain,
                "target_owner_keeper_rank": rank if rank < 999 else None,
                "target_owner_expected_to_keep": expected_keep,
                "target_owner_keeper_squeeze": squeeze,
                "target_owner_loss_if_traded": owner_loss if expected_keep else 0.0,
                "sam_portfolio_gain": sam_gain,
                "trade_confidence": "HIGH" if available and row.get("salary_origin", "").endswith("CONFIRMED") else "MEDIUM",
                "why_target_is_available": "Outside owner's projected keeper limit" if not expected_keep else "Owner may still keep",
                "why_target_helps_sam": f"+${sam_gain:.0f} depleted surplus" if sam_gain > 0 else "Limited surplus",
                "main_risk": "Partner may keep anyway" if expected_keep else "Contract data uncertainty",
                "ideal_target": available,
            })

    out = pd.DataFrame(rows)
    out = out.sort_values(["ideal_target", "depleted_alpha_for_sam"], ascending=[False, False])
    out.insert(0, "rank", range(1, len(out) + 1))
    return out


def simulate_trade(
    roster: pd.DataFrame,
    full_pool: pd.DataFrame,
    neutral_value: pd.Series,
    blend_weight: float,
    sam_team: str,
    partner_team: str,
    sam_gives: str,
    partner_gives: str,
) -> dict:
    """Level 2: transfer one-for-one and re-optimize both teams."""
    traded = roster.copy()
    sg = traded[(traded["team"] == sam_team) & (traded["player"] == sam_gives)]
    pg = traded[(traded["team"] == partner_team) & (traded["player"] == partner_gives)]
    if sg.empty or pg.empty:
        return {"error": "player not found"}

    si, pi = sg.index[0], pg.index[0]
    traded.loc[si, "team"] = partner_team
    traded.loc[pi, "team"] = sam_team
    if config.TRADE_SALARY_TRANSFERS_WITH_PLAYER:
        traded.loc[pi, "salary_2025"], traded.loc[si, "salary_2025"] = (
            traded.loc[si, "salary_2025"], traded.loc[pi, "salary_2025"]
        )

    before = keeper_market.iterate_keeper_market(roster, full_pool, neutral_value, blend_weight, max_iterations=3)
    after = keeper_market.iterate_keeper_market(traded, full_pool, neutral_value, blend_weight, max_iterations=3)

    def _team_surplus(res, team):
        t = res.roster[res.roster["team"] == team]
        kept = t[t["will_keep"]]
        return float(kept["depleted_market_alpha"].sum())

    sam_before = _team_surplus(before, sam_team)
    sam_after = _team_surplus(after, sam_team)
    partner_before = _team_surplus(before, partner_team)
    partner_after = _team_surplus(after, partner_team)

    return {
        "sam_keeper_surplus_before": round(sam_before, 2),
        "sam_keeper_surplus_after": round(sam_after, 2),
        "sam_gain": round(sam_after - sam_before, 2),
        "partner_keeper_surplus_before": round(partner_before, 2),
        "partner_keeper_surplus_after": round(partner_after, 2),
        "partner_gain": round(partner_after - partner_before, 2),
        "sam_keeper_set_before": before.roster[(before.roster["team"] == sam_team) & before.roster["will_keep"]]["player"].tolist(),
        "sam_keeper_set_after": after.roster[(after.roster["team"] == sam_team) & after.roster["will_keep"]]["player"].tolist(),
        "trade_rule_assumptions": f"contract_transfer={config.TRADE_CONTRACT_TRANSFERS_WITH_PLAYER}, salary_transfer={config.TRADE_SALARY_TRANSFERS_WITH_PLAYER}",
    }


def build_trade_packages(targets: pd.DataFrame, roster: pd.DataFrame, sam_team: str | None = None) -> pd.DataFrame:
    sam_team = sam_team or config.SAM_TEAM_NAME
    ideal = targets[targets["ideal_target"]].head(15).copy()
    packages = []
    for _, tgt in ideal.iterrows():
        sam_releases = roster[
            (roster["team"] == sam_team)
            & ~roster["will_keep"]
            & (roster["depleted_market_alpha"] < 5)
        ].sort_values("depleted_market_alpha").head(3)["player"].tolist()
        category = "HIGH_PRIORITY" if tgt["depleted_alpha_for_sam"] > 20 else "FAIR_OFFER"
        packages.append({
            "target_team": tgt["target_team"],
            "target_player": tgt["target_player"],
            "sam_gives": sam_releases[0] if sam_releases else "(cash/pick TBD)",
            "sam_receives": tgt["target_player"],
            "sam_portfolio_gain": tgt["sam_portfolio_gain"],
            "partner_incentive": tgt["why_target_is_available"],
            "category": category,
            "trade_confidence": tgt["trade_confidence"],
            "explanation": (
                f"Offer {sam_releases[0] if sam_releases else 'compensation'} for "
                f"{tgt['target_player']} — owner ranks them #{tgt['target_owner_keeper_rank']} "
                f"and may not keep ({tgt['why_target_is_available'].lower()})."
            ),
        })
    out = pd.DataFrame(packages)
    if not out.empty:
        out = out.sort_values("sam_portfolio_gain", ascending=False)
        out.insert(0, "rank", range(1, len(out) + 1))
    return out
