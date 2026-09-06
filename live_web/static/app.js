const API = "/api";
let currentBoard = [];
let pendingSale = null;

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), 3500);
}

function recClass(rec) {
  if (!rec) return "rec-gray";
  if (rec === "CRITICAL_REVIEW_REQUIRED") return "rec-critical";
  if (rec.startsWith("STRONG_BUY") || rec.startsWith("BUY_AT_DISCOUNT") || rec.startsWith("PRIORITY")) return "rec-green";
  if (rec.startsWith("PASS") || rec.startsWith("INSUFFICIENT")) return "rec-red";
  if (rec === "INELIGIBLE") return "rec-gray";
  return "rec-yellow";
}

// V3 Part 14: LAN mutation token, entered once by the user (printed to
// the terminal by run_live_web.py --host 0.0.0.0), stored only in this
// tab's memory -- never required for the default 127.0.0.1-only launch.
let lanAuthToken = null;
try { lanAuthToken = localStorage.getItem("sunday_lan_auth_token"); } catch (e) {}

async function api(path, opts) {
  opts = opts || {};
  if (opts.method && opts.method !== "GET" && lanAuthToken) {
    opts.headers = Object.assign({}, opts.headers, { "X-Auth-Token": lanAuthToken });
  }
  const res = await fetch(API + path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401) { window.__connectionStatus = "AUTH_REQUIRED"; }
    const err = new Error(data.detail || res.statusText);
    err.status = res.status;  // preserved so callers can show a specific 401 message, without racing window.__connectionStatus against unrelated background polls
    throw err;
  }
  window.__connectionStatus = "OK";
  return data;
}

// Usability fix (real Sunday-week bug): every mutation click handler
// below now catches and shows the failure via toast() instead of
// letting a rejected api() promise fail silently -- this is exactly
// what happened to Sam when LAN mode (--host 0.0.0.0) required an
// X-Auth-Token he hadn't entered yet: every click just did nothing
// visible. On a 401 specifically, tell the user exactly what to do
// (paste the token the server printed at startup into the LAN token
// field above) rather than a bare "unauthorized."
function toastError(e, actionLabel) {
  if (e && e.status === 401) {
    toast("Authentication required -- paste your LAN token (printed in the server's startup terminal output) into the LAN token field above, then try again.");
  } else {
    toast((actionLabel ? actionLabel + " failed: " : "ERROR: ") + (e && e.message ? e.message : e));
  }
}

// ---- Header ----
async function refreshHeader() {
  const s = await api("/status");
  document.getElementById("hdr-budget").textContent = "$" + s.budget_remaining.toFixed(2);
  document.getElementById("hdr-slots").textContent = s.open_slots;
  document.getElementById("hdr-maxbid").textContent = "$" + s.legal_max_bid.toFixed(2);
  return s;
}

// ---- Tabs ----
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(c => c.classList.add("hidden"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.remove("hidden");
    if (btn.dataset.tab === "roster") loadRoster();
    if (btn.dataset.tab === "targets") loadTargets();
    if (btn.dataset.tab === "league") loadLeague();
    if (btn.dataset.tab === "log") { loadLog(); loadMarket(); }
  });
});

// ---- Global search ----
let searchTimer = null;
document.getElementById("global-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  const resultsDiv = document.getElementById("global-search-results");
  if (!q) { resultsDiv.classList.add("hidden"); return; }
  searchTimer = setTimeout(async () => {
    const data = await api("/search?q=" + encodeURIComponent(q) + "&include_protected=true");
    resultsDiv.innerHTML = "";
    if (data.results.length === 0) {
      resultsDiv.innerHTML = "<div>No matches.</div>";
    } else {
      data.results.slice(0, 15).forEach(r => {
        const div = document.createElement("div");
        div.className = "search-row";
        const statusTxt = r.status === "AVAILABLE" ? "" : ` [${r.status}${r.owner ? " -- " + teamLabel(r.owner) : ""}]`;
        div.textContent = `${r.player} (${r.position})${statusTxt}`;
        if (r.status === "AVAILABLE") {
          div.addEventListener("click", () => { nominate(r.player); resultsDiv.classList.add("hidden"); document.getElementById("global-search").value = ""; });
          div.style.cursor = "pointer";
        }
        resultsDiv.appendChild(div);
      });
    }
    resultsDiv.classList.remove("hidden");
  }, 250);
});

// ---- League Room ----
async function loadLeague() {
  const data = await api("/league");
  const tbody = document.getElementById("league-body");
  tbody.innerHTML = "";
  data.teams.sort((a, b) => b.budget_remaining - a.budget_remaining).forEach(t => {
    const needs = Object.entries(t.position_needs).filter(([k, v]) => v > 0).map(([k, v]) => `${k}:${v}`).join(",") || "none";
    const tr = document.createElement("tr");
    if (t.is_sam) tr.style.fontWeight = "bold";
    tr.innerHTML = `<td>${teamLabel(t.team)}</td><td>$${t.budget_remaining.toFixed(0)}</td>
      <td>${t.open_slots}</td><td>$${t.min_reserve.toFixed(0)}</td><td>$${t.legal_max_bid.toFixed(0)}</td>
      <td>${t.position_counts.QB || 0}</td><td>${t.position_counts.RB || 0}</td>
      <td>${t.position_counts.WR || 0}</td><td>${t.position_counts.TE || 0}</td><td>${needs}</td>
      <td>${t.flex_capacity}</td><td>${t.latest_purchase || "--"}</td>
      <td>${t.current_nominee_demand || "--"}</td>`;
    tr.style.cursor = "pointer";
    tr.addEventListener("click", () => loadTeamDetail(t.team));
    tbody.appendChild(tr);
  });
}
function rosterRowHtml(p) {
  return `${p.slot_type === "STARTER" ? "<b>" : ""}${p.position} ${p.display_name} $${p.price.toFixed(0)}` +
    `${p.is_keeper ? " (keeper)" : ""} -- ${p.lineup_role}${p.slot_type === "STARTER" ? "</b>" : ""}`;
}

