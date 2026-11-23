"""Scenario lab (PRD F3): time until each zone loses service, best / base / worst case,
with and without following the balancer's recommendations. Reuses hydraulics.step."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from . import balancer as bl
from . import hydraulics as hy
from .config import Building
from .usage import DemandProvider

SCENARIOS = {
    "normal": "Normal operation",
    "pump_failure": "Pump failure",
    "power_outage": "Power outage",
    "city_supply_cut": "City supply cut",
}
CASES = {"best": -1, "base": 0, "worst": 1}


def assumptions(b: Building, prov: DemandProvider) -> list[str]:
    t = b.tank
    res = ", ".join(f"priority {p} zones isolated below {v}% usable tank" for p, v in
                    sorted((t.get("priority_reserve_pct") or {}).items(), reverse=True))
    return [
        "SIMULATED estimate for operator review; not a guarantee and not a 'safe' rating.",
        f"Demand: {prov.label}; best/worst = demand × (1 ∓ cv), cv per zone "
        + ", ".join(f"{z} {v:.0%}" for z, v in prov.cv.items()) + ".",
        "Twin-lite hydraulics: one friction coefficient per zone, no pipe network; pump curve H = H0 − aQ².",
        "Zone pressure is capped at its set-point; pressure vessels bridge short gaps and decay linearly.",
        f"A zone is out of service when pressure < its minimum or served flow < its minimum ratio for "
        f"{b.service['breach_after_min']} consecutive minutes.",
        f"Tank {t['capacity_m3']} m³ (dead volume {t['dead_volume_m3']} m³), city inflow up to "
        f"{t['city_inflow_max_m3h']} m³/h. Low-level reserve (building setting): {res or 'none'}.",
        f"Generator covers {', '.join(b.generator.get('covers_pumps', [])) or 'no pumps'} with "
        f"{b.generator.get('fuel_hours', 0)} h of fuel.",
        "Thresholds and capacities must come from the building's engineers; the system never sets safety limits.",
        "'With balancer' assumes the operator approves each recommendation as soon as it appears.",
        "Preparation time runs from the first warning (the disturbance alarm, or in normal operation a zone "
        f"within {b.service['warning_margin_bar']} bar of its minimum or the tank below 25% usable) to the "
        "first zone losing service.",
    ]


def _run_case(b, prov, start, scenario, opts, case_sign, horizon_min, with_balancer):
    t0 = start["sim_time"]
    c = start["controls"]
    failed = set(c["pump_failed"])
    grid, gen, city = c["grid_on"], c["generator_available"], c["city_supply_on"]
    if scenario == "pump_failure":
        failed.add(opts.get("pump") or b.pumps[0].id)
    elif scenario == "power_outage":
        grid, gen = False, bool(opts.get("generator", False))
    elif scenario == "city_supply_cut":
        city = False
    alloc = {z: dict(s) for z, s in start["allocation"].items()}
    state = start["state"]
    state = replace(state, below_min={z.id: 0.0 for z in b.zones})
    first_breach = {}
    min_p = {z.id: 99.0 for z in b.zones}
    wanted = {z.id: 0.0 for z in b.zones}
    served = {z.id: 0.0 for z in b.zones}
    # A disturbance raises its own alarm when it happens; in normal operation the first warning
    # is a zone within the warning margin of its minimum pressure, or a low tank.
    warn_at = 0 if scenario != "normal" else None
    series = []
    moves_applied = 0
    cooldown = 0
    dt = 1 / 60
    for m in range(horizon_min):
        t = t0 + timedelta(minutes=m)
        demand = {z.id: prov.mean(z.id, t) * c["demand_mult"] * c["zone_mult"][z.id] *
                  (1 + case_sign * prov.cv[z.id]) for z in b.zones}
        inp = hy.Inputs(demand, alloc, frozenset(failed), grid, gen, city)
        if with_balancer and m % 15 == 0 and m >= cooldown:
            _, probe = hy.step(b, state, inp, dt)
            crowded = {p: r["healthy"] and r["load_pct"] >= b.balancer["crowded_pct"] for p, r in probe["pumps"].items()}
            rec = bl.plan(b, state, inp, prov.cv, crowded)
            if rec.get("moves"):
                alloc = rec["allocation_after"]
                inp = replace(inp, allocation=alloc)
                moves_applied += 1
                cooldown = m + b.balancer["cooldown_min"]
        state, res = hy.step(b, state, inp, dt)
        for z in b.zones:
            zr = res["zones"][z.id]
            min_p[z.id] = min(min_p[z.id], zr["pressure_bar"])
            wanted[z.id] += zr["demand_m3h"] * dt
            served[z.id] += zr["served_m3h"] * dt
            if zr["breached"] and z.id not in first_breach:
                first_breach[z.id] = m + 1 - b.service["breach_after_min"]   # breach began here
            if warn_at is None and (zr["pressure_bar"] < z.min_pressure_bar + b.service["warning_margin_bar"]
                                    or not zr["in_service"]):
                warn_at = m
        if warn_at is None and res["tank"]["usable_pct"] < 25:
            warn_at = m
        if m % 5 == 0:
            series.append({"min": m, "tank_pct": round(res["tank"]["pct"], 2),
                           "pressure": {z: round(r["pressure_bar"], 3) for z, r in res["zones"].items()},
                           "unserved_m3h": round(res["unserved_m3h"], 3)})
    prio = min(z.priority for z in b.zones)
    pz = [z.id for z in b.zones if z.priority == prio]
    return {
        "first_breach_min": first_breach, "min_pressure": min_p,
        "demand_met": {z: (served[z] / wanted[z] if wanted[z] > 0 else 1.0) for z in wanted},
        "priority_demand_met": sum(served[z] for z in pz) / max(1e-9, sum(wanted[z] for z in pz)),
        "total_demand_met": sum(served.values()) / max(1e-9, sum(wanted.values())),
        "warning_min": warn_at, "series": series, "balancer_moves": moves_applied,
    }


def fmt_min(m):
    if m is None:
        return "not within the horizon"
    h, mm = divmod(int(round(m)), 60)
    return f"{h} h {mm:02d} min" if h else f"{mm} min"


def run(b: Building, prov: DemandProvider, start: dict, scenario: str, horizon_h: float = 12,
        options: dict | None = None) -> dict:
    if scenario not in SCENARIOS:
        raise ValueError(f"scenario must be one of {list(SCENARIOS)}")
    horizon_min = int(max(1, min(48, horizon_h)) * 60)
    opts = options or {}
    runs = {(case, wb): _run_case(b, prov, start, scenario, opts, sign, horizon_min, wb)
            for case, sign in CASES.items() for wb in (False, True)}
    zones = []
    for z in b.zones:
        fb = {case: runs[(case, False)]["first_breach_min"].get(z.id) for case in CASES}
        zones.append({
            "zone": z.id, "name": z.name, "priority": z.priority,
            "time_to_threshold_min": fb,
            "time_to_threshold_with_balancer_min": runs[("base", True)]["first_breach_min"].get(z.id),
            "lowest_pressure_bar": runs[("base", False)]["min_pressure"][z.id],
            "min_pressure_bar": z.min_pressure_bar,
            "demand_met": runs[("base", False)]["demand_met"][z.id],
            "demand_met_with_balancer": runs[("base", True)]["demand_met"][z.id],
        })
    base = runs[("base", False)]
    hits = [(zz["time_to_threshold_min"]["base"], zz) for zz in zones if zz["time_to_threshold_min"]["base"] is not None]
    if hits:
        t, zz = min(hits, key=lambda x: (x[0], -x[1]["priority"]))
        early, late = zz["time_to_threshold_min"]["worst"], zz["time_to_threshold_min"]["best"]
        headline = (f"{zz['zone']} loses service first, about {fmt_min(t)} after the event "
                    f"(earliest {fmt_min(early)}, latest {fmt_min(late) if late is not None else 'beyond the horizon'}).")
        prep = None if base["warning_min"] is None else max(0, t - base["warning_min"])
    else:
        headline = f"Every zone stays in service for the {horizon_min // 60} h horizon in the base case."
        t, prep = None, None
    return {
        "scenario": scenario, "scenario_name": SCENARIOS[scenario], "options": opts,
        "start": start["sim_time"].isoformat(), "horizon_h": horizon_min / 60,
        "headline": headline, "first_breach_min": t, "preparation_min": prep,
        "first_warning_min": base["warning_min"],
        "priority_demand_met": {"without_balancer": base["priority_demand_met"],
                                "with_balancer": runs[("base", True)]["priority_demand_met"]},
        "total_demand_met": {"without_balancer": base["total_demand_met"],
                             "with_balancer": runs[("base", True)]["total_demand_met"]},
        "balancer_moves": runs[("base", True)]["balancer_moves"],
        "zones": zones,
        "series": {"base": base["series"], "best": runs[("best", False)]["series"],
                   "worst": runs[("worst", False)]["series"], "base_with_balancer": runs[("base", True)]["series"]},
        "assumptions": assumptions(b, prov), "cv": prov.cv,
        "data_label": "SIMULATED", "requires_operator_approval": True, "controls_real_pumps": False,
    }
