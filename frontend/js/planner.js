// District outage planner: XGBoost forecast + MILP tank allocation, drawn as a line map
// (each tank is a terminus, each served area a station on its tank's line).
import { get, post, tankStyle } from "./api.js";
import { t, getLang, onLang, num, pct, dateTime, trLimit } from "./i18n.js";
import { lineChart, groupedBars, hbars, shapChart } from "./charts.js";
import { esc, statusPill, icon } from "./ui.js";

const $ = (id) => document.getElementById(id);
let net = null, scenarios = [], plan = null, evalData = null, series = null;
let mode = "stored", policy = "milp_xgb", scenarioId = "S391";
let custom = { start: "2022-12-12T08:00", dur: 8, areas: new Set(["A03", "A09", "A15", "A16", "A21", "A26", "A30", "A36"]), fill: 40 };
let started = false, solving = false, errMsg = null, hoverArea = null;

// ---------- projection ----------
// Diagrammatic, like a transit map: distances from the city centre are compressed (r^0.55),
// so dense central districts spread out and outlying ones pull in. Not to scale.
function projector(areas, tanks, W, H, pad = 44) {
  const pts = [...areas, ...tanks];
  const med = (a) => [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)];
  const lat0 = med(areas.map((p) => p.lat)), lon0 = med(areas.map((p) => p.lon));
  const k = Math.cos((lat0 * Math.PI) / 180);
  const warp = (p) => {
    const dx = (p.lon - lon0) * k, dy = p.lat - lat0, r = Math.hypot(dx, dy) || 1e-9;
    const r2 = Math.pow(r, 0.55);
    return [(dx / r) * r2, (dy / r) * r2];
  };
  const ws = pts.map(warp);
  const [x0, x1] = [Math.min(...ws.map((w) => w[0])), Math.max(...ws.map((w) => w[0]))];
  const [y0, y1] = [Math.min(...ws.map((w) => w[1])), Math.max(...ws.map((w) => w[1]))];
  const s = Math.min((W - 2 * pad) / (x1 - x0), (H - 2 * pad) / (y1 - y0));
  const ox = (W - s * (x1 - x0)) / 2, oy = (H - s * (y1 - y0)) / 2;
  return (p) => { const w = warp(p); return [ox + (w[0] - x0) * s, H - (oy + (w[1] - y0) * s)]; };
}

// ---------- Istanbul base map ----------
// Coast, Bosphorus, Golden Horn and the Princes' Islands as [lat, lon], hand-traced to about 1 km.
// Drawn through the same projection as the stations, so every district sits on its own side of
// the strait; the shapes are as warped (not to scale) as the rest of the diagram.
const BOSPHORUS = [[40.985, 28.998], [41.0, 29.0], [41.012, 29.0], [41.025, 29.006], [41.035, 29.017], [41.045, 29.034],
  [41.055, 29.04], [41.068, 29.048], [41.08, 29.055], [41.092, 29.061], [41.105, 29.064], [41.118, 29.068], [41.13, 29.073],
  [41.145, 29.08], [41.16, 29.082], [41.175, 29.083], [41.19, 29.095], [41.205, 29.108], [41.222, 29.118], [41.242, 29.128]];
const STRAIT = BOSPHORUS.slice(2, -1);   // the land edge each side shares (the water line is drawn over it)
const EUROPE = [[41.1, 27.6],
  // Sea of Marmara, west to east: Silivri, Büyükçekmece, Avcılar, Yeşilköy, Bakırköy, Yenikapı, Sarayburnu
  [41.085, 28.1], [41.056, 28.25], [41.056, 28.33], [41.04, 28.4], [41.02, 28.47], [41.008, 28.53], [41.01, 28.585],
  [40.99, 28.62], [40.978, 28.66], [40.972, 28.7], [40.975, 28.74], [40.985, 28.765], [40.965, 28.8], [40.968, 28.84],
  [40.972, 28.87], [40.985, 28.905], [40.995, 28.935], [41.0, 28.95], [41.004, 28.968], [41.008, 28.979], [41.016, 28.987],
  ...STRAIT,
  // Black Sea, east to west: Rumelifeneri, Kilyos, Karaburun, towards Çatalca's coast
  [41.232, 29.112], [41.245, 29.06], [41.255, 29.02], [41.275, 28.95], [41.31, 28.82], [41.35, 28.69], [41.37, 28.6],
  [41.42, 28.45], [41.5, 28.27], [41.58, 28.08], [41.7, 27.6]];
