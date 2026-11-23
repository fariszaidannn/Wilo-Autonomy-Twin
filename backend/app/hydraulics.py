"""Twin-lite hydraulics: ONE physics step used by the live twin, the scenario lab and the
balancer's safety check, so they cannot disagree (PRD section 9).

step() is pure: it never mutates the state it is given and returns a new one.
Limits: one friction coefficient per zone (no pipe network), linear vessel decay,
thresholds come from building.json. Replace with EPANET for network-level accuracy.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .config import Building

BAR_PER_M = 1 / 10.197
EPS = 1e-9


@dataclass(frozen=True)
class State:
    tank_m3: float
    buffers_m3: dict            # zone -> vessel volume
    fuel_h: float               # generator fuel left, hours of running
    below_min: dict = field(default_factory=dict)   # zone -> consecutive minutes out of service


@dataclass(frozen=True)
class Inputs:
    demand_m3h: dict            # zone -> wanted flow
    allocation: dict            # zone -> {pump: share}
    pump_failed: frozenset = frozenset()
    grid_on: bool = True
    generator_available: bool = False
    city_supply_on: bool = True
    isolated: frozenset = frozenset()   # zones an operator has valved off (scenario mitigation only)


def initial_state(b: Building, tank_m3: float | None = None) -> State:
    return State(tank_m3=b.tank["initial_volume_m3"] if tank_m3 is None else tank_m3,
                 buffers_m3={z.id: z.buffer_m3 for z in b.zones},
                 fuel_h=float(b.generator.get("fuel_hours", 0)),
                 below_min={z.id: 0.0 for z in b.zones})


def power_status(b: Building, state: State, inp: Inputs) -> tuple[dict, str]:
    if inp.grid_on:
        return {p.id: True for p in b.pumps}, "grid"
    if inp.generator_available and state.fuel_h > 0:
        cov = set(b.generator.get("covers_pumps", []))
        return {p.id: p.id in cov for p in b.pumps}, "generator"
    return {p.id: False for p in b.pumps}, "none"


def effective_allocation(b: Building, allocation: dict, healthy: dict) -> dict:
    """Auto-failover: a zone's share on a down pump moves to its other allowed, healthy pumps."""
    out = {}
    for z in b.zones:
        shares = {p: s for p, s in allocation.get(z.id, {}).items() if s > EPS}
        live = {p: s for p, s in shares.items() if healthy.get(p)}
        lost = sum(s for p, s in shares.items() if not healthy.get(p))
        if lost > EPS:
            backups = [p for p in z.allowed_pumps if healthy.get(p)]
            if backups:
                base = sum(live.get(p, 0.0) for p in backups)
                for p in backups:
                    w = live.get(p, 0.0) / base if base > EPS else 1 / len(backups)
                    live[p] = live.get(p, 0.0) + lost * w
        out[z.id] = live
    return out


def _curtail(flows: dict, zones_order: list[str], excess: float) -> float:
    """Reduce flows (zone -> m3/h) lowest priority first by `excess`. Returns what remains unremoved."""
    for zid in zones_order:
        if excess <= EPS:
            break
        cut = min(flows.get(zid, 0.0), excess)
        if cut > 0:
            flows[zid] -= cut
            excess -= cut
    return excess


