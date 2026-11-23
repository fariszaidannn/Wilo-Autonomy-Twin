// Outage outlook (bento at the top of the Building rail): if the city water or the power is cut now,
// when does each zone lose service, how much of the critical zones' demand is met, and how long is
// there to prepare. The backend recomputes it from the live twin state with the same hydraulics, so
// it follows the topology, the building settings and every disturbance and demand control.
import { get } from "./api.js";
import { topo, zoneHex, label } from "./topo.js";
import { t, onLang, pct, dur, hhmm } from "./i18n.js";
import { icon, esc } from "./ui.js";

const $ = (id) => document.getElementById(id);
const EVENTS = ["city_supply_cut", "power_outage"];
const WINDOWS = [6, 12, 24];
const REFRESH_MS = 10000;      // while the clock runs the plant drifts; a paused twin is a cache hit

let event = "city_supply_cut", windowH = 12;
try {
  const v = JSON.parse(localStorage.getItem("wat.outlook") || "{}");
  if (EVENTS.includes(v.event)) event = v.event;
  if (WINDOWS.includes(v.window)) windowH = v.window;
} catch { /* storage blocked */ }

let data = null, err = null, inflight = false, again = false;
let liveKey = "", lastFetch = 0, lastSim = null, stale = false;

const visible = () => !document.hidden && !$("p-building").hidden;
const matches = (d) => d && d.event === event && d.window_h === windowH;

async function refresh() {
  if (inflight) { again = true; return; }
  inflight = true;
  const ev = event, w = windowH;
  try {
    const r = await get(`/api/outlook?event=${ev}&window_h=${w}`);
    if (ev === event && w === windowH) { data = r; err = null; }
  } catch (e) {
    // 404: the page is newer than the running server (started before /api/outlook existed).
    if (ev === event && w === windowH) err = e.status === 404 ? t("o.restart") : e.message;
  } finally {
    inflight = false;
    lastFetch = Date.now();
    render();
    if (again) { again = false; refresh(); }
  }
}

function save() {
  try { localStorage.setItem("wat.outlook", JSON.stringify({ event, window: windowH })); } catch { /* ignore */ }
}

// ---------------- formatting ----------------
const nb = (s) => s.replace(/ /g, " ");   // a duration never breaks across lines
const beyond = () => nb(t("o.beyond", { h: windowH }));
const fmt = (m) => (m == null ? beyond() : nb(dur(m)));
// Range across the demand cases; null means "not within the window", so it sorts last.
function span(a, b) {
  const k = (v) => (v == null ? Infinity : v);
  const [lo, hi] = k(a) <= k(b) ? [a, b] : [b, a];
  return `${fmt(lo)} – ${fmt(hi)}`;
}
const cls = (p) => t("o.class")[p] ?? t("priority", { n: p });
const pctRange = (m) => t("o.range", { a: pct(Math.min(m.worst, m.best)), b: pct(Math.max(m.worst, m.best)) });
const sim = () => `<span class="o-sim">${icon("flask", "currentColor", 13)}${esc(t("o.sim"))}</span>`;
const foot = (note) => `<div class="o-foot"><p>${note}</p>${sim()}</div>`;

// ---------------- tiles ----------------
function tileThreshold(d) {
  const rows = d.zones.map((z) => {
    const tm = z.threshold_min;
    const sub = tm.worst == null && tm.best == null && tm.expected == null ? t("o.inservice") : span(tm.worst, tm.best);
    return `<li class="o-zone">
      <span class="o-dot" style="background:${zoneHex(z.zone)}" aria-hidden="true"></span>
      <span class="o-zn" title="${esc(label(z.zone))}"><b>${esc(z.zone)}</b> <span>${esc(cls(z.priority))}</span></span>
      <span class="o-zv"><b>${esc(fmt(tm.expected))}</b><small>${esc(sub)}</small></span></li>`;
  }).join("");
  let note = t(d.already_active ? "o.ttt.note.active" : "o.ttt.note");
  if (d.event === "power_outage") note += ` ${t(d.generator ? "o.gen.on" : "o.gen.off")}`;
  return `<article class="tile wide"><h3>${icon("timer", "currentColor", 16)}${esc(t("o.ttt"))}</h3>
    <ul class="o-zones">${rows}</ul>${foot(esc(note))}</article>`;
}

function tilePriority(d) {
  const zs = d.priority.zones.join(", ");
  const no = d.priority.no_action, mit = d.priority.mitigated, m = d.mitigation;
  let body = `<p class="o-k">${esc(t("o.noaction"))}</p><p class="o-big">${pct(no.expected)}</p><p class="o-sub">${esc(pctRange(no))}</p>`;
  let note = esc(t("o.prio.note", { zs, h: windowH }));
  if (mit && m) {
    const iso = m.zones.join(", ");
    body += `<hr class="o-sep"><p class="o-k">${esc(t("o.mitig"))}</p><p class="o-big mit">${pct(mit.expected)}</p><p class="o-sub">${esc(pctRange(mit))}</p>`;
    if (Math.abs(mit.expected - no.expected) < 0.005) note += ` ${esc(t("o.mit.nogain", { zs: iso }))}`;
    note += ` ${esc(t("o.mit.what", { zs: iso, p: m.below_pct }))}`;
  } else {
    note += ` ${esc(t("o.mit.none"))}`;
  }
  return `<article class="tile"><h3>${icon("gauge", "currentColor", 16)}${esc(t("o.prio", { zs }))}</h3>${body}${foot(note)}</article>`;
}

