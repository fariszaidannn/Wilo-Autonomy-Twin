// Building tab: line-map schematic, disturbance controls, load balancer rail, live trends.
import { get, post, ApiError } from "./api.js";
import { topo, loadTopo, pumpHex, zoneHex, label, layout, linePath, busPath, PX, PW, SX } from "./topo.js";
import { t, tf, getLang, onLang, num, pctRaw, hhmm } from "./i18n.js";
import { lineChart } from "./charts.js";
import { icon, statusPill, esc } from "./ui.js";

let S = null;            // last /api/state
let railKey = "";        // re-render the rail only when its content changes
let busy = false;
let toast = null;
let prevFeeds = null;   // feeds drawn last time; a new one draws itself in once

const $ = (id) => document.getElementById(id);
const pumpName = (id) => label(id);
const zoneName = (id) => label(id);
let topoKey = "";
const pumpTone = (st) => ({ Normal: "good", "Light load": "neutral", Crowded: "warn", Overloaded: "crit", Failed: "crit", "No power": "crit", "No suction": "crit" }[st] || "neutral");

// ---------------- schematic ----------------
function schematic(s, rec) {
  const lang = getLang();
  const w = (q) => 3 + Math.min(40, q) * 0.42;
  const tank = s.tank;
  const lvl = Math.max(0, Math.min(1, tank.volume_m3 / tank.capacity_m3));
  const parts = [];
  const { H, mid, PY, ZY } = layout(s.pumps.map((p) => p.id), s.zones.map((z) => z.id));
  // feeds per zone, in a stable pump order so parallel lines keep their place
  const feeds = [];
  s.zones.forEach((z) => {
    const fed = Object.entries(z.fed_by).filter(([, q]) => q > 0.05);
    fed.forEach(([pid, q], k) => feeds.push({ z: z.id, pid, q, k, n: fed.length }));
  });
  const proposed = rec && rec.moves ? rec.moves : [];
  // trunk from tank to pumps
  const out = tank.outflow_m3h;
  const tw = w(out);
  parts.push(`<path d="M104,${mid} H150" stroke="#0e1a2b" stroke-width="${tw}" fill="none" stroke-linecap="round"/>`);
  s.pumps.forEach((p) => {
    const q = p.flow_m3h;
    parts.push(`<path d="${busPath(150, mid, 196, PX, PY[p.id])}" stroke="#0e1a2b" stroke-width="${q > 0.05 ? w(q) : 2}" fill="none" stroke-linejoin="round" ${q > 0.05 ? "" : 'stroke-dasharray="3 5" opacity=".5"'}/>`);
  });
  // feed lines pump -> zone
  feeds.forEach((f) => {
    const off = (f.k - (f.n - 1) / 2) * 16;
    const d = linePath(PX + PW, PY[f.pid] + off * 0.6, SX, ZY[f.z] + off);
    const dur = Math.max(0.6, 6 - f.q / 6).toFixed(2);
    const isNew = prevFeeds && !prevFeeds.has(`${f.z}-${f.pid}`);
    parts.push(`<path ${isNew ? 'class="line-new" pathLength="1"' : ""} d="${d}" stroke="${pumpHex(f.pid)}" stroke-width="${w(f.q)}" fill="none" stroke-linejoin="round"/>`);
    parts.push(`<path class="flow-dash" style="--flow-dur:${dur}s" d="${d}" stroke="#fff" stroke-width="${Math.max(1.5, w(f.q) * 0.22)}" fill="none" stroke-linecap="round" opacity=".85"/>`);
  });
  proposed.forEach((m) => {
    const d = linePath(PX + PW, PY[m.to] - 14, SX, ZY[m.zone] + 12);
    parts.push(`<path class="proposed-line" d="${d}" stroke="${pumpHex(m.to)}" stroke-width="6" fill="none" stroke-linejoin="round" stroke-linecap="round" opacity=".8"/>`);
    const ly = PY[m.to] - 46;
    parts.push(`<g transform="translate(384,${ly})"><rect x="-6" y="-17" width="196" height="26" rx="13" fill="#fff" stroke="${pumpHex(m.to)}" stroke-width="2" stroke-dasharray="5 4"/>
      <text x="92" y="1" text-anchor="middle" font-size="14" font-weight="800" fill="#0e1a2b">${esc(lang === "tr" ? "önerilen" : "proposed")} +${num(m.m3h)} m³/h</text></g>`);
  });
  // tank terminus
  const city = s.controls.city_supply_on;
  parts.push(`<g>
    <rect x="34" y="${mid - 104}" width="70" height="208" rx="14" fill="#fff" stroke="#0e1a2b" stroke-width="4"/>
    <clipPath id="tclip"><rect x="38" y="${mid - 100}" width="62" height="200" rx="10"/></clipPath>
    <rect clip-path="url(#tclip)" x="38" y="${mid - 100 + 200 * (1 - lvl)}" width="62" height="${200 * lvl}" fill="#cfe6f5"/>
    <line x1="38" x2="100" y1="${mid - 100 + 200 * (1 - lvl)}" y2="${mid - 100 + 200 * (1 - lvl)}" stroke="#0072bc" stroke-width="2"/>
    <text x="69" y="${mid + 130}" text-anchor="middle" font-size="15" font-weight="800" fill="#0e1a2b">${esc(t("tank"))} ${num(tank.pct, 0)}%</text>
    <text x="69" y="${mid + 150}" text-anchor="middle" font-size="13" fill="#586478">${num(tank.volume_m3, 0)} / ${tank.capacity_m3} m³</text>
    <path d="M69,${mid - 170} V${mid - 106}" stroke="${city ? "#0e1a2b" : "#c8102e"}" stroke-width="${city ? 4 : 3}" ${city ? "" : 'stroke-dasharray="4 5"'} fill="none"/>
    <text x="20" y="${mid - 180}" font-size="13" font-weight="700" fill="${city ? "#33415a" : "#c8102e"}">${esc(t("tank.city"))} ${city ? `${num(tank.inflow_m3h)} m³/h` : (lang === "tr" ? "kesildi" : "cut")}</text>
  </g>`);
  // pump interchanges with load bar (0-150 %, marks at 85 and 100)
  s.pumps.forEach((p) => {
    const y = PY[p.id];
    const down = !p.healthy;
    const load = Math.min(150, p.load_pct);
    const after = rec && rec.loads_after ? rec.loads_after[p.id].load_pct : null;
    const bx = PX, bw = 210, bar = (v) => (Math.min(150, v) / 150) * bw;
    parts.push(`<g>
      <rect x="${PX}" y="${y - 24}" width="${PW}" height="48" rx="24" fill="#fff" stroke="${down ? "#c8102e" : "#0e1a2b"}" stroke-width="4" ${down ? 'stroke-dasharray="6 5"' : ""}/>
      <circle cx="${PX + 24}" cy="${y}" r="14" fill="${pumpHex(p.id)}"/><text x="${PX + 24}" y="${y + 5}" text-anchor="middle" font-size="14" font-weight="800" fill="#fff">${p.id.slice(1)}</text>
      <text x="${PX + 46}" y="${y + 7}" font-size="21" font-weight="800" fill="#0e1a2b">${num(p.load_pct, 0)}%</text>
      <text x="${bx}" y="${y - 34}" font-size="14" font-weight="800" fill="#0e1a2b">${esc(pumpName(p.id))}</text>
      <g transform="translate(${bx},${y + 42})">
        <rect width="${bw}" height="8" rx="4" fill="#e8ecf1"/>
        <rect width="${bar(load)}" height="8" rx="4" fill="${p.load_pct >= 100 ? "#c8102e" : pumpHex(p.id)}"/>
        ${after != null ? `<rect y="-5" width="${bar(after)}" height="18" rx="5" fill="none" stroke="#0e1a2b" stroke-width="2" stroke-dasharray="4 3"/>` : ""}
        <line x1="${bar(85)}" x2="${bar(85)}" y1="-4" y2="12" stroke="#0e1a2b" stroke-width="2"/>
        <line x1="${bar(100)}" x2="${bar(100)}" y1="-4" y2="12" stroke="#0e1a2b" stroke-width="2"/>
        <text x="${bar(85) - 3}" y="-7" font-size="12" text-anchor="end" fill="#586478">85</text>
        <text x="${bar(100) + 3}" y="-7" font-size="12" fill="#586478">100%</text>
        <text x="0" y="30" font-size="15" font-weight="800" fill="${pumpTone(p.status) === "crit" ? "#c8102e" : pumpTone(p.status) === "warn" ? "#a65800" : "#33415a"}">${esc(t("pump.status")[p.status] || p.status)} · ${num(p.flow_m3h)} m³/h</text>
      </g></g>`);
  });
  // zone stations
  s.zones.forEach((z) => {
    const y = ZY[z.id];
    const nLines = Object.values(z.fed_by).filter((q) => q > 0.05).length;
    const out = !z.in_service;
    const h = Math.max(30, 14 + nLines * 16);
    const word = z.shed_low_tank ? t("zone.shed") : out ? t("zone.out") : z.unserved_m3h > 0.05 ? t("zone.short", { q: num(z.unserved_m3h) }) : t("zone.ok");
    const tone = out || z.shed_low_tank ? "#c8102e" : z.unserved_m3h > 0.05 ? "#a65800" : "#1b7f4c";
    parts.push(`<g>
      <rect x="${SX - 2}" y="${y - h / 2}" width="30" height="${h}" rx="15" fill="#fff" stroke="${out ? "#c8102e" : "#0e1a2b"}" stroke-width="4" ${out ? 'stroke-dasharray="5 4"' : ""}/>
      <text x="${SX + 44}" y="${y - 14}" font-size="16" font-weight="800" fill="#0e1a2b">${z.id} · ${esc(zoneName(z.id))}</text>
      <text x="${SX + 44}" y="${y + 14}" font-size="27" font-weight="800" fill="#0e1a2b">${num(z.pressure_bar)} bar</text>
      <text x="${SX + 150}" y="${y + 12}" font-size="15" font-weight="800" fill="${tone}">${esc(word)}</text>
      <text x="${SX + 44}" y="${y + 34}" font-size="13" fill="#586478">${esc(t("zone.wanted", { q: num(z.demand_m3h) }))} · ${esc(t("priority", { n: z.priority }))}${z.priority === 1 ? ` (${esc(t("critical"))})` : ""} · min ${num(z.min_pressure_bar)}</text>
    </g>`);
  });
  prevFeeds = new Set(feeds.map((f) => `${f.z}-${f.pid}`));
  const svg = `<svg class="schematic" viewBox="0 0 1000 ${H}" role="img" aria-label="${esc(t("b.system"))}">${parts.join("")}</svg>`;
  return svg.replace(/ · /g, "\u2009\u00b7\u2002");
}

