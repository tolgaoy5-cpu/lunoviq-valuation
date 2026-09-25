/* Lunoviq web UI — vanilla JS, no build step. Talks to lunoviq/web/server.py. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = (d) => new Intl.NumberFormat("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const isNum = (v) => typeof v === "number" && isFinite(v);
const money = (v) => (isNum(v) ? (v < 0 ? "−$" : "$") + nf(2).format(Math.abs(v)) : "—");
let unitDiv = 1e3;                               // model unit of the run shown (USD thousands or millions)
const bn = (v) => (isNum(v) ? (v < 0 ? "−$" : "$") + nf(1).format(Math.abs(v) * unitDiv / 1e9) + "B" : "—");
const pct = (v, d = 1) => (isNum(v) ? nf(d).format(v * 100) + "%" : "—");
const mult = (v) => (isNum(v) ? nf(1).format(v) + "x" : "—");
const signed = (v) => (isNum(v) ? (v >= 0 ? "+" : "−") + nf(1).format(Math.abs(v * 100)) + "%" : "—");
const date = (s) => (s ? new Date(s).toLocaleString("en-US", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "");

const STEPS = [
  ["fundamentals", "Fetching SEC financials"],
  ["market", "Market data and cost of capital"],
  ["excel", "Populating the Excel model"],
  ["peers", "Selecting peer companies"],
  ["recalc", "Recalculating in Excel"],
  ["audit", "Independent audit"],
];
const METHOD_LABEL = { dcf_perpetuity: "DCF · perpetuity growth", dcf_exit: "DCF · exit multiple", trading_comps: "Trading comparables", precedents: "Precedent transactions" };
const INPUT_LABEL = {
  ctl_SharePrice: "Share price", val_RiskFree: "Risk-free rate", val_ERP: "Equity risk premium", val_Beta: "Beta",
  val_CostOfDebt: "Cost of debt (pre-tax)", drv_TaxRate: "Tax rate", val_DebtWeight: "Debt weight",
  val_EquityWeight: "Equity weight", val_ExitMultiple: "Exit multiple (EV/EBITDA)", val_TerminalGrowth: "Terminal growth",
  gm_RevenueGrowth: "Revenue growth (2026–30)", drv_CapexPct: "Capex / revenue", drv_DSO: "Receivable days",
  drv_DIO: "Inventory days", drv_DPO: "Payable days", drv_Payout: "Dividend payout", drv_LifeExisting: "Asset life",
  val_NonOpAssets: "Non-operating investments", val_MinorityInterest: "Minority interest", fin_DebtRate: "Rate on existing debt",
  fin_CashRate: "Interest rate on cash", drv_NonOpIncome: "Non-operating income (forecast)", scn_RevGrowthDelta: "Scenario range: growth",
  scn_CogsMarginDelta: "Scenario range: cost margin",
};

const ITEM_LABEL = { interest_expense: "interest expense", operating_income: "operating income", inventory: "inventory",
  deferred_tax_liability: "deferred tax liability", receivables: "receivables", payables: "payables",
  total_debt: "total debt", cogs: "cost of goods sold", sga: "SG&A", d_and_a: "D&A",
  current_assets: "current assets", current_liabilities: "current liabilities", pretax_income: "pre-tax income" };
const IGNORE_MISSING = ["tax_loss_carryforward", "total_liabilities", "dividends", "interest_income", "minority_interest",
  "nonop_investments", "other_opex", "other_assets", "other_liabilities", "other_equity", "capex"];

function rationaleText(r) {
  const m = /^Same Damodaran industry \((.*)\); revenue ([\d.]+)bn vs target ([\d.]+)bn$/.exec(r || "");
  if (!m) return r || "";
  return `Same Damodaran industry (${m[1]}); revenue $${m[2]}B vs target $${m[3]}B`;
}

/* ---------------------------------------------------------------- api */
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || "Request failed."), { status: r.status });
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (t.hidden = true), 3200);
}

/* ---------------------------------------------------------------- router */
function show(id) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== id));
  $("#search-top").hidden = id === "view-home" || id === "view-stopped";
}

let pollTimer = null;
async function route() {
  clearTimeout(pollTimer);
  const [, kind, arg] = (location.hash || "#/").split("/");
  if (kind === "job" && arg) return pollJob(arg);
  if (kind === "run" && arg) {
    try {
      const d = await api("/api/summary?run=" + encodeURIComponent(arg));
      renderResult(d);
    } catch (e) {
      renderError("Run not found", e.message, "", null);
    }
    return;
  }
  renderHome();
}
window.addEventListener("hashchange", route);