function tileLead(d) {
  const L = d.lead, e = L.expected;
  const big = fmt(e ? e.lead_min : null);
  const sub = e ? t("o.until", { z: e.zone }) : t("o.lead.none", { h: windowH });
  const range = t("o.lead.range", { w: fmt(L.worst ? L.worst.lead_min : null), b: fmt(L.best ? L.best.lead_min : null) });
  const early = [L.expected, L.worst, L.best].some((x) => x && x.before_detection);
  const note = d.already_active ? t("o.lead.note.active") : t("o.lead.note", { d: d.detection_min });
  return `<article class="tile"><h3>${icon("alarm", "currentColor", 16)}${esc(t("o.lead"))}</h3>
    <p class="o-big">${esc(big)}</p><p class="o-k">${esc(sub)}</p><p class="o-sub">${esc(range)}</p>
    ${early ? `<p class="o-alert">${icon("alert", "currentColor", 15)}<span>${esc(t("o.lead.early"))}</span></p>` : ""}
    ${foot(esc(note))}</article>`;
}

// ---------------- rendering ----------------
function renderControls() {
  const host = $("o-ctl");
  if (!host) return;
  host.innerHTML = `
    <div class="seg" role="group" aria-label="${esc(t("o.title"))}">${EVENTS.map((k) =>
      `<button type="button" data-ev="${k}" aria-pressed="${k === event}">${esc(t("o.event")[k])}</button>`).join("")}</div>
    <div class="seg" role="group" aria-label="${esc(t("o.window"))}">${WINDOWS.map((h) =>
      `<button type="button" data-h="${h}" aria-pressed="${h === windowH}">${h} ${esc(t("h"))}</button>`).join("")}</div>`;
}

function render() {
  const body = $("o-body");
  if (!body) return;
  $("o-title").textContent = t("o.title");
  const ok = matches(data);
  $("o-note").textContent = ok ? t("o.note", { t: hhmm(data.sim_time), m: t("o.demand")[data.demand_kind] || data.demand_kind }) : "";
  if (err && !ok) { body.innerHTML = `<p class="toast err">${esc(t("o.error"))} ${esc(err)}</p>`; return; }
  if (!ok) { body.innerHTML = `<p class="loading">${esc(t("o.loading"))}</p>`; return; }
  body.innerHTML = `<div class="bento">${tileThreshold(data)}${tilePriority(data)}${tileLead(data)}</div>`;
}

function select(ev, h) {
  if (ev === event && h === windowH) return;
  event = ev; windowH = h;
  save();
  $("o-ctl").querySelectorAll("[data-ev]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.ev === event)));
  $("o-ctl").querySelectorAll("[data-h]").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.h === windowH)));
  render();      // shows "calculating" until the matching result arrives
  refresh();
}

export function mountOutlook(host) {
  host.innerHTML = `<header><h2 id="o-title"></h2><span class="panel-note" id="o-note"></span></header>
    <div class="o-ctl" id="o-ctl"></div><div id="o-body" aria-live="polite"></div>`;
  renderControls();
  render();
  host.addEventListener("click", (e) => {
    const b = e.target.closest("[data-ev], [data-h]");
    if (!b) return;
    select(b.dataset.ev || event, b.dataset.h ? +b.dataset.h : windowH);
  });
}

// Called with every live state: refetch at once when the plant or the controls change,
// when the clock jumps, and otherwise every REFRESH_MS while the Building tab is visible.
export function syncOutlook(s) {
  if (!$("o-body")) return;
  const c = s.controls;
  const key = JSON.stringify([topo.pumps, topo.zones, c.pump_failed, c.grid_on, c.generator_available, c.city_supply_on,
    c.demand_mult, c.zone_mult, s.zones.map((z) => [z.id, z.allocation])]);
  const now = new Date(s.sim_time).getTime();
  // Same rule as the time bar: more than a few seconds' worth of running means a seek, not the clock.
  const jumped = lastSim != null && (now < lastSim || now - lastSim > (c.speed * 3 + 5) * 60000);
  lastSim = now;
  if (key !== liveKey || jumped) { liveKey = key; stale = true; }
  if (!visible()) return;
  if (stale || Date.now() - lastFetch >= REFRESH_MS) { stale = false; refresh(); }
}

onLang(() => { renderControls(); render(); });
window.addEventListener("wat:topo", () => { data = null; err = null; liveKey = ""; render(); });