const ASIA = [...STRAIT,
  // Black Sea, west to east: Anadolufeneri, Riva, Şile, Ağva
  [41.215, 29.135], [41.218, 29.17], [41.228, 29.22], [41.215, 29.3], [41.2, 29.42], [41.185, 29.55], [41.178, 29.61],
  [41.16, 29.72], [41.14, 29.85], [41.12, 30.0], [41.12, 30.5], [40.7, 30.5],
  // Sea of Marmara, east to west: Gebze, Tuzla, Pendik, Kartal, Maltepe, Bostancı, Fenerbahçe, Moda, Haydarpaşa
  [40.75, 29.55], [40.785, 29.4], [40.803, 29.3], [40.866, 29.235], [40.885, 29.185], [40.918, 29.13], [40.952, 29.09],
  [40.968, 29.035], [40.981, 29.022], [40.998, 29.012]];
const GOLDEN_HORN = [[41.017, 28.982], [41.024, 28.968], [41.033, 28.953], [41.043, 28.942], [41.054, 28.935], [41.066, 28.929]];
const ISLANDS = [[40.86, 29.123, 0.013], [40.877, 29.095, 0.008], [40.882, 29.066, 0.006], [40.908, 29.05, 0.005]];   // lat, lon, radius°
const GEO_LABELS = [   // [key, lat, lon, kind]
  ["eu", 41.25, 28.52, "side"], ["as", 41.1, 29.45, "side"], ["black", 41.245, 29.42, "sea"],
  ["marmara", 40.9, 28.68, "sea"], ["bos", 41.258, 29.122, "strait"]];
const LAND_EU = "#f8f9fb", LAND_AS = "#ebeef3", WATER = "#dde9f4", COAST = "#cdd5df";

function geoPath(P, pts, close) {
  // densified (every ~0.004°) so the curved projection bends the coast smoothly
  const out = [];
  const n = close ? pts.length : pts.length - 1;
  for (let i = 0; i < n; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    const steps = Math.max(1, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / 0.004));
    for (let k = 0; k < steps; k++) out.push(P({ lat: a[0] + ((b[0] - a[0]) * k) / steps, lon: a[1] + ((b[1] - a[1]) * k) / steps }));
  }
  if (!close) { const z = pts[pts.length - 1]; out.push(P({ lat: z[0], lon: z[1] })); }
  return `M${out.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("L")}${close ? "Z" : ""}`;
}

let baseCache = { key: null, svg: "" };
function baseMap(P, W, H) {
  const key = `${getLang()}|${net.areas.length}|${W}x${H}`;
  if (baseCache.key === key) return baseCache.svg;
  const fallback = { eu: "European side", as: "Asian side", black: "Black Sea", marmara: "Sea of Marmara", bos: "Bosphorus" };
  const names = { ...fallback, ...(typeof t("p.geo") === "object" ? t("p.geo") : {}) };   // the drawing never fails on a label
  const up = (s) => s.toLocaleUpperCase(getLang() === "tr" ? "tr-TR" : "en");
  const islands = ISLANDS.map(([la, lo, r]) => {
    const [x, y] = P({ lat: la, lon: lo }), [, y2] = P({ lat: la + r, lon: lo });
    return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${Math.max(2.5, Math.abs(y - y2)).toFixed(1)}" fill="${LAND_AS}" stroke="${COAST}" stroke-width="1.2"/>`;
  }).join("");
  const labels = GEO_LABELS.map(([k, la, lo, kind]) => {
    const [x, y] = P({ lat: la, lon: lo });
    const side = kind === "side";
    return `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" font-size="${side ? 14 : 12}" font-weight="800"
      letter-spacing="${side ? "0.16em" : "0.12em"}" fill="${side ? "#6b778a" : "#7d9bb5"}" ${kind === "sea" || kind === "strait" ? 'font-style="italic"' : ""}>${esc(up(names[k]))}</text>`;
  }).join("");
  const svg = `<g aria-hidden="true">
    <rect width="${W}" height="${H}" fill="${WATER}"/>
    <path d="${geoPath(P, EUROPE, true)}" fill="${LAND_EU}" stroke="${COAST}" stroke-width="1.2" stroke-linejoin="round"/>
    <path d="${geoPath(P, ASIA, true)}" fill="${LAND_AS}" stroke="${COAST}" stroke-width="1.2" stroke-linejoin="round"/>
    ${islands}
    <path d="${geoPath(P, BOSPHORUS, false)}" fill="none" stroke="${COAST}" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="${geoPath(P, BOSPHORUS, false)}" fill="none" stroke="${WATER}" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="${geoPath(P, GOLDEN_HORN, false)}" fill="none" stroke="${COAST}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="${geoPath(P, GOLDEN_HORN, false)}" fill="none" stroke="${WATER}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
    ${labels}</g>`;
  baseCache = { key, svg };
  return svg;
}

