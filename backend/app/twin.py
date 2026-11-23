"""Live twin: simulated clock, pump-load monitor, balancer status and operator decisions.

"Apply" changes only the SIMULATED allocation inside the twin. No code path sends a
command to a pump or valve.
"""
from __future__ import annotations

import copy
import threading
import time
from collections import deque
from datetime import datetime, timedelta

import numpy as np

from . import balancer as bl
from . import db
from . import hydraulics as hy
from .config import Building
from .synthetic import SIM_START
from .usage import DemandProvider


class StalePlan(Exception):
    """The plan id no longer matches the current recommendation (HTTP 409)."""


class Twin:
    def __init__(self, b: Building, con, provider: DemandProvider, seed: int = 7):
        self.b = b
        self.con = con
        self.provider = provider
        self.lock = threading.RLock()
        self.seed = seed
        self._thread = None
        self._stop = threading.Event()
        self.reset()

    # ---------- lifecycle ----------
    def reset(self):
        with self.lock:
            b = self.b
            self.sim_time = SIM_START
            self.state = hy.initial_state(b)
            self.allocation = {z: dict(s) for z, s in b.default_allocation.items()}
            self.prev_allocation = None
            self.controls = {"pump_failed": [], "grid_on": True, "generator_available": True,
                             "city_supply_on": True, "demand_mult": 1.0,
                             "zone_mult": {z.id: 1.0 for z in b.zones}, "speed": 2.0, "running": True}
            self.rng = np.random.default_rng(self.seed)
            self.noise = {z.id: 0.0 for z in b.zones}
            self.smoothed = None
            self.crowded = {p.id: False for p in b.pumps}
            self.history = deque(maxlen=1440)
            self.rec = None            # current recommendation (stable while the operator reads it)
            self.lb = {"status": "balanced", "headline": "Starting up."}
            self.cooldown_until = None
            self.pause_until = None
            self.settle = None         # watch window after an approved change
            self.last_forecast_check = None
            self.forecast_peak = None
            self.last = None
            self.timeline = {}         # sim minute -> recorded state, so the time bar can seek like a video
            self._tick(advance=False)
            self._record()

    # ---------- recorded timeline (seek back, replay forward) ----------
    KEEP_DAYS = 3
    MAX_FAST_FORWARD = timedelta(days=2)

    def _record(self):
        """Store the state at the current minute (the start of that minute)."""
        self.timeline[self.sim_time] = {
            "state": self.state,
            "allocation": copy.deepcopy(self.allocation),
            "prev_allocation": copy.deepcopy(self.prev_allocation),
            "controls": copy.deepcopy({k: v for k, v in self.controls.items() if k not in ("speed", "running")}),
            "noise": dict(self.noise),
            "cooldown_until": self.cooldown_until, "pause_until": self.pause_until,
            "settle": copy.deepcopy(self.settle),
        }
        if self.sim_time.minute == 0:   # prune once per simulated hour
            cutoff = self.sim_time - timedelta(days=self.KEEP_DAYS)
            for k in [k for k in self.timeline if k < cutoff]:
                del self.timeline[k]

    def _restore(self, snap: dict, seek: bool):
        changed = {k: v for k, v in snap["controls"].items() if self.controls.get(k) != v}
        self.state = snap["state"]
        self.allocation = copy.deepcopy(snap["allocation"])
        self.prev_allocation = copy.deepcopy(snap["prev_allocation"])
        self.controls.update(copy.deepcopy(snap["controls"]))
        self.noise = dict(snap["noise"])
        self.cooldown_until, self.pause_until = snap["cooldown_until"], snap["pause_until"]
        self.settle = copy.deepcopy(snap["settle"])
        if seek or changed:
            self.rec = None
        if seek:
            self.smoothed = None
            self.crowded = {p.id: False for p in self.b.pumps}
            self.last_forecast_check = None

    def _branch(self):
        """A change made now invalidates the recorded future: drop it and continue from here."""
        for k in [k for k in self.timeline if k > self.sim_time]:
            del self.timeline[k]

    def _seek(self, target: datetime):
        keys = [k for k in self.timeline if k <= target]
        if target in self.timeline:
            self.sim_time = target
            self._restore(self.timeline[target], seek=True)
        elif keys and target - max(keys) <= self.MAX_FAST_FORWARD:
            start = max(keys)
            self.sim_time = start
            self._restore(self.timeline[start], seek=True)
            self._trim_history()
            while self.sim_time < target:      # simulate forward to the requested time
                self._tick(fast=True)
        else:
            # Nothing recorded near this time: start a fresh run here from the starting tank level.
            self.sim_time = target
            self.state = hy.initial_state(self.b)
            self.timeline = {}
            self.history.clear()
            self.smoothed = None
            self.crowded = {p.id: False for p in self.b.pumps}
            self.last_forecast_check = None
            self.rec = None
            self._record()
        self._trim_history()

    def _trim_history(self):
        cut = self.sim_time.isoformat()
        self.history = deque((h for h in self.history if h["t"] < cut), maxlen=1440)

    def recorded_range(self) -> tuple[datetime, datetime] | None:
        return (min(self.timeline), max(self.timeline)) if self.timeline else None

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="twin-clock")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        carry = 0.0
        last = time.monotonic()
        while not self._stop.is_set():
            time.sleep(0.1)
            now = time.monotonic()
            dt, last = now - last, now
            with self.lock:
                if not self.controls["running"]:
                    carry = 0.0
                    continue
                carry += dt * self.controls["speed"]
                n = min(int(carry), 180)
                carry -= n
                for _ in range(n):
                    try:
                        self._tick()
                    except Exception:   # keep the twin running if a single tick fails
                        pass

    # ---------- simulation ----------
    def inputs(self, demand: dict) -> hy.Inputs:
        c = self.controls
        return hy.Inputs(demand_m3h=demand, allocation=self.allocation, pump_failed=frozenset(c["pump_failed"]),
                         grid_on=c["grid_on"], generator_available=c["generator_available"],
                         city_supply_on=c["city_supply_on"])

    def expected_demand(self, t: datetime) -> dict:
        c = self.controls
        return {z.id: self.provider.mean(z.id, t) * c["demand_mult"] * c["zone_mult"][z.id] for z in self.b.zones}

    def live_demand(self) -> dict:
        exp = self.expected_demand(self.sim_time)
        return {z: v * (1 + self.noise[z]) for z, v in exp.items()}

    def _tick(self, advance: bool = True, fast: bool = False):
        if advance:
            for z in self.noise:
                self.noise[z] = 0.9 * self.noise[z] + self.rng.normal(0, 0.01)
        demand = self.live_demand()
        inp = self.inputs(demand)
        new_state, res = hy.step(self.b, self.state, inp, 1 / 60)
        loads = {pid: p["load_pct"] if p["healthy"] else 0.0 for pid, p in res["pumps"].items()}
        a = self.b.balancer["ema_alpha"]
        if self.smoothed is None:
            self.smoothed = dict(loads)
        elif advance:   # a refresh after a control change must not count as a minute of smoothing
            self.smoothed = {p: a * loads[p] + (1 - a) * self.smoothed[p] for p in loads}
        else:
            self.smoothed = {p: (v if res["pumps"][p]["healthy"] else 0.0) for p, v in self.smoothed.items()}
        cfg = self.b.balancer
        for p, v in self.smoothed.items():
            if not res["pumps"][p]["healthy"]:
                self.crowded[p] = False
            elif self.crowded[p]:
                self.crowded[p] = v >= cfg["release_pct"]       # hysteresis
            else:
                self.crowded[p] = v >= cfg["crowded_pct"]
        if advance:
            self.state = new_state
        self.last = {"demand": demand, "result": res}
        if not fast:   # fast-forwarding to a sought time skips advice until it arrives
            self._update_balancer(demand)
        if not advance:
            return
        self.history.append({
            "t": self.sim_time.isoformat(),
            "pump_load": {p: round(v, 1) for p, v in loads.items()},
            "pump_smoothed": {p: round(v, 1) for p, v in self.smoothed.items()},
            "zone_pressure": {z: round(r["pressure_bar"], 3) for z, r in res["zones"].items()},
            "tank_pct": round(res["tank"]["pct"], 2),
            "unserved_m3h": round(res["unserved_m3h"], 3),
        })
        self.sim_time += timedelta(minutes=1)
        recorded = self.timeline.get(self.sim_time)
        if recorded is not None:
            self._restore(recorded, seek=False)   # replay the recorded future exactly
        else:
            self._record()

    # ---------- balancer monitor ----------
    def _forecast_peak(self):
        """Forecast loads over the horizon with the current allocation (predictive mode)."""
        cfg = self.b.balancer
        best = None
        healthy = {p: r["healthy"] for p, r in self.last["result"]["pumps"].items()}
        for m in range(10, cfg["forecast_horizon_min"] + 1, 10):
            t = self.sim_time + timedelta(minutes=m)
            d = self.expected_demand(t)
            eff = hy.effective_allocation(self.b, self.allocation, healthy)
            loads = bl.pump_loads(self.b, d, eff, healthy)
            top = max(v["load_pct"] for v in loads.values() if v["healthy"]) if any(healthy.values()) else 0
            if best is None or top > best[0]:
                best = (top, t, d, loads)
        return best

    def _update_balancer(self, demand):
        now = self.sim_time
        cfg = self.b.balancer
        if self.cooldown_until and now < self.cooldown_until:
            res = self.last["result"]
            if self.settle:
                lost = [z for z in self.settle["in_service"] if res["zones"][z]["breached"]]
                hot = [p for p in self.smoothed if self.smoothed[p] > cfg["crowded_pct"] and res["pumps"][p]["healthy"]]
                if lost or hot:
                    self.settle["rollback_advised"] = (
                        f"Problem while settling: {', '.join(lost)} out of service" if lost else
                        f"Problem while settling: {', '.join(hot)} above {cfg['crowded_pct']}%")
            left = (self.cooldown_until - now).total_seconds() / 60
            self.lb = {"status": "settling", "minutes_left": round(left),
                       "headline": f"An approved change was applied; watching for {left:.0f} more minute(s) "
                                   "before suggesting anything else.",
                       "rollback_advised": (self.settle or {}).get("rollback_advised")}
            self.rec = None
            return
        if self.cooldown_until and now >= self.cooldown_until:
            self.cooldown_until, self.settle = None, None
        if self.pause_until and now < self.pause_until:
            self.rec = None
            self.lb = {"status": "balanced", "paused": True,
                       "minutes_left": round((self.pause_until - now).total_seconds() / 60),
                       "headline": "Recommendation dismissed; pausing advice for "
                                   f"{(self.pause_until - now).total_seconds() / 60:.0f} minute(s)."}
            return
        self.pause_until = None
        self._evaluate(demand)

    def _evaluate(self, demand, force: bool = False):
        cv = self.provider.cv
        result = None
        if any(self.crowded.values()):
            result = bl.plan(self.b, self.state, self.inputs(demand), cv, self.crowded,
                             basis="current", basis_time=self.sim_time.isoformat(), smoothed=self.smoothed)
        else:
            if force or self.last_forecast_check is None or \
                    (self.sim_time - self.last_forecast_check) >= timedelta(minutes=5) or self.sim_time < self.last_forecast_check:
                self.forecast_peak = self._forecast_peak()
                self.last_forecast_check = self.sim_time
            peak = self.forecast_peak
            cfg = self.b.balancer
            if peak and peak[0] >= cfg["crowded_pct"] and peak[1] > self.sim_time:
                top, t, d, loads = peak
                fc_crowded = {p: v["healthy"] and v["load_pct"] >= cfg["crowded_pct"] for p, v in loads.items()}
                result = bl.plan(self.b, self.state, self.inputs(d), cv, fc_crowded, basis="forecast",
                                 basis_time=t.isoformat(), smoothed={p: v["load_pct"] for p, v in loads.items()})
                if result["status"] == "balanced":
                    result = None
            if result is None:
                result = bl.plan(self.b, self.state, self.inputs(demand), cv, self.crowded,
                                 smoothed=self.smoothed)
        if "moves" in result:
            keep = (not force and self.rec is not None and self.rec["signature"] == result["signature"]
                    and all(abs(a["m3h"] - b_["m3h"]) <= max(1.0, 0.1 * b_["m3h"])
                            for a, b_ in zip(result["moves"], self.rec["moves"])))
            if not keep:
                result["plan_id"] = f"LB-{result['plan_hash']}-{self.sim_time:%H%M}"
                result["created_sim_time"] = self.sim_time.isoformat()
                self.rec = result
        else:
            self.rec = None
        self.lb = {k: v for k, v in result.items() if k not in ("signature",)} if self.rec is None else \
            {"status": self.rec["status"], "headline": self.rec["headline"]}

    # ---------- operator actions ----------
    def control(self, **kw):
        with self.lock:
            c = self.controls
            before = copy.deepcopy({k: v for k, v in c.items() if k not in ("speed", "running")})
            topology_changed = False
            for k in ("grid_on", "generator_available", "city_supply_on", "running"):
                if kw.get(k) is not None:
                    topology_changed |= k != "running" and c[k] != bool(kw[k])
                    c[k] = bool(kw[k])
            if kw.get("pump_failed") is not None:
                new = sorted(p for p in kw["pump_failed"] if p in self.b.pump_by_id)
                topology_changed |= new != c["pump_failed"]
                c["pump_failed"] = new
            if kw.get("demand_mult") is not None:
                c["demand_mult"] = float(min(2.5, max(0.0, kw["demand_mult"])))
            if kw.get("zone_mult"):
                for z, v in kw["zone_mult"].items():
                    if z in c["zone_mult"]:
                        c["zone_mult"][z] = float(min(2.5, max(0.0, v)))
            if kw.get("speed") is not None:
                c["speed"] = float(min(120, max(0.25, kw["speed"])))
            if kw.get("jump_to"):
                hh, mm = (int(x) for x in kw["jump_to"].split(":"))
                day = self.sim_time.date()
                if kw.get("jump_day") == "weekend":
                    day = day + timedelta(days=(5 - day.weekday()) % 7)
                elif kw.get("jump_day") == "weekday" and day.weekday() >= 5:
                    day = day + timedelta(days=7 - day.weekday())
                # Seek: the plant goes back (or forward) to how it was at that minute.
                self._seek(datetime(day.year, day.month, day.day, hh, mm))
            if topology_changed:
                self.rec = None   # a plan made for the old plant is stale
            changed = before != {k: v for k, v in c.items() if k not in ("speed", "running")}
            if changed and not kw.get("jump_to"):
                self._branch()    # a disturbance changed now rewrites the future from here
            self._tick(advance=False)
            if changed and not kw.get("jump_to"):
                self._record()
            return self.snapshot()

    def step(self, minutes: int):
        with self.lock:
            for _ in range(max(0, min(int(minutes), 24 * 60))):
                self._tick()
            return self.snapshot()

    def evaluate_now(self):
        with self.lock:
            self.pause_until = None
            if self.cooldown_until and self.sim_time < self.cooldown_until:
                return self.balancer_view()
            self._evaluate(self.last["demand"], force=True)
            return self.balancer_view()

    def apply(self, plan_id: str, operator: str, note: str):
        with self.lock:
            if not self.rec or self.rec["plan_id"] != plan_id:
                raise StalePlan("This recommendation no longer matches the plant. Re-evaluate and review again.")
            rec = self.rec
            self.prev_allocation = {z: dict(s) for z, s in self.allocation.items()}
            self.allocation = {z: dict(s) for z, s in rec["allocation_after"].items()}
            entry = db.log_action(self.con, self.sim_time, "apply", plan_id, operator, note, _strip(rec))
            self.cooldown_until = self.sim_time + timedelta(minutes=self.b.balancer["cooldown_min"])
            in_service = [z for z, r in self.last["result"]["zones"].items() if r["in_service"]]
            self.settle = {"plan_id": plan_id, "in_service": in_service, "rollback_advised": None}
            self.rec = None
            self._branch()   # a decision taken now rewrites the future from here
            self._tick(advance=False)
            self._record()
            return entry

    def dismiss(self, plan_id: str | None, operator: str, note: str):
        with self.lock:
            if plan_id and (not self.rec or self.rec["plan_id"] != plan_id):
                raise StalePlan("This recommendation is no longer current.")
            entry = db.log_action(self.con, self.sim_time, "dismiss", plan_id, operator, note,
                                  _strip(self.rec) if self.rec else None)
            self.rec = None
            self.pause_until = self.sim_time + timedelta(minutes=self.b.balancer["dismiss_pause_min"])
            self._branch()   # a decision taken now rewrites the future from here
            self._tick(advance=False)
            self._record()
            return entry

    def rollback(self, operator: str, note: str):
        with self.lock:
            if self.prev_allocation is None:
                raise StalePlan("Nothing to roll back.")
            entry = db.log_action(self.con, self.sim_time, "rollback", (self.settle or {}).get("plan_id"),
                                  operator, note, {"restored_allocation": self.prev_allocation,
                                                   "replaced_allocation": self.allocation})
            self.allocation, self.prev_allocation = self.prev_allocation, None
            self.cooldown_until, self.settle = None, None
            self.pause_until = self.sim_time + timedelta(minutes=self.b.balancer["dismiss_pause_min"])
            self.rec = None
            self._branch()   # a decision taken now rewrites the future from here
            self._tick(advance=False)
            self._record()
            return entry

    def set_provider(self, provider: DemandProvider):
        with self.lock:
            self.provider = provider
            self.last_forecast_check = None
            self._branch()
            self._tick(advance=False)
            self._record()

    # ---------- views ----------
    def balancer_view(self) -> dict:
        with self.lock:
            v = dict(self.lb)
            v["status_text"] = bl.STATUS_TEXT.get(v["status"], v["status"])
            v["recommendation"] = _strip(self.rec) if self.rec else None
            v["can_rollback"] = self.prev_allocation is not None
            v["requires_operator_approval"] = True
            v["controls_real_pumps"] = False
            v["recent_decisions"] = db.read_log(self.con, 5)
            v["thresholds"] = {k: self.b.balancer[k] for k in ("crowded_pct", "release_pct", "underused_pct",
                                                              "receiver_cap_pct", "target_max_pct")}
            return v

    def pump_status_word(self, pid: str, r: dict) -> str:
        cfg = self.b.balancer
        if r["failed"]:
            return "Failed"
        if not r["powered"]:
            return "No power"
        if not r["healthy"]:
            return "No suction"
        if r["load_pct"] >= 100:
            return "Overloaded"
        if self.crowded[pid]:
            return "Crowded"
        if self.smoothed[pid] < cfg["underused_pct"]:
            return "Light load"
        return "Normal"

    def snapshot(self) -> dict:
        with self.lock:
            res = self.last["result"]
            b = self.b
            pumps = []
            for p in b.pumps:
                r = res["pumps"][p.id]
                pumps.append({"id": p.id, "name": p.name, "rated_flow_m3h": p.rated_flow_m3h,
                              "max_flow_m3h": p.max_flow_m3h, **{k: r[k] for k in (
                                  "powered", "failed", "healthy", "requested_m3h", "flow_m3h", "load_pct",
                                  "head_m", "power_kw", "feeds")},
                              "smoothed_load_pct": self.smoothed[p.id], "crowded": self.crowded[p.id],
                              "status": self.pump_status_word(p.id, r)})
            zones = []
            for z in b.zones:
                r = res["zones"][z.id]
                zones.append({"id": z.id, "name": z.name, "priority": z.priority, "elevation_m": z.elevation_m,
                              "min_pressure_bar": z.min_pressure_bar, "setpoint_bar": z.setpoint_bar,
                              "allowed_pumps": list(z.allowed_pumps), "crossover_valve": z.crossover_valve,
                              "allocation": self.allocation.get(z.id, {}),
                              **{k: r[k] for k in ("demand_m3h", "served_m3h", "unserved_m3h", "pressure_bar",
                                                   "in_service", "breached", "fed_by", "shed_low_tank", "buffer_m3")},
                              "buffer_capacity_m3": z.buffer_m3})
            t = res["tank"]
            return {
                "sim_time": self.sim_time.isoformat(),
                "recorded": {"from": self.recorded_range()[0].isoformat(), "to": self.recorded_range()[1].isoformat()}
                if self.timeline else None,
                "weekday": self.sim_time.strftime("%A"),
                "building": b.name,
                "tank": {"id": b.tank["id"], "name": b.tank["name"], "capacity_m3": b.tank["capacity_m3"],
                         "dead_volume_m3": b.tank["dead_volume_m3"], **t},
                "pumps": pumps, "zones": zones,
                "power_source": res["power_source"], "generator_fuel_h": self.state.fuel_h,
                "unserved_m3h": res["unserved_m3h"],
                "controls": dict(self.controls, zone_mult=dict(self.controls["zone_mult"])),
                "loadbalancer": self.balancer_view(),
                "demand_label": self.provider.label,
                "requires_operator_approval": True, "controls_real_pumps": False,
            }

    def scenario_start(self) -> dict:
        """Everything the scenario lab needs to start from the live state."""
        with self.lock:
            return {"sim_time": self.sim_time, "state": self.state,
                    "allocation": {z: dict(s) for z, s in self.allocation.items()},
                    "controls": dict(self.controls, pump_failed=list(self.controls["pump_failed"]),
                                     zone_mult=dict(self.controls["zone_mult"]))}


def _strip(rec: dict | None) -> dict | None:
    if rec is None:
        return None
    return {k: v for k, v in rec.items() if k not in ("signature",)}
