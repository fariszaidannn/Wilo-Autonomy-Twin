// Usage & data tab: totals, hourly profile, 24 h forecast band, back-test, data source and CSV import.
import { get, post, postForm } from "./api.js";
import { zoneHex, label } from "./topo.js";
import { t, getLang, onLang, num, pct, hhmm, dateTime } from "./i18n.js";
import { lineChart } from "./charts.js";
import { esc, statusPill } from "./ui.js";

const $ = (id) => document.getElementById(id);
let data = null, loaded = false, msg = null;

async function load() {
  const [summary, forecast, accuracy, status] = await Promise.all([
    get("/api/usage/summary"), get("/api/usage/forecast?hours=24"), get("/api/usage/accuracy"), get("/api/data/status")]);
  data = { summary, forecast, accuracy, status };
  render();
}

function render() {
  if (!data) return;
  const { summary, forecast, accuracy, status } = data;
  const tot = summary.total;
  const zones = Object.keys(summary.zones);
  const lpd = zones.map((z) => `${z} ${num(summary.zones[z].l_per_person_day, 0)}`).join(" · ");
  const label = { SIMULATED: "warn", MIXED: "info", MEASURED: "good" }[status.label] || "neutral";
  const flags = Object.entries(status.flags).filter(([, f]) => f.length);
  $("usage").innerHTML = (`
    <div class="panel"><header><h2>${esc(t("tab.usage"))}</h2>${statusPill(label, t(status.label.toLowerCase()) )}</header>
      <div class="facts">
        <div class="fact"><div class="v">${num(tot.m3_per_day, 0)} m³</div><div class="k">${esc(t("u.tiles.day"))}</div></div>
        <div class="fact"><div class="v">${num(tot.peak_m3h)} m³/h</div><div class="k">${esc(t("u.tiles.peak"))} · ${esc(dateTime(tot.peak_local))}</div></div>
        <div class="fact"><div class="v">${num(tot.avg_m3h)} m³/h</div><div class="k">${esc(t("u.tiles.avg"))}</div></div>
        <div class="fact"><div class="v" style="font-size:22px">${esc(lpd)}</div><div class="k">${esc(t("u.tiles.lpd"))}</div></div>
      </div></div>
    <div class="charts2">
      <div class="panel"><h3>${esc(t("u.profile"))}</h3><div class="chart" id="u-profile"></div></div>
      <div class="panel"><h3>${esc(t("u.forecast"))}</h3><p class="panel-note">${esc(t("u.baseline"))}</p><div class="chart" id="u-forecast"></div></div>
    </div>
    <div class="charts2">
      <div class="panel"><h3>${esc(t("u.accuracy"))}</h3>
        <table style="margin-top:8px"><thead><tr><th>${esc(t("u.acc.zone"))}</th><th class="r">${esc(t("u.acc.mape"))}</th><th class="r">${esc(t("u.acc.y"))}</th><th class="r">${esc(t("u.acc.sigma"))}</th></tr></thead>
        <tbody>${Object.entries(accuracy.zones).map(([z, a]) => `<tr><td><b>${z}</b>&thinsp;·&ensp;${esc(label(z))}</td><td class="r"><b>${pct(a.mape, 1)}</b></td><td class="r">${pct(a.mape_yesterday, 1)}</td><td class="r">${pct(a.within_1sigma, 0)}</td></tr>`).join("")}</tbody></table>
        <p class="panel-note" style="margin-top:8px">${esc(getLang() === "tr" ? "Simüle veride yöntemi gösterir; gerçek bir bina hakkında bir şey söylemez." : "Demonstrates the method on simulated data; says nothing about a real building.")}</p>
      </div>
      <div class="panel"><h3>${esc(t("u.source"))}</h3>
        <table style="margin-top:8px"><tbody>
          <tr><td>${esc(t("u.repo"))}</td><td>${esc(status.repository)}</td></tr>
          <tr><td>${esc(t("u.rows", { n: status.rows.toLocaleString() }))}</td><td>${esc(Object.entries(status.per_zone).map(([z, v]) => `${z}: ${v.rows} (${v.sources.map((x) => t(x.toLowerCase())).join(" + ")})`).join(" · "))}</td></tr>
          <tr><td>${esc(t("u.range"))}</td><td>${esc(status.first_utc?.slice(0, 16) || "—")} → ${esc(status.last_utc?.slice(0, 16) || "—")} UTC</td></tr>
          ${flags.length ? `<tr><td>${esc(t("u.flags"))}</td><td>${esc(flags.map(([z, f]) => `${z}: ${f.join("; ")}`).join(" · "))}</td></tr>` : ""}
        </tbody></table>
        <form id="u-import" style="margin-top:16px;display:grid;gap:10px">
          <h3>${esc(t("u.import"))}</h3><p class="panel-note">${esc(t("u.import.help"))}</p>
          <div class="file-pick"><input type="file" id="u-file" accept=".csv,text/csv" required class="sr-only" ${status.mapping_enabled ? "disabled" : ""}>
            <label for="u-file" class="btn">${esc(t("u.choose"))}</label><span id="u-fname" class="muted">${esc(t("u.nofile"))}</span></div>
          <label class="switch" style="justify-content:flex-start;gap:12px"><input type="checkbox" id="u-replace"><span>${esc(t("u.replace"))}</span></label>
          <div class="btn-row"><button class="btn primary" type="submit" ${status.mapping_enabled ? "disabled" : ""}>${esc(t("u.upload"))}</button>
            <a class="btn" href="/api/data/template.csv" download>${esc(t("u.template"))}</a>
            <button class="btn" type="button" id="u-reseed">${esc(t("u.reseed"))}</button></div>
          <div aria-live="polite">${msg ? `<p class="toast ${msg.err ? "err" : "ok"}">${esc(msg.text)}</p>` : ""}</div>
        </form>
      </div>
    </div>`).replace(/ · /g, "\u2009\u00b7\u2002");
  lineChart($("u-profile"), {
    x: [...Array(24).keys()], xFmt: (h) => `${String(h).padStart(2, "0")}:00`, yFmt: (v) => num(v, v < 10 ? 1 : 0), yMin: 0, height: 220,
    series: zones.map((z) => ({ name: z, color: zoneHex(z), values: summary.zones[z].hourly_profile })),
  });
  const pts = forecast.points;
  lineChart($("u-forecast"), {
    x: pts.map((p) => p.local), xFmt: (v) => hhmm(v), yFmt: (v) => num(v, 0), yMin: 0, height: 220,
    series: [{ name: t("u.mean"), color: "#0e1a2b", values: pts.map((p) => p.total.mean) }],
    bands: [{ color: "#0072bc", lo: pts.map((p) => p.total.lo), hi: pts.map((p) => p.total.hi) }],
    legendExtra: [{ name: "±1σ", color: "#0072bc", cls: "band" }],
  });
  $("u-import").onsubmit = async (e) => {
    e.preventDefault();
    const f = $("u-file").files[0];
    if (!f) return;
    const fd = new FormData(); fd.append("file", f); fd.append("replace_simulated", $("u-replace").checked ? "true" : "false");
    try {
      const r = await postForm("/api/data/import-csv", fd);
      msg = { text: `${t("u.imported", { n: r.imported_rows })} ${r.warnings.join(" ")}` };
    } catch (err) { msg = { err: true, text: err.message }; }
    await load(); window.dispatchEvent(new Event("wat:data"));
  };
  $("u-file").onchange = (e) => { $("u-fname").textContent = e.target.files[0]?.name || t("u.nofile"); };
  $("u-reseed").onclick = async () => { await post("/api/data/reseed-demo"); msg = null; await load(); window.dispatchEvent(new Event("wat:data")); };
}

export function initUsage() {
  const open = () => { if (!loaded) { loaded = true; load().catch((e) => { $("usage").innerHTML = `<p class="toast err">${esc(e.message)}</p>`; }); } };
  window.addEventListener("wat:tab", (e) => { if (e.detail === "usage") open(); });
  onLang(render);
  window.addEventListener("wat:topo", () => { if (loaded) load().catch(() => {}); });
}
