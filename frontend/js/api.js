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

const LINE_HEX = ["#0072bc", "#c27c00", "#00968f", "#7b3f99", "#6e8b00", "#c2187b"];
// 12 tanks: six hues in fixed order, T07-T12 repeat them with a white centre stripe (composite encoding).
export function tankStyle(tankId) {
  const i = parseInt(tankId.slice(1), 10) - 1;
  return { color: LINE_HEX[i % 6], cased: i >= 6 };
}
