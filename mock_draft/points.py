"""Projected-points lookup for every player who might end up on a roster
(auction pool AND keepers) -- the actual optimization objective here is
"most projected points on a 15-man roster," not dollars.

`veteran_auction_price_sheet.csv` only carries projected_points for ~66%
of the auction pool (whatever run_valuation.py's own name-matching found),
and keepers aren't in that file at all (they're excluded from the
non-keeper pool by definition). This re-derives points directly from
data/projections_2026.csv for anyone missing, using the same scoring
function the real model uses, so every rostered player -- keeper or
auction pick -- has a comparable points figure.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from auction_model import config as auction_config  # noqa: E402

BASE_DIR = Path(__file__).parent.parent


def _normalize_name(name: str) -> str:
    name = re.sub(r"[.'’]", "", str(name))
    name = re.sub(r"\s+(Jr|Sr|II|III|IV)\.?$", "", name, flags=re.IGNORECASE)
    return name.strip().lower()


def build_points_lookup(projections_path: Path = BASE_DIR / "data" / "projections_2026.csv") -> dict[str, float]:
    proj = pd.read_csv(projections_path)
    proj = proj.dropna(subset=["player"])

    def _points(row) -> float:
        if pd.notna(row.get("projected_points")):
            return float(row["projected_points"])
        return auction_config.score_from_stats(row.to_dict())

    proj["_points"] = proj.apply(_points, axis=1)
    proj["_key"] = proj["player"].map(_normalize_name)
    # If a name appears twice (shouldn't after the merge script's dedupe,
    # but be defensive), keep the higher point total rather than guessing.
    proj = proj.sort_values("_points", ascending=False).drop_duplicates("_key")
    return dict(zip(proj["_key"], proj["_points"]))


def points_for(name: str, lookup: dict[str, float], fallback_per_dollar: float, base_value: float) -> tuple[float, bool]:
    """Return (points, is_real). Falls back to base_value * a league-wide
    points-per-dollar ratio (computed from players where both are known)
    only when no real projection exists at all -- flagged, never silent."""
    key = _normalize_name(name)
    if key in lookup:
        return lookup[key], True
    return max(0.0, base_value) * fallback_per_dollar, False


def compute_fallback_ratio(pool_df: pd.DataFrame, lookup: dict[str, float]) -> float:
    """Median points-per-dollar among pool players where both a real
    projection and a real base_value exist."""
    pool_df = pool_df.copy()
    pool_df["_key"] = pool_df["player"].map(_normalize_name)
    pool_df["_points"] = pool_df["_key"].map(lookup)
    known = pool_df.dropna(subset=["_points"])
    known = known[known["base_value"] > 0]
    if known.empty:
        return 1.0
    return float((known["_points"] / known["base_value"]).median())
