// Scenario lab: time to threshold per zone (best / base / worst), with and without the balancer.
import { get, post } from "./api.js";
import { topo, pumpHex, zoneHex, label } from "./topo.js";
import { t, getLang, onLang, num, pct, dur } from "./i18n.js";
import { lineChart } from "./charts.js";
import { esc, statusPill } from "./ui.js";

const $ = (id) => document.getElementById(id);
let type = "power_outage", horizon = 12, pump = null, gen = false;
let last = null, cfg = null;

function assumptionsTr(r) {
  const tk = cfg.tank, res = Object.entries(tk.priority_reserve_pct || {}).sort((a, b) => b[0] - a[0])
    .map(([p, v]) => `öncelik ${p} bölgeleri kullanılabilir depo %${v} altına düşünce ayrılır`).join(", ");
  return [
    "Operatör incelemesi için SİMÜLE tahmin; garanti veya “güvenli” derecelendirmesi değildir.",
    `Talep: son 8 haftanın haftanın-saati ortalaması; en iyi/en kötü = talep × (1 ∓ cv), bölge başına cv ${Object.entries(r.cv).map(([z, v]) => `${z} %${Math.round(v * 100)}`).join(", ")}.`,
    "İkiz-hafif hidrolik: bölge başına tek sürtünme katsayısı, boru ağı yok; pompa eğrisi H = H0 − aQ².",
    "Bölge basıncı ayar noktasıyla sınırlıdır; basınç tankları kısa boşlukları köprüler ve doğrusal olarak boşalır.",
    `Bir bölge, basınç asgarinin altında veya karşılanan debi asgari oranın altında ${cfg.service.breach_after_min} dakika üst üste kalırsa hizmet dışıdır.`,
    `Depo ${tk.capacity_m3} m³ (ölü hacim ${tk.dead_volume_m3} m³), şebeke girişi en fazla ${tk.city_inflow_max_m3h} m³/sa. Düşük seviye rezervi (bina ayarı): ${res || "yok"}.`,
    `Jeneratör ${(cfg.generator.covers_pumps || []).join(", ")} pompasını ${cfg.generator.fuel_hours} saat yakıtla besler.`,
    "Eşikler ve kapasiteler binanın mühendislerinden gelmelidir; sistem güvenlik sınırlarını asla kendisi belirlemez.",
    "“Dengeleyiciyle” sonucu, operatörün her öneriyi göründüğü anda onayladığını varsayar.",
    "Hazırlık süresi, ilk uyarıdan (arıza alarmı veya normal işletmede asgari basınca yaklaşma ya da düşük depo) ilk bölgenin hizmet dışı kalmasına kadardır.",
  ];
}

function renderForm() {
  const types = ["normal", "pump_failure", "power_outage", "city_supply_cut"];
  $("s-type").innerHTML = types.map((k) => `<button type="button" data-type="${k}" aria-pressed="${k === type}">${esc(t("s.types")[k])}</button>`).join("");
  $("s-horizon").innerHTML = [6, 12, 24].map((h) => `<button type="button" data-h="${h}" aria-pressed="${h === horizon}">${h} ${t("h")}</button>`).join("");
  let opts = "";
  if (!topo.pumps.includes(pump)) pump = topo.pumps[topo.pumps.length - 1];
  if (type === "pump_failure") opts = `<h3>${esc(t("s.pump"))}</h3><div class="seg" id="s-pump">${topo.pumps.map((p) => `<button type="button" data-p="${p}" aria-pressed="${p === pump}">${esc(label(p))}</button>`).join("")}</div>`;
  if (type === "power_outage") opts = `<h3>&nbsp;</h3><label class="switch"><span>${esc(t("s.gen"))}</span><input type="checkbox" id="s-gen" ${gen ? "checked" : ""}></label>`;
  $("s-opts").innerHTML = opts;
  $("s-type").onclick = (e) => { const b = e.target.closest("[data-type]"); if (b) { type = b.dataset.type; renderForm(); } };
  $("s-horizon").onclick = (e) => { const b = e.target.closest("[data-h]"); if (b) { horizon = +b.dataset.h; renderForm(); } };
  if ($("s-pump")) $("s-pump").onclick = (e) => { const b = e.target.closest("[data-p]"); if (b) { pump = b.dataset.p; renderForm(); } };
  if ($("s-gen")) $("s-gen").onchange = (e) => { gen = e.target.checked; };
}

function headline(r) {
  const hits = r.zones.filter((z) => z.time_to_threshold_min.base != null);
  if (!hits.length) return t("s.allok", { h: r.horizon_h });
  const z = hits.sort((a, b) => a.time_to_threshold_min.base - b.time_to_threshold_min.base || b.priority - a.priority)[0];
  const tt = z.time_to_threshold_min;
  return t("s.first", { z: z.zone, t: dur(tt.base), e: dur(tt.worst), l: tt.best == null ? t("s.beyond") : dur(tt.best) });
}

