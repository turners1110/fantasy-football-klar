"""Identify keeper and auction assets."""

from __future__ import annotations

import pandas as pd

from . import config


def build_asset_board(
    roster: pd.DataFrame,
    cf_audit: pd.DataFrame,
    team_summaries: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    cf = cf_audit.set_index(["team", "player"]) if not cf_audit.empty else pd.DataFrame()

    for _, row in roster.iterrows():
        key = (row["team"], row["player"])
        cf_row = cf.loc[key] if key in cf.index else None
        low = cf_row["depleted_alpha_low"] if cf_row is not None else row.get("depleted_market_alpha", 0)
        exp = cf_row["depleted_alpha_expected"] if cf_row is not None else row.get("depleted_market_alpha", 0)
        margin = config.KEEPER_DECISION_MARGIN

        if row["team"] == config.SAM_TEAM_NAME and exp > margin:
            rows.append(_asset_row("OWNED_KEEPER_ASSET", row, cf_row, low, exp, "Sam positive keeper surplus"))

        if row["team"] != config.SAM_TEAM_NAME and exp > margin:
            ts = team_summaries[team_summaries["team"] == row["team"]]
            squeeze = int(ts["keeper_squeeze"].iloc[0]) if len(ts) else 0
            rank = _owner_rank(row, roster)
            if rank > config.MAX_KEEPERS_PER_TEAM:
                rows.append(_asset_row(
                    "EXCESS_KEEPER_ASSET", row, cf_row, low, exp,
                    f"Owner rank #{rank}, squeeze={squeeze}",
                ))

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("expected_alpha", ascending=False)
    return out


def _owner_rank(row: pd.Series, roster: pd.DataFrame) -> int:
    team = roster[roster["team"] == row["team"]].copy()
    col = "depleted_alpha_expected" if "depleted_alpha_expected" in team.columns else "depleted_market_alpha"
    if col not in team.columns:
        return 999
    ranked = team.sort_values(col, ascending=False).reset_index(drop=True)
    match = ranked[ranked["player"] == row["player"]]
    return int(match.index[0] + 1) if len(match) else 999


def _asset_row(asset_type, row, cf_row, low, exp, reason):
    return {
        "asset_type": asset_type,
        "team": row["team"],
        "player": row["player"],
        "position": row["position"],
        "keeper_cost": row.get("standard_keeper_cost", row.get("selected_keeper_cost")),
        "neutral_value": row.get("neutral_value"),
        "depleted_low": cf_row["released_low_price"] if cf_row is not None else None,
        "depleted_expected": cf_row["released_expected_price"] if cf_row is not None else None,
        "depleted_high": cf_row["released_high_price"] if cf_row is not None else None,
        "conservative_alpha": low,
        "expected_alpha": exp,
        "action": "KEEP" if row["team"] == config.SAM_TEAM_NAME else "TRADE_TARGET",
        "reason": reason,
    }