function octi(a, b) {
  // diagonal leg from the station, then straight into the terminus
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const d = Math.min(Math.abs(dx), Math.abs(dy));
  const m = [a[0] + Math.sign(dx) * d, a[1] + Math.sign(dy) * d];
  return `M${a[0].toFixed(1)},${a[1].toFixed(1)}L${m[0].toFixed(1)},${m[1].toFixed(1)}L${b[0].toFixed(1)},${b[1].toFixed(1)}`;
}

function mapSvg() {
  const W = 900, H = 560;
  const P = projector(net.areas, net.tanks, W, H);
  const byArea = Object.fromEntries((plan?.areas || []).map((a) => [a.area_id, a]));
  const affected = plan ? new Set(plan.areas.map((a) => a.area_id)) : (mode === "custom" ? custom.areas : new Set());
  const tankPos = Object.fromEntries(net.tanks.map((tk) => [tk.tank_id, P(tk)]));
  const maxFc = Math.max(1, ...(plan?.areas || []).map((a) => a.forecast_m3));
  const lines = [], casings = [], stations = [], labels = [];
  net.areas.forEach((a) => {
    const p = P(a), pa = byArea[a.area_id];
    const tid = pa ? pa.by_policy[policy] : null;
    if (pa && tid) {
      const st = tankStyle(tid);
      const w = 2.5 + 7 * Math.sqrt(pa.forecast_m3 / maxFc);
      const d = octi(p, tankPos[tid]);
      const dim = hoverArea && hoverArea !== a.area_id ? 0.25 : 1;
      lines.push(`<path d="${d}" stroke="${st.color}" stroke-width="${w.toFixed(1)}" fill="none" stroke-linejoin="round" stroke-linecap="round" opacity="${dim}"/>`);
      if (st.cased) casings.push(`<path d="${d}" stroke="#fff" stroke-width="${(w * 0.34).toFixed(1)}" fill="none" stroke-linejoin="round" stroke-linecap="round" opacity="${dim}"/>`);
    }
    const isAff = affected.has(a.area_id);
    const unserved = pa && !tid;
    const r = isAff ? 7 : 4;
    stations.push(`<g class="station" tabindex="0" role="button" data-area="${a.area_id}" aria-label="${esc(a.district)}">
      <circle cx="${p[0]}" cy="${p[1]}" r="14" fill="transparent"/>
      ${unserved ? `<circle cx="${p[0]}" cy="${p[1]}" r="15" fill="#fbe7ea" stroke="#c8102e" stroke-width="3" stroke-dasharray="5 4"/>` : ""}
      <circle class="pip" cx="${p[0]}" cy="${p[1]}" r="${r}" fill="${isAff ? "#fff" : "#c3cad5"}" stroke="${unserved ? "#c8102e" : isAff ? "#0e1a2b" : "#c3cad5"}" stroke-width="${isAff ? 3 : 1}"/></g>`);
    if (unserved || hoverArea === a.area_id) {
      labels.push(`<text x="${p[0] + 15}" y="${p[1] + 4}" font-size="13" font-weight="800" fill="${unserved ? "#c8102e" : "#0e1a2b"}" paint-order="stroke" stroke="#fbfcfd" stroke-width="4">${esc(a.district)}</text>`);
    }
  });
  const termini = net.tanks.map((tk) => {
    const [x, y] = tankPos[tk.tank_id], st = tankStyle(tk.tank_id);
    const tp = plan?.tanks.find((q) => q.tank_id === tk.tank_id);
    const over = tp && tp.overdraw_m3 > 0.5;
    return `<g><rect x="${x - 17}" y="${y - 17}" width="34" height="34" rx="9" fill="#fff" stroke="${over ? "#c8102e" : "#0e1a2b"}" stroke-width="3.5"/>
      <circle cx="${x}" cy="${y}" r="11.5" fill="${st.color}"/>${st.cased ? `<circle cx="${x}" cy="${y}" r="8" fill="none" stroke="#fff" stroke-width="2"/>` : ""}
      <text x="${x}" y="${y + 4.5}" text-anchor="middle" font-size="11" font-weight="800" fill="#fff">${tk.tank_id.slice(1)}</text></g>`;
  });
  const note = `<text x="${W - 14}" y="${H - 12}" text-anchor="end" font-size="13" fill="#586478">${esc(getLang() === "tr" ? "Şematik, ölçeksiz · İstanbul ilçeleri" : "Diagram, not to scale · Istanbul districts")}</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(t("p.title"))}">${baseMap(P, W, H)}${note}${lines.join("")}${casings.join("")}${stations.join("")}${termini.join("")}${labels.join("")}</svg><div class="tip" id="p-tip"></div>`;
}