// ---------------- headline ----------------
function headline(s) {
  const out = s.zones.filter((z) => !z.in_service || z.shed_low_tank).map((z) => z.id);
  const short = s.zones.filter((z) => z.in_service && z.unserved_m3h > 0.05).map((z) => z.id);
  const lb = s.loadbalancer;
  let h;
  if (out.length) h = t("head.out", { zs: out.join(", ") });
  else if (short.length) h = t("head.short", { zs: short.join(", ") });
  else if (s.pumps.some((p) => p.healthy && p.load_pct >= 100)) {
    const p = s.pumps.reduce((a, b) => (a.load_pct >= b.load_pct ? a : b));
    h = t("head.crowded", { a: pumpName(p.id), l: num(p.load_pct, 0) });
  }
  else h = t("head.ok");
  $("b-headline").textContent = h;
  const loads = s.pumps.map((p) => `${p.id} ${pctRaw(p.load_pct)}`).join(", ");
  $("b-subline").textContent = t("sub.ok", { loads, t: num(s.tank.pct, 0), power: t("power")[s.power_source] });
}

function recHeadline(s, rec) {
  const loads = rec.loads_before;
  const ids = Object.keys(loads).filter((k) => loads[k].healthy);
  const donor = ids.reduce((a, b) => (loads[a].load_pct >= loads[b].load_pct ? a : b));
  const recv = ids.reduce((a, b) => (loads[a].load_pct <= loads[b].load_pct ? a : b));
  const sm = (id) => (rec.basis === "current" ? s.pumps.find((p) => p.id === id).smoothed_load_pct : loads[id].load_pct);
  const key = rec.basis === "forecast" ? "lb.forecast" : "lb.crowded";
  return t(key, { a: pumpName(donor), b: pumpName(recv), la: num(sm(donor), 0), lb: num(sm(recv), 0), c: 85, t: hhmm(rec.basis_time) });
}