async function loadTeamDetail(teamId) {
  const t = await api("/league/" + encodeURIComponent(teamId));
  const div = document.getElementById("team-detail");
  const rosterRows = t.roster.map(rosterRowHtml).join("<br>");
  const saleRows = t.sale_history.map(s => `${s.player} ($${s.price.toFixed(0)})`).join(", ") || "none yet";
  const rightsNote = t.college_rights_holdings.length
    ? `<br><i>College-rights protected players occupy roster capacity but remain outside the veteran auction pool and do not consume veteran auction cash: ${t.college_rights_holdings.join(", ")}</i>` : "";
  const b = t.protected_breakdown || {};
  const unnamedNote = b.unnamed_protected_count > 0
    ? ` <b style="color:#a05a00">(${b.unnamed_protected_count} protected slot identity unknown -- see warning banner)</b>` : "";
  div.innerHTML = `<h4>${teamLabel(teamId)}</h4>Budget: $${t.budget_remaining.toFixed(2)} | Open slots: ${t.open_slots} | Legal max: $${t.legal_max_bid.toFixed(2)}<br>
    <b>Protected breakdown:</b> veteran roster ${b.veteran_roster_count} + college-rights ${b.college_rights_count} + unnamed protected ${b.unnamed_protected_count} = ${b.total_occupied_count} occupied, ${b.open_auction_slots} open auction slots${unnamedNote}<br>
    <b>Roster (${t.roster_count}):</b><br>${rosterRows}<br><b>Auction purchases:</b> ${saleRows}${rightsNote}`;
}
document.getElementById("refresh-league").addEventListener("click", loadLeague);

// ---- All-team full rosters (V2.2 Request 3) ----
async function loadAllRosters() {
  const data = await api("/rosters");
  const div = document.getElementById("all-rosters");
  div.classList.remove("hidden");
  div.innerHTML = data.teams.map(t => {
    const rosterRows = t.roster.map(rosterRowHtml).join("<br>");
    const rightsNote = t.college_rights_holdings.length
      ? `<br><i>College-rights protected players occupy roster capacity but remain outside the veteran auction pool and do not consume veteran auction cash: ${t.college_rights_holdings.join(", ")}</i>` : "";
    return `<div class="team-roster-block">
      <h4>${teamLabel(t.team)} — ${t.roster_count} players, $${t.budget_remaining.toFixed(0)} left</h4>
      ${rosterRows}${rightsNote}
    </div>`;
  }).join("");
}
document.getElementById("btn-all-rosters").addEventListener("click", loadAllRosters);

// ---- Draft Board ----
async function loadBoard() {
  const data = await api("/board");
  currentBoard = data.players;
  if (data.nominated) showNominated(data.nominated);
  renderBoard();
  try {
    const s = await api("/status");
    renderPositionCountsTable("board-position-body", s);
  } catch (e) { /* summary bar is a convenience, not critical -- don't block the board on it */ }
  renderDraftGrade("board-grade-badge");
}

// V2.2 Request 2: live, as-you-type table filtering. Pure client-side
// filter over the already-fetched `currentBoard` array (341 rows max) --
// no server round-trip, no debounce needed, well under the 300ms target.
function normalizeNameQuery(s) {
  return s.replace(/_/g, " ").trim().toLowerCase();
}

