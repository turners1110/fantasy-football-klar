let sortKey = "my_target_price";
let sortDir = -1;
let lastState = null;

async function fetchState() {
  const res = await fetch("/api/state");
  lastState = await res.json();
  render(lastState);
}

function fmtMoney(v) {
  return v === null || v === undefined ? "-" : `$${Math.round(v)}`;
}

function render(s) {
  document.getElementById("summary").innerHTML = `
    <span><strong>${s.my_team}</strong></span>
    <span>My budget: <strong>${fmtMoney(s.my_remaining_budget)}</strong></span>
    <span>League pool left: ${fmtMoney(s.remaining_league_budget)}</span>
    <span>Live inflation: ${s.live_inflation_multiplier.toFixed(3)}x</span>
  `;

  const filterText = document.getElementById("filter").value.trim().toLowerCase();
  let rows = s.available.filter(p =>
    !filterText || p.player.toLowerCase().includes(filterText) || p.position.toLowerCase().includes(filterText)
  );
  rows = rows.slice().sort((a, b) => (a[sortKey] > b[sortKey] ? 1 : a[sortKey] < b[sortKey] ? -1 : 0) * sortDir);

  document.getElementById("pool-body").innerHTML = rows.map(p => `
    <tr>
      <td>${p.player}</td>
      <td>${p.position}</td>
      <td>${p.nfl_team || ""}</td>
      <td>${fmtMoney(p.recommended_live)}</td>
      <td class="target">${fmtMoney(p.my_target_price)}</td>
      <td>${p.need_multiplier.toFixed(2)}</td>
    </tr>
  `).join("");

  document.getElementById("player-list").innerHTML = s.available
    .map(p => `<option value="${p.player}">`).join("");

  document.getElementById("roster-body").innerHTML = s.my_roster.map(p => `
    <tr><td>${p.player}</td><td>${p.position}</td><td>${fmtMoney(p.price)}</td><td>${p.source}</td></tr>
  `).join("");

  document.getElementById("log-body").innerHTML = s.draft_log.map((e, i) => `
    <tr>
      <td>${s.draft_log.length - i}</td>
      <td>${e.player}</td><td>${e.position}</td><td>${fmtMoney(e.price)}</td>
      <td>${e.is_me ? "✓" : ""}</td><td>${fmtMoney(e.recommended_at_time)}</td>
    </tr>
  `).join("");
}

document.querySelectorAll("th[data-sort]").forEach(th => {
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    sortDir = sortKey === key ? -sortDir : -1;
    sortKey = key;
    if (lastState) render(lastState);
  });
});

document.getElementById("filter").addEventListener("input", () => lastState && render(lastState));

document.getElementById("pick-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const player = document.getElementById("pick-player").value.trim();
  const price = parseFloat(document.getElementById("pick-price").value);
  const is_me = document.getElementById("pick-is-me").checked;
  const msg = document.getElementById("msg");
  msg.textContent = "";
  try {
    const res = await fetch("/api/pick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ player, price, is_me }),
    });
    if (!res.ok) {
      const err = await res.json();
      msg.textContent = err.detail || "Error logging pick.";
      return;
    }
    document.getElementById("pick-form").reset();
    fetchState();
  } catch (err) {
    msg.textContent = String(err);
  }
});

document.getElementById("undo-btn").addEventListener("click", async () => {
  await fetch("/api/undo", { method: "POST" });
  fetchState();
});

document.getElementById("reset-btn").addEventListener("click", async () => {
  if (!confirm("Reset the whole draft? This clears all logged picks.")) return;
  await fetch("/api/reset", { method: "POST" });
  fetchState();
});

document.getElementById("export-btn").addEventListener("click", async () => {
  const res = await fetch("/api/export", { method: "GET" });
  const data = await res.json();
  const msg = document.getElementById("msg");
  msg.textContent = res.ok ? `Exported ${data.n_picks} picks -> ${data.written}` : (data.detail || "Export failed.");
});

document.getElementById("log-toggle").addEventListener("click", () => {
  const table = document.getElementById("log-table");
  table.hidden = !table.hidden;
});

fetchState();
setInterval(fetchState, 5000);
