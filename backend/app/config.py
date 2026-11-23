"""Load and validate building.json and db_mapping.json (topology and limits are data, not code)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BUILDING_FILE = DATA_DIR / "building.json"
DEFAULT_BUILDING_FILE = DATA_DIR / "building.default.json"   # the shipped demo building, for "restore"
MAPPING_FILE = DATA_DIR / "db_mapping.json"
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Pump:
    id: str
    name: str
    rated_flow_m3h: float
    max_flow_m3h: float
    shutoff_head_m: float
    rated_head_m: float
    efficiency: float

    @property
    def curve_a(self) -> float:
        # H(Q) = H0 - a Q^2 passes through the rated duty point.
        return (self.shutoff_head_m - self.rated_head_m) / self.rated_flow_m3h ** 2

    def head(self, q: float) -> float:
        return max(0.0, self.shutoff_head_m - self.curve_a * q * q)


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    priority: int
    elevation_m: float
    min_pressure_bar: float
    setpoint_bar: float
    min_flow_ratio: float
    friction_k: float
    buffer_m3: float
    allowed_pumps: tuple[str, ...]
    crossover_valve: str
    occupancy: int


@dataclass(frozen=True)
class Building:
    raw: dict
    name: str
    tz_offset_h: int
    tank: dict
    pumps: tuple[Pump, ...]
    zones: tuple[Zone, ...]
    generator: dict
    default_allocation: dict
    balancer: dict
    forecast: dict
    service: dict
    synthetic: dict
    pump_by_id: dict = field(default_factory=dict)
    zone_by_id: dict = field(default_factory=dict)


def parse_building(raw: dict) -> Building:
    text = ("id", "name", "crossover_valve")
    try:
        pumps = tuple(Pump(**{k: (str(v) if k in text else float(v)) for k, v in p.items()}) for p in raw["pumps"])
        zones = tuple(Zone(**{k: (str(v) if k in text else tuple(v) if k == "allowed_pumps"
                                  else int(v) if k in ("priority", "occupancy") else float(v))
                              for k, v in z.items()}) for z in raw["zones"])
        b = Building(raw=raw, name=raw["name"], tz_offset_h=int(raw.get("timezone_offset_h", 0)),
                     tank=raw["tank"], pumps=pumps, zones=zones, generator=raw.get("generator", {}),
                     default_allocation=raw["default_allocation"], balancer=raw["balancer"],
                     forecast=raw["forecast"], service=raw["service"], synthetic=raw.get("synthetic", {}),
                     pump_by_id={p.id: p for p in pumps}, zone_by_id={z.id: z for z in zones})
    except (KeyError, TypeError, ValueError) as exc:
        msg = f"A required field is missing or not a number: {exc}"
        raise ConfigError(msg, [msg]) from exc
    errors = _errors(b)
    if errors:
        raise ConfigError("; ".join(errors), errors)
    return b


ID_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}$")
MAX_PUMPS, MAX_ZONES = 4, 6

# Demand shapes a new zone can take (hourly multipliers of its peak), from the demo building.
PROFILES = {
    "clinic": {"weekend_factor": 0.6,
               "shape": [0.3, 0.25, 0.25, 0.25, 0.3, 0.4, 0.6, 0.8, 0.9, 0.95, 1, 1, 1, 0.95, 0.9, 0.85, 0.8,
                         0.7, 0.6, 0.55, 0.5, 0.45, 0.4, 0.35]},
    "classroom": {"weekend_factor": 0.2,
                  "shape": [0.05, 0.05, 0.05, 0.05, 0.05, 0.08, 0.15, 0.4, 0.75, 0.9, 0.95, 1, 1, 0.95, 0.9, 0.85,
                            0.7, 0.45, 0.25, 0.15, 0.1, 0.08, 0.06, 0.05]},
    "residential": {"weekend_factor": 1.15,
                    "shape": [0.15, 0.1, 0.08, 0.08, 0.1, 0.25, 0.7, 1.0, 0.75, 0.45, 0.4, 0.383, 0.383, 0.4, 0.4,
                              0.45, 0.55, 0.7, 0.85, 0.95, 0.9, 0.7, 0.45, 0.25]},
}


def _errors(b: Building) -> list[str]:
    e = []
    if not 1 <= len(b.pumps) <= MAX_PUMPS:
        e.append(f"The building needs 1 to {MAX_PUMPS} pumps.")
    if not 1 <= len(b.zones) <= MAX_ZONES:
        e.append(f"The building needs 1 to {MAX_ZONES} zones.")
    if len(b.pump_by_id) != len(b.pumps) or len(b.zone_by_id) != len(b.zones) or \
            set(b.pump_by_id) & set(b.zone_by_id):
        e.append("Pump and zone ids must be unique.")
    for p in b.pumps:
        if not ID_RE.match(p.id):
            e.append(f"{p.id}: ids are 1-6 capital letters or digits, starting with a letter.")
        if not p.name.strip():
            e.append(f"{p.id}: give the pump a name.")
        if not 0 < p.rated_flow_m3h <= p.max_flow_m3h:
            e.append(f"{p.id}: rated flow must be above 0 and not above max flow.")
        if not p.shutoff_head_m > p.rated_head_m > 0:
            e.append(f"{p.id}: shut-off head must be above rated head, and rated head above 0.")
        if not 0 < p.efficiency <= 1:
            e.append(f"{p.id}: efficiency must be between 0 and 1.")
    for z in b.zones:
        if not ID_RE.match(z.id):
            e.append(f"{z.id}: ids are 1-6 capital letters or digits, starting with a letter.")
        if not z.name.strip():
            e.append(f"{z.id}: give the zone a name.")
        if not z.allowed_pumps or any(pid not in b.pump_by_id for pid in z.allowed_pumps):
            e.append(f"{z.id}: connect it to at least one existing pump.")
        if not 0 < z.min_pressure_bar < z.setpoint_bar:
            e.append(f"{z.id}: minimum pressure must be above 0 and below the set-point.")
        if z.priority < 1 or z.priority > 5:
            e.append(f"{z.id}: priority must be 1 (most critical) to 5.")
        if z.elevation_m < 0 or z.friction_k < 0 or z.buffer_m3 < 0 or z.occupancy < 0:
            e.append(f"{z.id}: elevation, pipe loss, vessel and occupancy cannot be negative.")
        if not 0 < z.min_flow_ratio <= 1:
            e.append(f"{z.id}: minimum flow ratio must be between 0 and 1.")
        shares = b.default_allocation.get(z.id)
        if not shares:
            e.append(f"{z.id}: choose which pump normally feeds it.")
        elif abs(sum(shares.values()) - 1) > 1e-6 or any(pid not in z.allowed_pumps for pid in shares):
            e.append(f"{z.id}: its normal feed must come from pumps it is connected to.")
        syn = b.synthetic.get("zones", {}).get(z.id)
        if syn is not None and not syn.get("peak_m3h", 0) > 0:
            e.append(f"{z.id}: typical peak demand must be above 0.")
    t = b.tank
    try:
        if not 0 <= t["dead_volume_m3"] < t["capacity_m3"]:
            e.append("Tank: dead volume must be at least 0 and below capacity.")
        if not 0 <= t["initial_volume_m3"] <= t["capacity_m3"]:
            e.append("Tank: starting volume must be between 0 and capacity.")
        if t["city_inflow_max_m3h"] < 0:
            e.append("Tank: city inflow cannot be negative.")
        for prio, pct in (t.get("priority_reserve_pct") or {}).items():
            if not str(prio).isdigit() or not 0 <= float(pct) < 100:
                e.append("Tank: low-tank reserves need a priority number and a percentage from 0 to 99.")
    except (KeyError, TypeError, ValueError):
        e.append("Tank: capacity, dead volume, starting volume and city inflow are required.")
    if any(pid not in b.pump_by_id for pid in b.generator.get("covers_pumps", [])):
        e.append("Generator: it can only cover existing pumps.")
    try:
        if not 0 <= float(b.service.get("detection_delay_min", 5)) <= 120:
            e.append("Service: outage detection delay must be 0 to 120 minutes.")
    except (TypeError, ValueError):
        e.append("Service: outage detection delay must be a number of minutes.")
    bal = b.balancer
    if bal["release_pct"] >= bal["crowded_pct"]:
        e.append("Balancer: release_pct must be below crowded_pct (hysteresis).")
    return e


def warnings_for(b: Building) -> list[str]:
    """Physics sanity checks that do not block saving."""
    w = []
    for z in b.zones:
        best = max((b.pump_by_id[p].shutoff_head_m for p in z.allowed_pumps if p in b.pump_by_id), default=0)
        need = z.elevation_m + z.min_pressure_bar * 10.197
        if best < need:
            w.append(f"{z.id} can never reach {z.min_pressure_bar} bar: its best pump gives {best:.0f} m of head "
                     f"at zero flow, but elevation plus minimum pressure needs {need:.0f} m.")
    peaks = b.synthetic.get("zones", {})
    total_peak = sum(peaks.get(z.id, {}).get("peak_m3h", 0) for z in b.zones)
    cap = sum(p.max_flow_m3h for p in b.pumps)
    if total_peak > cap:
        w.append(f"Typical peak demand ({total_peak:.1f} m³/h) is above the pumps' combined max flow "
                 f"({cap:.1f} m³/h); low-priority zones will be curtailed at peak.")
    for z in b.zones:
        if len(z.allowed_pumps) == 1:
            w.append(f"{z.id} is connected to one pump only, so the balancer cannot move it and it has no backup.")
    if not b.generator.get("covers_pumps"):
        w.append("No pump is on the generator: a power outage stops every pump.")
    return w


def normalize(raw: dict) -> dict:
    """Make an edited config self-consistent: demand shapes from profiles, no orphans."""
    raw = json.loads(json.dumps(raw))
    zone_ids = [z.get("id") for z in raw.get("zones", [])]
    pump_ids = [p.get("id") for p in raw.get("pumps", [])]
    syn = raw.setdefault("synthetic", {}).setdefault("zones", {})
    for zid in list(syn):
        if zid not in zone_ids:
            del syn[zid]
    for zid in zone_ids:
        entry = syn.setdefault(zid, {"peak_m3h": 5.0, "profile": "residential"})
        prof = PROFILES.get(entry.get("profile"), PROFILES["residential"])
        entry["profile"] = entry.get("profile") if entry.get("profile") in PROFILES else "residential"
        entry["shape"] = list(prof["shape"])
        entry["weekend_factor"] = prof["weekend_factor"]
    raw["default_allocation"] = {z: a for z, a in raw.get("default_allocation", {}).items() if z in zone_ids}
    gen = raw.setdefault("generator", {"covers_pumps": [], "fuel_hours": 6})
    gen["covers_pumps"] = [p for p in gen.get("covers_pumps", []) if p in pump_ids]
    return raw


def check_raw(raw: dict) -> tuple[list[str], list[str], Building | None]:
    """(errors, warnings, building) for an edited config, without raising."""
    try:
        b = parse_building(normalize(raw))
    except ConfigError as exc:
        return (exc.args[1] if len(exc.args) > 1 else [str(exc)]), [], None
    return [], warnings_for(b), b


def load_building(path: Path = BUILDING_FILE) -> Building:
    return parse_building(json.loads(path.read_text(encoding="utf-8")))


def validate_mapping(m: dict) -> dict:
    """Table and column names reach SQL as identifiers, so only plain identifiers are allowed."""
    for key in ("table", "ts_col", "zone_col", "value_col"):
        if not isinstance(m.get(key), str) or not IDENT.match(m[key]):
            raise ConfigError(f"db_mapping.{key} must be a plain identifier")
    if m.get("occupancy_col") is not None and not IDENT.match(str(m["occupancy_col"])):
        raise ConfigError("db_mapping.occupancy_col must be a plain identifier")
    if m.get("value_kind") not in ("flow_m3h", "volume_m3", "cumulative_m3"):
        raise ConfigError("db_mapping.value_kind must be flow_m3h, volume_m3 or cumulative_m3")
    if m.get("ts_format") not in ("iso", "epoch_s", "epoch_ms"):
        raise ConfigError("db_mapping.ts_format must be iso, epoch_s or epoch_ms")
    return m


def load_mapping(path: Path = MAPPING_FILE) -> dict:
    if not path.exists():
        return {"enabled": False}
    return json.loads(path.read_text(encoding="utf-8"))