function renderBoard() {
  const posFilter = document.getElementById("f-position").value;
  const recFilter = document.getElementById("f-rec").value;
  const maxPrice = parseFloat(document.getElementById("f-maxprice").value) || null;
  const startingOnly = document.getElementById("f-starting").checked;
  const sortKey = document.getElementById("f-sort").value;
  const nameQuery = normalizeNameQuery(document.getElementById("f-name").value);

  let rows = currentBoard.filter(r => {
    if (posFilter && r.position !== posFilter) return false;
    if (recFilter && r.recommendation !== recFilter) return false;
    if (maxPrice && r.live_expected_price > maxPrice) return false;
    if (startingOnly && r.expected_role === "bench depth") return false;
    if (nameQuery) {
      const nameHay = normalizeNameQuery(r.player);
      const posHay = r.position.toLowerCase();
      if (!nameHay.includes(nameQuery) && !posHay.includes(nameQuery)) return false;
    }
    return true;
  });

  // Stop - Expected is derived (recommended_stop minus live_expected_price),
  // not a stored field on the row -- computed here rather than persisted,
  // same value shown in the column itself. Rows with no expected price
  // sort last regardless of direction, since there's nothing to compare.
  const stopMinusExpectedOf = r => (r.live_expected_price != null ? r.recommended_stop - r.live_expected_price : null);

  if (sortKey === "player") rows.sort((a, b) => a.player.localeCompare(b.player));
  else if (sortKey === "stop_minus_expected") {
    rows.sort((a, b) => {
      const av = stopMinusExpectedOf(a), bv = stopMinusExpectedOf(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return bv - av;
    });
  }
  else rows.sort((a, b) => (b[sortKey] || 0) - (a[sortKey] || 0));

  // populate recommendation filter options once
  const recSelect = document.getElementById("f-rec");
  if (recSelect.options.length <= 1) {
    const recs = [...new Set(currentBoard.map(r => r.recommendation))];
    recs.forEach(r => { const o = document.createElement("option"); o.value = r; o.textContent = r; recSelect.appendChild(o); });
  }

  const tbody = document.getElementById("board-body");
  tbody.innerHTML = "";
  rows.forEach(r => {
    const tr = document.createElement("tr");
    tr.className = recClass(r.recommendation);
    let stopMinusExpected = "--";
    let stopMinusExpectedClass = "";
    if (r.live_expected_price != null) {
      const diff = r.recommended_stop - r.live_expected_price;
      stopMinusExpected = (diff >= 0 ? "+$" : "-$") + Math.abs(diff).toFixed(0);
      stopMinusExpectedClass = diff >= 0 ? "stop-above-expected" : "stop-below-expected";
    }
    tr.innerHTML = `
      <td>${r.player}</td><td>${r.position}</td><td>${r.projected_points.toFixed(0)}</td>
      <td>$${r.live_expected_price.toFixed(0)}</td><td>$${r.marginal_value.toFixed(0)}</td>
      <td>$${r.recommended_stop.toFixed(0)}</td>
      <td class="${stopMinusExpectedClass}">${stopMinusExpected}</td>
      <td>${r.recommendation}</td>
      <td>${r.calculation_label === "SOLVER_FAILURE_FALLBACK" ? "LOW" : "OK"}</td>
      <td>
        <button class="nominate-btn" data-player="${r.player}">Nominate</button>
        <button class="sold-btn" data-player="${r.player}">Mark Sold</button>
      </td>`;
    tbody.appendChild(tr);
  });
  document.getElementById("board-count").textContent = `${rows.length} / ${currentBoard.length} players`;

  tbody.querySelectorAll(".nominate-btn").forEach(b => b.addEventListener("click", () => nominate(b.dataset.player)));
  tbody.querySelectorAll(".sold-btn").forEach(b => b.addEventListener("click", () => openSaleModal(b.dataset.player)));
}

["f-name", "f-position", "f-rec", "f-maxprice", "f-starting", "f-sort"].forEach(id =>
  document.getElementById(id).addEventListener("input", renderBoard));
document.getElementById("refresh-board").addEventListener("click", () => { loadBoard(); refreshHeader(); });

// ---- Nominated panel ----
async function nominate(player) {
  try {
    await api("/nominate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ player }) });
    showNominated(player);
  } catch (e) { toastError(e, "Nominate"); }
}
document.getElementById("nom-clear").addEventListener("click", async () => {
  try {
    await api("/nominate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ player: null }) });
    document.getElementById("nominated-panel").classList.add("hidden");
  } catch (e) { toastError(e, "Clear nomination"); }
});

let nomState = { player: null, check: null, exact: null, leadingTeam: null, verdict: null };

async function showNominated(player) {
  const panel = document.getElementById("nominated-panel");
  panel.classList.remove("hidden");
  document.getElementById("nom-name").textContent = player;
  document.getElementById("nom-detail").textContent = "loading...";
  document.getElementById("nom-current-bid").value = "";
  document.getElementById("nom-exact-result").classList.add("hidden");
  document.getElementById("nom-ladder-result").classList.add("hidden");
  nomState = { player, check: null, exact: null, leadingTeam: null, verdict: null };
  try {
    const c = await api("/check/" + encodeURIComponent(player));
    nomState.check = c;
    document.getElementById("nom-detail").innerHTML =
      `Expected: $${c.live_expected_price.toFixed(0)} | Conservative: $${c.conservative_price.toFixed(0)} | ` +
      `Marginal value (fantasy points): ${c.marginal_value.toFixed(1)}pts, role: ${c.expected_role} [${c.calculation_label}] | ` +
      `<b>RECOMMENDED STOP: $${c.recommended_stop.toFixed(0)} [${c.recommendation}]</b> -- Limiting factor: ${(c.governed_calculation_label||"").split("->")[0].trim()}<br>${c.reason}`;
    renderVerdict();
  } catch (e) {
    document.getElementById("nom-detail").textContent = "SOLVER_FAILURE / unavailable: " + e.message;
  }
}

function governingCeiling() {
  // Prefer a CURRENT exact result over the fast approximate check, per
  // the spec's own rule -- exact ceiling always wins when fresh.
  if (nomState.exact && nomState.exact.stale_status === "CURRENT") {
    return { ceiling: nomState.exact.safety_adjusted_maximum, source: "exact (current)", critical: nomState.exact.critical_review_required };
  }
  if (nomState.check) {
    return { ceiling: nomState.check.recommended_stop, source: "approximate (no current exact)", critical: nomState.check.critical_review_required };
  }
  return null;
}

// V3 Parts 9-10: the verdict now comes from ONE backend-authoritative
// endpoint (GET /api/verdict/{player}) using the exact required
// taxonomy (BID / BID_BUT_RUN_EXACT_SOON / HOLD / ONE_MORE_DOLLAR /
// PASS / ILLEGAL / CRITICAL_REVIEW_REQUIRED) -- this replaces the
// client-side-only formula that used to live here (a second,
// UI-only valuation path is exactly the class of bug this whole
// repair exists to catch).
let _verdictRequestToken = 0;

async function renderVerdict() {
  const bidInput = document.getElementById("nom-current-bid");
  const bidRaw = bidInput.value;
  const bid = bidRaw === "" ? null : parseFloat(bidRaw);
  const box = document.getElementById("nom-verdict");
  if (!nomState.player) { box.textContent = "Loading recommendation..."; return; }
  if (bidRaw !== "" && isNaN(bid)) { box.textContent = "Enter a valid numeric bid."; box.className = "verdict-box"; return; }

  const myToken = ++_verdictRequestToken;
  let url = "/verdict/" + encodeURIComponent(nomState.player);
  const params = [];
  if (bid !== null) params.push("current_bid=" + encodeURIComponent(bid));
  if (nomState.leadingTeam) params.push("leading_team=" + encodeURIComponent(nomState.leadingTeam));
  if (params.length) url += "?" + params.join("&");

  let v;
  try {
    v = await api(url);
  } catch (e) {
    if (myToken === _verdictRequestToken) { box.textContent = "Verdict unavailable: " + e.message; box.className = "verdict-box"; }
    return;
  }
  if (myToken !== _verdictRequestToken) return;  // a newer request already superseded this one

  box.textContent = `${v.verdict} -- ${v.reason}`;
  box.className = "verdict-box verdict-" + v.verdict.toLowerCase();
  nomState.verdict = v;
}

document.getElementById("nom-current-bid").addEventListener("input", renderVerdict);
document.querySelectorAll(".quick-bid").forEach(btn => btn.addEventListener("click", () => {
  const input = document.getElementById("nom-current-bid");
  const cur = parseFloat(input.value) || 0;
  input.value = cur + parseInt(btn.dataset.inc);
  renderVerdict();
}));
document.getElementById("nom-pass").addEventListener("click", () => { toast(`Passed on ${nomState.player}.`); });

document.getElementById("nom-run-exact").addEventListener("click", async () => {
  const resultDiv = document.getElementById("nom-exact-result");
  resultDiv.classList.remove("hidden");
  resultDiv.textContent = "Running exact solve (a few seconds)...";
  try {
    const bid = parseFloat(document.getElementById("nom-current-bid").value);
    const testPrice = isNaN(bid) ? undefined : bid;
    const e = await api("/exact", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ player: nomState.player, test_price: testPrice }) });
    nomState.exact = e;
    resultDiv.innerHTML = `<b>EXACT [${e.stale_status}]</b>: test price $${e.test_price} -- surplus ${e.exact_surplus.toFixed(2)} | ` +
      `Exact ceiling: $${e.exact_ceiling} | Safety max: $${e.safety_adjusted_maximum} | Displaced: ${e.displaced_player || "none"} | ` +
      `Solver: ${e.solver_status} (${e.cache_status}) | Runtime: ${e.runtime}s | Sequence: ${e.state_sequence}`;
    renderVerdict();
  } catch (err) {
    resultDiv.textContent = "SOLVER_FAILURE: " + err.message;
  }
});

