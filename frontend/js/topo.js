// The building's current topology (from /api/config): colours, names and diagram geometry
// for any number of pumps and zones. Every screen reads it, so an edited system applies everywhere.
import { get } from "./api.js";
import { getLang } from "./i18n.js";

// Fixed categorical order (validated): pumps take the first slots, zones the slots after them.
export const LINE_HEX = ["#0072bc", "#c27c00", "#00968f", "#7b3f99", "#6e8b00", "#c2187b"];
export const topo = { cfg: null, pumps: [], zones: [] };

export async function loadTopo() {
  setTopo(await get("/api/config"));
  return topo;
}
export function setTopo(cfg) {
  topo.cfg = cfg;
  topo.pumps = cfg.pumps.map((p) => p.id);
  topo.zones = cfg.zones.map((z) => z.id);
  window.dispatchEvent(new CustomEvent("wat:topo", { detail: topo }));
}

export const pumpHex = (id, pumps = topo.pumps) => LINE_HEX[Math.max(0, pumps.indexOf(id)) % LINE_HEX.length];
export const zoneHex = (id, zones = topo.zones, nPumps = topo.pumps.length) =>
  LINE_HEX[(nPumps + Math.max(0, zones.indexOf(id))) % LINE_HEX.length];

// Turkish names exist only for the demo building's own parts; anything the user names keeps its name.
const DEMO = {
  PA: ["Booster Pump A", "Hidrofor Pompası A"], PB: ["Booster Pump B", "Hidrofor Pompası B"],
  Z1: ["Ground to L2 · clinic and emergency", "Zemin–K2 · klinik ve acil"],
  Z2: ["L3 to L6 · classrooms and labs", "K3–K6 · derslikler ve laboratuvarlar"],
  Z3: ["L7 to L10 · dormitory and offices", "K7–K10 · yurt ve ofisler"],
};
export function localName(id, name) {
  const d = DEMO[id];
  return getLang() === "tr" && d && name === d[0] ? d[1] : name;
}
export function label(id, cfg = topo.cfg) {
  const o = cfg && [...cfg.pumps, ...cfg.zones].find((x) => x.id === id);
  return localName(id, o ? o.name : id);
}

// Diagram geometry: pumps and zones spread evenly; the viewBox grows with the number of parts.
export const PX = 236, PW = 112, SX = 600;
export function layout(pumpIds, zoneIds) {
  const H = Math.max(380, 172 * pumpIds.length + 40, 122 * zoneIds.length + 20);
  const spread = (ids, top, bottom) => Object.fromEntries(ids.map((id, i) => [id, top + ((i + 0.5) * (H - top - bottom)) / ids.length]));
  return { H, mid: H / 2, PY: spread(pumpIds, 50, 60), ZY: spread(zoneIds, 20, 20) };
}
export function linePath(x0, y0, x1, y1) {
  // octilinear: horizontal, 45° diagonal, then vertical if the drop is longer than the room allows, horizontal
  const dy = y1 - y0, s = Math.sign(dy) || 1;
  const diag = Math.max(0, Math.min(Math.abs(dy), x1 - x0 - 60));
  const xd = Math.max(x0 + 20, x1 - diag - 40);
  return `M${x0},${y0} H${xd} L${xd + diag},${y0 + s * diag} V${y1} H${x1}`;
}
// Tank -> pump trunk: a short bus that drops vertically, so it never crosses pump labels.
export function busPath(x0, y0, xBus, x1, y1) {
  return `M${x0},${y0} H${xBus} V${y1} H${x1}`;
}
