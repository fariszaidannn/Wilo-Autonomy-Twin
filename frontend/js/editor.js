// "Edit the water system": tank, pumps, zones and the pipes between them, in one page.
// Saving validates on the server, writes building.json and rebuilds the twin, so the new
// system applies to the Building tab, the load balancer, the scenario lab and usage alike.
import { post, ApiError } from "./api.js";
import { t, onLang, num } from "./i18n.js";
import { topo, setTopo, pumpHex, label, layout, linePath, busPath } from "./topo.js";
import { esc, icon, statusPill } from "./ui.js";

const $ = (id) => document.getElementById(id);
let draft = null, check = { errors: [], warnings: [] }, saving = false, msg = null, timer = null;
const copy = (o) => JSON.parse(JSON.stringify(o));
const dirty = () => draft && topo.cfg && JSON.stringify(draft) !== JSON.stringify(topo.cfg);
const PROFILES = ["clinic", "classroom", "residential"];

// ---------- path helpers: "pumps.0.rated_flow_m3h" ----------
function getP(path) { return path.split(".").reduce((o, k) => (o == null ? o : o[k]), draft); }
function setP(path, v) {
  const keys = path.split("."); const last = keys.pop();
  const o = keys.reduce((a, k) => (a[k] ??= {}), draft);
  o[last] = v;
}

function field(lbl, path, { unit = "", step = "any", min = 0, type = "number", wide = false, value } = {}) {
  const v = value ?? getP(path);
  return `<label class="field ed-f${wide ? " wide" : ""}"><span>${esc(lbl)}${unit ? ` <i>${esc(unit)}</i>` : ""}</span>
    <input type="${type}" data-path="${path}" ${type === "number" ? `step="${step}" min="${min}" inputmode="decimal"` : 'maxlength="60"'} value="${esc(v ?? "")}"></label>`;
}

// ---------- structure edits ----------
function nextPumpId() {
  for (const c of "ABCDEFGHIJKLMNOPQRSTUVWXYZ") if (!draft.pumps.some((p) => p.id === `P${c}`)) return `P${c}`;
  return `P${draft.pumps.length + 1}`;
}
function nextZoneId() {
  for (let n = 1; ; n++) if (!draft.zones.some((z) => z.id === `Z${n}`)) return `Z${n}`;
}
function addPump() {
  const base = draft.pumps[draft.pumps.length - 1];
  const id = nextPumpId();
  draft.pumps.push({ ...copy(base), id, name: `Booster Pump ${id.slice(1)}` });
  render();
}
function removePump(id) {
  draft.pumps = draft.pumps.filter((p) => p.id !== id);
  draft.zones.forEach((z) => {
    z.allowed_pumps = z.allowed_pumps.filter((p) => p !== id);
    const feed = draft.default_allocation[z.id] || {};
    if (feed[id] !== undefined) draft.default_allocation[z.id] = z.allowed_pumps.length ? { [z.allowed_pumps[0]]: 1 } : {};
  });
  draft.generator.covers_pumps = (draft.generator.covers_pumps || []).filter((p) => p !== id);
  render();
}
function addZone() {
  const id = nextZoneId(), n = id.slice(1), first = draft.pumps[0].id;
  draft.zones.push({ id, name: `New zone ${n}`, priority: 3, elevation_m: 10, min_pressure_bar: 1.5, setpoint_bar: 2.5,
    min_flow_ratio: 0.9, friction_k: 0.16, buffer_m3: 0.3, allowed_pumps: draft.pumps.map((p) => p.id),
    crossover_valve: `XV-${n}`, occupancy: 100 });
  draft.default_allocation[id] = { [first]: 1 };
  draft.synthetic.zones[id] = { peak_m3h: 5, profile: "residential" };
  render();
}
function removeZone(id) {
  draft.zones = draft.zones.filter((z) => z.id !== id);
  delete draft.default_allocation[id];
  delete draft.synthetic.zones[id];
  render();
}

// ---------- panels ----------
function tankPanel() {
  const tk = draft.tank;
  const prios = [...new Set(draft.zones.map((z) => +z.priority))].filter((p) => p >= 2).sort();
  return `<div class="panel"><header><h2>${esc(t("ed.tank"))}</h2><span class="panel-note">${esc(t("ed.tank.note"))}</span></header>
    <div class="ed-fields">
      ${field(t("ed.name"), "tank.name", { type: "text", wide: true })}
      ${field(t("ed.capacity"), "tank.capacity_m3", { unit: "m³", step: 1 })}
      ${field(t("ed.dead"), "tank.dead_volume_m3", { unit: "m³", step: 0.5 })}
      ${field(t("ed.start"), "tank.initial_volume_m3", { unit: "m³", step: 1 })}
      ${field(t("ed.city"), "tank.city_inflow_max_m3h", { unit: "m³/h", step: 0.5 })}
      ${field(t("ed.fuel"), "generator.fuel_hours", { unit: "h", step: 0.5 })}
      ${prios.map((p) => field(t("ed.reserve", { p }), `tank.priority_reserve_pct.${p}`, { unit: "%", step: 1 })).join("")}
    </div></div>`;
}

