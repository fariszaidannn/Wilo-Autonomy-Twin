import { applyStatic, setLang, getLang, t } from "./i18n.js";
import { get } from "./api.js";
import { loadTopo } from "./topo.js";
import { initBuilding } from "./building.js";
import { initScenario } from "./scenario.js";
import { initUsage } from "./usage.js";
import { initPlanner } from "./planner.js";
import { initEditor, openEditor } from "./editor.js";

const TABS = ["building", "scenario", "usage", "planner"];

// "edit" is a page of its own (the water-system editor), reached from the Building tab.
function select(name, focus = false) {
  const editing = name === "edit";
  TABS.forEach((n) => {
    const tab = document.getElementById(`t-${n}`), panel = document.getElementById(`p-${n}`);
    const on = n === name;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on || (editing && n === "building") ? 0 : -1;
    panel.hidden = !on;
    if (on && focus) tab.focus();
  });
  document.getElementById("p-editor").hidden = !editing;
  if (!editing) { try { localStorage.setItem("wat.tab", name); } catch { /* ignore */ } }
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  if (editing) { window.scrollTo(0, 0); openEditor(); }
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
  window.addEventListener("hashchange", () => {
    const h = location.hash.slice(1);
    if (h === "edit" || TABS.includes(h)) select(h);
  });
  document.getElementById("b-edit").addEventListener("click", () => select("edit"));
}

async function dataPill() {
  try {
    const st = await get("/api/data/status");
    const pill = document.getElementById("data-pill");
    pill.dataset.i18n = st.label === "MEASURED" ? "measured" : st.label === "MIXED" ? "mixed" : "simulated";
    pill.textContent = t(pill.dataset.i18n);
  } catch { /* keep the default label */ }
}

function flash() {
  let timer = null;
  window.addEventListener("wat:flash", (e) => {
    const el = document.getElementById("b-flash");
    el.textContent = e.detail; el.hidden = false;
    clearTimeout(timer); timer = setTimeout(() => { el.hidden = true; }, 7000);
  });
}

async function boot() {
  document.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => setLang(b.dataset.lang)));
  setLang(getLang());
  applyStatic();
  for (let i = 0; ; i++) {   // every screen draws from the topology, so load it first
    try { await loadTopo(); break; } catch { await new Promise((r) => setTimeout(r, Math.min(3000, 500 * (i + 1)))); }
  }
  initTabs();
  flash();
  initBuilding();
  initScenario();
  initUsage();
  initPlanner();
  initEditor();
  dataPill();
  window.addEventListener("wat:data", dataPill);
  window.addEventListener("wat:topo", dataPill);
  let start = location.hash.slice(1);
  if (start !== "edit" && !TABS.includes(start)) { try { start = localStorage.getItem("wat.tab") || "building"; } catch { start = "building"; } }
  select(start === "edit" || TABS.includes(start) ? start : "building");
}

boot();
