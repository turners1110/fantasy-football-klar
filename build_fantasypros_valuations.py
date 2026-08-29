#!/usr/bin/env python3
"""Two rank-only auction valuations from FantasyPros ECR, independent of any
point-projection source (Yahoo). FantasyPros' file has ranks/tiers, never
points, so neither method scores a fabricated point total -- both convert
rank directly into a dollar share of the same remaining live-auction budget
the rest of the pipeline uses.

Model 1 -- rank_curve: overall ECR rank -> dollars via a standard auction
  rank-decay curve (1/rank**RANK_DECAY_EXPONENT), the common "rank-to-value"
  technique reference sheets use.
Model 2 -- vbd_position_rank: position rank (RB1, RB2, ...) treated as a
  pseudo-points scale, run through this league's actual VBD replacement-rank
  math (auction_model.config.replacement_rank) instead of a generic curve --
  so the 3-FLEX / no-K-DEF roster shape still drives scarcity the same way
  it does for the point-based models.

Both prices are rescaled to sum to the same `remaining_budget` used
elsewhere and clipped to this league's [$1, $100] price bounds.

CAVEAT -- read before using either column as a bid target: rescaling to
`remaining_budget` only fixes the total dollars in play. The *ordering and
relative spacing* between players still comes from FantasyPros' generic
redraft consensus -- a market with no keepers, no returning rosters, and no
$10/$5 keeper-bump inflation dynamic. This league strips its best players
out via keepers every year (60 keepers, $2,129 off the top this cycle),
which concentrates the live-auction market on a different, thinner set of
players than a standard league ever prices. These two columns are useful
for spotting general consensus/pattern (who's trending up, tier breaks) --
NOT as a substitute for `price_yahoo_forward` in `output/veteran_auction_price_sheet.csv`,
which is the only model actually built on this league's real salary history
and keeper-inflation math. Treat rank_curve / vbd_position_rank as a sanity
check, not a bid sheet.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from auction_model import config, data_pipeline, keepers, valuation

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

RANK_DECAY_EXPONENT = 0.72  # ASSUMPTION: tuned so #1 overall lands near $100 given this league's budget/pool size


def build() -> pd.DataFrame:
    salaries, _ = data_pipeline.load_historical_salaries(DATA_DIR / "historical_salaries_2025_raw.csv")
    overrides = data_pipeline.load_optional_csv(DATA_DIR / "keeper_overrides.csv")
    with_keepers = keepers.apply_keeper_overrides(salaries, overrides)
    with_keepers = keepers.price_keepers(with_keepers)
    inflation = keepers.inflation_summary(with_keepers)
    remaining_budget = inflation["remaining_budget"]

    kept_keys = set(with_keepers.loc[with_keepers["will_keep"], "player"].map(data_pipeline._normalize_name))

    fp = data_pipeline.load_fantasypros_rankings(BASE_DIR / "FantasyPros_2026_Draft_ALL_Rankings.csv")
    fp = fp[~fp["_key"].isin(kept_keys)].copy()

    # --- Model 1: rank_curve ---
    fp["rank_curve_raw"] = 1.0 / (fp["fp_overall_rank"].astype(float) ** RANK_DECAY_EXPONENT)
    fp["price_fp_rank_curve"] = (
        fp["rank_curve_raw"] / fp["rank_curve_raw"].sum() * remaining_budget
    ).clip(lower=config.MIN_PRICE, upper=config.MAX_PRICE).round(0)

    # --- Model 2: vbd_position_rank ---
    fp["fp_position_rank"] = pd.to_numeric(fp["fp_position_rank"], errors="coerce")
    fp["replacement_rank"] = fp["position"].map(lambda p: config.replacement_rank(p) if p in ("QB", "RB", "WR", "TE") else np.nan)
    fp["vbd_proxy"] = fp["replacement_rank"] - fp["fp_position_rank"]
    fp.loc[fp["vbd_proxy"] < 0, "vbd_proxy"] = 0.0
    fp["price_fp_vbd_position_rank"] = valuation._proportional_dollars(fp["vbd_proxy"], remaining_budget)
    fp["price_fp_vbd_position_rank"] = fp["price_fp_vbd_position_rank"].clip(
        lower=config.MIN_PRICE, upper=config.MAX_PRICE
    ).round(0)
    # Only players priced 0 dollars in the proportional split are truly
    # below replacement -- leave them unpriced rather than floor them at $1.
    fp.loc[fp["vbd_proxy"] <= 0, "price_fp_vbd_position_rank"] = np.nan

    out = fp[[
        "player", "position", "nfl_team", "fp_overall_rank", "fp_position_rank", "fp_tier",
        "price_fp_rank_curve", "price_fp_vbd_position_rank",
    ]].rename(columns={
        "price_fp_rank_curve": "price_fp_rank_curve_REFERENCE_ONLY",
        "price_fp_vbd_position_rank": "price_fp_vbd_position_rank_REFERENCE_ONLY",
    })
    return out


def main() -> None:
    out = build()
    out_path = DATA_DIR.parent / "output" / "fantasypros_rank_valuations.csv"
    out.sort_values("fp_overall_rank").to_csv(out_path, index=False)
    print(f"Wrote {out_path} ({len(out)} players)")
    print(out.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
