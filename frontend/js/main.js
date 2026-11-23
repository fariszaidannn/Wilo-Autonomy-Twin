import { applyStatic, setLang, getLang, t } from "./i18n.js";
import { get } from "./api.js";
import { initBuilding } from "./building.js";
import { initScenario } from "./scenario.js";
import { initUsage } from "./usage.js";
import { initPlanner } from "./planner.js";

const TABS = ["building", "scenario", "usage", "planner"];

function select(name, focus = false) {
  TABS.forEach((n) => {
    const tab = document.getElementById(`t-${n}`), panel = document.getElementById(`p-${n}`);
    const on = n === name;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    panel.hidden = !on;
    if (on && focus) tab.focus();
  });
  try { localStorage.setItem("wat.tab", name); } catch { /* ignore */ }
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  window.dispatchEvent(new CustomEvent("wat:tab", { detail: name }));
}

function initTabs() {
  const list = document.querySelector('[role="tablist"]');
  list.addEventListener("click", (e) => { const b = e.target.closest('[role="tab"]'); if (b) select(b.id.slice(2)); });
  list.addEventListener("keydown", (e) => {
    const cur = TABS.indexOf(document.activeElement.id?.slice(2));
    if (cur < 0) return;
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      select(TABS[(cur + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length], true);
    }
  });
}

async function dataPill() {
  try {
    const st = await get("/api/data/status");
    const pill = document.getElementById("data-pill");
    pill.dataset.i18n = st.label === "MEASURED" ? "measured" : st.label === "MIXED" ? "mixed" : "simulated";
    pill.textContent = t(pill.dataset.i18n);
  } catch { /* keep the default label */ }
}

document.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => setLang(b.dataset.lang)));
setLang(getLang());
applyStatic();
initTabs();
initBuilding();
initScenario();
initUsage();
initPlanner();
dataPill();
window.addEventListener("wat:data", dataPill);
let start = location.hash.slice(1);
if (!TABS.includes(start)) { try { start = localStorage.getItem("wat.tab") || "building"; } catch { start = "building"; } }
select(TABS.includes(start) ? start : "building");