function bindMap() {
  const wrap = $("p-map");
  const tip = $("p-tip");
  wrap.querySelectorAll(".station").forEach((g) => {
    const id = g.dataset.area;
    const a = net.areas.find((x) => x.area_id === id);
    const show = () => {
      const pa = plan?.areas.find((x) => x.area_id === id);
      const tid = pa ? pa.by_policy[policy] : null;
      tip.innerHTML = `<b>${esc(a.district)}</b> (${id})<br>${pa ? t("p.tip", { d: `w ${a.priority_weight.toFixed(2)}`, c: a.priority_class, f: num(pa.forecast_m3, 0), a: num(pa.actual_m3, 0), t: tid ? `${t("p.tank")} ${tid}` : t("p.noserve") }) : esc(t("p.legend.unaffected"))}`;
      const r = wrap.getBoundingClientRect(), b = g.getBoundingClientRect();
      tip.style.left = `${Math.min(r.width - 200, b.left - r.left + 18)}px`; tip.style.top = `${b.top - r.top - 10}px`;
      tip.classList.add("on");
      document.querySelectorAll(`tr[data-area]`).forEach((tr) => tr.classList.toggle("hl", tr.dataset.area === id));
    };
    const hide = () => { tip.classList.remove("on"); document.querySelectorAll("tr.hl").forEach((tr) => tr.classList.remove("hl")); };
    g.addEventListener("pointerenter", show); g.addEventListener("focus", show);
    g.addEventListener("pointerleave", hide); g.addEventListener("blur", hide);
    g.addEventListener("click", () => {
      if (mode !== "custom") return;
      if (custom.areas.has(id)) custom.areas.delete(id); else custom.areas.add(id);
      plan = null; render();
    });
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); g.click(); } });
  });
}