function pumpsPanel() {
  const gen = draft.generator.covers_pumps || [];
  const ids = draft.pumps.map((p) => p.id);
  const rows = draft.pumps.map((p, i) => `
    <div class="ed-row">
      <div class="ed-id"><span class="bullet" style="background:${pumpHex(p.id, ids)}">${esc(p.id.slice(1))}</span><b>${esc(p.id)}</b></div>
      <div class="ed-fields">
        ${field(t("ed.name"), `pumps.${i}.name`, { type: "text", wide: true })}
        ${field(t("ed.rated"), `pumps.${i}.rated_flow_m3h`, { unit: "m³/h", step: 0.5 })}
        ${field(t("ed.max"), `pumps.${i}.max_flow_m3h`, { unit: "m³/h", step: 0.5 })}
        ${field(t("ed.shutoff"), `pumps.${i}.shutoff_head_m`, { unit: "m", step: 0.5 })}
        ${field(t("ed.ratedhead"), `pumps.${i}.rated_head_m`, { unit: "m", step: 0.5 })}
        ${field(t("ed.eff"), `pumps.${i}.efficiency`, { unit: "0–1", step: 0.01 })}
        <label class="switch ed-gen"><span>${esc(t("ed.ongen"))}</span><input type="checkbox" data-gen="${p.id}" ${gen.includes(p.id) ? "checked" : ""}></label>
      </div>
      <button type="button" class="btn sm" data-del-pump="${p.id}" ${draft.pumps.length <= 1 ? "disabled" : ""}>${esc(t("ed.remove"))}</button>
    </div>`).join("");
  return `<div class="panel"><header><h2>${esc(t("ed.pumps"))}</h2><button type="button" class="btn sm" id="ed-add-pump" ${draft.pumps.length >= 4 ? "disabled" : ""}>+ ${esc(t("ed.addpump"))}</button></header>${rows}</div>`;
}

function zonesPanel() {
  const rows = draft.zones.map((z, i) => {
    const syn = draft.synthetic.zones[z.id] || {};
    const peak = +syn.peak_m3h || 0;
    const loss = (+z.friction_k || 0) * peak * peak;
    return `
    <div class="ed-row">
      <div class="ed-id"><span class="ed-pip"></span><b>${esc(z.id)}</b></div>
      <div class="ed-fields">
        ${field(t("ed.name"), `zones.${i}.name`, { type: "text", wide: true })}
        <label class="field ed-f"><span>${esc(t("ed.priority"))}</span><select data-path="zones.${i}.priority" data-int="1">
          ${[1, 2, 3, 4, 5].map((n) => `<option value="${n}" ${+z.priority === n ? "selected" : ""}>${n}${n === 1 ? ` · ${esc(t("critical"))}` : ""}</option>`).join("")}</select></label>
        ${field(t("ed.elev"), `zones.${i}.elevation_m`, { unit: "m", step: 0.5 })}
        ${field(t("ed.minp"), `zones.${i}.min_pressure_bar`, { unit: "bar", step: 0.1 })}
        ${field(t("ed.setp"), `zones.${i}.setpoint_bar`, { unit: "bar", step: 0.1 })}
        ${field(t("ed.peak"), `synthetic.zones.${z.id}.peak_m3h`, { unit: "m³/h", step: 0.1 })}
        <label class="field ed-f"><span>${esc(t("ed.profile"))}</span><select data-path="synthetic.zones.${z.id}.profile">
          ${PROFILES.map((p) => `<option value="${p}" ${syn.profile === p ? "selected" : ""}>${esc(t("ed.prof")[p])}</option>`).join("")}</select></label>
        ${field(t("ed.occ"), `zones.${i}.occupancy`, { step: 1 })}
        ${field(t("ed.vessel"), `zones.${i}.buffer_m3`, { unit: "m³", step: 0.05 })}
        ${field(t("ed.loss"), "", { unit: "m", step: 0.1, value: +loss.toFixed(2) }).replace('data-path=""', `data-loss="${i}"`)}
      </div>
      <button type="button" class="btn sm" data-del-zone="${z.id}" ${draft.zones.length <= 1 ? "disabled" : ""}>${esc(t("ed.remove"))}</button>
    </div>`;
  }).join("");
  return `<div class="panel"><header><h2>${esc(t("ed.zones"))}</h2><button type="button" class="btn sm" id="ed-add-zone" ${draft.zones.length >= 6 ? "disabled" : ""}>+ ${esc(t("ed.addzone"))}</button></header>${rows}</div>`;
}