// ---------------- balancer rail ----------------
function loadRows(rec, s) {
  return s.pumps.map((p) => {
    const before = rec ? rec.loads_before[p.id].load_pct : p.load_pct;
    const after = rec && rec.loads_after ? rec.loads_after[p.id].load_pct : null;
    const sc = (v) => `${(Math.min(150, v) / 150) * 100}%`;
    return `<div class="loadrow">
      <span class="bullet" style="background:${pumpHex(p.id)}" title="${esc(pumpName(p.id))}">${p.id}</span>
      <div class="loadbar" aria-label="${esc(pumpName(p.id))}">
        <span class="track"></span><span class="fill" style="width:${sc(before)};background:${before >= 100 ? "var(--crit)" : pumpHex(p.id)}"></span>
        ${after != null ? `<span class="after" style="width:${sc(after)}"></span>` : ""}
        <span class="mark" style="left:${sc(85)}"><span>85</span></span><span class="mark" style="left:${sc(100)}"><span>100</span></span>
      </div>
      <span class="loadval">${num(before, 0)}%${after != null ? ` → ${num(after, 0)}%` : ""}</span></div>`;
  }).join("");
}

function railText(s, lb) {
  const rec = lb.recommendation;
  if (rec) return recHeadline(s, rec);
  if (lb.status === "settling") return t("lb.settling", { n: lb.minutes_left ?? "" });
  if (lb.paused) return t("lb.paused", { n: lb.minutes_left ?? "" });
  if (lb.status === "cannot_balance") return `${t("lb.cannot")} ${lb.shortfall_m3h > 0 ? t("lb.shortfall", { q: num(lb.shortfall_m3h) }) : ""}`;
  if (lb.status === "crowded_no_safe_move") return t("lb.nosafe");
  return t("lb.balanced");
}

