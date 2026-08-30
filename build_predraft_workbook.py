#!/usr/bin/env python3
"""Static pre-draft Excel workbook: one row per available player with the
real recommended auction value and your (Sam's) target price at zero picks
made, plus your existing roster and the FantasyPros reference-only columns
for pattern-checking. Re-run any time the underlying data changes.

    python build_predraft_workbook.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from draft_ui import engine

BASE_DIR = Path(__file__).parent
OUTPUT_PATH = BASE_DIR / "output" / "predraft_auction_valuation.xlsx"


def _autofit(writer, sheet_name: str, df: pd.DataFrame) -> None:
    from openpyxl.styles import Font

    ws = writer.sheets[sheet_name]
    ws.freeze_panes = "A2"
    for i, col in enumerate(df.columns, start=1):
        cell_lengths = [len(str(v)) for v in df[col]] if len(df) else [0]
        width = max(len(str(col)), max(cell_lengths)) + 2
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(width, 40)
    for cell in ws[1]:
        cell.font = Font(bold=True)


def main() -> None:
    state = engine.recompute(engine.load_baseline())

    board = pd.DataFrame(state["available"].values())
    board = board.rename(columns={
        "baseline_price": "recommended_auction_value",
        "recommended_live": "recommended_auction_value_live",
        "my_target_price": "my_target_price_sam",
        "need_multiplier": "sam_need_multiplier",
    })
    board = board[[
        "player", "position", "nfl_team",
        "recommended_auction_value", "my_target_price_sam", "sam_need_multiplier",
    ]].sort_values("recommended_auction_value", ascending=False)

    roster = pd.DataFrame(state["my_roster"]).rename(columns={"price": "keeper_price_2026"})
    if not roster.empty:
        roster = roster[["player", "position", "keeper_price_2026", "source"]]

    fp_path = BASE_DIR / "output" / "fantasypros_rank_valuations.csv"
    fp = pd.read_csv(fp_path) if fp_path.exists() else pd.DataFrame()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as writer:
        board.to_excel(writer, sheet_name="Auction Board", index=False)
        _autofit(writer, "Auction Board", board)

        roster.to_excel(writer, sheet_name="My Roster (Sam)", index=False)
        if not roster.empty:
            _autofit(writer, "My Roster (Sam)", roster)

        if not fp.empty:
            fp.to_excel(writer, sheet_name="FantasyPros Reference Only", index=False)
            _autofit(writer, "FantasyPros Reference Only", fp)

    print(f"Wrote {OUTPUT_PATH}")
    print(f"  Auction Board: {len(board)} players")
    print(f"  My Roster (Sam): {len(roster)} keepers, ${state['my_remaining_budget']} remaining")
    if not fp.empty:
        print(f"  FantasyPros Reference Only: {len(fp)} players")


if __name__ == "__main__":
    main()