document.getElementById("nom-run-ladder").addEventListener("click", async () => {
  const resultDiv = document.getElementById("nom-ladder-result");
  resultDiv.classList.remove("hidden");
  resultDiv.textContent = "Running ladder...";
  try {
    const l = await api("/ladder", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ player: nomState.player }) });
    resultDiv.innerHTML = "<b>LADDER:</b> " + l.ladder.map(r => `$${r.price}: ${r.exact_surplus !== null ? r.exact_surplus.toFixed(1) : "?"} (${r.recommended_action})`).join(" | ");
  } catch (err) {
    resultDiv.textContent = "SOLVER_FAILURE: " + err.message;
  }
});

document.getElementById("nom-mark-sold-sam").addEventListener("click", () => openSaleModalPrefill(nomState.player, "Sam"));
document.getElementById("nom-mark-sold-other").addEventListener("click", () => openSaleModal(nomState.player));
function openSaleModalPrefill(player, team) {
  openSaleModal(player);
  document.getElementById("modal-team").value = team;
}

// ---- Sale modal ----
function openSaleModal(player) {
  pendingSale = { player, confirm: false };
  document.getElementById("modal-player").textContent = player;
  document.getElementById("modal-team").value = "";
  document.getElementById("modal-price").value = "";
  document.getElementById("modal-confirm-msg").classList.add("hidden");
  document.getElementById("sale-modal").classList.remove("hidden");
}
document.getElementById("modal-cancel").addEventListener("click", () => {
  document.getElementById("sale-modal").classList.add("hidden");
});
document.getElementById("modal-submit").addEventListener("click", async () => {
  const team = document.getElementById("modal-team").value.trim();
  const price = parseFloat(document.getElementById("modal-price").value);
  if (!team || isNaN(price)) { toast("Enter a team and price."); return; }
  try {
    const result = await api("/sale", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ player: pendingSale.player, team, price, confirm: pendingSale.confirm }),
    });
    if (result.needs_confirmation) {
      document.getElementById("modal-confirm-msg").textContent = result.message;
      document.getElementById("modal-confirm-msg").classList.remove("hidden");
      pendingSale.confirm = true; // next click proceeds with confirm=true
      return;
    }
    document.getElementById("sale-modal").classList.add("hidden");
    toast(result.message.split("\n")[0]);
    loadBoard();
    refreshHeader();
  } catch (e) {
    toastError(e, "Sale");
  }
});

// ---- My Roster ----
// Official league structure (16 total: 9 starters [1 QB, 2 RB, 2 WR, 1 TE,
// 3 FLEX] + 7 bench). FLEX-eligible positions are RB/WR/TE, so a team can
// legally exceed a position's own required-starter count and still be
// using those extra players productively via FLEX -- "Status" reflects
// that instead of just flagging any count above the named-starter number
// as a surplus.
const REQUIRED_STARTERS = { QB: 1, RB: 2, WR: 2, TE: 1 };
const FLEX_ELIGIBLE = ["RB", "WR", "TE"];
const FLEX_SLOTS = 3;