const STATUS_TONE = { balanced: "good", recommendation_ready: "warn", watch_peak_ahead: "info", settling: "info", cannot_balance: "crit", crowded_no_safe_move: "crit" };

function renderRail(s) {
  const lb = s.loadbalancer;
  const rec = lb.recommendation;
  const key = JSON.stringify([getLang(), lb.status, lb.paused, lb.minutes_left, rec && rec.plan_id, lb.can_rollback, lb.recent_decisions.map((d) => d.id), toast, lb.rollback_advised]);
  const host = $("balancer");
  if (!host.dataset.init) {
    host.innerHTML = `<div class="panel" id="rail-main"></div>
      <div class="panel"><div class="decide">
        <div class="who"><label class="field"><span data-i18n="lb.operator">${t("lb.operator")}</span><input type="text" id="op-name" maxlength="60" autocomplete="name"></label>
        <label class="field"><span data-i18n="lb.note">${t("lb.note")}</span><input type="text" id="op-note" maxlength="500"></label></div>
        <div class="btn-row" id="rail-btns"></div><div id="rail-toast" aria-live="polite"></div></div></div>
      <div class="panel"><h3 data-i18n="lb.recent">${t("lb.recent")}</h3><ul class="log" id="rail-log"></ul></div>`;
    try { $("op-name").value = localStorage.getItem("wat.operator") || ""; } catch { /* ignore */ }
    $("op-name").addEventListener("change", (e) => { try { localStorage.setItem("wat.operator", e.target.value); } catch { /* ignore */ } });
    host.dataset.init = "1";
  }
  if (key === railKey) { const lr = $("rail-loads"); if (lr) lr.innerHTML = loadRows(rec, s); return; }
  railKey = key;
  const cfg = lb.thresholds;
  let body = `<header><h2>${esc(t("lb.title"))}</h2>${statusPill(STATUS_TONE[lb.status] || "neutral", t("lb.status")[lb.status] || lb.status)}</header>
    <p class="rec-title">${esc(railText(s, lb))}</p>`;
  if (lb.rollback_advised) body += `<p class="toast err" style="margin-top:8px">${esc(t("lb.rollback.advised"))}</p>`;
  if (rec) {
    const moves = rec.moves.map((m) => t("lb.move", { q: num(m.m3h), f: num(m.fraction_of_zone * 100, 0), z: m.zone, a: pumpName(m.from), b: pumpName(m.to), v: m.crossover_valve })).join(" ");
    const range = Object.entries(rec.loads_after_range).map(([p, r]) => `${p} ${num(r.lo, 0)}–${num(r.hi, 0)}%`).join(" · ");
    const passed = rec.safety_checks.filter((c) => c.passed).length;
    const steps = [];
    rec.moves.forEach((m) => {
      const z = s.zones.find((zz) => zz.id === m.zone);
      t("lb.step").forEach((tpl) => steps.push(tf(tpl, { a: pumpName(m.from), b: pumpName(m.to), z: m.zone, v: m.crossover_valve, q: num(m.m3h), n: m.stages, p: num(z.min_pressure_bar + 0.1) })));
    });
    steps.push(t("lb.step.last", { c: cfg.crowded_pct }));
    const checkTpl = t("lb.check");
    body += `<p style="margin-top:10px;font-size:17px">${esc(moves)}</p>
      <div style="margin-top:14px;display:grid;gap:10px" id="rail-loads">${loadRows(rec, s)}</div>
      <p class="muted" style="font-size:14px;margin-top:6px">${esc(t("lb.range", { r: range }))} · ${esc(t("lb.unserved", { a: num(rec.unserved_before_m3h), b: num(rec.unserved_after_m3h) }))}</p>
      <p style="margin-top:8px;font-size:15px"><b>${esc(t("lb.confidence"))}:</b> ${esc(t("conf")[rec.confidence])} · ${esc(rec.basis === "forecast" ? t("lb.basis.forecast", { t: hhmm(rec.basis_time) }) : t("lb.basis.current"))}</p>
      <details open><summary>${esc(t("lb.checks", { n: passed }))}</summary><ul class="checks">${rec.safety_checks.map((c, i) => `<li>${icon(c.passed ? "check" : "x", c.passed ? "var(--good)" : "var(--crit)")}<span>${esc(tf(checkTpl[i] || c.name, { cap: cfg.receiver_cap_pct, m: "0.1", ramp: 60 }))}<small>${esc(c.detail)}</small></span></li>`).join("")}</ul></details>
      <details><summary>${esc(t("lb.steps"))}</summary><ol class="steps">${steps.map((x) => `<li><span>${esc(x)}</span></li>`).join("")}</ol></details>
      <details><summary>${esc(t("lb.why"))}</summary><p style="margin-top:6px;font-size:15px">${esc(t("lb.why.text", { z: rec.moves[0].zone, p: rec.moves[0].priority }))}</p></details>`;
  } else {
    body += `<div style="margin-top:14px;display:grid;gap:10px" id="rail-loads">${loadRows(null, s)}</div>`;
  }
  $("rail-main").innerHTML = body;
  $("rail-btns").innerHTML = `
    <button class="btn primary" id="lb-apply" ${rec ? "" : "disabled"}>${esc(t("lb.approve"))}</button>
    <button class="btn" id="lb-dismiss" ${rec ? "" : "disabled"}>${esc(t("lb.dismiss"))}</button>
    <button class="btn" id="lb-rollback" ${lb.can_rollback ? "" : "disabled"}>${esc(t("lb.rollback"))}</button>
    <button class="btn" id="lb-eval">${esc(t("lb.reeval"))}</button>`;
  $("rail-toast").innerHTML = toast ? `<p class="toast ${toast.err ? "err" : "ok"}">${esc(toast.text)}</p>` : "";
  $("rail-log").innerHTML = lb.recent_decisions.length ? lb.recent_decisions.map((d) =>
    `<li><b>${hhmm(d.sim_time)}</b> ${esc(d.operator)} ${esc(t("lb.action")[d.action] || d.action)} ${esc(d.plan_id || "")}${d.note ? ` · “${esc(d.note)}”` : ""}</li>`).join("") : `<li>${esc(t("lb.none"))}</li>`;
  const who = () => ({ operator: ($("op-name").value || "operator").trim().slice(0, 60) || "operator", note: $("op-note").value.slice(0, 500) });
  const act = async (path, body, okKey) => {
    if (busy) return; busy = true;
    try {
      const r = await post(path, body);
      toast = { text: t(okKey) }; $("op-note").value = "";
      update(r.state || await get("/api/state"));
    } catch (e) {
      toast = { err: true, text: e instanceof ApiError && e.status === 409 ? t("lb.stale") : e.message };
      update(await get("/api/state"));
    } finally { busy = false; }
  };
  $("lb-apply").onclick = () => act("/api/loadbalancer/apply", { plan_id: rec.plan_id, ...who() }, "lb.applied");
  $("lb-dismiss").onclick = () => act("/api/loadbalancer/dismiss", { plan_id: rec.plan_id, ...who() }, "lb.dismissed");
  $("lb-rollback").onclick = () => act("/api/loadbalancer/rollback", who(), "lb.rolled");
  $("lb-eval").onclick = async () => { toast = null; await post("/api/loadbalancer/evaluate"); update(await get("/api/state")); };
}