// ---------- panels ----------
function controls() {
  const opts = (split) => scenarios.filter((s) => s.split === split).map((s) =>
    `<option value="${s.scenario_id}" ${s.scenario_id === scenarioId ? "selected" : ""}>${s.scenario_id} · ${s.duration_h} ${t("h")} · ${s.n_areas_affected} · ${s.all_served ? "✓" : (getLang() === "tr" ? "stok yetersiz" : "stock short")}</option>`).join("");
  const areaList = net.areas.map((a) => `<label><input type="checkbox" value="${a.area_id}" ${custom.areas.has(a.area_id) ? "checked" : ""}>${esc(a.district)}</label>`).join("");
  return `<div class="stack">
    <div class="seg" id="p-mode"><button type="button" data-m="stored" aria-pressed="${mode === "stored"}">${esc(t("p.stored"))}</button><button type="button" data-m="custom" aria-pressed="${mode === "custom"}">${esc(t("p.custom"))}</button></div>
    ${mode === "stored" ? `<label class="field">${esc(t("p.scenario"))}<select id="p-scen"><optgroup label="test">${opts("test")}</optgroup><optgroup label="train (in-sample)">${opts("train")}</optgroup></select></label>` : `
      <label class="field">${esc(t("p.start"))}<input type="datetime-local" id="p-start" value="${custom.start}" min="2022-01-08T00:00" max="2022-12-31T00:00" step="3600"></label>
      <div class="field">${esc(t("p.duration"))}<div class="seg" id="p-dur">${[4, 6, 8, 12, 24].map((d) => `<button type="button" data-d="${d}" aria-pressed="${d === custom.dur}">${d} ${t("h")}</button>`).join("")}</div></div>
      <label class="range"><span>${esc(t("p.levels"))}</span><output id="p-fill-o">${custom.fill}%</output><input type="range" id="p-fill" min="10" max="100" step="5" value="${custom.fill}"></label>
      <div class="field"><span>${esc(t("p.areas"))} (${custom.areas.size})</span><div class="btn-row"><button type="button" class="btn sm" id="p-all">${esc(t("p.all"))}</button><button type="button" class="btn sm" id="p-none">${esc(t("p.none"))}</button></div><div class="area-pick" id="p-areas">${areaList}</div></div>
      <button class="btn primary" id="p-go" ${solving || !custom.areas.size ? "disabled" : ""}>${esc(solving ? t("s.running") : t("p.plan"))}</button>`}
    <div class="field">${esc(t("p.policy"))}<div class="seg" id="p-pol">${["milp_xgb", "oracle", "greedy"].map((p) => `<button type="button" data-p="${p}" aria-pressed="${p === policy}">${esc(t("p.policies")[p])}</button>`).join("")}</div>
      <span class="panel-note">${esc(t("p.policy.desc")[policy])}</span></div>
    ${errMsg ? `<p class="toast err">${esc(errMsg)}</p>` : ""}
  </div>`;
}

function rightPanel() {
  if (!plan) return `<p class="empty">${esc(solving ? t("s.running") : t("p.plan"))}</p>`;
  const rows = ["milp_xgb", "oracle", "greedy"].map((p) => ({ label: t("p.policies")[p], value: plan.comparison[p].unserved_share, me: p === "milp_xgb" }));
  const tanks = plan.tanks.filter((tk) => tk.assigned_actual_m3 > 0 || tk.level_m3 > 0).map((tk) => {
    const st = tankStyle(tk.tank_id), scale = Math.max(tk.level_m3, tk.assigned_actual_m3) || 1;
    return `<div class="tankrow"><span class="bullet ${st.cased ? "cased" : ""}" style="background:${st.color};color:${st.color}"><span style="color:#fff">${tk.tank_id.slice(1)}</span></span>
      <div><div style="display:flex;justify-content:space-between"><span>${tk.tank_id}</span><span class="num"><b>${num(tk.assigned_actual_m3, 0)}</b> / ${num(tk.level_m3, 0)} m³${tk.overdraw_m3 > 0.5 ? ` · <span style="color:var(--crit);font-weight:800">+${num(tk.overdraw_m3, 0)}</span>` : ""}</span></div>
      <div class="tankbar"><i style="width:${Math.min(100, (tk.assigned_actual_m3 / scale) * 100)}%;background:${st.color}"></i><b style="left:${(tk.level_m3 / scale) * 100}%"></b></div></div></div>`;
  }).join("");
  return `<div class="stack"><div class="panel"><h3>${esc(t("p.unserved"))}</h3><div id="p-pbars" style="margin-top:10px"></div></div>
    <div class="panel"><h3>${esc(t("p.tanks"))}</h3><div style="display:grid;gap:10px;margin-top:10px">${tanks}</div></div></div>`;
}