/* ---------------------------------------------------------------- search */
function bindSearch(form) {
  const input = $("input", form), list = $(".suggest", form);
  let items = [], sel = -1, seq = 0;
  const close = () => { list.hidden = true; sel = -1; input.setAttribute("aria-expanded", "false"); };
  const paint = () => {
    list.innerHTML = items.map((it, i) => `<li role="option" id="${form.id}-o${i}" aria-selected="${i === sel}" data-t="${esc(it.ticker)}"><span class="tk">${esc(it.ticker)}</span><span class="nm">${esc(it.name)}</span></li>`).join("");
    list.hidden = !items.length;
    input.setAttribute("aria-expanded", String(!list.hidden));
    if (sel >= 0) input.setAttribute("aria-activedescendant", `${form.id}-o${sel}`);
  };
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.addEventListener("input", async () => {
    const q = input.value.trim(), my = ++seq;
    if (!q) { items = []; return close(); }
    try {
      const res = await api("/api/tickers?q=" + encodeURIComponent(q));
      if (my !== seq) return;
      items = Array.isArray(res) ? res : [];
      sel = items.length ? 0 : -1;
      paint();
    } catch { items = []; close(); }
  });
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") { sel = Math.min(items.length - 1, sel + 1); paint(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = Math.max(0, sel - 1); paint(); e.preventDefault(); }
    else if (e.key === "Escape") close();
  });
  list.addEventListener("mousedown", (e) => {
    const li = e.target.closest("li");
    if (li) { input.value = li.dataset.t; close(); startRun(li.dataset.t); }
  });
  input.addEventListener("blur", () => setTimeout(close, 120));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const pick = !list.hidden && items[sel] ? items[sel].ticker : input.value.trim().toUpperCase();
    close();
    if (!pick) { input.focus(); return toast("Type a ticker or company name."); }
    startRun(pick);
  });
}

/* ---------------------------------------------------------------- run */
let lastRequest = null;
async function startRun(ticker, overrides = {}) {
  lastRequest = { ticker, overrides };
  try {
    const { job } = await api("/api/runs", { method: "POST", body: JSON.stringify({ ticker, overrides }) });
    location.hash = "#/job/" + job;
  } catch (e) {
    toast(e.message);
  }
}

async function pollJob(id) {
  let job;
  try { job = await api("/api/runs/" + id); }
  catch { return renderError("Job not found", "If the app was restarted, start the run again.", "", null); }
  if (job.status === "done") { history.replaceState(null, "", "#/run/" + job.run); return renderResult({ ...job.summary, run: job.run }); }
  if (job.status === "error") return renderError(job.ticker, job.error, job.detail, job.retryable ? job.ticker : null);
  renderRunning(job);
  pollTimer = setTimeout(() => pollJob(id), 1000);
}

const stepStart = {};
function renderRunning(job) {
  show("view-run");
  document.title = `Building ${job.ticker} · Lunoviq`;
  $("#run-title").textContent = job.ticker;
  $("#run-eyebrow").textContent = job.status === "queued" ? `Queued${job.queue_position ? " (position " + job.queue_position + ")" : ""}` : "Building the model";
  const idx = STEPS.findIndex(([k]) => k === job.step);
  if (job.step && !stepStart[job.id + job.step]) stepStart[job.id + job.step] = Date.now();
  $("#run-steps").innerHTML = STEPS.map(([k, label], i) => {
    const cls = i < idx ? "done" : i === idx ? "active" : "";
    const secs = i === idx && stepStart[job.id + k] ? Math.round((Date.now() - stepStart[job.id + k]) / 1000) + " s" : "";
    return `<li class="${cls}"><span class="ic" aria-hidden="true"></span><span>${label}</span><span class="t">${secs}</span></li>`;
  }).join("");
}

function renderError(title, msg, detail, ticker) {
  show("view-error");
  document.title = "Error · Lunoviq";
  $("#err-title").textContent = title || "Error";
  $("#err-msg").textContent = msg || "";
  $("#err-detail").textContent = detail || "—";
  $("#err-detail").closest("details").hidden = !detail;
  const retry = $("#err-retry");
  retry.hidden = !ticker;
  retry.onclick = () => startRun(ticker, (lastRequest && lastRequest.ticker === ticker && lastRequest.overrides) || {});
}

/* ---------------------------------------------------------------- home */
async function renderHome() {
  show("view-home");
  document.title = "Lunoviq";
  $("#q-home").value = "";
  let rows = [];
  try { rows = await api("/api/history"); } catch { /* shown as empty */ }
  $("#recent-empty").hidden = rows.length > 0;
  $("#recent-table").hidden = rows.length === 0;
  $("#recent-count").textContent = rows.length ? rows.length + (rows.length === 1 ? " run" : " runs") : "";
  $("#recent-table tbody").innerHTML = rows.map((r) => `
    <tr class="link" tabindex="0" data-run="${esc(r.run)}">
      <td><div class="co"><b>${esc(r.ticker)}</b><span>${esc(title(r.company))}</span></div></td>
      <td class="num">${money(r.price)}</td>
      <td class="num">${money(r.methods.dcf_perpetuity)}</td>
      <td class="num">${money(r.methods.dcf_exit)}</td>
      <td class="num">${money(r.methods.trading_comps)}</td>
      <td class="muted small">${date(r.generated)}</td>
      <td>${r.healthy ? '<span class="pill ok">Checks OK</span>' : '<span class="pill warn">Review</span>'}</td>
    </tr>`).join("");
  document.querySelectorAll("#recent-table tr.link").forEach((tr) => {
    const go = () => (location.hash = "#/run/" + tr.dataset.run);
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => e.key === "Enter" && go());
  });
}