function render(r) {
  const tone = (v) => (v >= 0.999 ? "good" : v >= 0.9 ? "warn" : "crit");
  const rows = r.zones.map((z) => {
    const tt = z.time_to_threshold_min;
    const cell = (v) => (v == null ? `<span class="muted">${esc(t("s.never"))}</span>` : `<b>${esc(dur(v))}</b>`);
    return `<tr><td><b>${z.zone}</b>&thinsp;·&ensp;${esc(label(z.zone))}<br><span class="muted">${esc(t("priority", { n: z.priority }))}</span></td>
      <td class="r">${cell(tt.worst)}</td><td class="r">${cell(tt.base)}</td><td class="r">${cell(tt.best)}</td><td class="r">${cell(z.time_to_threshold_with_balancer_min)}</td>
      <td class="r">${num(z.lowest_pressure_bar)} bar<br><span class="muted">min ${num(z.min_pressure_bar)}</span></td>
      <td class="r">${statusPill(tone(z.demand_met), pct(z.demand_met, 0))}</td></tr>`;
  }).join("");
  const assumptions = getLang() === "tr" && cfg ? assumptionsTr(r) : r.assumptions;
  $("s-result").innerHTML = `
    <div class="stack">
      <h1 class="headline" style="max-width:40ch">${esc(headline(r))}</h1>
      <div class="facts">
        <div class="fact"><div class="v">${r.preparation_min == null ? "—" : esc(dur(r.preparation_min))}</div><div class="k">${esc(t("s.prep"))}</div></div>
        <div class="fact"><div class="v">${pct(r.priority_demand_met.without_balancer, 0)} → ${pct(r.priority_demand_met.with_balancer, 0)}</div><div class="k">${esc(t("s.prio"))} (${esc(t("s.without"))} → ${esc(t("s.with"))})</div></div>
        <div class="fact"><div class="v">${pct(r.total_demand_met.without_balancer, 0)} → ${pct(r.total_demand_met.with_balancer, 0)}</div><div class="k">${esc(t("s.total"))}</div></div>
        <div class="fact"><div class="v">${r.balancer_moves}</div><div class="k">${esc(t("s.moves"))}</div></div>
      </div>
      <div class="panel"><table><thead><tr><th>${esc(t("s.zone"))}</th><th class="r">${esc(t("s.earliest"))}</th><th class="r">${esc(t("s.base"))}</th><th class="r">${esc(t("s.latest"))}</th><th class="r">${esc(t("s.withlb"))}</th><th class="r">${esc(t("s.lowp"))}</th><th class="r">${esc(t("s.met"))}</th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="charts2">
        <div class="panel"><h3>${esc(t("s.tankchart"))}</h3><div class="chart" id="s-tank"></div></div>
        <div class="panel"><h3>${esc(t("s.presschart"))}</h3><div class="chart" id="s-press"></div></div>
      </div>
      <div class="panel"><h3>${esc(t("s.assume"))}</h3><ul class="limits" style="margin-top:8px">${assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></div>
    </div>`;
  const base = r.series.base, x = base.map((p) => p.min);
  const xf = (v) => `+${dur(v)}`;
  lineChart($("s-tank"), {
    x, xFmt: xf, yFmt: (v) => `${Math.round(v)}%`, yMin: 0, yMax: 100, height: 220,
    series: [
      { name: getLang() === "tr" ? "temel" : "base case", color: "#0e1a2b", values: base.map((p) => p.tank_pct) },
      { name: t("s.with"), color: pumpHex(topo.pumps[0]), dash: true, values: r.series.base_with_balancer.map((p) => p.tank_pct) },
    ],
    bands: [{ color: "#0e1a2b", lo: r.series.worst.map((p) => p.tank_pct), hi: r.series.best.map((p) => p.tank_pct) }],
    legendExtra: [{ name: t("s.band"), color: "#0e1a2b", cls: "band" }],
  });
  lineChart($("s-press"), {
    x, xFmt: xf, yFmt: (v) => (+v).toFixed(1), yMin: 0, height: 220,
    series: r.zones.map((z) => z.zone).map((z) => ({ name: z, color: zoneHex(z), values: base.map((p) => p.pressure[z]) })),
    hlines: Object.entries(r.zones.reduce((g, z) => { (g[z.min_pressure_bar] ||= []).push(z.zone); return g; }, {}))
      .map(([v, ids], k) => ({ y: +v, label: `min ${ids.join(" / ")}`, dash: true, start: k % 2 === 1, below: k > 0 })),
  });
}

export function initScenario() {
  renderForm();
  get("/api/config").then((c) => { cfg = c; }).catch(() => {});
  $("s-form").onsubmit = async (e) => {
    e.preventDefault();
    const btn = e.submitter || $("s-form").querySelector("[type=submit]");
    btn.disabled = true; btn.textContent = t("s.running");
    try {
      last = await post("/api/scenarios/run", { scenario: type, horizon_h: horizon, pump: type === "pump_failure" ? pump : null, generator: gen });
      render(last);
    } catch (err) {
      $("s-result").innerHTML = `<p class="toast err">${esc(err.message)}</p>`;
    } finally { btn.disabled = false; btn.textContent = t("s.run"); }
  };
  onLang(() => { renderForm(); if (last) render(last); });
  window.addEventListener("wat:topo", () => { last = null; renderForm(); $("s-result").innerHTML = `<p class="empty">${esc(t("s.empty"))}</p>`; });
}