// Shared by My Roster and the Draft Board summary bar -- renders the
// position counts/requirements rows into whichever tbody id is passed.
function renderPositionCountsTable(tbodyId, s) {
  const posBody = document.getElementById(tbodyId);
  if (!posBody) return;
  posBody.innerHTML = "";
  // Include college-rights holds (Mendoza/Bond) in the position tally --
  // they occupy a real roster slot at their real position even though
  // they aren't auction-eligible or currently lineup-eligible. "Status"
  // (need/filled/extra) still derives from s.position_needs, which the
  // backend computes from the true starting-eligible roster only, so a
  // college-rights hold never masks a real starting need.
  const counts = Object.assign({}, s.position_counts || {});
  (s.college_rights_holdings || []).forEach(cr => {
    if (cr.position) counts[cr.position] = (counts[cr.position] || 0) + 1;
  });
  Object.keys(REQUIRED_STARTERS).forEach(pos => {
    const required = REQUIRED_STARTERS[pos];
    const current = counts[pos] || 0;
    const stillNeeded = (s.position_needs && s.position_needs[pos]) || 0;
    let status;
    if (stillNeeded > 0) status = `Need ${stillNeeded} more starter${stillNeeded > 1 ? "s" : ""}`;
    else if (FLEX_ELIGIBLE.includes(pos) && current > required) status = `${current - required} extra (FLEX-eligible)`;
    else status = "Filled";
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${pos}</td><td>${required}</td><td>${current}</td><td>${status}</td>`;
    posBody.appendChild(tr);
  });
  const flexNeeded = (s.position_needs && s.position_needs.FLEX) || 0;
  const flexRow = document.createElement("tr");
  flexRow.innerHTML = `<td>FLEX (RB/WR/TE)</td><td>${FLEX_SLOTS}</td><td>--</td><td>${flexNeeded > 0 ? `Need ${flexNeeded} more` : "Filled"}</td>`;
  posBody.appendChild(flexRow);
}

// Real, comparative draft score (0-100): Sam's current best legal
// starting lineup, ranked against all 12 teams' current best legal
// starting lineups at THIS exact moment in the draft -- a fair snapshot
// comparison since everyone is measured at the same point, regardless
// of how many total picks have happened. Backed by /api/draft-score
// (api_draft_score in live_auction_cli.py), which reuses the same
// greedy_best_lineup lineup logic already proven for Sam's own values --
// not a client-side guess. Recomputed fresh on every board refresh, so
// it updates after every real sale (Sam's or any other team's).
async function renderDraftGrade(elId) {
  const badge = document.getElementById(elId);
  if (!badge) return;
  try {
    const d = await api("/draft-score");
    badge.textContent = d.score_out_of_100;
    let cls;
    if (d.score_out_of_100 >= 80) cls = "grade-a";
    else if (d.score_out_of_100 >= 60) cls = "grade-b";
    else if (d.score_out_of_100 >= 40) cls = "grade-c";
    else if (d.score_out_of_100 >= 20) cls = "grade-d";
    else cls = "grade-f";
    badge.className = "grade-badge " + cls;
    badge.title = `Score ${d.score_out_of_100}/100 -- rank #${d.rank} of ${d.teams_total} teams right now. ` +
      `Sam's current best legal starting lineup: ${d.sam_starting_points} pts ` +
      `(league range: ${d.worst_starting_points}-${d.best_starting_points} pts). ` +
      `Live comparison of current roster strength across all 12 teams at this exact point in the draft -- ` +
      `not the same as the governed bid-recommendation engine.`;
  } catch (e) {
    badge.textContent = "--";
    badge.className = "grade-badge grade-pending";
    badge.title = "Draft score unavailable: " + (e && e.message ? e.message : e);
  }
}

async function loadRoster() {
  const s = await api("/status");
  document.getElementById("roster-summary").innerHTML =
    `Budget remaining: <b>$${s.budget_remaining.toFixed(2)}</b> | Open slots: <b>${s.open_slots}</b> | ` +
    `Min reserve: <b>$${s.min_reserve.toFixed(2)}</b> | Legal max bid: <b>$${s.legal_max_bid.toFixed(2)}</b>`;

  renderPositionCountsTable("roster-position-body", s);

  const needs = Object.entries(s.position_needs).filter(([k, v]) => v > 0);
  document.getElementById("roster-needs").textContent = needs.length
    ? needs.map(([k, v]) => `${k}: ${v} needed`).join(" | ") : "All starting needs filled.";

  // 16 total slots, correctly including protected-but-unlisted occupancy
  // (college-rights players like Mendoza/Bond aren't in s.roster but ARE
  // counted in s.open_slots -- so "filled" = 16 - open_slots, not
  // roster.length, or this undercounts real occupancy).
  const TOTAL_SLOTS = 16;
  const filledCount = TOTAL_SLOTS - s.open_slots;
  const visual = document.getElementById("roster-slots-visual");
  visual.innerHTML = "";
  for (let i = 0; i < TOTAL_SLOTS; i++) {
    const span = document.createElement("span");
    span.className = i < filledCount ? "slot-filled" : "slot-open";
    span.textContent = i < filledCount ? "✓" : "";
    visual.appendChild(span);
  }
  const tbody = document.getElementById("roster-body");
  tbody.innerHTML = "";
  s.roster.forEach(p => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${p.position}</td><td>${p.display_name}</td><td>$${p.price.toFixed(0)}</td><td>${p.is_keeper ? "Yes" : "No"}</td>`;
    tbody.appendChild(tr);
  });
  // College-rights holds (Mendoza/Bond): occupy a roster slot, already
  // reflected in open_slots, and count toward the 16-player roster during
  // the draft, but are NOT veteran keepers or auction purchases. The $1
  // shown is their flat conversion fee -- confirmed separate from and
  // never charged against Sam's $225 veteran-auction budget.
  (s.college_rights_holdings || []).forEach(cr => {
    const tr = document.createElement("tr");
    tr.className = "college-rights-row";
    tr.innerHTML = `<td>${cr.position || "--"}</td><td>${cr.display_name}</td><td>$${cr.conversion_fee}</td><td>College-rights</td>`;
    tr.title = "College-rights hold: counts toward the 16-player roster, not a veteran keeper, not auction-eligible. The $1 conversion fee is separate from the $225 auction budget.";
    tbody.appendChild(tr);
  });
}

// ---- Targets ----
async function loadTargets() {
  const data = await api("/targets");
  const tbody = document.getElementById("targets-body");
  tbody.innerHTML = "";
  // V3 Part 9: every column explicitly unit-labeled in the header
  // above (pts vs $) -- never render a points field with a $ sign.
  data.targets.forEach(t => {
    const tr = document.createElement("tr");
    tr.className = recClass(t.recommendation_class.includes("PRIORITY") || t.recommendation_class.includes("BUY") ? "BUY" :
                              t.recommendation_class.includes("PASS") ? "PASS" : "");
    const ceiling = t.exact_ceiling_dollars != null ? `$${t.exact_ceiling_dollars.toFixed(0)} (exact)` :
                    t.approximate_ceiling_dollars != null ? `$${t.approximate_ceiling_dollars.toFixed(0)} (approx.)` : "--";
    // Stop - Expected: positive means Sam's own ceiling exceeds what the
    // room is expected to pay -- a signal this player is likely
    // acquirable within his stop, not a claim the player is "cheap" in
    // any absolute sense. Only meaningful when an expected market price
    // actually exists (Monte Carlo/live-adjustment data), so left blank
    // rather than a misleading number when that input is null.
    let stopMinusExpected = "--";
    let stopMinusExpectedClass = "";
    if (t.expected_market_price_dollars != null) {
      const diff = t.recommended_stop_dollars - t.expected_market_price_dollars;
      stopMinusExpected = (diff >= 0 ? "+$" : "-$") + Math.abs(diff).toFixed(0);
      stopMinusExpectedClass = diff >= 0 ? "stop-above-expected" : "stop-below-expected";
    }
    tr.innerHTML = `<td>${t.player}</td><td>${t.position}</td><td>${t.tier != null ? t.tier : "--"}</td>
      <td>${t.projected_points != null ? t.projected_points.toFixed(1) : "--"}</td>
      <td>${t.marginal_lineup_points != null ? t.marginal_lineup_points.toFixed(1) : "--"}</td>
      <td>$${t.team_specific_value_dollars.toFixed(0)}</td>
      <td>${t.expected_market_price_dollars != null ? "$" + t.expected_market_price_dollars.toFixed(0) : "--"}</td>
      <td>${ceiling}</td>
      <td><b>$${t.recommended_stop_dollars.toFixed(0)}</b></td>
      <td class="${stopMinusExpectedClass}">${stopMinusExpected}</td>
      <td>$${t.surplus_or_deficit_dollars.toFixed(1)}</td>
      <td>${t.confidence}</td>
      <td>${t.total_score.toFixed(3)}</td>
      <td>${t.critical_review_required ? "CRITICAL_REVIEW_REQUIRED" : t.recommendation_class}</td>
      <td>${t.reason || ""}</td>`;
    tbody.appendChild(tr);
  });
}
document.getElementById("refresh-targets").addEventListener("click", loadTargets);

// ---- Roster Paths ----
document.getElementById("refresh-paths").addEventListener("click", async () => {
  const container = document.getElementById("paths-container");
  container.textContent = "Computing exact roster paths (a few seconds)...";
  try {
    const paths = await api("/paths");
    container.innerHTML = "";
    for (const [style, r] of Object.entries(paths)) {
      const div = document.createElement("div");
      const players = (r.players || []).map(p => `${p.player} (${p.position}) $${p.price}`).join(", ");
      div.innerHTML = `<h4>${style} -- ${r.status}</h4>
        Spend: $${r.spend || 0} | Unused: $${(r.unused_cash || 0).toFixed(2)} | Starting pts: ${r.starting_points ? r.starting_points.toFixed(1) : "n/a"}<br>
        ${players}`;
      container.appendChild(div);
    }
  } catch (e) {
    container.textContent = "SOLVER_FAILURE: " + e.message;
  }
});

// ---- Log / Controls ----
async function loadLog() {
  const data = await api("/log");
  const tbody = document.getElementById("log-body");
  tbody.innerHTML = "";
  data.events.forEach(e => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${e.sequence}</td><td>${e.player}</td><td>${e.position}</td><td>${teamLabel(e.team)}</td><td>$${e.price.toFixed(0)}</td>`;
    tbody.appendChild(tr);
  });
}
async function loadMarket() {
  const m = await api("/market");
  let html = `Active prior: <b>${m.active_prior}</b><br>League-wide ratio: ${m.league_ratio.toFixed(3)} (n=${m.league_n})<br>`;
  for (const [pos, v] of Object.entries(m.positions)) html += `${pos}: ${v.ratio.toFixed(3)} (n=${v.n})<br>`;
  document.getElementById("market-summary").innerHTML = html;
}
document.getElementById("btn-undo").addEventListener("click", async () => {
  try {
    const r = await api("/undo", { method: "POST" });
    toast(r.message.split("\n")[0]);
    loadBoard(); refreshHeader(); loadLog();
  } catch (e) { toastError(e, "Undo"); }
});
document.getElementById("btn-correct").addEventListener("click", async () => {
  const player = document.getElementById("correct-player").value.trim();
  const team = document.getElementById("correct-team").value.trim();
  const price = parseFloat(document.getElementById("correct-price").value);
  try {
    const r = await api("/correct", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ player, team, price }) });
    toast(r.message.split("\n")[0]);
    loadBoard(); refreshHeader(); loadLog();
  } catch (e) { toastError(e, "Correct"); }
});
document.getElementById("btn-save").addEventListener("click", async () => {
  const name = document.getElementById("snapshot-name").value.trim();
  try {
    const r = await api("/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    toast(r.message);
  } catch (e) { toastError(e, "Save snapshot"); }
});
document.getElementById("btn-load").addEventListener("click", async () => {
  const name = document.getElementById("snapshot-name").value.trim();
  try {
    const r = await api("/load", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    toast("Loaded snapshot.");
    loadBoard(); refreshHeader(); loadLog();
  } catch (e) { toastError(e, "Load snapshot"); }
});
document.getElementById("btn-emergency").addEventListener("click", async () => {
  const text = await fetch(API + "/emergency").then(r => r.text());
  const el = document.getElementById("emergency-content");
  el.textContent = text;
  el.classList.remove("hidden");
});

// ---- Practice Mode (V2.1 Part 6) ----
async function refreshModeBanner() {
  const m = await api("/mode");
  const banner = document.getElementById("practice-banner");
  const select = document.getElementById("mode-select");
  if (m.mode === "practice") {
    banner.classList.remove("hidden");
    document.getElementById("practice-scenario-label").textContent = m.scenario;
    select.value = m.scenario;
    if (m.proof && m.proof.rb_marginal_value_before !== undefined) {
      toast("RB-overload proof: RB marginal value " + m.proof.rb_marginal_value_before +
            " -> " + m.proof.rb_marginal_value_after + " | WR/TE " +
            m.proof.wr_te_marginal_value_before + " -> " + m.proof.wr_te_marginal_value_after);
    }
  } else {
    banner.classList.add("hidden");
    select.value = "production";
  }
  return m;
}

document.getElementById("mode-select").addEventListener("change", async (e) => {
  const val = e.target.value;
  try {
    if (val === "production") {
      await api("/mode/production", { method: "POST" });
    } else {
      await api("/mode/practice", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario: val }),
      });
    }
    await refreshModeBanner();
    loadBoard(); refreshHeader(); loadLog();
    document.getElementById("nominated-panel").classList.add("hidden");
  } catch (e2) { toastError(e2, "Switch mode"); }
});