function areaTable() {
  if (!plan) return "";
  const rows = [...plan.areas].sort((a, b) => b.priority_weight - a.priority_weight).map((a) => {
    const tid = a.by_policy[policy], st = tid && tankStyle(tid);
    return `<tr data-area="${a.area_id}"><td><b>${esc(a.district)}</b> <span class="muted">${a.area_id}</span></td><td>${a.priority_class} · ${a.priority_weight.toFixed(2)}</td>
      <td class="r">${num(a.forecast_m3, 0)}</td><td class="r">${num(a.actual_m3, 0)}</td>
      <td>${tid ? `<span class="bullet ${st.cased ? "cased" : ""}" style="background:${st.color};color:${st.color};width:24px;height:24px;font-size:11px"><span style="color:#fff">${tid.slice(1)}</span></span> ${tid}` : statusPill("crit", t("p.noserve"))}</td></tr>`;
  }).join("");
  return `<div class="panel" style="margin-top:24px"><div style="max-height:420px;overflow:auto"><table><thead><tr><th>${esc(t("p.area"))}</th><th>${esc(t("p.class"))}</th><th class="r">${esc(t("p.fc"))}</th><th class="r">${esc(t("p.act"))}</th><th>${esc(t("p.tank"))}</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

function headline() {
  if (!plan) return { h: t("p.title"), s: t("p.note") };
  const c = plan.comparison[policy];
  const m = plan.areas.length;
  const h = c.areas_not_served ? t("p.head", { n: c.areas_not_served, m, s: pct(c.unserved_share, 1) }) : t("p.headok", { m });
  const s = t("p.sub", { start: dateTime(plan.outage_start), dur: plan.duration_h, fc: num(plan.total_forecast_m3, 0), act: num(plan.total_actual_m3, 0), w: pct(plan.forecast_wape, 1) });
  return { h, s };
}

function evidence() {
  if (!evalData) return `<div class="panel"><h2>${esc(t("p.evidence"))}</h2><p class="loading">${esc(t("p.evloading"))}</p></div>`;
  const ht = evalData.hourly_test;
  return `<div class="panel"><header><h2>${esc(t("p.evidence"))}</h2>${statusPill("warn", t("simulated"))}</header>
    <p style="font-size:17px">${esc(t("p.reproduced", { a: pct(ht.wape_model, 2), b: pct(ht.wape_naive, 2) }))}</p></div>
    <div class="charts2">
      <div class="panel"><h3>${esc(t("p.ev.policies"))}</h3><p class="panel-note">${esc(t("p.ev.nobuffer"))}</p><div id="ev-pol" style="margin-top:10px"></div>
        <p class="panel-note" style="margin-top:12px">${esc(t("p.ev.buffer"))}: MILP + XGBoost ${pct(evalData.policies_by_buffer["0.05"].milp_xgb, 2)}</p></div>
      <div class="panel"><h3>${esc(t("p.ev.duration"))}</h3><div class="chart" id="ev-dur"></div></div>
    </div>
    <div class="charts2">
      <div class="panel"><h3>${esc(t("p.ev.series"))}</h3><div class="field" style="margin:6px 0"><select id="ev-area">${net.areas.map((a) => `<option value="${a.area_id}" ${a.area_id === (series?.area_id || "A15") ? "selected" : ""}>${a.area_id} · ${esc(a.district)}</option>`).join("")}</select></div><div class="chart" id="ev-series"></div></div>
      <div class="panel"><h3>${esc(t("p.ev.hour"))}</h3><div class="chart" id="ev-hour"></div></div>
    </div>
    <div class="charts2">
      <div class="panel"><h3>${esc(t("p.ev.importance"))}</h3><div id="ev-imp" style="margin-top:10px"></div></div>
      <div class="panel"><h3>${esc(t("p.ev.shap"))}</h3><div class="chart" id="ev-shap"></div></div>
    </div>`;
}

function drawEvidence() {
  if (!evalData) return;
  const b0 = evalData.policies_by_buffer["0.00"];
  hbars($("ev-pol"), [
    { label: t("p.policies").oracle, value: b0.oracle },
    { label: t("p.policies").milp_xgb, value: b0.milp_xgb, me: true },
    { label: t("p.policies").greedy, value: b0.greedy },
  ], (v) => pct(v, 2));
  groupedBars($("ev-dur"), {
    groups: evalData.wape_by_duration.map((r) => `${r.duration_h} ${t("h")}`), yFmt: (v) => pct(v, 1), height: 220,
    series: [{ name: t("p.xgb"), color: "#0072bc", values: evalData.wape_by_duration.map((r) => r.wape_model) },
      { name: t("p.naive"), color: "#c27c00", values: evalData.wape_by_duration.map((r) => r.wape_naive) }],
  });
  lineChart($("ev-hour"), {
    x: evalData.wape_by_hour.map((r) => r.hour), xFmt: (h) => `${String(h).padStart(2, "0")}:00`, yFmt: (v) => pct(v, 1), yMin: 0, height: 220,
    series: [{ name: t("p.xgb"), color: "#0072bc", values: evalData.wape_by_hour.map((r) => r.wape_model) },
      { name: t("p.naive"), color: "#c27c00", values: evalData.wape_by_hour.map((r) => r.wape_naive) }],
  });
  const imp = evalData.importance_gain.slice(0, 10);
  hbars($("ev-imp"), imp.map((r) => ({ label: r.feature, value: r.share, color: "#7b3f99" })), (v) => pct(v, 1));
  shapChart($("ev-shap"), evalData.shap, t("p.shap.low"), t("p.shap.high"));
  drawSeries();
  $("ev-area").onchange = async (e) => { series = await get(`/api/outage/series?area_id=${e.target.value}&start=2022-11-01&hours=168`); drawSeries(); };
}

function drawSeries() {
  if (!series || !$("ev-series")) return;
  const p = series.points;
  lineChart($("ev-series"), {
    x: p.map((r) => r.ts), xFmt: (v, i, full) => (full ? dateTime(v) : v.slice(5, 10)), yFmt: (v) => num(v, 0), height: 220,
    series: [{ name: t("p.actual"), color: "#0e1a2b", values: p.map((r) => r.actual), width: 2 },
      { name: t("p.xgb"), color: "#0072bc", values: p.map((r) => r.pred) },
      { name: t("p.naive"), color: "#c27c00", values: p.map((r) => r.naive), dash: true }],
  });
}

function render() {
  if (!net) return;
  const { h, s } = headline();
  const lim = (evalData?.limits || plan?.limits || []);
  const legend = net.tanks.map((tk) => { const st = tankStyle(tk.tank_id); return `<span><i style="background:${st.color};height:6px;${st.cased ? "box-shadow:inset 0 0 0 1.5px #fff" : ""}"></i>${tk.tank_id}</span>`; }).join("");
  $("planner").innerHTML = (`
    <div><h1 class="headline" style="max-width:40ch">${esc(h)}</h1><p class="subline">${esc(s)}</p>
      ${plan?.in_sample ? `<p class="toast err" style="margin-top:8px;display:inline-block">${esc(t("p.insample"))}</p>` : ""}</div>
    <div class="planner">
      <div class="p-left">${controls()}</div>
      <div><h2 style="margin-bottom:10px">${esc(t("p.maptitle"))}</h2><div class="map-wrap" id="p-map">${mapSvg()}</div>
        <div class="legend" style="margin-top:10px">${legend}<span><svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><circle cx="9" cy="9" r="7" fill="#fbe7ea" stroke="#c8102e" stroke-width="2" stroke-dasharray="3 2"/></svg>${esc(t("p.legend.unserved"))}</span><span><i style="background:#c3cad5;width:8px;height:8px;border-radius:50%"></i>${esc(t("p.legend.unaffected"))}</span></div>
        ${areaTable()}</div>
      <div class="p-right">${rightPanel()}</div>
    </div>
    ${evidence()}
    <div class="panel"><h3>${esc(t("p.limits"))}</h3><ul class="limits" style="margin-top:8px">${lim.map((x, i) => `<li>${esc(trLimit(x, i))}</li>`).join("")}</ul></div>`).replace(/ · /g, "\u2009\u00b7\u2002");
  if (plan) hbars($("p-pbars"), ["milp_xgb", "oracle", "greedy"].map((p) => ({ label: t("p.policies")[p], value: plan.comparison[p].unserved_share, me: p === "milp_xgb" })), (v) => pct(v, 2));
  bindMap();
  bindControls();
  drawEvidence();
  document.querySelectorAll("tr[data-area]").forEach((tr) => {
    tr.addEventListener("pointerenter", () => { hoverArea = tr.dataset.area; $("p-map").innerHTML = mapSvg(); bindMap(); });
    tr.addEventListener("pointerleave", () => { hoverArea = null; $("p-map").innerHTML = mapSvg(); bindMap(); });
  });
}

