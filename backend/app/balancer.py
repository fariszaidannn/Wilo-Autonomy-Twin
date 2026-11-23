"""AI load balancer, building level (PRD section 8).

plan() is a pure function: it reads a situation and returns a status and, when a safe
re-allocation exists, a recommendation. It never mutates state and never commands a pump.
Today the candidate generator is rule-based water-filling with priority ordering; the
verify / explain / approve steps do not depend on how a candidate was produced.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from . import hydraulics as hy
from .config import Building

STATUS_TEXT = {
    "balanced": "Balanced",
    "recommendation_ready": "Recommendation ready",
    "watch_peak_ahead": "Watch: peak ahead",
    "settling": "Settling",
    "cannot_balance": "Cannot balance",
    "crowded_no_safe_move": "Crowded, no safe move",
}


def pump_loads(b: Building, demand: dict, alloc: dict, healthy: dict) -> dict:
    flows = {p.id: 0.0 for p in b.pumps}
    for zid, shares in alloc.items():
        for pid, s in shares.items():
            flows[pid] += demand.get(zid, 0.0) * s
    return {pid: {"flow": f, "load_pct": 100 * f / b.pump_by_id[pid].rated_flow_m3h, "healthy": healthy[pid]}
            for pid, f in flows.items()}


def _zone_ok(b: Building, zres: dict, zid: str, margin: float) -> bool:
    z = b.zone_by_id[zid]
    return zres["pump_pressure_bar"] >= z.min_pressure_bar + margin - 1e-9 and \
        zres["pump_flow_ratio"] >= z.min_flow_ratio - 1e-9


def _dry_run(b, state, inp, alloc, demand):
    _, res = hy.step(b, state, replace(inp, allocation=alloc, demand_m3h=demand), 1 / 60)
    return res


def plan(b: Building, state: hy.State, inp: hy.Inputs, cv: dict, crowded: dict,
         basis: str = "current", basis_time: str | None = None, smoothed: dict | None = None) -> dict:
    """inp.demand_m3h is the basis demand (live now, or the forecast peak in predictive mode).
    crowded: pump -> bool (smoothed + hysteresis, or forecast). smoothed: pump -> load % shown in text."""
    cfg = b.balancer
    margin = cfg["pressure_margin_bar"]
    demand = inp.demand_m3h
    base_res = _dry_run(b, state, inp, inp.allocation, demand)
    healthy = {pid: p["healthy"] for pid, p in base_res["pumps"].items()}
    alloc0 = {z: dict(s) for z, s in base_res["allocation_effective"].items()}
    before = pump_loads(b, demand, alloc0, healthy)
    shown = smoothed or {pid: v["load_pct"] for pid, v in before.items()}
    names = {p.id: p.name for p in b.pumps}
    live = [pid for pid in healthy if healthy[pid]]
    crowded_live = [pid for pid in live if crowded.get(pid)]
    out = {"basis": basis, "basis_time": basis_time, "loads_before": before,
           "requires_operator_approval": True, "controls_real_pumps": False}

    if not crowded_live:
        return {**out, "status": "balanced", "headline": "Pumps are sharing the load; nothing to do."}

    if len(live) < 2:
        total = sum(demand.values())
        cap = sum(b.pump_by_id[p].max_flow_m3h for p in live)
        down = [names[p] for p in healthy if not healthy[p]]
        short = max(0.0, total - cap)
        why = f"Only {len(live)} healthy, powered pump ({', '.join(down)} unavailable)."
        if short > 0:
            why += f" Demand {total:.0f} m³/h exceeds capacity {cap:.0f} m³/h; lowest-priority zones will be curtailed."
        return {**out, "status": "cannot_balance", "headline": why, "shortfall_m3h": short,
                "reason": "This is a capacity problem, not a balancing problem."}

    loads = [before[p]["load_pct"] for p in live]
    if max(loads) - min(loads) < cfg["min_spread_pct"]:
        return {**out, "status": "crowded_no_safe_move",
                "headline": "All pumps are equally busy; there is no pump with room to take load.",
                "reason": "Capacity problem, not a balancing problem."}

    # "In service before" is judged on pump supply at base demand with the current allocation.
    in_service_before = {zid for zid, zr in base_res["zones"].items() if _zone_ok(b, zr, zid, 0.0)}
    high = {zid: d * (1 + cv.get(zid, 0.1)) for zid, d in demand.items()}

    def verify(alloc):
        res_b = _dry_run(b, state, inp, alloc, demand)
        res_h = _dry_run(b, state, inp, alloc, high)
        bad_b = [z for z in in_service_before if not _zone_ok(b, res_b["zones"][z], z, margin)]
        bad_h = [z for z in in_service_before if not _zone_ok(b, res_h["zones"][z], z, margin)]
        return res_b, res_h, bad_b, bad_h

    alloc = {z: dict(s) for z, s in alloc0.items()}
    moved = {}          # (zone, from, to) -> m3/h
    blocked = []
    for _ in range(12):
        cur = pump_loads(b, demand, alloc, healthy)
        donor = max(live, key=lambda p: cur[p]["load_pct"])
        if cur[donor]["load_pct"] <= cfg["target_max_pct"]:
            break
        receivers = sorted((p for p in live if p != donor and cur[p]["load_pct"] < cfg["receiver_cap_pct"]),
                           key=lambda p: cur[p]["load_pct"])
        progress = False
        for r in receivers:
            R_d = b.pump_by_id[donor].rated_flow_m3h
            R_r = b.pump_by_id[r].rated_flow_m3h
            cands = [b.zone_by_id[z] for z, s in alloc.items() if s.get(donor, 0) > 1e-9]
            cands = [z for z in cands if r in z.allowed_pumps]
            for z in sorted(cands, key=lambda z: -z.priority):      # least critical first
                d_z = demand.get(z.id, 0.0)
                if d_z <= 1e-6:
                    continue
                F_d, F_r = cur[donor]["flow"], cur[r]["flow"]
                already = sum(q for (zz, _, _), q in moved.items() if zz == z.id)
                q = min((F_d * R_r - F_r * R_d) / (R_d + R_r),            # equalise load %
                        cfg["receiver_cap_pct"] / 100 * R_r - F_r,        # receiver room
                        cfg["ramp_max_fraction"] * d_z - already,         # ramp limit
                        d_z * alloc[z.id].get(donor, 0.0))                # what the donor carries
                if q < 0.1:
                    continue
                trial = {k: dict(v) for k, v in alloc.items()}
                share = q / d_z
                trial[z.id][donor] = max(0.0, trial[z.id].get(donor, 0.0) - share)
                trial[z.id][r] = trial[z.id].get(r, 0.0) + share
                trial[z.id] = {p: s for p, s in trial[z.id].items() if s > 1e-9}
                _, _, bad_b, bad_h = verify(trial)
                if bad_b or bad_h:
                    blocked.append(f"Moving {q:.1f} m³/h of {z.id} to {names[r]} fails the pressure check "
                                   f"for {', '.join(sorted(set(bad_b + bad_h)))}.")
                    continue
                alloc = trial
                moved[(z.id, donor, r)] = moved.get((z.id, donor, r), 0.0) + q
                progress = True
                break
            if progress:
                break
        if not progress:
            break

    after = pump_loads(b, demand, alloc, healthy)
    max_before = max(before[p]["load_pct"] for p in live)
    max_after = max(after[p]["load_pct"] for p in live)
    improvement = max_before - max_after
    donor0 = max(live, key=lambda p: before[p]["load_pct"])
    receiver0 = min(live, key=lambda p: before[p]["load_pct"])
    trigger = (f"{names[donor0]} is {'forecast to be ' if basis == 'forecast' else ''}crowded "
               f"({shown[donor0]:.0f}% ≥ {cfg['crowded_pct']}%)"
               f"{' at ' + basis_time[11:16] if basis == 'forecast' and basis_time else ''} "
               f"while {names[receiver0]} has room ({shown[receiver0]:.0f}%).")

    if not moved or improvement < cfg["min_improvement_pct"]:
        reason = blocked[0] if blocked else "No zone fed by the crowded pump may use a pump with room."
        return {**out, "status": "crowded_no_safe_move", "headline": trigger,
                "reason": f"{reason} This is a capacity problem, not a balancing problem.", "blocked": blocked}

    res_b, res_h, bad_b, bad_h = verify(alloc)
    low = {zid: d * (1 - cv.get(zid, 0.1)) for zid, d in demand.items()}
    after_lo = pump_loads(b, low, alloc, healthy)
    after_hi = pump_loads(b, high, alloc, healthy)
    receivers_used = {r for (_, _, r) in moved}
    checks = [
        {"name": f"Receiver stays at or below {cfg['receiver_cap_pct']}%",
         "passed": all(after[r]["load_pct"] <= cfg["receiver_cap_pct"] + 1e-6 for r in receivers_used),
         "detail": ", ".join(f"{names[r]} {after[r]['load_pct']:.0f}%" for r in sorted(receivers_used))},
        {"name": "No pump above its max flow",
         "passed": all(after[p]["flow"] <= b.pump_by_id[p].max_flow_m3h + 1e-6 for p in live),
         "detail": ", ".join(f"{names[p]} {after[p]['flow']:.1f}/{b.pump_by_id[p].max_flow_m3h:.0f} m³/h" for p in live)},
        {"name": f"Every zone in service stays in service at base demand (+{margin} bar margin)",
         "passed": not bad_b,
         "detail": ", ".join(f"{z} {res_b['zones'][z]['pump_pressure_bar']:.2f} bar" for z in sorted(res_b["zones"]))},
        {"name": "Every zone in service stays in service at high demand (+cv)",
         "passed": not bad_h,
         "detail": ", ".join(f"{z} {res_h['zones'][z]['pump_pressure_bar']:.2f} bar" for z in sorted(res_h["zones"]))},
        {"name": f"Allowed crossover piping only, within the {cfg['ramp_max_fraction']:.0%} ramp limit",
         "passed": all(r in b.zone_by_id[z].allowed_pumps and q <= cfg["ramp_max_fraction"] * demand[z] + 1e-6
                       for (z, _, r), q in moved.items()),
         "detail": ", ".join(f"{z} via {b.zone_by_id[z].crossover_valve}" for (z, _, _) in moved)},
    ]
    min_margin = min(res_h["zones"][z]["pump_pressure_bar"] - b.zone_by_id[z].min_pressure_bar
                     for z in in_service_before) if in_service_before else 0.0
    max_cv = max(cv.values()) if cv else 0.1
    if not all(c["passed"] for c in checks) or max_cv > 0.2:
        confidence = "low"
    elif basis == "forecast" or min_margin < 0.3:
        confidence = "medium"
    else:
        confidence = "high"

    moves = []
    steps = []
    for (zid, frm, to), q in moved.items():
        z = b.zone_by_id[zid]
        stages = max(1, -(-int(round(100 * q / demand[zid])) // int(100 * cfg["ramp_stage_fraction"])))
        moves.append({"zone": zid, "zone_name": z.name, "priority": z.priority, "from": frm, "to": to,
                      "m3h": q, "fraction_of_zone": q / demand[zid],
                      "share_before": {p: alloc0[zid].get(p, 0.0) for p in z.allowed_pumps},
                      "share_after": {p: alloc[zid].get(p, 0.0) for p in z.allowed_pumps},
                      "crossover_valve": z.crossover_valve, "stages": stages})
    for i, m in enumerate(moves):
        z = b.zone_by_id[m["zone"]]
        steps += [
            f"Confirm {names[m['to']]} is healthy and powered.",
            f"Open crossover valve {m['crossover_valve']} so {names[m['to']]} can feed {m['zone']}.",
            f"Shift {m['m3h']:.1f} m³/h of {m['zone']} from {names[m['from']]} to {names[m['to']]} in "
            f"{m['stages']} stage(s) of at most {cfg['ramp_stage_fraction']:.0%} of the zone flow, "
            f"one every {cfg['ramp_stage_min']} minutes.",
            f"Watch {m['zone']} pressure stays at or above {z.min_pressure_bar + margin:.1f} bar.",
        ]
    steps.append(f"Roll back if any zone drops below its minimum pressure, or a pump goes above "
                 f"{cfg['crowded_pct']}% within {cfg['cooldown_min']} minutes.")
    steps = [f"{i + 1}. {s}" for i, s in enumerate(steps)]
    m0 = moves[0]
    change = "; ".join(f"Move {m['m3h']:.1f} m³/h (about {m['fraction_of_zone']:.0%} of {m['zone']}'s flow) "
                       f"from {names[m['from']]} to {names[m['to']]} via crossover valve {m['crossover_valve']}"
                       for m in moves)
    signature = json.dumps({"basis": basis, "moves": [(m["zone"], m["from"], m["to"]) for m in moves],
                            "alloc0": {z: {p: round(s, 3) for p, s in v.items()} for z, v in alloc0.items()}},
                           sort_keys=True)
    return {
        **out,
        "status": "watch_peak_ahead" if basis == "forecast" else "recommendation_ready",
        "headline": trigger,
        "trigger_rule": (f"Smoothed load ≥ {cfg['crowded_pct']}% on {names[donor0]} and a healthy pump under "
                         f"{cfg['receiver_cap_pct']}%" if basis == "current" else
                         f"Forecast load ≥ {cfg['crowded_pct']}% on {names[donor0]} within "
                         f"{cfg['forecast_horizon_min']} minutes"),
        "change_text": change + ".",
        "moves": moves,
        "loads_after": after,
        "loads_after_range": {p: {"lo": after_lo[p]["load_pct"], "hi": after_hi[p]["load_pct"]} for p in after},
        "improvement_pct": improvement,
        "spread_before": max_before - min(before[p]["load_pct"] for p in live),
        "spread_after": max_after - min(after[p]["load_pct"] for p in live),
        "zone_pressures_after": {z: {"base": res_b["zones"][z]["pump_pressure_bar"],
                                     "high": res_h["zones"][z]["pump_pressure_bar"],
                                     "min": b.zone_by_id[z].min_pressure_bar} for z in res_b["zones"]},
        "zone_pressures_before": {z: base_res["zones"][z]["pump_pressure_bar"] for z in base_res["zones"]},
        "unserved_before_m3h": sum(max(0.0, demand[z] - base_res["zones"][z]["pump_served_m3h"]) for z in demand),
        "unserved_after_m3h": sum(max(0.0, demand[z] - res_b["zones"][z]["pump_served_m3h"]) for z in demand),
        "safety_checks": checks,
        "blocked": blocked,
        "confidence": confidence,
        "operator_steps": steps,
        "rollback_condition": (f"Restore the previous allocation if any zone falls below its minimum pressure "
                               f"or {names[m0['from']]} / {names[m0['to']]} exceeds {cfg['crowded_pct']}% "
                               f"during the {cfg['cooldown_min']}-minute settling window."),
        "allocation_before": alloc0,
        "allocation_after": alloc,
        "signature": signature,
        "plan_hash": hashlib.sha1(signature.encode()).hexdigest()[:8],
    }