// ---------------- controls ----------------
function buildControls() {
  const host = $("controls");
  host.innerHTML = `
    <div class="ctl-group"><h3 id="cg-pumps"></h3>${topo.pumps.map((p) => `<label class="switch"><span id="lbl-f-${p}"></span><input type="checkbox" class="danger" id="c-f-${p}" data-pump="${p}"></label>`).join("")}</div>
    <div class="ctl-group"><h3 id="cg-power"></h3><label class="switch"><span id="lbl-grid"></span><input type="checkbox" id="c-grid"></label><label class="switch"><span id="lbl-gen"></span><input type="checkbox" id="c-gen"></label><label class="switch"><span id="lbl-city"></span><input type="checkbox" id="c-city"></label></div>
    <div class="ctl-group"><h3 id="cg-demand"></h3>
      <label class="range"><span id="lbl-all"></span><output id="o-all"></output><input type="range" id="c-all" min="50" max="200" step="5"></label>
      ${topo.zones.map((z) => `<label class="range"><span id="lbl-${z}"></span><output id="o-${z}"></output><input type="range" id="c-${z}" min="50" max="250" step="5"></label>`).join("")}</div>`;
  const send = (body) => post("/api/twin/control", body).then(update).catch(() => {});
  host.querySelectorAll("[data-pump]").forEach((box) => {
    box.onchange = () => send({ pump_failed: [...host.querySelectorAll("[data-pump]:checked")].map((x) => x.dataset.pump) });
  });
  $("c-grid").onchange = (e) => send({ grid_on: e.target.checked });
  $("c-gen").onchange = (e) => send({ generator_available: e.target.checked });
  $("c-city").onchange = (e) => send({ city_supply_on: e.target.checked });
  $("c-all").oninput = (e) => { $("o-all").textContent = `${e.target.value}%`; };
  $("c-all").onchange = (e) => send({ demand_mult: e.target.value / 100 });
  topo.zones.forEach((z) => {
    $(`c-${z}`).oninput = (e) => { $(`o-${z}`).textContent = `${e.target.value}%`; };
    $(`c-${z}`).onchange = (e) => send({ zone_mult: { [z]: e.target.value / 100 } });
  });
  labelControls();
}

