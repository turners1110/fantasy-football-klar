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
    throw new Error(data.detail || res.statusText);
  }
  window.__connectionStatus = "OK";
  return data;
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
        const statusTxt = r.status === "AVAILABLE" ? "" : ` [${r.status}${r.owner ? " -- " + r.owner : ""}]`;
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
    tr.innerHTML = `<td>${t.team}${t.is_sam ? " (Sam)" : ""}</td><td>$${t.budget_remaining.toFixed(0)}</td>
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
  div.innerHTML = `<h4>${teamId}</h4>Budget: $${t.budget_remaining.toFixed(2)} | Open slots: ${t.open_slots} | Legal max: $${t.legal_max_bid.toFixed(2)}<br>
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
      <h4>${t.team} (${t.roster_count} players, $${t.budget_remaining.toFixed(0)} left)</h4>
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

  if (sortKey === "player") rows.sort((a, b) => a.player.localeCompare(b.player));
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
    tr.innerHTML = `
      <td>${r.player}</td><td>${r.position}</td><td>${r.projected_points.toFixed(0)}</td>
      <td>$${r.live_expected_price.toFixed(0)}</td><td>$${r.marginal_value.toFixed(0)}</td>
      <td>$${r.recommended_stop.toFixed(0)}</td><td>${r.recommendation}</td>
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
  await api("/nominate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ player }) });
  showNominated(player);
}
document.getElementById("nom-clear").addEventListener("click", async () => {
  await api("/nominate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ player: null }) });
  document.getElementById("nominated-panel").classList.add("hidden");
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
    toast("ERROR: " + e.message);
  }
});

// ---- My Roster ----
async function loadRoster() {
  const s = await api("/status");
  document.getElementById("roster-summary").innerHTML =
    `Budget remaining: <b>$${s.budget_remaining.toFixed(2)}</b> | Open slots: <b>${s.open_slots}</b> | ` +
    `Min reserve: <b>$${s.min_reserve.toFixed(2)}</b> | Legal max bid: <b>$${s.legal_max_bid.toFixed(2)}</b>`;
  const needs = Object.entries(s.position_needs).filter(([k, v]) => v > 0);
  document.getElementById("roster-needs").textContent = needs.length
    ? needs.map(([k, v]) => `${k}: ${v} needed`).join(" | ") : "All starting needs filled.";
  const visual = document.getElementById("roster-slots-visual");
  visual.innerHTML = "";
  for (let i = 0; i < 15; i++) {
    const span = document.createElement("span");
    span.className = i < s.roster.length ? "slot-filled" : "slot-open";
    span.textContent = i < s.roster.length ? "✓" : "";
    visual.appendChild(span);
  }
  const tbody = document.getElementById("roster-body");
  tbody.innerHTML = "";
  s.roster.forEach(p => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${p.position}</td><td>${p.display_name}</td><td>$${p.price.toFixed(0)}</td><td>${p.is_keeper ? "Yes" : "No"}</td>`;
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
    tr.innerHTML = `<td>${t.player}</td><td>${t.position}</td><td>${t.tier != null ? t.tier : "--"}</td>
      <td>${t.projected_points != null ? t.projected_points.toFixed(1) : "--"}</td>
      <td>${t.marginal_lineup_points != null ? t.marginal_lineup_points.toFixed(1) : "--"}</td>
      <td>$${t.team_specific_value_dollars.toFixed(0)}</td>
      <td>${t.expected_market_price_dollars != null ? "$" + t.expected_market_price_dollars.toFixed(0) : "--"}</td>
      <td>${ceiling}</td>
      <td><b>$${t.recommended_stop_dollars.toFixed(0)}</b></td>
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
    tr.innerHTML = `<td>${e.sequence}</td><td>${e.player}</td><td>${e.position}</td><td>${e.team}</td><td>$${e.price.toFixed(0)}</td>`;
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
  const r = await api("/undo", { method: "POST" });
  toast(r.message.split("\n")[0]);
  loadBoard(); refreshHeader(); loadLog();
});
document.getElementById("btn-correct").addEventListener("click", async () => {
  const player = document.getElementById("correct-player").value.trim();
  const team = document.getElementById("correct-team").value.trim();
  const price = parseFloat(document.getElementById("correct-price").value);
  try {
    const r = await api("/correct", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ player, team, price }) });
    toast(r.message.split("\n")[0]);
    loadBoard(); refreshHeader(); loadLog();
  } catch (e) { toast("ERROR: " + e.message); }
});
document.getElementById("btn-save").addEventListener("click", async () => {
  const name = document.getElementById("snapshot-name").value.trim();
  const r = await api("/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
  toast(r.message);
});
document.getElementById("btn-load").addEventListener("click", async () => {
  const name = document.getElementById("snapshot-name").value.trim();
  try {
    const r = await api("/load", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    toast("Loaded snapshot.");
    loadBoard(); refreshHeader(); loadLog();
  } catch (e) { toast("ERROR: " + e.message); }
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
  } catch (e2) { toast("ERROR switching mode: " + e2.message); }
});

// ---- V3 Part 14: official-team dropdown + operational status ----
async function populateTeamDropdowns() {
  try {
    const data = await api("/teams");
    ["modal-team", "correct-team"].forEach(id => {
      const sel = document.getElementById(id);
      if (!sel) return;
      data.teams.forEach(t => {
        const o = document.createElement("option");
        o.value = t; o.textContent = t;
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

document.getElementById("pd-start").addEventListener("click", async () => {
  pdSessionId = "practice-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);
  try { localStorage.setItem("sunday_practice_draft_session_id", pdSessionId); } catch (e) {}
  const seed = parseInt(document.getElementById("pd-seed").value) || 909001;
  const r = await api("/practice-draft/start", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: pdSessionId, seed }) });
  document.getElementById("pd-status").textContent = "Practice draft started (session " + pdSessionId + ").";
  renderPracticeDraftPending(r.pending);
});

document.getElementById("pd-pass-btn").addEventListener("click", async () => {
  const r = await api(`/practice-draft/${pdSessionId}/pass`, { method: "POST" });
  document.getElementById("pd-status").textContent = "Status: " + r.status;
  renderPracticeDraftPending(r.pending);
});

document.getElementById("pd-bid-btn").addEventListener("click", async () => {
  const amount = parseFloat(document.getElementById("pd-bid-amount").value);
  if (isNaN(amount)) { toast("Enter a bid amount."); return; }
  const r = await api(`/practice-draft/${pdSessionId}/bid`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ amount }) });
  document.getElementById("pd-status").textContent = "Status: " + r.status;
  document.getElementById("pd-bid-amount").value = "";
  renderPracticeDraftPending(r.pending);
});

document.getElementById("pd-undo-btn").addEventListener("click", async () => {
  const r = await api(`/practice-draft/${pdSessionId}/undo`, { method: "POST" });
  document.getElementById("pd-status").textContent = r.message;
  renderPracticeDraftPending(r.pending);
});

document.getElementById("pd-review-btn").addEventListener("click", async () => {
  if (!pdSessionId) { toast("Start a practice draft first."); return; }
  const r = await api(`/practice-draft/${pdSessionId}/review`);
  const out = document.getElementById("pd-review-output");
  out.textContent = JSON.stringify(r, null, 2);
  out.classList.remove("hidden");
});

// ---- init ----
refreshHeader();
refreshModeBanner();
populateTeamDropdowns();
refreshOperationalStatus();
loadBoard();