function bindControls() {
  $("p-mode").onclick = (e) => { const b = e.target.closest("[data-m]"); if (!b) return; mode = b.dataset.m; errMsg = null; if (mode === "stored") loadStored(); else { plan = null; render(); } };
  $("p-pol").onclick = (e) => { const b = e.target.closest("[data-p]"); if (b) { policy = b.dataset.p; render(); } };
  if (mode === "stored") { $("p-scen").onchange = (e) => { scenarioId = e.target.value; loadStored(); }; return; }
  $("p-start").onchange = (e) => { custom.start = e.target.value; };
  $("p-dur").onclick = (e) => { const b = e.target.closest("[data-d]"); if (b) { custom.dur = +b.dataset.d; render(); } };
  $("p-fill").oninput = (e) => { custom.fill = +e.target.value; $("p-fill-o").textContent = `${custom.fill}%`; };
  $("p-areas").onchange = (e) => { if (e.target.checked) custom.areas.add(e.target.value); else custom.areas.delete(e.target.value); plan = null; render(); };
  $("p-all").onclick = () => { custom.areas = new Set(net.areas.map((a) => a.area_id)); plan = null; render(); };
  $("p-none").onclick = () => { custom.areas = new Set(); plan = null; render(); };
  $("p-go").onclick = runCustom;
}