// ---------------- simulation time bar (00:00 -> 24:00, like a video timeline) ----------------
let dragging = false, lastMin = null;
const fmtMin = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const PLAY = '<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.5v15l12.5-7.5z" fill="currentColor"/></svg>';
const PAUSE = '<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="4.5" width="4" height="15" rx="1.2" fill="currentColor"/><rect x="14" y="4.5" width="4" height="15" rx="1.2" fill="currentColor"/></svg>';

function buildPlayer() {
  const send = (body) => post("/api/twin/control", body).then(update).catch(() => {});
  $("player").innerHTML = `
    <div class="pl-bar" id="pl-bar" role="slider" tabindex="0" aria-valuemin="0" aria-valuemax="1439">
      <div class="pl-track">
        <div class="pl-night" style="left:0;width:25%"></div><div class="pl-night" style="left:91.667%;width:8.333%"></div>
        <div class="pl-loaded" id="pl-loaded"></div><div class="pl-hover" id="pl-hover"></div><div class="pl-played" id="pl-played"></div>
      </div>
      <div class="pl-knob" id="pl-knob"></div>
      <div class="pl-tip" id="pl-tip"></div>
      <div class="pl-ticks" aria-hidden="true">${[0, 6, 12, 18, 24].map((h) => `<span style="left:${(h / 24) * 100}%">${String(h).padStart(2, "0")}:00</span>`).join("")}</div>
    </div>
    <div class="pl-row">
      <button type="button" class="pl-btn" id="pl-play"></button>
      <span class="pl-time"><b id="pl-now">--:--</b><span class="muted"> / 24:00</span></span>
      <span class="pl-day" id="pl-day"></span>
      <div class="pl-right">
        <div class="seg" id="pl-dtype" role="group"><button type="button" data-day="weekday"></button><button type="button" data-day="weekend"></button></div>
        <div class="seg" id="c-speed" role="group">${[1, 2, 10, 30, 60].map((n) => `<button type="button" data-speed="${n}">${n}×</button>`).join("")}</div>
        <button type="button" class="btn sm" id="c-reset"></button>
      </div>
    </div>`;
  const bar = $("pl-bar");
  const minuteAt = (e) => {
    const r = bar.getBoundingClientRect();
    return Math.max(0, Math.min(1439, Math.round(((e.clientX - r.left) / r.width) * 1440)));
  };
  const preview = (m) => {
    $("pl-played").style.width = `${(m / 1440) * 100}%`;
    $("pl-knob").style.left = `${(m / 1440) * 100}%`;
    $("pl-now").textContent = fmtMin(m);
  };
  const tip = (e) => {
    const m = minuteAt(e), x = (m / 1440) * 100;
    $("pl-tip").textContent = fmtMin(m);
    $("pl-tip").style.left = `${Math.max(2.5, Math.min(97.5, x))}%`;
    $("pl-hover").style.width = `${x}%`;
    return m;
  };
  bar.addEventListener("pointermove", (e) => { const m = tip(e); if (dragging) preview(m); });
  bar.addEventListener("pointerleave", () => { $("pl-hover").style.width = "0"; });
  bar.addEventListener("pointerdown", (e) => {
    dragging = true; bar.classList.add("dragging"); bar.setPointerCapture(e.pointerId); preview(tip(e));
  });
  bar.addEventListener("pointerup", (e) => {
    if (!dragging) return;
    dragging = false; bar.classList.remove("dragging");
    send({ jump_to: fmtMin(minuteAt(e)) });
  });
  bar.addEventListener("keydown", (e) => {
    if (lastMin == null) return;
    const step = e.shiftKey ? 60 : 15;
    const to = { ArrowRight: lastMin + step, ArrowUp: lastMin + step, ArrowLeft: lastMin - step, ArrowDown: lastMin - step, Home: 0, End: 1439 }[e.key];
    if (to !== undefined) { e.preventDefault(); send({ jump_to: fmtMin(Math.max(0, Math.min(1439, to))) }); }
    if (e.key === " " || e.key === "k") { e.preventDefault(); send({ running: !S.controls.running }); }
  });
  $("pl-play").onclick = () => send({ running: !S.controls.running });
  $("c-speed").onclick = (e) => { const b = e.target.closest("[data-speed]"); if (b) send({ speed: +b.dataset.speed }); };
  $("pl-dtype").onclick = (e) => { const b = e.target.closest("[data-day]"); if (b && lastMin != null) send({ jump_to: fmtMin(lastMin), jump_day: b.dataset.day }); };
  $("c-reset").onclick = () => { toast = null; lastMin = null; post("/api/twin/reset").then(update); };
}

