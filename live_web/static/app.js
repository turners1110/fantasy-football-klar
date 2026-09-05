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
  if (rec.startsWith("STRONG_BUY") || rec.startsWith("BUY_AT_DISCOUNT") || rec.startsWith("PRIORITY")) return "rec-green";
  if (rec.startsWith("PASS") || rec.startsWith("INSUFFICIENT")) return "rec-red";
  if (rec === "INELIGIBLE") return "rec-gray";
  return "rec-yellow";
}

async function api(path, opts) {
  const res = await fetch(API + path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || res.statusText);
  }
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
    if (btn.dataset.tab === "log") { loadLog(); loadMarket(); }
  });
});

// ---- Draft Board ----
async function loadBoard() {
  const data = await api("/board");
  currentBoard = data.players;
  if (data.nominated) showNominated(data.nominated);
  renderBoard();
}

function renderBoard() {
  const posFilter = document.getElementById("f-position").value;
  const recFilter = document.getElementById("f-rec").value;
  const maxPrice = parseFloat(document.getElementById("f-maxprice").value) || null;
  const startingOnly = document.getElementById("f-starting").checked;
  const sortKey = document.getElementById("f-sort").value;

  let rows = currentBoard.filter(r => {
    if (posFilter && r.position !== posFilter) return false;
    if (recFilter && r.recommendation !== recFilter) return false;
    if (maxPrice && r.live_expected_price > maxPrice) return false;
    if (startingOnly && r.expected_role === "bench depth") return false;
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

["f-position", "f-rec", "f-maxprice", "f-starting", "f-sort"].forEach(id =>
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

async function showNominated(player) {
  const panel = document.getElementById("nominated-panel");
  panel.classList.remove("hidden");
  document.getElementById("nom-name").textContent = player;
  document.getElementById("nom-detail").textContent = "loading...";
  try {
    const c = await api("/check/" + encodeURIComponent(player));
    document.getElementById("nom-detail").innerHTML =
      `Expected: $${c.live_expected_price.toFixed(0)} | Conservative: $${c.conservative_price.toFixed(0)} | ` +
      `Marginal value: $${c.marginal_value.toFixed(0)} [${c.calculation_label}] | ` +
      `<b>RECOMMENDED STOP: $${c.recommended_stop.toFixed(0)} [${c.recommendation}]</b><br>${c.reason}`;
  } catch (e) {
    document.getElementById("nom-detail").textContent = "SOLVER_FAILURE / unavailable: " + e.message;
  }
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
  data.targets.forEach(t => {
    const tr = document.createElement("tr");
    tr.className = recClass(t.recommendation_class.includes("PRIORITY") || t.recommendation_class.includes("BUY") ? "BUY" :
                              t.recommendation_class.includes("PASS") ? "PASS" : "");
    tr.innerHTML = `<td>${t.player}</td><td>${t.position}</td><td>${t.total_score.toFixed(3)}</td>
      <td>${t.recommendation_class}</td><td>$${t.recommended_stop.toFixed(0)}</td>
      <td>$${t.expected_surplus_at_price.toFixed(1)}</td><td>$${t.starting_lineup_gain.toFixed(1)}</td>
      <td>$${t.team_specific_value.toFixed(1)}</td><td>${t.role_probability_score}</td>
      <td>${t.scarcity_score}</td><td>${t.tier_cliff_bonus}</td><td>${t.remaining_alternatives_count}</td>
      <td>${t.price_confidence}</td><td>${t.position_need_score}</td><td>${t.price_evidence_score}</td><td>${t.bench_probability}</td>`;
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

// ---- init ----
refreshHeader();
loadBoard();
