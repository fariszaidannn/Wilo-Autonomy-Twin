// Shared bits: escaping, authored icons (one stroke weight), status pills (colour + icon + word).
export const esc = (s) => String(s ?? "")
  .replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]))
  .replace(/ · /g, "\u2009\u00b7\u2002");   // Overpass sets the middle dot tight on its right

const PATHS = {
  check: '<path d="M5 12.5l4.5 4.5L19 7.5" />',
  x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />',
  alert: '<path d="M12 3.5L22 20.5H2z" /><path d="M12 10v4.5M12 17.5v.3" />',
  info: '<circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7.5v.3" />',
  dot: '<circle cx="12" cy="12" r="5" fill="currentColor" />',
  clock: '<circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" />',
};

export function icon(name, color = "currentColor", size = 18) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="color:${color}">${PATHS[name] || ""}</svg>`;
}

const TONE_ICON = { good: "check", warn: "alert", crit: "alert", info: "clock", neutral: "dot" };
export function statusPill(tone, text) {
  return `<span class="status st-${tone}">${icon(TONE_ICON[tone] || "dot", "currentColor", 15)}${esc(text)}</span>`;
}