function syncPlayer(s) {
  const d = new Date(s.sim_time);
  const m = d.getHours() * 60 + d.getMinutes();
  const weekend = ((d.getDay() + 6) % 7) >= 5;
  const bar = $("pl-bar");
  // Snap (no glide) on a jump or when the day wraps past midnight; glide while time just runs.
  const jumped = lastMin == null || m < lastMin || m - lastMin > s.controls.speed * 3 + 2;
  bar.classList.toggle("snap", jumped);
  lastMin = m;
  if (!dragging) {
    $("pl-played").style.width = `${(m / 1440) * 100}%`;
    $("pl-knob").style.left = `${(m / 1440) * 100}%`;
    $("pl-now").textContent = fmtMin(m);
  }
  // Recorded stretch of this day (like a video's buffered range): seeking inside it restores that moment.
  const lo = $("pl-loaded");
  if (s.recorded) {
    const dayStart = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const frac = (iso) => Math.max(0, Math.min(1, (new Date(iso).getTime() - dayStart) / 86400000));
    const a = frac(s.recorded.from), z = frac(s.recorded.to);
    lo.style.left = `${a * 100}%`; lo.style.width = `${Math.max(0, z - a) * 100}%`;
  } else { lo.style.width = "0"; }
  bar.setAttribute("aria-valuenow", m);
  bar.setAttribute("aria-valuetext", `${fmtMin(m)}, ${t("weekday")[(d.getDay() + 6) % 7]}`);
  bar.setAttribute("aria-label", t("pl.label"));
  bar.title = t("pl.hint");
  const running = s.controls.running;
  const btn = $("pl-play");
  btn.innerHTML = running ? PAUSE : PLAY;
  btn.setAttribute("aria-label", running ? t("pl.pause") : t("pl.play"));
  btn.title = running ? t("pl.pause") : t("pl.play");
  $("pl-day").textContent = `${t("weekday")[(d.getDay() + 6) % 7]} · ${t("clock.speed", { n: s.controls.speed })}`;
  document.querySelectorAll("#pl-dtype [data-day]").forEach((b) => {
    b.textContent = t(`pl.${b.dataset.day}`);
    b.setAttribute("aria-pressed", String((b.dataset.day === "weekend") === weekend));
  });
}