// ---- Team labels: "Yahoo team name (owner)" everywhere a team is shown ----
let teamLabels = {};
function teamLabel(teamId) {
  if (!teamId) return teamId;
  return teamLabels[teamId] || teamId;
}

// ---- V3 Part 14: official-team dropdown + operational status ----
async function populateTeamDropdowns() {
  try {
    const data = await api("/teams");
    teamLabels = data.labels || {};
    ["modal-team", "correct-team"].forEach(id => {
      const sel = document.getElementById(id);
      if (!sel) return;
      data.teams.forEach(t => {
        const o = document.createElement("option");
        o.value = t; o.textContent = teamLabel(t);
        sel.appendChild(o);
      });
    });
  } catch (e) { /* non-fatal -- selects just stay with the placeholder option */ }
}

document.getElementById("lan-token-input").addEventListener("change", (e) => {
  lanAuthToken = e.target.value.trim() || null;
  try {
    if (lanAuthToken) localStorage.setItem("sunday_lan_auth_token", lanAuthToken);
    else localStorage.removeItem("sunday_lan_auth_token");
  } catch (err) {}
  toast(lanAuthToken ? "LAN token set for this browser." : "LAN token cleared.");
});
if (lanAuthToken) document.getElementById("lan-token-input").value = lanAuthToken;

