"""Phase 3D item 5's HISTORICAL_LEAGUE_PRICE input: this league's own
real, observed 2025 salaries (data/historical_salaries_2025_raw.csv),
rescaled to this year's live-auction budget scale so last year's dollar
figures are comparable to this year's prices.

DISCLOSED SIMPLIFICATION: only 191 (player, salary) rows exist for last
season, matched to this year's pool by exact player name -- players who
changed teams, were cut, or are new to the league (rookies, waiver
adds) have no historical match and get historical_anchor_value=None (not
a fabricated $0 or league-average guess). The single rescale factor
(this year's live-auction budget total / matched players' total 2025
salary) corrects for the overall budget/keeper landscape changing
year-over-year; it does NOT attempt to model any individual player's
year-over-year value change (e.g. a breakout or decline) -- that is
exactly what the projection-based neutral value component of
base_market_anchor is for.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent.parent


def build_historical_league_anchor(players: dict, live_budget_total: float) -> pd.DataFrame:
    """players: {name: Player}. Returns a DataFrame with player, position,
    salary_2025 (raw), historical_anchor_value (rescaled), matched (bool)."""
    hist_path = BASE_DIR / "data" / "historical_salaries_2025_raw.csv"
    hist = pd.read_csv(hist_path) if hist_path.exists() else pd.DataFrame(columns=["player", "salary_2025"])
    hist = hist.dropna(subset=["salary_2025"]).drop_duplicates("player", keep="first")
    hist_lookup = hist.set_index("player")["salary_2025"].to_dict()

    matched_total = sum(hist_lookup[n] for n in players if n in hist_lookup)
    rescale = live_budget_total / matched_total if matched_total else 1.0

    rows = []
    for name, player in players.items():
        salary = hist_lookup.get(name)
        rows.append({
            "player": name, "position": player.position,
            "salary_2025": salary,
            "historical_anchor_value": round(salary * rescale, 2) if salary is not None else None,
            "matched": salary is not None,
        })
    df = pd.DataFrame(rows)
    df.attrs["matched_total_2025_salary"] = matched_total
    df.attrs["live_budget_total"] = live_budget_total
    df.attrs["rescale_factor"] = rescale
    df.attrs["n_matched"] = int(df["matched"].sum())
    df.attrs["n_total"] = len(df)
    return df
