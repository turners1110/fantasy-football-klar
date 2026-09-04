"""Phase 3D item 8: normalize the item-7 public-anchor hierarchy's dollar
values to this league's REAL post-keeper live-auction budget total, so a
generic external ranking (which prices players as if every keeper were
still on the market) doesn't overstate what's actually available to spend
on the live-auction pool this simulator prices.

DISCLOSURE: the original phase-3D spec described this as a "10-step
procedure" but the exact numbered steps were not available to reconstruct
verbatim in this session -- the 10-step procedure below is this project's
own honest reconstruction of a normalization achieving the same stated
goal (rescale to the real post-keeper budget total; never redistribute
the signed reported-vs-formula budget discrepancy as hidden inflation),
not a literal recovery of lost text. Disclosed rather than silently
presented as verbatim.

The 10 steps:
  1. Start from auction_model.public_anchor's per-player normalized_value
     (item 7's resolved anchor, whichever source tier produced it).
  2. Restrict to the live-auction pool only (players already exclude every
     team's fixed keepers -- load_confirmed_pool_and_teams never returns
     a keeper as a pool player).
  3. Compute the RAW total of those anchor values across the live pool
     (pre-normalization sum).
  4. Load REPORTED_BUDGETS_WITH_SAM_OVERRIDE (this league's own
     team_starting_states.csv primary_auction_budget column) as the
     PRIMARY real post-keeper budget target.
  5. Load FORMULA_RECONCILED_BUDGETS (team_starting_states_formula_reconciled.csv)
     as the SENSITIVITY target -- a second, independently-derived total,
     never averaged into the primary figure.
  6. Compute one scalar rescale factor per scenario: target_total / raw_total.
     A SINGLE multiplicative scalar applied to every player equally -- this
     rescales the aggregate anchor pool to a real total, it does not touch
     the signed net gap BETWEEN individual teams' reported budgets (that
     gap is a team-level bookkeeping question, not a player-pricing one,
     and redistributing it through player prices would be exactly the
     "hidden inflation" this item forbids).
  7. Apply the rescale factor to every player's normalized_value to get
     the keeper-removed anchor for that scenario (primary and sensitivity).
  8. Clip to this league's own $1 price floor (config.MIN_PRICE) after
     rescaling, since a rescale can theoretically push a bottom-of-pool
     player below $1.
  9. Recompute the POST-clip total and report the residual vs. the target
     total directly (the clip floor can pull the achieved total slightly
     above target) -- disclosed, not silently absorbed.
  10. Output both scenarios' rescaled values side by side with the raw
      pre-rescale anchor and both scalar factors, so every number in the
      final figure is traceable back to its source.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import config

BASE_DIR = Path(__file__).parent.parent


def _budget_total(path: Path, column: str = "primary_auction_budget") -> float:
    return float(pd.read_csv(path)[column].sum())


def normalize_anchors_after_keeper_removal(anchor_df: pd.DataFrame) -> pd.DataFrame:
    """anchor_df: output of auction_model.public_anchor.build_public_anchor_hierarchy
    (must have player/normalized_value columns, already keeper-excluded).
    Returns anchor_df with three added columns: keeper_removed_anchor_primary,
    keeper_removed_anchor_sensitivity, and the two scalar rescale factors."""
    raw_total = float(anchor_df["normalized_value"].sum())

    primary_target = _budget_total(BASE_DIR / "outputs" / "auction_rebuild" / "data" / "team_starting_states.csv")
    sensitivity_target = _budget_total(
        BASE_DIR / "outputs" / "auction_rebuild" / "phase3a" / "team_starting_states_formula_reconciled.csv"
    )

    primary_factor = primary_target / raw_total if raw_total else 1.0
    sensitivity_factor = sensitivity_target / raw_total if raw_total else 1.0

    out = anchor_df.copy()
    out["keeper_removed_anchor_primary"] = (out["normalized_value"] * primary_factor).clip(lower=config.MIN_PRICE).round(2)
    out["keeper_removed_anchor_sensitivity"] = (out["normalized_value"] * sensitivity_factor).clip(lower=config.MIN_PRICE).round(2)
    out["rescale_factor_primary_REPORTED_BUDGETS_WITH_SAM_OVERRIDE"] = round(primary_factor, 4)
    out["rescale_factor_sensitivity_FORMULA_RECONCILED_BUDGETS"] = round(sensitivity_factor, 4)
    out.attrs["raw_total"] = raw_total
    out.attrs["primary_target_total"] = primary_target
    out.attrs["sensitivity_target_total"] = sensitivity_target
    out.attrs["primary_post_clip_total"] = float(out["keeper_removed_anchor_primary"].sum())
    out.attrs["sensitivity_post_clip_total"] = float(out["keeper_removed_anchor_sensitivity"].sum())
    return out