async function loadStored() {
  solving = true; errMsg = null; render();
  try { plan = await post("/api/outage/plan", { scenario_id: scenarioId }); } catch (e) { errMsg = e.message; plan = null; }
  solving = false; render();
}

async function runCustom() {
  solving = true; errMsg = null; render();
  try {
    plan = await post("/api/outage/plan", {
      outage_start: custom.start.replace("T", " "), duration_h: custom.dur, area_ids: [...custom.areas],
      tank_levels_m3: net.tanks.map((tk) => Math.round((tk.capacity_m3 * custom.fill) / 100)),
    });
  } catch (e) { errMsg = e.message; plan = null; }
  solving = false; render();
}

async function boot() {
  const host = $("planner");
  for (;;) {
    const st = await get("/api/outage/status");
    if (st.status === "ready") break;
    if (st.status === "error") { host.innerHTML = `<p class="toast err">${esc(t("p.error"))} ${esc(st.error)}</p>`; return; }
    host.innerHTML = `<p class="loading">${esc(t("p.loading"))} <span class="muted">${esc(st.message || "")}</span></p>`;
    await new Promise((r) => setTimeout(r, 1500));
  }
  [net, { scenarios }] = await Promise.all([get("/api/outage/network"), get("/api/outage/scenarios")]);
  series = await get("/api/outage/series?area_id=A15&start=2022-11-01&hours=168");
  render();
  loadStored();
  for (;;) {
    try { evalData = await get("/api/outage/evaluation"); render(); break; } catch { await new Promise((r) => setTimeout(r, 3000)); }
  }
}

export function initPlanner() {
  window.addEventListener("wat:tab", (e) => { if (e.detail === "planner" && !started) { started = true; boot().catch((err) => { $("planner").innerHTML = `<p class="toast err">${esc(err.message)}</p>`; }); } });
  onLang(render);
}