function connPanel() {
  const ids = draft.pumps.map((p) => p.id);
  const head = draft.pumps.map((p) => `<th class="c"><span class="bullet" style="background:${pumpHex(p.id, ids)};width:26px;height:26px">${esc(p.id.slice(1))}</span><br>${esc(p.id)}</th>`).join("");
  const rows = draft.zones.map((z, i) => {
    const feed = Object.keys(draft.default_allocation[z.id] || {})[0];
    const cells = draft.pumps.map((p) => {
      const on = z.allowed_pumps.includes(p.id);
      return `<td class="c"><label class="ed-conn" title="${esc(t("ed.connected"))}"><input type="checkbox" data-allow="${i}" value="${p.id}" ${on ? "checked" : ""}><span>${esc(t("ed.connected"))}</span></label>
        <label class="ed-conn" title="${esc(t("ed.normal"))}"><input type="radio" name="feed-${z.id}" data-feed="${z.id}" value="${p.id}" ${feed === p.id ? "checked" : ""} ${on ? "" : "disabled"}><span>${esc(t("ed.normal"))}</span></label></td>`;
    }).join("");
    return `<tr><td><b>${esc(z.id)}</b> <span class="muted">${esc(z.name)}</span></td>${cells}<td>${field("", `zones.${i}.crossover_valve`, { type: "text" }).replace("<span></span>", "")}</td></tr>`;
  }).join("");
  return `<div class="panel"><header><h2>${esc(t("ed.conn"))}</h2><span class="panel-note">${esc(t("ed.conn.note"))}</span></header>
    <div style="overflow-x:auto"><table class="ed-matrix"><thead><tr><th>${esc(t("s.zone"))}</th>${head}<th>${esc(t("ed.valve"))}</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

// ---------- preview: the edited system drawn as a line map ----------
function preview() {
  const pIds = draft.pumps.map((p) => p.id), zIds = draft.zones.map((z) => z.id);
  const { H, mid, PY, ZY } = layout(pIds, zIds);
  const X0 = 150, X1 = 252, SX = 600;
  const parts = [`<path d="M104,${mid} H${X0 - 30}" stroke="#0e1a2b" stroke-width="10" fill="none" stroke-linecap="round"/>`];
  pIds.forEach((p) => parts.push(`<path d="${busPath(104, mid, X0 - 24, X0, PY[p])}" stroke="#0e1a2b" stroke-width="6" fill="none" stroke-linejoin="round"/>`));
  draft.zones.forEach((z) => {
    const feed = Object.keys(draft.default_allocation[z.id] || {})[0];
    z.allowed_pumps.forEach((p, k) => {
      if (!(p in PY)) return;
      const off = (k - (z.allowed_pumps.length - 1) / 2) * 12;
      const d = linePath(X1, PY[p] + off * 0.6, SX, ZY[z.id] + off);
      parts.push(p === feed
        ? `<path d="${d}" stroke="${pumpHex(p, pIds)}" stroke-width="10" fill="none" stroke-linejoin="round"/>`
        : `<path d="${d}" stroke="${pumpHex(p, pIds)}" stroke-width="4" fill="none" stroke-dasharray="8 7" stroke-linejoin="round" opacity=".75"/>`);
    });
  });
  parts.push(`<rect x="34" y="${mid - 90}" width="70" height="180" rx="14" fill="#cfe6f5" stroke="#0e1a2b" stroke-width="4"/>
    <text x="69" y="${mid + 116}" text-anchor="middle" font-size="28" font-weight="800" fill="#0e1a2b">${num(+draft.tank.capacity_m3 || 0, 0)} m³</text>`);
  draft.pumps.forEach((p) => {
    const y = PY[p.id];
    parts.push(`<rect x="${X0}" y="${y - 26}" width="${X1 - X0}" height="52" rx="26" fill="#fff" stroke="#0e1a2b" stroke-width="4"/>
      <circle cx="${X0 + 26}" cy="${y}" r="16" fill="${pumpHex(p.id, pIds)}"/><text x="${X0 + 26}" y="${y + 6}" text-anchor="middle" font-size="17" font-weight="800" fill="#fff">${esc(p.id.slice(1))}</text>
      <text x="${X0 + 50}" y="${y + 8}" font-size="22" font-weight="800" fill="#0e1a2b">${num(+p.rated_flow_m3h || 0, 0)}</text>
      <text x="${X0}" y="${y - 36}" font-size="26" font-weight="800" fill="#0e1a2b">${esc(label(p.id, draft))}</text>`);
  });
  draft.zones.forEach((z) => {
    const y = ZY[z.id];
    const bad = !z.allowed_pumps.length;
    parts.push(`<circle cx="${SX + 14}" cy="${y}" r="14" fill="#fff" stroke="${bad ? "#c8102e" : "#0e1a2b"}" stroke-width="5" ${bad ? 'stroke-dasharray="5 4"' : ""}/>
      <text x="${SX + 42}" y="${y - 4}" font-size="32" font-weight="800" fill="#0e1a2b">${esc(z.id)} · P${esc(z.priority)}</text>
      <text x="${SX + 42}" y="${y + 28}" font-size="24" fill="#33415a">${esc(label(z.id, draft)).slice(0, 24)}</text>`);
  });
  return `<svg class="schematic" viewBox="0 0 1000 ${H}" role="img" aria-label="${esc(t("ed.preview"))}">${parts.join("")}</svg>`.replace(/ · /g, " · ");
}

function checkPanel() {
  const e = check.errors, w = check.warnings;
  const head = e.length ? statusPill("crit", t("ed.errors", { n: e.length })) : w.length ? statusPill("warn", t("ed.warnings", { n: w.length })) : statusPill("good", t("ed.ok"));
  return `<header><h3>${esc(t("ed.check"))}</h3>${head}</header>
    <ul class="checks">${e.map((x) => `<li>${icon("x", "var(--crit)")}<span>${esc(x)}</span></li>`).join("")}${w.map((x) => `<li>${icon("alert", "var(--warn)")}<span>${esc(x)}</span></li>`).join("")}</ul>`;
}

// ---------- render & bind ----------
function render() {
  if (!draft) return;
  $("editor").innerHTML = `
    <div class="ed-head">
      <div><button type="button" class="btn sm" id="ed-back">← ${esc(t("tab.building"))}</button>
        <h1 class="headline" id="ed-title" style="margin-top:12px">${esc(t("ed.title"))}</h1>
        <p class="subline" style="max-width:70ch">${esc(t("ed.sub"))}</p></div>
      <div class="btn-row ed-actions">
        <button type="button" class="btn" id="ed-restore">${esc(t("ed.restore"))}</button>
        <button type="button" class="btn" id="ed-discard" ${dirty() ? "" : "disabled"}>${esc(t("ed.discard"))}</button>
        <button type="button" class="btn primary" id="ed-save" ${saving || check.errors.length || !dirty() ? "disabled" : ""}>${esc(saving ? t("ed.saving") : t("ed.save"))}</button>
      </div>
    </div>
    <div aria-live="polite" id="ed-msg">${msg ? `<p class="toast ${msg.err ? "err" : "ok"}">${esc(msg.text)}</p>` : ""}</div>
    <div class="ed-grid">
      <div class="stack">${tankPanel()}${pumpsPanel()}${zonesPanel()}${connPanel()}</div>
      <aside class="ed-side">
        <div class="panel"><header><h3>${esc(t("ed.preview"))}</h3></header><div id="ed-preview">${preview()}</div>
          <div class="legend" style="margin-top:8px"><span><i style="background:#0e1a2b;height:5px"></i>${esc(t("ed.leg.normal"))}</span><span><i class="dash" style="color:#0e1a2b"></i>${esc(t("ed.leg.cross"))}</span></div></div>
        <div class="panel" id="ed-check">${checkPanel()}</div>
      </aside>
    </div>`;
  bind();
}

function refreshSide() {
  $("ed-preview").innerHTML = preview();
  $("ed-check").innerHTML = checkPanel();
  const save = $("ed-save");
  if (save) save.disabled = saving || check.errors.length > 0 || !dirty();
  const disc = $("ed-discard");
  if (disc) disc.disabled = !dirty();
}

function validateSoon() {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    try { check = await post("/api/config/validate", draft); } catch (e) { check = { errors: [e.message], warnings: [] }; }
    refreshSide();
  }, 250);
}

function bind() {
  const root = $("editor");
  root.querySelectorAll("[data-path]").forEach((inp) => {
    inp.addEventListener(inp.tagName === "SELECT" ? "change" : "input", () => {
      const path = inp.dataset.path;
      let v = inp.value;
      if (inp.type === "number") v = v === "" ? (path.includes("priority_reserve_pct") ? null : NaN) : +v;
      if (inp.dataset.int) v = parseInt(v, 10);
      if (path.includes("priority_reserve_pct") && v === null) delete draft.tank.priority_reserve_pct[path.split(".").pop()];
      else setP(path, Number.isNaN(v) ? inp.value : v);
      if (inp.tagName === "SELECT" && path.endsWith("priority")) { render(); validateSoon(); return; }
      refreshSide(); validateSoon();
    });
  });
  root.querySelectorAll("[data-loss]").forEach((inp) => inp.addEventListener("input", () => {
    const z = draft.zones[+inp.dataset.loss], peak = +(draft.synthetic.zones[z.id]?.peak_m3h) || 1;
    z.friction_k = +((+inp.value || 0) / (peak * peak)).toFixed(5);
    validateSoon();
  }));
  root.querySelectorAll("[data-gen]").forEach((box) => box.addEventListener("change", () => {
    const set = new Set(draft.generator.covers_pumps || []);
    if (box.checked) set.add(box.dataset.gen); else set.delete(box.dataset.gen);
    draft.generator.covers_pumps = draft.pumps.map((p) => p.id).filter((p) => set.has(p));
    refreshSide(); validateSoon();
  }));
  root.querySelectorAll("[data-allow]").forEach((box) => box.addEventListener("change", () => {
    const z = draft.zones[+box.dataset.allow];
    const set = new Set(z.allowed_pumps);
    if (box.checked) set.add(box.value); else set.delete(box.value);
    z.allowed_pumps = draft.pumps.map((p) => p.id).filter((p) => set.has(p));
    const feed = Object.keys(draft.default_allocation[z.id] || {})[0];
    if (!z.allowed_pumps.includes(feed)) draft.default_allocation[z.id] = z.allowed_pumps.length ? { [z.allowed_pumps[0]]: 1 } : {};
    render(); validateSoon();
  }));
  root.querySelectorAll("[data-feed]").forEach((r) => r.addEventListener("change", () => {
    draft.default_allocation[r.dataset.feed] = { [r.value]: 1 };
    refreshSide(); validateSoon();
  }));
  root.querySelectorAll("[data-del-pump]").forEach((b) => b.addEventListener("click", () => { removePump(b.dataset.delPump); validateSoon(); }));
  root.querySelectorAll("[data-del-zone]").forEach((b) => b.addEventListener("click", () => { removeZone(b.dataset.delZone); validateSoon(); }));
  $("ed-add-pump").onclick = () => { addPump(); validateSoon(); };
  $("ed-add-zone").onclick = () => { addZone(); validateSoon(); };
  $("ed-back").onclick = () => leave();
  $("ed-discard").onclick = () => { draft = copy(topo.cfg); msg = null; render(); validateSoon(); };
  $("ed-save").onclick = save;
  $("ed-restore").onclick = restore;
}

async function save() {
  saving = true; msg = null; refreshSide();
  try {
    const r = await post("/api/config", draft);
    setTopo(r.config);
    draft = copy(r.config);
    saving = false;
    window.dispatchEvent(new CustomEvent("wat:flash", { detail: t("ed.saved") }));
    location.hash = "building";
  } catch (e) {
    saving = false;
    msg = { err: true, text: e instanceof ApiError ? e.message.replace(/^\[|\]$/g, "").replace(/","/g, " · ").replace(/"/g, "") : String(e) };
    render();
  }
}

async function restore() {
  if (!window.confirm(t("ed.restore.confirm"))) return;
  try {
    const r = await post("/api/config/restore");
    setTopo(r.config);
    draft = copy(r.config);
    window.dispatchEvent(new CustomEvent("wat:flash", { detail: t("ed.restored") }));
    location.hash = "building";
  } catch (e) { msg = { err: true, text: e.message }; render(); }
}

function leave() {
  if (dirty() && !window.confirm(t("ed.leave.confirm"))) return;
  draft = null;
  location.hash = "building";
}

export function openEditor() {
  if (!draft) { draft = copy(topo.cfg); msg = null; check = { errors: [], warnings: [] }; }
  render();
  validateSoon();
  $("ed-title")?.focus?.();
}

export function initEditor() {
  onLang(() => { if (draft && !$("p-editor").hidden) render(); });
}
