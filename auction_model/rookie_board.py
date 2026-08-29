"""College / rookie draft board -- a separate track from the dollar auction.

Players who haven't debuted in the NFL aren't bid on in the veteran auction;
they go through the mid-season 3-round, non-snake college draft (or a flat
$1 debut fee if a rookie is already rostered/available). This board is
ranked by draft-slot value, not dollars, and is intentionally a separate
output from the auction price sheet.

We have zero prospect data in the historical salary dataset (every player
in it already has an NFL salary, i.e. has already debuted), so this board
is driven entirely by an optional user-supplied input file. Without one,
we emit an empty board plus the template so it's obvious what to fill in.
"""

from __future__ import annotations

import math

import pandas as pd

NUM_ROUNDS = 3
NUM_TEAMS = 12
TOTAL_PICKS = NUM_ROUNDS * NUM_TEAMS

INPUT_COLUMNS = [
    "player", "position", "college_team", "has_debuted",
    "external_rank", "draft_projection_notes",
]

OUTPUT_COLUMNS = [
    "player", "position", "college_team",
    "draft_projection_notes", "suggested_draft_round", "suggested_pick_range",
]


def build_rookie_board(rookie_pool: pd.DataFrame | None) -> pd.DataFrame:
    if rookie_pool is None or rookie_pool.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    df = rookie_pool.copy()
    df["has_debuted"] = df.get("has_debuted", False)
    if df["has_debuted"].dtype == object:
        df["has_debuted"] = df["has_debuted"].astype(str).str.lower().isin(["true", "1", "yes", "y"])

    # Debuted players are auction-eligible (veteran auction, or the $1 flat
    # debut fee), not college-draft-eligible -- exclude them here.
    board = df[~df["has_debuted"]].copy()
    if board.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    if "external_rank" in board.columns and board["external_rank"].notna().any():
        board = board.sort_values("external_rank", na_position="last")
    else:
        board = board.reset_index(drop=True)

    board = board.reset_index(drop=True)
    board["overall_pick"] = board.index + 1
    board["suggested_draft_round"] = board["overall_pick"].apply(
        lambda p: min(NUM_ROUNDS, math.ceil(p / NUM_TEAMS)) if p <= TOTAL_PICKS
        else NUM_ROUNDS + 1  # beyond the 3-round draft: no-draft-list / $1 stash territory
    )

    def pick_range(row):
        rnd = row["suggested_draft_round"]
        if rnd > NUM_ROUNDS:
            return "undrafted (no-draft-list / $1 stash)"
        lo = (rnd - 1) * NUM_TEAMS + 1
        hi = rnd * NUM_TEAMS
        return f"{lo}-{hi}"

    board["suggested_pick_range"] = board.apply(pick_range, axis=1)

    if "draft_projection_notes" not in board.columns:
        board["draft_projection_notes"] = ""
    if "college_team" not in board.columns:
        board["college_team"] = ""

    return board[OUTPUT_COLUMNS]