function labelControls() {
  const L = (id, s) => { const n = $(id); if (n) n.textContent = s; };
  L("cg-pumps", t("ctl.pumps")); L("cg-power", t("ctl.power")); L("cg-demand", t("ctl.demand"));
  topo.pumps.forEach((p) => L(`lbl-f-${p}`, t("ctl.fail", { p: pumpName(p) })));
  L("lbl-grid", t("ctl.grid")); L("lbl-gen", t("ctl.gen")); L("lbl-city", t("ctl.city")); L("lbl-all", t("ctl.all"));
  topo.zones.forEach((z) => L(`lbl-${z}`, t("ctl.zone", { z })));
  L("c-reset", t("ctl.reset"));
}

function syncControls(s) {
  const c = s.controls;
  const set = (id, v) => { const n = $(id); if (n && document.activeElement !== n) { if (n.type === "checkbox") n.checked = v; else n.value = v; } };
  topo.pumps.forEach((p) => set(`c-f-${p}`, c.pump_failed.includes(p)));
  set("c-grid", c.grid_on); set("c-gen", c.generator_available); set("c-city", c.city_supply_on);
  set("c-all", Math.round(c.demand_mult * 100)); $("o-all").textContent = `${Math.round(c.demand_mult * 100)}%`;
  topo.zones.forEach((z) => {
    if (c.zone_mult[z] === undefined || !$(`o-${z}`)) return;
    set(`c-${z}`, Math.round(c.zone_mult[z] * 100)); $(`o-${z}`).textContent = `${Math.round(c.zone_mult[z] * 100)}%`;
  });
  document.querySelectorAll("#c-speed [data-speed]").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.speed === c.speed)));
  labelControls();
}

// ---------------- trends ----------------
function minLines() {
  const groups = {};
  (topo.cfg?.zones || []).forEach((z) => { (groups[z.min_pressure_bar] ||= []).push(z.id); });
  return Object.entries(groups).sort((a, b) => b[0] - a[0]).map(([v, ids], i) => ({
    y: +v, label: `min ${ids.join(" / ")} ${v} bar`, dash: true, start: i % 2 === 1, below: i > 0,
  }));
}
async function trends() {
  const h = (await get("/api/history?last=240")).samples;
  if (!h.length) return;
  const x = h.map((r) => r.t);
  lineChart($("trend-load"), {
    x, xFmt: (v) => hhmm(v), yFmt: (v) => `${Math.round(v)}%`, yMin: 0, yMax: Math.max(150, ...h.flatMap((r) => Object.values(r.pump_load))), height: 220,
    series: topo.pumps.map((p) => ({ name: pumpName(p), color: pumpHex(p), values: h.map((r) => r.pump_load[p]) })),
    hlines: [{ y: 85, label: getLang() === "tr" ? "yoğun %85" : "crowded 85%", dash: true }, { y: 100, label: getLang() === "tr" ? "anma %100" : "rated 100%", start: true }],
  });
  lineChart($("trend-pressure"), {
    x, xFmt: (v) => hhmm(v), yFmt: (v) => `${(+v).toFixed(1)}`, yMin: 0, height: 220,
    series: topo.zones.map((z) => ({ name: z, color: zoneHex(z), values: h.map((r) => r.zone_pressure[z] ?? null) })),
    hlines: minLines(),
  });
}

function update(s) {
  const key = `${s.pumps.map((p) => p.id).join()}|${s.zones.map((z) => z.id).join()}`;
  if (key !== topoKey) {
    topoKey = key;
    if (key !== `${topo.pumps.join()}|${topo.zones.join()}`) { loadTopo().then(() => { buildControls(); trends().catch(() => {}); }); }
    else { buildControls(); trends().catch(() => {}); }
  }
  S = s;
  window.dispatchEvent(new CustomEvent("wat:state", { detail: s }));
  headline(s);
  $("schematic").innerHTML = schematic(s, s.loadbalancer.recommendation);
  syncControls(s);
  syncPlayer(s);
  renderRail(s);
}

export function initBuilding() {
  buildControls();
  buildPlayer();
  const tick = async () => { try { if (!busy) update(await get("/api/state")); } catch { /* server restarting */ } };
  tick();
  setInterval(() => { if (!document.hidden) tick(); }, 1000);
  trends();
  setInterval(() => { if (!document.hidden && !$("p-building").hidden) trends().catch(() => {}); }, 5000);
  onLang(() => { railKey = ""; if (S) update(S); trends().catch(() => {}); });
}