function title(s) {
  if (!s) return "";
  return s.toLowerCase().replace(/(^|[\s(&-])([a-z])/g, (m, a, b) => a + b.toUpperCase()).replace(/\b(Inc|Co|Corp|Plc|Llc)\b/g, (m) => m);
}

/* ---------------------------------------------------------------- charts */
function niceTicks(lo, hi, n = 5) {
  const span = hi - lo || 1, step0 = span / n, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
  return out;
}

// charts are drawn at the width they are shown, so text stays at its real size
const chartWidth = (max) => Math.max(300, Math.min(max, (window.innerWidth || max) - 72));

function footballField(d) {
  const rows = d.methods.filter((m) => m.key !== "precedents");
  const vals = rows.flatMap((m) => [m.low, m.high, m.value]).filter(isNum).concat([d.price]);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.08 || 1;
  lo = Math.max(0, lo - pad); hi += pad;
  const W = chartWidth(640), L = W < 480 ? 104 : 150, R = 18, rowH = 40, top = 16, H = top + rows.length * rowH + 30;
  const x = (v) => L + ((v - lo) / (hi - lo)) * (W - L - R);
  const ticks = niceTicks(lo, hi, 5);
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Value range by method and market price">`;
  ticks.forEach((t) => { s += `<line class="gridl" x1="${x(t)}" x2="${x(t)}" y1="${top - 4}" y2="${H - 26}"/><text x="${x(t)}" y="${H - 10}" text-anchor="middle">${nf(0).format(t)}</text>`; });
  rows.forEach((m, i) => {
    const y = top + i * rowH + rowH / 2;
    s += `<text class="lab" x="0" y="${y + 4}">${METHOD_LABEL[m.key]}</text>`;
    if (isNum(m.low) && isNum(m.high)) {
      s += `<rect class="${i === 0 ? "band" : "band-2"}" x="${x(m.low)}" y="${y - 9}" width="${Math.max(2, x(m.high) - x(m.low))}" height="18" rx="3"><title>${money(m.low)} – ${money(m.high)}</title></rect>`;
    }
    if (isNum(m.value)) s += `<circle class="dot" cx="${x(m.value)}" cy="${y}" r="5"><title>${money(m.value)}</title></circle>`;
  });
  const px = x(d.price);
  s += `<line class="price" x1="${px}" x2="${px}" y1="${top - 8}" y2="${H - 26}"/><text class="price-lab" x="${px}" y="${top - 10 < 8 ? 8 : top - 10}" text-anchor="${px > W - 80 ? "end" : "middle"}">Price ${money(d.price)}</text>`;
  return s + "</svg>";
}

function finChart(d) {
  const f = d.financials, n = d.years.length, W = chartWidth(1040), H = W < 600 ? 220 : 280, L = 34, B = 26, T = 10;
  const vals = f.revenue.concat(f.ebitda).filter(isNum);
  const hi = Math.max(...vals) * 1.08, ticks = niceTicks(0, hi / 1e6, 4);
  const y = (v) => T + (1 - v / (ticks[ticks.length - 1] * 1e6)) * (H - T - B);
  const slot = (W - L) / n, bw = Math.min(26, slot * 0.32);
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Revenue and EBITDA, actual and forecast">`;
  ticks.forEach((t) => { s += `<line class="gridl" x1="${L}" x2="${W}" y1="${y(t * 1e6)}" y2="${y(t * 1e6)}"/><text x="${L - 8}" y="${y(t * 1e6) + 4}" text-anchor="end">${nf(0).format(t)}</text>`; });
  d.years.forEach((yr, i) => {
    const cx = L + slot * i + slot / 2, fc = i >= 3;
    if (isNum(f.revenue[i])) s += `<rect class="${fc ? "bar-f" : "bar-h"}" x="${cx - bw - 1}" y="${y(f.revenue[i])}" width="${bw}" height="${H - B - y(f.revenue[i])}" rx="2"><title>${yr} revenue ${bn(f.revenue[i])}</title></rect>`;
    if (isNum(f.ebitda[i]) && f.ebitda[i] > 0) s += `<rect class="${fc ? "bar-ef" : "bar-e"}" x="${cx + 1}" y="${y(f.ebitda[i])}" width="${bw}" height="${H - B - y(f.ebitda[i])}" rx="2"><title>${yr} EBITDA ${bn(f.ebitda[i])}</title></rect>`;
    s += `<text x="${cx}" y="${H - 8}" text-anchor="middle">${esc(W < 600 ? yr.slice(2) : yr)}</text>`;
  });
  const dx = L + slot * 3;
  s += `<line class="div" x1="${dx}" x2="${dx}" y1="${T}" y2="${H - B}"/><text x="${dx + 6}" y="${T + 10}">forecast →</text>`;
  return s + "</svg>";
}

function heatmap(d) {
  const g = d.grid, cw = d.wacc.wacc, tg = d.wacc.terminal_growth;
  if (!g.wacc.every(isNum)) return '<p class="muted">No sensitivity table.</p>';
  const ci = g.wacc.reduce((b, v, i) => (Math.abs(v - cw) < Math.abs(g.wacc[b] - cw) ? i : b), 0);
  const cj = g.growth.reduce((b, v, j) => (Math.abs(v - tg) < Math.abs(g.growth[b] - tg) ? j : b), 0);
  let s = `<div class="table-wrap" style="border:0"><table class="heat"><thead><tr><th>WACC ↓ / growth →</th>${g.growth.map((v) => `<th>${pct(v)}</th>`).join("")}</tr></thead><tbody>`;
  g.price.forEach((row, i) => {
    s += `<tr><th>${pct(g.wacc[i])}</th>`;
    row.forEach((p, j) => {
      const prem = isNum(p) ? p / d.price - 1 : 0, a = Math.min(0.55, Math.abs(prem) * 0.9 + 0.06);
      const col = prem >= 0 ? `rgba(var(--heat-pos),${a})` : `rgba(var(--heat-neg),${a})`;
      s += `<td class="${i === ci && j === cj ? "cur" : ""}" style="background:${col}" title="WACC ${pct(g.wacc[i])}, growth ${pct(g.growth[j])}: ${money(p)} (${signed(prem)})">${isNum(p) ? nf(0).format(p) : "—"}</td>`;
    });
    s += "</tr>";
  });
  return s + "</tbody></table></div>";
}

function bridge(d) {
  const b = d.bridge;
  const items = [
    ["PV of 2026–30 cash flows", b.pv_discrete, "p"],
    ["PV of terminal value (after 2030)", b.pv_terminal, "p"],
    ["Enterprise value", b.ev, "t"],
    ["− Net debt (debt − cash)", -(b.net_debt_adj + (b.nonop || 0) - (b.minority || 0)), "n"],
    ["+ Non-operating investments", b.nonop, "p"],
    ["− Minority interest", -(b.minority || 0), "n"],
    ["Equity value", b.equity, "t"],
  ].filter(([, v]) => isNum(v) && Math.abs(v) > 0);
  const max = Math.max(...items.map(([, v]) => Math.abs(v)));
  let s = '<div class="bridge">';
  items.forEach(([lab, v, k]) => {
    const w = (Math.abs(v) / max) * 100;
    s += `<div class="br"><span>${lab}</span><span class="track"><span class="seg ${k === "t" ? "tot" : v < 0 ? "neg" : ""}" style="left:0;width:${w}%"></span></span><span class="v">${bn(v)}</span></div>`;
  });
  s += `<div class="br"><span><b>÷ ${nf(0).format(d.shares * unitDiv / 1e6)}M diluted shares</b></span><span></span><span class="v"><b>${money(d.methods[0].value)}</b></span></div></div>`;
  const tshare = isNum(b.pv_terminal) && isNum(b.ev) ? b.pv_terminal / b.ev : null;
  return s + `<p class="axis-note">${pct(tshare, 0)} of enterprise value comes from after 2030${tshare > 0.75 ? " — the value is highly sensitive to terminal growth and WACC." : "."}</p>`;
}

/* ---------------------------------------------------------------- result */
function verdictText(d) {
  const vals = d.methods.filter((m) => m.key !== "precedents" && isNum(m.value)).map((m) => m.value);
  const lo = Math.min(...vals), hi = Math.max(...vals), c = d.dcf_checks || {};
  const out = [];
  out.push(`The three methods give <b>${money(lo)} – ${money(hi)}</b> per share; the market price is <b>${money(d.price)}</b>.`);
  if (d.price > hi) out.push(`The price is <b>${pct(d.price / hi - 1, 0)}</b> above the range: the market is pricing in more growth or less risk than the model's standard assumptions. That alone does not mean "overvalued" — review the growth assumption first.`);
  else if (d.price < lo) out.push(`The price is <b>${pct(1 - d.price / lo, 0)}</b> below the range: the model is more optimistic than the market. Check that the assumptions fit the company.`);
  else out.push("The price is inside the range: the valuation is consistent with the market.");
  if (isNum(c.implied_terminal_growth)) out.push(`<b>Reverse DCF:</b> today's price implies <b>${pct(c.implied_terminal_growth)}</b> annual growth after 2030 (model: ${pct(d.wacc.terminal_growth)})${isNum(c.implied_wacc) ? ` — or a WACC of <b>${pct(c.implied_wacc)}</b> instead of ${pct(d.wacc.wacc)}` : ""}.`);
  return out.map((p) => `<p>${p}</p>`).join("");
}

function reviewItems(d) {
  const out = [];
  const g = d.inputs.gm_RevenueGrowth;
  if (g && g.status === "override") out.push(["info", `<b>Revenue growth is your input:</b> ${pct(Array.isArray(g.value) ? g.value[0] : g.value)} a year for 2026–30.`]);
  else if (g && Array.isArray(g.value)) out.push(["", `<b>Revenue growth</b> is derived mechanically from history: it starts at ${pct(g.value[0])} and fades to ${pct(g.value[4])} by 2030. If you know the company (or its guidance), enter your own view below.`]);
  const tg = d.inputs.val_TerminalGrowth;
  if (tg && tg.status === "template_default") out.push(["", `<b>Terminal growth</b> is the standard ${pct(tg.value)} (long-run inflation / GDP). Most of the value depends on it.`]);
  if ((d.peer_source || "").startsWith("automatic")) out.push(["", `<b>Peers were selected automatically</b> (same industry, closest revenue). If they do not fit, provide your own list in <code>peers/${esc(d.ticker)}.csv</code>.`]);
  (d.peers_skipped || []).forEach((m) => out.push(["info", `Peer excluded: ${esc(skipText(m))}`]));
  out.push(["info", "<b>Precedent transactions</b> (M&A deals) have no free data source; this method is left empty."]);
  if (d.inputs.fin_DebtRate && d.inputs.fin_DebtRate.value === 0) out.push(["info", "Interest expense is not reported separately; it sits in other income historically and stays there in the forecast."]);
  const miss = (d.missing_items || []).filter((k) => !IGNORE_MISSING.includes(k)).map((k) => ITEM_LABEL[k] || k);
  if (miss.length) out.push(["info", `Not reported separately in SEC data: ${esc(miss.join(", "))} (included in the model's "other" lines).`]);
  out.push(["info", unitDiv === 1e6 ? "Excel figures are in <b>USD millions</b> (50,300 = $50.3B)." : "Excel figures are in <b>USD thousands</b> (50,300 = $50.3M)."]);
  return out.map(([c, t]) => `<li class="${c}"><span>${t}</span></li>`).join("");
}

function skipText(m) {
  const [tk, why = ""] = String(m).split(" skipped: ");
  if (/share count/.test(why)) return `${tk} — SEC reports no single share count (dual-class shares)`;
  if (/positive earnings/.test(why)) return `${tk} — no positive earnings, multiples not meaningful`;
  if (/Yahoo|finance/.test(why)) return `${tk} — share price unavailable`;
  return tk + (why ? " — " + why : "");
}

let lastResult = null;
window.addEventListener("resize", () => {
  clearTimeout(window.__rz);
  window.__rz = setTimeout(() => { if (lastResult && !$("#view-result").hidden) renderResult(lastResult, true); }, 200);
});

function renderResult(d, keepScroll = false) {
  lastResult = d;
  unitDiv = d.unit_div || 1e3;
  show("view-result");
  document.title = `${d.ticker} · Lunoviq`;
  const m = Object.fromEntries(d.methods.map((x) => [x.key, x]));
  const healthy = d.health && d.health.Overall === "OK" && !(d.errors || []).length;
  const a = d.audit || {};
  const overrides = d.review.filter((r) => r.status === "override");
  const gIn = d.inputs.gm_RevenueGrowth || {};
  const growth0 = Array.isArray(gIn.value) ? gIn.value[0] : isNum(gIn.value) ? gIn.value : null;
  const growthNote = gIn.status === "override" ? `Now: your input, ${pct(growth0)} every year` : `Automatic: starts at ${pct(growth0)}, fades to ${pct(d.wacc.terminal_growth)}`;
  const methodCard = (k) => {
    const x = m[k];
    if (!x || !isNum(x.value)) return `<div class="m na"><div class="lbl">${METHOD_LABEL[k]}</div><div class="val">no data</div></div>`;
    const prem = x.value / d.price - 1;
    return `<div class="m"><div class="lbl">${METHOD_LABEL[k]}</div><div class="val">${money(x.value)}</div><div class="vs ${prem >= 0 ? "up" : "down"}">${signed(prem)} vs price</div></div>`;
  };
  const f = d.financials, yrs = d.years;
  const finRows = [["Revenue", f.revenue, bn], ["EBITDA", f.ebitda, bn],
    ["EBITDA margin", f.ebitda.map((v, i) => (isNum(v) && f.revenue[i] ? v / f.revenue[i] : null)), (v) => pct(v)],
    ["Net income", f.net_income, bn], ["Unlevered free cash flow", f.ufcf, bn]];
  const peersRows = d.peers.map((p) => `<tr><td>${esc(p.name)}<div class="src-method">${esc(rationaleText(p.rationale))}</div></td><td class="num">${mult(p.ev_ebitda)}</td><td class="num">${mult(p.pe)}</td><td class="num">${mult(p.ev_revenue)}</td></tr>`).join("");
  const benchKeys = ["EBITDA Margin", "Net Profit Margin", "ROE", "Debt / EBITDA", "Revenue Growth", "Current Ratio"];
  const benchTr = { "EBITDA Margin": "EBITDA margin", "Net Profit Margin": "Net margin", ROE: "Return on equity", "Debt / EBITDA": "Debt / EBITDA", "Revenue Growth": "Revenue growth", "Current Ratio": "Current ratio" };
  const isPct = (k) => !["Debt / EBITDA", "Current Ratio"].includes(k);
  const benchRows = d.benchmarks.filter((b) => benchKeys.includes(b.metric)).map((b) => {
    const fm = isPct(b.metric) ? (v) => pct(v) : mult;
    return `<tr><td>${benchTr[b.metric]}</td><td class="num">${fm(b.company)}</td><td class="num muted">${fm(b.low)} – ${fm(b.high)}</td><td class="num">${fm(b.median)}</td></tr>`;
  }).join("");
  const sources = Object.entries(d.inputs).filter(([k]) => INPUT_LABEL[k]).map(([k, v]) => {
    const val = Array.isArray(v.value) ? v.value.map((x) => (isNum(x) ? nf(Math.abs(x) < 1 ? 3 : 0).format(x) : "—")).join(" · ") : isNum(v.value) ? (Math.abs(v.value) < 1 ? pct(v.value, 2) : nf(2).format(v.value)) : "—";
    const st = v.status === "override" ? '<span class="pill neutral">your input</span>' : v.status === "template_default" ? '<span class="pill warn">standard default</span>' : "";
    return `<tr><td>${INPUT_LABEL[k]} ${st}</td><td class="num">${val}</td><td>${esc(v.source)}<div class="src-method">${esc(v.method)}</div></td><td class="muted small">${esc(v.as_of || "")}</td></tr>`;
  }).join("");

  $("#view-result").innerHTML = `
    <div class="res-head">
      <div>
        <p class="eyebrow">${esc(d.ticker)} · ${esc(d.scenario || "Base")} case · 5-year DCF</p>
        <h1>${esc(title(d.company))}</h1>
        <div class="meta"><span>Price <b>${money(d.price)}</b></span><span>WACC <b>${pct(d.wacc.wacc)}</b></span><span>${date(d.generated)}</span>
          ${healthy ? '<span class="pill ok">All checks OK</span>' : '<span class="pill crit">Checks need attention</span>'}
          ${a.lines_checked ? `<span class="pill ${a.mismatches ? "crit" : "ok"}">Audit ${a.lines_checked - a.mismatches}/${a.lines_checked}</span>` : ""}</div>
        ${overrides.length ? `<p class="override-note"><span class="pill neutral">Changed assumptions: ${overrides.map((o) => INPUT_LABEL[o.name] || o.name).join(", ")}</span></p>` : ""}
      </div>
      <div class="res-actions">
        <button class="btn btn-primary" id="open-xl">Open in Excel</button>
        <a class="btn" href="/api/download?run=${encodeURIComponent(d.run)}">Download Excel</a>
        <a class="btn" href="#/">New company</a>
      </div>
    </div>

    <section class="card verdict" style="margin-top:22px" aria-labelledby="v-h">
      <div>
        <h2 id="v-h">Valuation</h2>
        <p class="how">Value per share from three independent methods. Dot = the method's result, band = its sensitivity range, line = today's price.</p>
        ${footballField(d)}
        <div class="methods">${methodCard("dcf_perpetuity")}${methodCard("dcf_exit")}${methodCard("trading_comps")}</div>
      </div>
      <div>
        <h3>How to read this</h3>
        <div class="readout">${verdictText(d)}</div>
      </div>
    </section>

    <div class="grid g-2">
      <section class="card" aria-labelledby="w-h">
        <div class="card-head"><h2 id="w-h">Cost of capital (WACC)</h2><span class="num">${pct(d.wacc.wacc, 2)}</span></div>
        <p class="how">The rate used to discount the company's cash flows to today. Every input is calculated from market data with a standard method.</p>
        <dl class="kv">
          <dt>Risk-free rate (10-year US Treasury)</dt><dd>${pct(d.wacc.risk_free, 2)}</dd>
          <dt>Beta (Blume-adjusted)</dt><dd>${nf(2).format(d.wacc.beta || 0)}</dd>
          <dt>Equity risk premium (Damodaran)</dt><dd>${pct(d.wacc.erp, 2)}</dd>
          <dt class="total">Cost of equity</dt><dd class="total">${pct(d.wacc.cost_of_equity, 2)}</dd>
          <span class="sep"></span>
          <dt>Cost of debt (synthetic rating)</dt><dd>${pct(d.wacc.pretax_kd, 2)}</dd>
          <dt>After-tax cost of debt</dt><dd>${pct(d.wacc.after_tax_kd, 2)}</dd>
          <dt>Weights (equity / debt)</dt><dd>${pct(d.wacc.equity_weight, 0)} / ${pct(d.wacc.debt_weight, 0)}</dd>
          <span class="sep"></span>
          <dt class="total">WACC</dt><dd class="total">${pct(d.wacc.wacc, 2)}</dd>
          <dt>Terminal growth</dt><dd>${pct(d.wacc.terminal_growth, 2)}</dd>
        </dl>
        <p class="formula">Cost of equity = ${pct(d.wacc.risk_free, 2)} + ${nf(2).format(d.wacc.beta || 0)} × ${pct(d.wacc.erp, 2)}</p>
      </section>
      <section class="card" aria-labelledby="b-h">
        <div class="card-head"><h2 id="b-h">From enterprise value to value per share</h2></div>
        <p class="how">DCF (perpetuity growth) bridge: the present value of cash flows, adjusted for debt, cash and investments, becomes value per share.</p>
        ${bridge(d)}
      </section>
    </div>

    <section class="card" style="margin-top:18px" aria-labelledby="f-h">
      <div class="card-head"><h2 id="f-h">Financials</h2><span class="muted small">2023–25 actual (SEC) · 2026–30 forecast</span></div>
      ${finChart(d)}
      <div class="legend"><span><i style="background:var(--accent)"></i>Revenue (actual)</span><span><i style="background:var(--band)"></i>Revenue (forecast)</span><span><i style="background:var(--ink-2);opacity:.75"></i>EBITDA</span><span>Axis: USD billions</span></div>
      <div class="table-wrap" style="margin-top:14px"><table class="tbl"><thead><tr><th></th>${yrs.map((y) => `<th class="num">${esc(y)}</th>`).join("")}</tr></thead><tbody>
        ${finRows.map(([lab, arr, fm]) => `<tr><td>${lab}</td>${arr.map((v) => `<td class="num">${fm(v)}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>
    </section>

    <div class="grid g-2">
      <section class="card" aria-labelledby="s-h">
        <div class="card-head"><h2 id="s-h">Sensitivity</h2></div>
        <p class="how">DCF value per share ($): rows are WACC, columns terminal growth. Green is above the price, red below; the outlined cell is the current assumption.</p>
        ${heatmap(d)}
      </section>
      <section class="card" aria-labelledby="p-h">
        <div class="card-head"><h2 id="p-h">Peers</h2><span class="muted small">${(d.peer_source || "").startsWith("analyst") ? "your list" : "automatic selection"}</span></div>
        <div class="table-wrap"><table class="tbl"><thead><tr><th>Company</th><th class="num">EV/EBITDA</th><th class="num">P/E</th><th class="num">EV/Revenue</th></tr></thead><tbody>${peersRows || '<tr><td colspan="4" class="muted">No peers found.</td></tr>'}</tbody></table></div>
        <h3 style="margin:16px 0 8px">Company vs peers (2030 forecast ↔ peers' latest year)</h3>
        <div class="table-wrap"><table class="tbl"><thead><tr><th>Ratio</th><th class="num">${esc(d.ticker)}</th><th class="num">Peer range</th><th class="num">Median</th></tr></thead><tbody>${benchRows}</tbody></table></div>
      </section>
    </div>

    <section class="card" style="margin-top:18px" aria-labelledby="a-h">
      <div class="card-head"><h2 id="a-h">Change assumptions</h2></div>
      <p class="how">Empty fields keep their automatic value. The model is rebuilt from the same template and audited again; the current run is kept.</p>
      <form class="form" id="ovr" novalidate>
        <div class="field"><label for="o-g">Revenue growth (every year)</label><div class="inp"><input id="o-g" inputmode="decimal" placeholder="${isNum(growth0) ? nf(1).format(growth0 * 100) : ""}"><span class="unit">%</span></div><div class="now">${growthNote}</div></div>
        <div class="field"><label for="o-t">Terminal growth</label><div class="inp"><input id="o-t" inputmode="decimal" placeholder="${nf(1).format((d.wacc.terminal_growth || 0) * 100)}"><span class="unit">%</span></div><div class="now">Must be below WACC (${pct(d.wacc.wacc)})</div></div>
        <div class="field"><label for="o-x">Exit multiple (EV/EBITDA)</label><div class="inp"><input id="o-x" inputmode="decimal" placeholder="${nf(1).format(d.bridge.exit_multiple || 0)}"><span class="unit">x</span></div><div class="now">Automatic: industry average</div></div>
      </form>
      <div class="form-actions"><button class="btn btn-primary" id="rerun">Rebuild model</button><span class="form-err" id="ovr-err" role="alert"></span></div>
    </section>

    <div class="grid g-2">
      <section class="card" aria-labelledby="c-h">
        <div class="card-head"><h2 id="c-h">Checks</h2></div>
        <p class="how">The model's own checks, an Excel error scan and an independent recalculation in Python.</p>
        <div class="checks">${Object.entries(d.health || {}).map(([k, v]) => `<span class="pill ${v === "OK" ? "ok" : v === "n/a" ? "neutral" : "crit"}">${esc(k)} ${esc(v)}</span>`).join("")}</div>
        <dl class="kv">
          <dt>Excel error cells</dt><dd>${(d.errors || []).length}</dd>
          <dt>Independent audit (forecast line-years)</dt><dd>${a.lines_checked ? `${a.lines_checked - a.mismatches} / ${a.lines_checked}` : "—"}</dd>
          <dt>Historical ratios vs SEC</dt><dd>${a.historical_ratio_issues && !a.historical_ratio_issues.length ? "match" : "—"}</dd>
          <dt>DCF (Excel = Python)</dt><dd>${d.dcf_checks && d.dcf_checks.ev_match ? "match" : "—"}</dd>
        </dl>
      </section>
      <section class="card" aria-labelledby="r-h">
        <div class="card-head"><h2 id="r-h">Review before presenting</h2></div>
        <ul class="review">${reviewItems(d)}</ul>
      </section>
    </div>

    <section class="card" style="margin-top:18px">
      <details class="sources"><summary>Data sources and assumptions (${Object.keys(d.inputs).filter((k) => INPUT_LABEL[k]).length})</summary>
        <div class="table-wrap"><table class="tbl"><thead><tr><th>Input</th><th class="num">Value</th><th>Source and method</th><th>As of</th></tr></thead><tbody>${sources}</tbody></table></div>
      </details>
    </section>`;

  $("#open-xl").onclick = async () => {
    try { await api("/api/open", { method: "POST", body: JSON.stringify({ run: d.run }) }); toast("Opening in Excel…"); }
    catch (e) { toast(e.message); }
  };
  $("#rerun").onclick = () => {
    const err = $("#ovr-err"); err.textContent = "";
    const read = (id) => { const s = $(id).value.trim().replace("%", ""); return s === "" ? null : Number(s); };
    const g = read("#o-g"), t = read("#o-t"), x = read("#o-x");
    if ([g, t, x].some((v) => v !== null && !isFinite(v))) return (err.textContent = "Enter a number (e.g. 4.5).");
    if (t !== null && t / 100 >= d.wacc.wacc) return (err.textContent = `Terminal growth must be below WACC (${pct(d.wacc.wacc)}).`);
    if (x !== null && (x < 1 || x > 80)) return (err.textContent = "Exit multiple must be between 1x and 80x.");
    const o = {};
    if (g !== null) o.revenue_growth = g / 100;
    if (t !== null) o.terminal_growth = t / 100;
    if (x !== null) o.exit_multiple = x;
    if (!Object.keys(o).length) return (err.textContent = "Fill in at least one field to change.");
    startRun(d.ticker, o);
  };
  if (!keepScroll) window.scrollTo(0, 0);
}

/* ---------------------------------------------------------------- quit */
function bindQuit() {
  const b = $("#quit");
  let armed = null;
  const reset = () => { b.textContent = "Quit"; b.classList.remove("arm"); armed = null; };
  const stop = async (force) => {
    try {
      await api("/api/quit", { method: "POST", body: JSON.stringify({ force }) });
    } catch (e) {
      if (e.status === 409) { b.textContent = "Model running — click to quit anyway"; b.classList.add("arm"); armed = "force"; return; }
    }
    clearTimeout(pollTimer);
    show("view-stopped");
    document.title = "Lunoviq (stopped)";
    b.hidden = true;
  };
  b.addEventListener("click", () => {
    if (armed === "force") return stop(true);
    if (armed) { clearTimeout(armed); return stop(false); }
    b.textContent = "Click again to quit"; b.classList.add("arm");
    armed = setTimeout(reset, 3000);
  });
}

/* ---------------------------------------------------------------- boot */
bindQuit();
bindSearch($("#search-home"));
bindSearch($("#search-top"));
route();
