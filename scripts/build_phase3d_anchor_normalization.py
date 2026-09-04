#!/usr/bin/env python3
"""Phase 3D item 8: normalize the public-anchor hierarchy to this league's
real post-keeper live-auction budget total (REPORTED_BUDGETS_WITH_SAM_OVERRIDE
primary, FORMULA_RECONCILED_BUDGETS sensitivity).

Writes:
  outputs/auction_rebuild/phase3d/anchor_normalization.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from auction_model.anchor_normalization import normalize_anchors_after_keeper_removal
from auction_model.public_anchor import build_public_anchor_hierarchy
from mock_draft.data import load_confirmed_pool_and_teams

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3d"


def main() -> None:
    players, _teams, _ = load_confirmed_pool_and_teams(budget_scenario="primary")
    anchors = build_public_anchor_hierarchy(players)
    normalized = normalize_anchors_after_keeper_removal(anchors)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "anchor_normalization.csv"
    normalized.to_csv(out_path, index=False)

    print(f"Wrote {out_path} ({len(normalized)} players)")
    print(f"  raw anchor total: {normalized.attrs['raw_total']:.2f}")
    print(f"  primary target (REPORTED_BUDGETS_WITH_SAM_OVERRIDE): {normalized.attrs['primary_target_total']:.2f}"
          f" -> post-clip total {normalized.attrs['primary_post_clip_total']:.2f}")
    print(f"  sensitivity target (FORMULA_RECONCILED_BUDGETS): {normalized.attrs['sensitivity_target_total']:.2f}"
          f" -> post-clip total {normalized.attrs['sensitivity_post_clip_total']:.2f}")


if __name__ == "__main__":
    main()
