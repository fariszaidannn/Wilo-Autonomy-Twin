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
  timer: '<circle cx="12" cy="13.5" r="7.5" /><path d="M12 10v3.5l2.5 1.5M9.5 2.5h5M12 2.5V6" />',
  gauge: '<path d="M4.6 18a8.5 8.5 0 1 1 14.8 0" /><path d="M12 14l4-4.5" />',
  alarm: '<circle cx="12" cy="13" r="7.5" /><path d="M12 9.5V13l2.5 1.5M3.5 6L7 3M20.5 6L17 3" />',
  flask: '<path d="M9.5 3h5M10.5 3v6L5.2 18.6A1.6 1.6 0 0 0 6.6 21h10.8a1.6 1.6 0 0 0 1.4-2.4L13.5 9V3" />',
};

export function icon(name, color = "currentColor", size = 18) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="color:${color}">${PATHS[name] || ""}</svg>`;
}

const TONE_ICON = { good: "check", warn: "alert", crit: "alert", info: "clock", neutral: "dot" };
export function statusPill(tone, text) {
  return `<span class="status st-${tone}">${icon(TONE_ICON[tone] || "dot", "currentColor", 15)}${esc(text)}</span>`;
}