async function refreshOperationalStatus() {
  try {
    const s = await api("/operational-status");
    document.getElementById("opstat-mode").textContent = s.mode;
    document.getElementById("opstat-seq").textContent = s.sequence_number;
    document.getElementById("opstat-log").textContent = s.active_log_path ? s.active_log_path.split("/").slice(-1)[0] : "none";
    document.getElementById("opstat-last-event").textContent = s.last_persisted_event ?
      `${s.last_persisted_event.event_type} (#${s.last_persisted_event.sequence_number})` : "none yet";
    document.getElementById("opstat-connection").textContent = "OK";
    document.getElementById("opstat-exact").textContent = s.exact_freshness;
    document.getElementById("opstat-sim").textContent = s.market_prior_freshness;
    const warnBanner = document.getElementById("protected-warning-banner");
    if (s.protected_player_warning) {
      warnBanner.textContent = "⚠ " + s.protected_player_warning;
      warnBanner.classList.remove("hidden");
    } else {
      warnBanner.classList.add("hidden");
    }
  } catch (e) {
    const el = document.getElementById("opstat-connection");
    if (el) el.textContent = window.__connectionStatus === "AUTH_REQUIRED" ? "AUTH_REQUIRED (enter LAN token)" : "DISCONNECTED";
  }
}
setInterval(refreshOperationalStatus, 5000);

// ---- V3 Gate F: true, interactive Practice Draft ----
let pdSessionId = null;
try { pdSessionId = localStorage.getItem("sunday_practice_draft_session_id"); } catch (e) {}

function renderPracticeDraftPending(pending) {
  const panel = document.getElementById("pd-nomination");
  if (!pending) { panel.classList.add("hidden"); return; }
  panel.classList.remove("hidden");
  document.getElementById("pd-player").textContent = pending.player;
  document.getElementById("pd-position").textContent = pending.position;
  document.getElementById("pd-nominator").textContent = pending.nominator;
  document.getElementById("pd-ai-price").textContent = pending.ai_current_price;
  document.getElementById("pd-ai-leader").textContent = pending.ai_leading_team || "nobody (uncontested)";
  document.getElementById("pd-stop").textContent = pending.sam_recommended_stop;
  document.getElementById("pd-legal-max").textContent = pending.sam_legal_max_bid;
}

async function pdStartNewDraft() {
  const newSessionId = "practice-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);
  const seed = parseInt(document.getElementById("pd-seed").value) || 909001;
  const r = await api("/practice-draft/start", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: newSessionId, seed }) });
  pdSessionId = newSessionId;
  try { localStorage.setItem("sunday_practice_draft_session_id", pdSessionId); } catch (e) {}
  document.getElementById("pd-status").textContent = "Practice draft started (session " + pdSessionId + ").";
  renderPracticeDraftPending(r.pending);
  return r;
}

document.getElementById("pd-start").addEventListener("click", async () => {
  try { await pdStartNewDraft(); } catch (e) { toastError(e, "Start practice draft"); }
});

