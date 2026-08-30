#!/usr/bin/env python3
"""Keeper decision report: cross-references OUR model's keeper alpha
against FantasyPros' market-consensus rank, since real keeper-value
methodology anchors primarily on market consensus and uses a custom
projection model as an adjustment -- not the other way around (see
README changelog for the research this is based on). Where the two
disagree by a lot, that's exactly where you should apply your own
judgment rather than trust either number blindly.

    python keeper_decision_report.py

Also surfaces trade candidates: other teams' players with a big surplus
(good value at their keeper cost, or good value if drafted) who AREN'T
already flagged as one of their own team's best-6 keepers -- i.e.
plausibly available in a trade -- and your own roster's clearest
trade-away candidates (real overpays by both signals).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from auction_model import config, data_pipeline
from draft_ui import engine

BASE_DIR = Path(__file__).parent
MY_TEAM = engine.MY_TEAM
DISAGREEMENT_FLAG_THRESHOLD = 15  # dollars


def compute_fp_consensus_price(model_board: pd.DataFrame) -> pd.DataFrame:
    """FantasyPros rank -> dollar curve for EVERY player (kept included) --
    unlike build_fantasypros_valuations.py, which deliberately excludes
    kept players since it's pricing the live draftable pool. Here we want
    a consensus estimate for kept players too, to check keeper decisions.

    IMPORTANT: an earlier version of this used an untested decay exponent
    (0.72) and got a curve on a totally different SCALE from our backtested
    model -- 58 of 191 players "disagreed," almost all in the same
    direction, which is a calibration artifact, not real per-player signal.
    Fixed by calibrating the exponent so this curve's own top-12/top-36
    budget share matches our model's (both already checked against real
    2025 spending in backtest_2025.py) -- a fair comparison needs both
    curves on the same scale, not just the same total budget.
    """
    fp = data_pipeline.load_fantasypros_rankings(BASE_DIR / "FantasyPros_2026_Draft_ALL_Rankings.csv")

    target_top12_share = (
        model_board.nlargest(12, "model_value")["model_value"].sum() / model_board["model_value"].sum()
    )

    def price_with_power(power: float) -> pd.Series:
        raw = 1.0 / (fp["fp_overall_rank"].astype(float) ** power)
        return (raw / raw.sum() * config.TOTAL_LEAGUE_BUDGET).clip(
            lower=config.MIN_PRICE, upper=config.MAX_PRICE
        )

    best_power, best_gap = 0.72, float("inf")
    for power in np.arange(0.5, 2.01, 0.05):
        priced = price_with_power(power)
        top12_share = priced.nlargest(12).sum() / priced.sum()
        gap = abs(top12_share - target_top12_share)
        if gap < best_gap:
            best_power, best_gap = power, gap

    fp["fp_consensus_price"] = price_with_power(best_power).round(0)
    print(f"(FantasyPros consensus curve calibrated to power={best_power:.2f} to match our model's top-12 budget share)")
    return fp[["_key", "fp_overall_rank", "fp_consensus_price"]]


def main() -> None:
    rows = engine.compute_roster_board()
    board = pd.DataFrame(rows)
    board["_key"] = board["player"].map(data_pipeline._normalize_name)

    fp_consensus = compute_fp_consensus_price(board)
    board = board.merge(fp_consensus, on="_key", how="left")

    board["disagreement"] = board["model_value"] - board["fp_consensus_price"]
    board["needs_review"] = board["disagreement"].abs() >= DISAGREEMENT_FLAG_THRESHOLD

    # Consensus-based alpha, the same surplus concept but anchored to
    # market rank instead of our own model -- a second opinion, not a
    # replacement.
    board["consensus_alpha"] = board["fp_consensus_price"] - board["cost_to_keep_2026"]

    board.to_csv(BASE_DIR / "output" / "keeper_decision_report.csv", index=False)

    print(f"=== {MY_TEAM}'s roster: keep decisions ===")
    mine = board[board["team"] == MY_TEAM].sort_values("neutral_alpha", ascending=False)
    for _, r in mine.iterrows():
        flag = " <-- REVIEW (model vs consensus disagree)" if r["needs_review"] else ""
        rec = "KEEP" if (r["neutral_alpha"] or -999) >= 0 or (r["consensus_alpha"] or -999) >= 0 else "CONSIDER CUTTING"
        print(
            f"  {r['player']:22s} {r['position']:3s} cost=${r['cost_to_keep_2026']:.0f}  "
            f"our_model=${r['model_value']:.0f} (alpha {r['neutral_alpha']:+.0f})  "
            f"consensus=${r['fp_consensus_price']:.0f} (alpha {r['consensus_alpha']:+.0f})  "
            f"[{rec}]{flag}"
        )

    print(f"\n=== {MY_TEAM}'s clearest trade-away candidates (both signals say overpay) ===")
    tradeaway = mine[(mine["neutral_alpha"] < -5) & (mine["consensus_alpha"] < -5)]
    if tradeaway.empty:
        print("  None -- no player is a clear overpay by both signals.")
    else:
        for _, r in tradeaway.iterrows():
            print(f"  {r['player']} ({r['position']}): cost ${r['cost_to_keep_2026']:.0f} vs "
                  f"our ${r['model_value']:.0f} / consensus ${r['fp_consensus_price']:.0f}")

    print("\n=== Biggest-surplus players on other rosters (trade targets) ===")
    print("(marked [locked] if the model expects that team to keep them anyway -- still worth")
    print(" offering on, but a real sell requires giving up real value; unmarked ones are more")
    print(" plausibly gettable since the model doesn't even expect their own team to keep them)")
    others = board[board["team"] != MY_TEAM]
    targets = others[(others["neutral_alpha"] > 5) & (others["consensus_alpha"] > 5)].sort_values(
        "neutral_alpha", ascending=False
    )
    if targets.empty:
        print("  None found above the $5 surplus bar on both signals.")
    else:
        for _, r in targets.head(15).iterrows():
            lock_flag = " [locked]" if r["best_n_expected_keeper"] else ""
            print(f"  {r['player']} ({r['position']}, {r['team']}): cost ${r['cost_to_keep_2026']:.0f} vs "
                  f"our ${r['model_value']:.0f} / consensus ${r['fp_consensus_price']:.0f}{lock_flag}")

    n_review = int(board["needs_review"].sum())
    print(f"\n{n_review} of {len(board)} rostered players have a $15+ gap between our model and "
          f"market consensus league-wide -- see output/keeper_decision_report.csv, column "
          f"'needs_review', before trusting any single one blindly.")


if __name__ == "__main__":
    main()
