export class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; }
}

async function call(method, path, body, isForm = false) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  }
  const r = await fetch(path, opts);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) {
    const d = data && data.detail;
    throw new ApiError(r.status, typeof d === "string" ? d : JSON.stringify(d ?? data));
  }
  return data;
}

export const get = (p) => call("GET", p);
export const post = (p, b = {}) => call("POST", p, b);
export const postForm = (p, f) => call("POST", p, f, true);

// Building names and labels in Turkish; ids stay the same.
export const NAMES = {
  tr: {
    PA: "Hidrofor Pompası A", PB: "Hidrofor Pompası B",
    Z1: "Zemin–K2 · klinik ve acil", Z2: "K3–K6 · derslikler ve laboratuvarlar", Z3: "K7–K10 · yurt ve ofisler",
  },
};
export function nameOf(lang, id, fallback) { return (NAMES[lang] && NAMES[lang][id]) || fallback; }

export const PUMP_COLOR = { PA: "var(--l1)", PB: "var(--l2)" };
export const PUMP_HEX = { PA: "#0072bc", PB: "#c27c00" };
// Zones take the next slots of the same fixed order (pumps own slots 1-2).
export const ZONE_HEX = { Z1: "#00968f", Z2: "#7b3f99", Z3: "#6e8b00" };
const LINE_HEX = ["#0072bc", "#c27c00", "#00968f", "#7b3f99", "#6e8b00", "#c2187b"];
// 12 tanks: six hues in fixed order, T07-T12 repeat them with a white centre stripe (composite encoding).
export function tankStyle(tankId) {
  const i = parseInt(tankId.slice(1), 10) - 1;
  return { color: LINE_HEX[i % 6], cased: i >= 6 };
}