document.getElementById("pd-pass-btn").addEventListener("click", async () => {
  try {
    const r = await api(`/practice-draft/${pdSessionId}/pass`, { method: "POST" });
    document.getElementById("pd-status").textContent = "Status: " + r.status;
    renderPracticeDraftPending(r.pending);
  } catch (e) { toastError(e, "Pass"); }
});

document.getElementById("pd-bid-btn").addEventListener("click", async () => {
  const amount = parseFloat(document.getElementById("pd-bid-amount").value);
  if (isNaN(amount)) { toast("Enter a bid amount."); return; }
  try {
    const r = await api(`/practice-draft/${pdSessionId}/bid`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount }) });
    document.getElementById("pd-status").textContent = "Status: " + r.status;
    document.getElementById("pd-bid-amount").value = "";
    renderPracticeDraftPending(r.pending);
  } catch (e) { toastError(e, "Bid"); }
});

document.getElementById("pd-undo-btn").addEventListener("click", async () => {
  try {
    const r = await api(`/practice-draft/${pdSessionId}/undo`, { method: "POST" });
    document.getElementById("pd-status").textContent = r.message;
    renderPracticeDraftPending(r.pending);
  } catch (e) { toastError(e, "Undo"); }
});

document.getElementById("pd-review-btn").addEventListener("click", async () => {
  if (!pdSessionId) { toast("Start a practice draft first."); return; }
  try {
    const r = await api(`/practice-draft/${pdSessionId}/review`);
    const out = document.getElementById("pd-review-output");
    out.textContent = JSON.stringify(r, null, 2);
    out.classList.remove("hidden");
  } catch (e) { toastError(e, "Review"); }
});

// ---- Auto-Simulate: on every nomination, bid $1 above the current AI
// price whenever Sam's own recommended stop is still above that price,
// otherwise pass -- exactly the simple rule Sam asked for. This is a
// dumb, transparent loop over the SAME real bid/pass endpoints a human
// clicking through the UI would use -- no separate simulation logic,
// no shortcut through the event engine. Runs until the draft completes,
// the user clicks Stop, or an unexpected error occurs.
let pdAutoSimRunning = false;

function pdSleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

async function pdAutoSimStep() {
  const pendingPanel = document.getElementById("pd-nomination");
  if (pendingPanel.classList.contains("hidden")) return { done: true, reason: "no active nomination" };
  const aiPrice = parseFloat(document.getElementById("pd-ai-price").textContent);
  const stop = parseFloat(document.getElementById("pd-stop").textContent);
  const player = document.getElementById("pd-player").textContent;
  try {
    let r;
    if (!isNaN(stop) && !isNaN(aiPrice) && stop > aiPrice) {
      const bidAmount = aiPrice + 1;
      r = await api(`/practice-draft/${pdSessionId}/bid`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount: bidAmount }) });
      document.getElementById("pd-autosim-status").textContent = `Bid $${bidAmount} on ${player} (stop $${stop} > AI $${aiPrice}). Status: ${r.status}`;
    } else {
      r = await api(`/practice-draft/${pdSessionId}/pass`, { method: "POST" });
      document.getElementById("pd-autosim-status").textContent = `Passed on ${player} (stop $${isNaN(stop) ? "?" : stop} <= AI $${isNaN(aiPrice) ? "?" : aiPrice}). Status: ${r.status}`;
    }
    document.getElementById("pd-status").textContent = "Status: " + r.status;
    renderPracticeDraftPending(r.pending);
    return { done: r.status === "COMPLETE" || !r.pending, reason: r.status };
  } catch (e) {
    document.getElementById("pd-autosim-status").textContent = "Auto-Simulate stopped on error: " + (e && e.message ? e.message : e);
    return { done: true, reason: "error" };
  }
}

document.getElementById("pd-autosim-btn").addEventListener("click", async () => {
  if (pdAutoSimRunning) return;
  // If there's no active nomination showing -- no session yet, a
  // completed draft, or a stale session left over from before a server
  // restart -- start a fresh draft automatically instead of silently
  // doing nothing. This was a real bug: clicking Auto-Simulate with no
  // visible nomination used to exit instantly with zero bids/passes and
  // no visible explanation.
  if (document.getElementById("pd-nomination").classList.contains("hidden")) {
    try {
      document.getElementById("pd-autosim-status").textContent = "No active nomination -- starting a new practice draft first...";
      await pdStartNewDraft();
    } catch (e) {
      toastError(e, "Auto-Simulate: start practice draft");
      return;
    }
  }
  pdAutoSimRunning = true;
  document.getElementById("pd-autosim-btn").classList.add("hidden");
  document.getElementById("pd-autosim-stop-btn").classList.remove("hidden");
  const speedMs = parseInt(document.getElementById("pd-autosim-speed").value) || 0;
  while (pdAutoSimRunning) {
    const result = await pdAutoSimStep();
    if (result.done) {
      document.getElementById("pd-autosim-status").textContent += ` -- Auto-Simulate finished (${result.reason}).`;
      break;
    }
    if (speedMs > 0) await pdSleep(speedMs);
  }
  pdAutoSimRunning = false;
  document.getElementById("pd-autosim-btn").classList.remove("hidden");
  document.getElementById("pd-autosim-stop-btn").classList.add("hidden");
});

document.getElementById("pd-autosim-stop-btn").addEventListener("click", () => {
  pdAutoSimRunning = false;
  document.getElementById("pd-autosim-status").textContent += " -- Stopped by user.";
});

// ---- init ----
// Team labels first so the board's "sold to" column and log render with
// the Yahoo names on the very first paint, not only after a refresh.
populateTeamDropdowns().then(() => {
  refreshHeader();
  refreshModeBanner();
  refreshOperationalStatus();
  loadBoard();
});
