#!/usr/bin/env python3
"""Phase 3F Part 9: a simple, LOCAL-ONLY, UNPUBLISHED auction-day board.

This is a plain static HTML file with inline JS, generated from Phase 3F's
own CSVs. It is NOT the live draft_ui/ website, is not published anywhere,
and is not connected to any Claude Artifact. Opening the file in a browser
lets Sam record sales during the real auction (player/winning team/price);
the page then re-filters its own precomputed target/fallback/avoid lists to
remove sold players and recompute her live legal max bid from simple
budget/slot arithmetic -- clearly labeled STATIC_PLAN_UPDATED_FOR_AVAILABILITY,
never as a fresh exact live-state solve (Phase 3F Part 12 of the 3E spec
already established that a real live-state exact solver was not built).

Writes: outputs/auction_rebuild/phase3f/local_auction_board.html
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pandas as pd

OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "phase3f"

SAM_STARTING_BUDGET_223 = 223
SAM_STARTING_BUDGET_221 = 221
SAM_STARTING_SLOTS = 9  # 9 auction purchases required to reach 15


def load_rows():
    board_path = OUT_DIR / "sam_auction_bid_board.csv"
    if not board_path.exists():
        return []
    df = pd.read_csv(board_path)
    df = df.where(pd.notnull(df), None)
    return df.to_dict(orient="records")


def main():
    rows = load_rows()
    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Sam Auction Board -- LOCAL ONLY, UNPUBLISHED (Phase 3F)</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 1.5em; background: #fafafa; color: #222; }}
.banner {{ background: #7a1f1f; color: white; padding: 0.75em 1em; font-weight: bold; margin-bottom: 1em; }}
.panel {{ background: white; border: 1px solid #ddd; border-radius: 6px; padding: 1em; margin-bottom: 1em; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.85em; }}
th, td {{ border: 1px solid #ddd; padding: 4px 6px; text-align: left; }}
th {{ background: #eee; position: sticky; top: 0; }}
tr.sold {{ opacity: 0.35; text-decoration: line-through; }}
tr.priority {{ background: #e6ffe6; }}
tr.avoid {{ background: #ffe6e6; }}
input, select {{ margin: 0.2em; }}
.badge {{ display:inline-block; padding:1px 6px; border-radius:4px; font-size:0.75em; background:#ddd; }}
.label-provisional {{ background:#fff3cd; }}
.label-preliminary {{ background:#f8d7da; }}
</style>
</head>
<body>
<div class="banner">LOCAL-ONLY / UNPUBLISHED WORKING BOARD -- Phase 3F. Not the live draft_ui/ site. Not a published Claude Artifact.
Every price is either PROVISIONAL_SIMULATED_MARKET_PRICE or PRELIMINARY_NOT_FINAL -- never treat a number here as a confirmed market fact.</div>

<div class="panel" id="budget-panel">
  <label>Budget scenario:
    <select id="budgetScenario" onchange="renderAll()">
      <option value="223">$223 primary</option>
      <option value="221">$221 conversions</option>
    </select>
  </label>
  <div id="budgetSummary"></div>
</div>

<div class="panel">
  <h3>Record a sale</h3>
  <input id="salePlayer" placeholder="player name">
  <input id="saleTeam" placeholder="winning team">
  <input id="salePrice" placeholder="sale price" type="number">
  <button onclick="recordSale()">Record</button>
  <button onclick="undoLast()">Undo last</button>
  <div style="font-size:0.8em;color:#666;margin-top:0.4em;">
    Recording a sale only filters this static board's precomputed lists (STATIC_PLAN_UPDATED_FOR_AVAILABILITY)
    and does simple remaining-budget/slot arithmetic for Sam. It does NOT re-run a live-state exact solve.
  </div>
</div>

<div class="panel">
  <h3>Target board</h3>
  <table id="boardTable"><thead><tr>
    <th>Player</th><th>Pos</th><th>Provisional P50</th><th>Price label</th>
    <th>Exact ceiling ($223)</th><th>Exact ceiling ($221)</th><th>Hard max</th>
    <th>Confidence</th><th>Action</th><th>Status</th>
  </tr></thead><tbody></tbody></table>
</div>

<script>
const BOARD_ROWS = {json.dumps(rows)};
const STARTING_BUDGET = {{"223": {SAM_STARTING_BUDGET_223}, "221": {SAM_STARTING_BUDGET_221}}};
const STARTING_SLOTS = {SAM_STARTING_SLOTS};

function loadState() {{
  try {{
    return JSON.parse(localStorage.getItem("phase3f_sale_log") || "[]");
  }} catch (e) {{ return []; }}
}}
function saveState(log) {{
  try {{ localStorage.setItem("phase3f_sale_log", JSON.stringify(log)); }} catch (e) {{}}
}}

function recordSale() {{
  const player = document.getElementById("salePlayer").value.trim();
  const team = document.getElementById("saleTeam").value.trim();
  const price = parseFloat(document.getElementById("salePrice").value);
  if (!player || !team || isNaN(price)) {{ alert("fill in player, team, and price"); return; }}
  const log = loadState();
  log.push({{player, team, price}});
  saveState(log);
  document.getElementById("salePlayer").value = "";
  document.getElementById("saleTeam").value = "";
  document.getElementById("salePrice").value = "";
  renderAll();
}}
function undoLast() {{
  const log = loadState();
  log.pop();
  saveState(log);
  renderAll();
}}

function renderAll() {{
  const scen = document.getElementById("budgetScenario").value;
  const log = loadState();
  const soldNames = new Set(log.map(e => e.player));
  const samSpend = log.filter(e => e.team.toLowerCase() === "sam").reduce((s, e) => s + e.price, 0);
  const samPicks = log.filter(e => e.team.toLowerCase() === "sam").length;
  const remainingBudget = STARTING_BUDGET[scen] - samSpend;
  const remainingSlots = Math.max(0, STARTING_SLOTS - samPicks);
  const minReserve = Math.max(0, remainingSlots - 1); // $1 for every remaining slot after this one
  const legalMaxBid = Math.max(1, remainingBudget - minReserve);

  document.getElementById("budgetSummary").innerHTML = `
    <b>Remaining budget:</b> $${{remainingBudget.toFixed(2)}} &nbsp;
    <b>Remaining slots:</b> ${{remainingSlots}} &nbsp;
    <b>Min reserve for other slots:</b> $${{minReserve}} &nbsp;
    <b>Legal current max bid:</b> $${{legalMaxBid.toFixed(2)}} &nbsp;
    <span class="badge">STATIC_PLAN_UPDATED_FOR_AVAILABILITY -- not a fresh live-state exact solve</span>
  `;

  const tbody = document.querySelector("#boardTable tbody");
  tbody.innerHTML = "";
  BOARD_ROWS.forEach(r => {{
    const tr = document.createElement("tr");
    const sold = soldNames.has(r["Player"]);
    if (sold) tr.className = "sold";
    else if (r["Recommended action"] === "PRIORITY_TARGET") tr.className = "priority";
    else if (r["Recommended action"] === "AVOID_AT_EXPECTED_PRICE") tr.className = "avoid";
    const labelClass = r["Provisional market P50 label"] === "PROVISIONAL_SIMULATED_MARKET_PRICE" ? "label-provisional" : "label-preliminary";
    tr.innerHTML = `
      <td>${{r["Player"]}}</td><td>${{r["Position"]}}</td>
      <td>${{r["Provisional market P50"] ?? ""}}</td>
      <td><span class="badge ${{labelClass}}">${{r["Provisional market P50 label"] ?? ""}}</span></td>
      <td>${{r["Exact ceiling under $223"] ?? ""}}</td>
      <td>${{r["Exact ceiling under $221"] ?? ""}}</td>
      <td>${{r["Recommended hard maximum"] ?? ""}}</td>
      <td>${{r["Confidence 1-10"] ?? ""}}</td>
      <td>${{r["Recommended action"] ?? ""}}</td>
      <td>${{sold ? "SOLD" : "available"}}</td>
    `;
    tbody.appendChild(tr);
  }});
}}
renderAll();
</script>
</body>
</html>
"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "local_auction_board.html").write_text(html)
    print(f"Wrote {OUT_DIR / 'local_auction_board.html'} ({len(rows)} board rows embedded)")


if __name__ == "__main__":
    main()