def step(b: Building, state: State, inp: Inputs, dt_h: float) -> tuple[State, dict]:
    tank = b.tank
    zones = b.zones
    least_critical_first = [z.id for z in sorted(zones, key=lambda z: -z.priority)]

    powered, source = power_status(b, state, inp)
    suction = state.tank_m3 > tank["dead_volume_m3"] + EPS
    healthy = {p.id: powered[p.id] and p.id not in inp.pump_failed and suction for p in b.pumps}
    alloc = effective_allocation(b, inp.allocation, healthy)

    # Low-level priority reserve: below the configured fill, lower-priority zones are isolated
    # so the remaining water is kept for critical zones (a building setting, not a system choice).
    usable = max(0.0, state.tank_m3 - tank["dead_volume_m3"])
    usable_pct = 100 * usable / (tank["capacity_m3"] - tank["dead_volume_m3"])
    shed = {z.id: False for z in zones}
    for prio, pct in (tank.get("priority_reserve_pct") or {}).items():
        if usable_pct < float(pct):
            for z in zones:
                if z.priority >= int(prio):
                    shed[z.id] = True

    # Requested flow per pump and per (zone, pump).
    req = {p.id: {} for p in b.pumps}
    for z in zones:
        if shed[z.id] or z.id in inp.isolated:
            continue
        for pid, s in alloc[z.id].items():
            req[pid][z.id] = inp.demand_m3h.get(z.id, 0.0) * s
    requested = {pid: sum(v.values()) for pid, v in req.items()}

    # Pump max flow: curtail zones on that pump, lowest priority first.
    flows = {pid: dict(v) for pid, v in req.items()}
    for p in b.pumps:
        excess = requested[p.id] - p.max_flow_m3h
        if excess > EPS:
            _curtail(flows[p.id], least_critical_first, excess)

    # Tank cannot give more than it holds (plus what flows in during the step).
    q_in_max = tank["city_inflow_max_m3h"] if inp.city_supply_on else 0.0
    available = usable / dt_h + q_in_max
    total_out = sum(sum(v.values()) for v in flows.values())
    if total_out > available + EPS:
        excess = total_out - available
        for zid in least_critical_first:
            for pid in flows:
                if excess <= EPS:
                    break
                cut = min(flows[pid].get(zid, 0.0), excess)
                flows[pid][zid] = flows[pid].get(zid, 0.0) - cut
                excess -= cut

    pump_out = {}
    for p in b.pumps:
        q = sum(flows[p.id].values())
        h = p.head(q) if healthy[p.id] and q > EPS else (p.shutoff_head_m if healthy[p.id] else 0.0)
        pump_out[p.id] = {
            "powered": powered[p.id], "failed": p.id in inp.pump_failed, "healthy": healthy[p.id],
            "requested_m3h": requested[p.id], "flow_m3h": q,
            "load_pct": 100 * requested[p.id] / p.rated_flow_m3h,
            "head_m": h, "power_kw": (q / 3600) * 1000 * 9.81 * h / 1000 / p.efficiency if q > EPS else 0.0,
            "feeds": {zid: f for zid, f in flows[p.id].items() if f > EPS},
        }

    new_buffers, new_below = {}, {}
    zone_out = {}
    br_after = b.service["breach_after_min"]
    for z in zones:
        demand = inp.demand_m3h.get(z.id, 0.0)
        fed = {pid: flows[pid].get(z.id, 0.0) for pid in flows if flows[pid].get(z.id, 0.0) > EPS}
        q_pump = sum(fed.values())
        if q_pump > EPS:
            head = sum(pump_out[pid]["head_m"] * f for pid, f in fed.items()) / q_pump
            p_raw = (head - z.elevation_m - z.friction_k * q_pump ** 2) * BAR_PER_M
            p_pump = max(0.0, min(p_raw, z.setpoint_bar))
        else:
            p_pump = 0.0
        buf = state.buffers_m3.get(z.id, z.buffer_m3)
        deficit = max(0.0, demand - q_pump)
        if deficit <= 1e-6:
            served, pressure, buf_new = q_pump, p_pump, z.buffer_m3     # vessel recharges
        else:
            draw = min(deficit, buf / dt_h)
            buf_new = buf - draw * dt_h
            served = q_pump + draw
            fill = buf_new / z.buffer_m3 if z.buffer_m3 > 0 else 0.0
            p_vessel = z.setpoint_bar * fill if draw > EPS else 0.0   # decays linearly with fill
            pressure = max(p_pump, p_vessel)
        ratio = served / demand if demand > 0.05 else 1.0
        pump_ratio = q_pump / demand if demand > 0.05 else 1.0
        in_service = pressure >= z.min_pressure_bar - 1e-9 and ratio >= z.min_flow_ratio
        below = 0.0 if in_service else state.below_min.get(z.id, 0.0) + dt_h * 60
        new_buffers[z.id] = buf_new
        new_below[z.id] = below
        zone_out[z.id] = {
            "demand_m3h": demand, "served_m3h": served, "pump_served_m3h": q_pump,
            "unserved_m3h": max(0.0, demand - served), "pressure_bar": pressure,
            "pump_pressure_bar": p_pump, "pump_flow_ratio": pump_ratio, "flow_ratio": ratio,
            "in_service": in_service, "breached": below >= br_after - 1e-9,
            "fed_by": fed, "shed_low_tank": shed[z.id], "isolated": z.id in inp.isolated, "buffer_m3": buf_new,
        }

    q_out = sum(po["flow_m3h"] for po in pump_out.values())
    room = (tank["capacity_m3"] - state.tank_m3) / dt_h + q_out
    q_in = min(q_in_max, max(0.0, room))
    v_new = min(tank["capacity_m3"], max(0.0, state.tank_m3 + (q_in - q_out) * dt_h))
    fuel = state.fuel_h - dt_h if source == "generator" else state.fuel_h

    new_state = replace(state, tank_m3=v_new, buffers_m3=new_buffers, fuel_h=max(0.0, fuel), below_min=new_below)
    result = {
        "power_source": source,
        "tank": {"volume_m3": v_new, "pct": 100 * v_new / tank["capacity_m3"], "inflow_m3h": q_in,
                 "outflow_m3h": q_out, "usable_pct": usable_pct, "suction_ok": suction},
        "pumps": pump_out, "zones": zone_out, "allocation_effective": alloc,
        "unserved_m3h": sum(zo["unserved_m3h"] for zo in zone_out.values()),
    }
    return new_state, result
