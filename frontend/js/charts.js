// Small SVG chart helpers: line/band chart with crosshair tooltip, grouped bars, horizontal bars.
// Thin marks, recessive grid, legend for >= 2 series, a table view for every chart.
import { t } from "./i18n.js";

const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs = {}, parent) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
};
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function niceTicks(min, max, n = 4) {
  const span = max - min || 1;
  const step0 = span / n;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) || step0;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

function legendHtml(series, extra = []) {
  const items = series.filter((s) => !s.noLegend).map((s) =>
    `<span><i class="${s.dash ? "dash" : ""}" style="background:${s.color};color:${s.color}"></i>${esc(s.name)}</span>`);
  extra.forEach((e) => items.push(`<span><i class="${e.cls || ""}" style="background:${e.color};color:${e.color}"></i>${esc(e.name)}</span>`));
  return items.length >= 2 ? `<div class="legend">${items.join("")}</div>` : "";
}

function tableToggle(host, headers, rows) {
  const wrap = document.createElement("div");
  const btn = document.createElement("button");
  btn.type = "button"; btn.className = "btn sm table-toggle"; btn.textContent = t("chart.table");
  const box = document.createElement("div"); box.hidden = true; box.style.maxHeight = "240px"; box.style.overflow = "auto";
  box.innerHTML = `<table><thead><tr>${headers.map((h, i) => `<th class="${i ? "r" : ""}">${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${i ? "r" : ""}">${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  btn.onclick = () => { box.hidden = !box.hidden; btn.textContent = box.hidden ? t("chart.table") : t("chart.hide"); };
  wrap.append(btn, box); wrap.style.marginTop = "8px";
  host.appendChild(wrap);
}

/**
 * lineChart(host, {x: [labels or numbers], series: [{name, color, values, dash, width}],
 *   bands: [{color, lo:[], hi:[]}], hlines: [{y, color, label, dash}], yMin, yMax, yFmt, xFmt, height})
 */
export function lineChart(host, o) {
  host.innerHTML = "";
  const W = o.width || Math.max(360, Math.round(host.clientWidth || 640)), H = o.height || 240, m = { l: 48, r: 16, t: 14, b: 28 };
  const n = o.x.length;
  if (!n) { host.innerHTML = `<p class="muted">—</p>`; return; }
  const all = [];
  o.series.forEach((s) => s.values.forEach((v) => v != null && all.push(v)));
  (o.bands || []).forEach((b) => { b.lo.forEach((v) => all.push(v)); b.hi.forEach((v) => all.push(v)); });
  (o.hlines || []).forEach((h) => all.push(h.y));
  let yMin = o.yMin ?? Math.min(...all), yMax = o.yMax ?? Math.max(...all);
  if (o.yMin === undefined) yMin = Math.min(0, yMin);
  if (yMax - yMin < 1e-9) yMax = yMin + 1;
  yMax += (yMax - yMin) * 0.06;
  const X = (i) => m.l + (n === 1 ? 0 : (i / (n - 1)) * (W - m.l - m.r));
  const Y = (v) => H - m.b - ((v - yMin) / (yMax - yMin)) * (H - m.t - m.b);
  host.insertAdjacentHTML("beforeend", legendHtml(o.series, o.legendExtra || []));
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": o.label || "" });
  const g = el("g", { class: "grid" }, svg);
  const fmt = o.yFmt || ((v) => v);
  niceTicks(yMin, yMax).forEach((v) => {
    el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }, g);
    el("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end" }, svg).textContent = fmt(v);
  });
  const xf = o.xFmt || ((v) => v);
  const every = Math.max(1, Math.ceil(n / 6));
  for (let i = 0; i < n; i += every) el("text", { x: X(i), y: H - 6, "text-anchor": "middle" }, svg).textContent = xf(o.x[i], i);
  (o.bands || []).forEach((b) => {
    const top = b.hi.map((v, i) => `${X(i)},${Y(v)}`).join(" ");
    const bot = b.lo.map((v, i) => `${X(i)},${Y(v)}`).reverse().join(" ");
    el("polygon", { points: `${top} ${bot}`, fill: b.color, opacity: 0.16 }, svg);
  });
  (o.hlines || []).forEach((h) => {
    el("line", { x1: m.l, x2: W - m.r, y1: Y(h.y), y2: Y(h.y), stroke: h.color || "#0e1a2b", "stroke-width": 1.5, "stroke-dasharray": h.dash ? "5 4" : "" }, svg);
    if (h.label) el("text", { x: h.start ? m.l + 6 : W - m.r, y: Y(h.y) + (h.below ? 15 : -5), "text-anchor": h.start ? "start" : "end", style: "fill:#33415a;font-weight:700" }, svg).textContent = h.label;
  });
  o.series.forEach((s) => {
    let d = "", pen = false;
    s.values.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`; pen = true;
    });
    el("path", { d, fill: "none", stroke: s.color, "stroke-width": s.width || 2, "stroke-linejoin": "round", "stroke-linecap": "round", "stroke-dasharray": s.dash ? "6 4" : "" }, svg);
  });
  // hover layer: crosshair + tooltip
  const cross = el("line", { y1: m.t, y2: H - m.b, stroke: "#0e1a2b", "stroke-width": 1, opacity: 0 }, svg);
  const dots = o.series.map((s) => el("circle", { r: 4.5, fill: s.color, stroke: "#fff", "stroke-width": 2, opacity: 0 }, svg));
  const hit = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" }, svg);
  const tip = document.createElement("div"); tip.className = "tip";
  host.style.position = "relative";
  host.append(svg, tip);
  const show = (evt) => {
    const r = svg.getBoundingClientRect();
    const px = ((evt.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - m.l) / (W - m.l - m.r)) * (n - 1))));
    cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("opacity", 0.35);
    o.series.forEach((s, k) => {
      const v = s.values[i];
      if (v == null) { dots[k].setAttribute("opacity", 0); return; }
      dots[k].setAttribute("cx", X(i)); dots[k].setAttribute("cy", Y(v)); dots[k].setAttribute("opacity", 1);
    });
    tip.innerHTML = `<b>${esc(xf(o.x[i], i, true))}</b>` + o.series.filter((s) => !s.noTip).map((s) =>
      `<div class="r"><span><i style="background:${s.color}"></i>${esc(s.name)}</span><b>${s.values[i] == null ? "—" : esc(fmt(s.values[i], true))}</b></div>`).join("");
    tip.classList.add("on");
    const left = (X(i) / W) * r.width;
    tip.style.left = `${Math.min(r.width - 170, Math.max(0, left + 12))}px`;
    tip.style.top = "8px";
  };
  hit.addEventListener("pointermove", show);
  hit.addEventListener("pointerleave", () => { tip.classList.remove("on"); cross.setAttribute("opacity", 0); dots.forEach((d) => d.setAttribute("opacity", 0)); });
  if (o.table !== false) {
    const step = Math.max(1, Math.ceil(n / 48));
    const rows = [];
    for (let i = 0; i < n; i += step) rows.push([xf(o.x[i], i, true), ...o.series.map((s) => (s.values[i] == null ? "—" : fmt(s.values[i], true)))]);
    tableToggle(host, [t("chart.time"), ...o.series.map((s) => s.name)], rows);
  }
}

/** groupedBars(host, {groups: [label], series: [{name, color, values}], yFmt, height}) */
export function groupedBars(host, o) {
  host.innerHTML = "";
  const W = o.width || Math.max(360, Math.round(host.clientWidth || 640)), H = o.height || 220, m = { l: 48, r: 10, t: 18, b: 28 };
  const max = Math.max(...o.series.flatMap((s) => s.values)) * 1.15 || 1;
  const Y = (v) => H - m.b - (v / max) * (H - m.t - m.b);
  host.insertAdjacentHTML("beforeend", legendHtml(o.series));
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": o.label || "" });
  const g = el("g", { class: "grid" }, svg);
  const fmt = o.yFmt || ((v) => v);
  niceTicks(0, max).forEach((v) => {
    el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }, g);
    el("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end" }, svg).textContent = fmt(v);
  });
  const gw = (W - m.l - m.r) / o.groups.length;
  const bw = Math.min(34, (gw * 0.7) / o.series.length);
  const tip = document.createElement("div"); tip.className = "tip";
  o.groups.forEach((label, gi) => {
    const x0 = m.l + gi * gw + (gw - bw * o.series.length - 2 * (o.series.length - 1)) / 2;
    o.series.forEach((s, si) => {
      const v = s.values[gi];
      const x = x0 + si * (bw + 2), y = Y(v);
      const h = Math.max(1, H - m.b - y);
      el("path", { d: `M${x},${H - m.b}V${y + 4}q0,-4 4,-4h${bw - 8}q4,0 4,4V${H - m.b}Z`, fill: s.color }, svg);
      if (o.labels !== false && o.series.length <= 2) el("text", { x: x + bw / 2, y: y - 4, "text-anchor": "middle", style: "font-size:11px;fill:#33415a;font-weight:700" }, svg).textContent = fmt(v);
      const hit = el("rect", { x: x - 2, y: m.t, width: bw + 4, height: H - m.t - m.b, fill: "transparent" }, svg);
      hit.addEventListener("pointerenter", () => {
        tip.innerHTML = `<b>${esc(label)}</b><div class="r"><span><i style="background:${s.color}"></i>${esc(s.name)}</span><b>${esc(fmt(v, true))}</b></div>`;
        tip.classList.add("on");
        const r = svg.getBoundingClientRect();
        tip.style.left = `${Math.min(r.width - 170, (x / W) * r.width)}px`; tip.style.top = "0px";
        void h;
      });
      hit.addEventListener("pointerleave", () => tip.classList.remove("on"));
    });
    el("text", { x: m.l + gi * gw + gw / 2, y: H - 6, "text-anchor": "middle" }, svg).textContent = label;
  });
  host.style.position = "relative";
  host.append(svg, tip);
  tableToggle(host, ["", ...o.series.map((s) => s.name)], o.groups.map((g2, i) => [g2, ...o.series.map((s) => fmt(s.values[i], true))]));
}

/** hbars(host, rows: [{label, value, color}], fmt) — sorted horizontal bars with direct labels. */
export function hbars(host, rows, fmt) {
  const max = Math.max(...rows.map((r) => r.value)) || 1;
  host.innerHTML = `<div class="policy-bars">${rows.map((r) => `
    <div class="pbar ${r.me ? "me" : ""}"><span>${esc(r.label)}</span><span class="t"><i style="width:${(r.value / max) * 100}%;${r.color ? `background:${r.color}` : ""}"></i></span><b class="r">${esc(fmt(r.value))}</b></div>`).join("")}</div>`;
}

/** beeswarm-lite for SHAP: rows of dots (x = shap value, colour = feature value rank). */
export function shapChart(host, feats, lowLabel, highLabel) {
  host.innerHTML = "";
  const W = Math.max(360, Math.round(host.clientWidth || 640)), rowH = 26, m = { l: 130, r: 16, t: 8, b: 28 };
  const H = m.t + m.b + rowH * feats.length;
  const vals = feats.flatMap((f) => f.points.map((p) => p[1]));
  const ext = Math.max(Math.abs(Math.min(...vals)), Math.abs(Math.max(...vals))) || 1;
  const X = (v) => m.l + ((v + ext) / (2 * ext)) * (W - m.l - m.r);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "SHAP summary" });
  el("line", { x1: X(0), x2: X(0), y1: m.t, y2: H - m.b, stroke: "#99a3b3" }, svg);
  const lo = [0x9f, 0xc6, 0xe8], hi = [0x7b, 0x3f, 0x99];   // light blue -> violet, one sequential ramp
  const col = (r) => `rgb(${lo.map((c, i) => Math.round(c + (hi[i] - c) * r)).join(",")})`;
  feats.forEach((f, i) => {
    const y = m.t + rowH * i + rowH / 2;
    el("text", { x: m.l - 10, y: y + 4, "text-anchor": "end", style: "fill:#0e1a2b;font-weight:700" }, svg).textContent = f.feature;
    f.points.forEach((p, k) => {
      const jitter = ((k * 37) % 13) / 13 - 0.5;
      el("circle", { cx: X(p[1]), cy: y + jitter * (rowH * 0.6), r: 2.4, fill: col(p[0]), opacity: 0.85 }, svg);
    });
  });
  [-ext, 0, ext].forEach((v) => { el("text", { x: X(v), y: H - 8, "text-anchor": "middle" }, svg).textContent = v.toFixed(2); });
  host.insertAdjacentHTML("beforeend", `<div class="legend"><span><i style="background:${col(0)};height:8px;width:8px;border-radius:50%"></i>${esc(lowLabel)}</span><span><i style="background:${col(1)};height:8px;width:8px;border-radius:50%"></i>${esc(highLabel)}</span></div>`);
  host.appendChild(svg);
  tableToggle(host, ["feature", "mean |SHAP|"], feats.map((f) => [f.feature, f.mean_abs_shap.toFixed(4)]));
}
