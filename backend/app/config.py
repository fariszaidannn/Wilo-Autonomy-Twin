"""Load and validate building.json and db_mapping.json (topology and limits are data, not code)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BUILDING_FILE = DATA_DIR / "building.json"
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
    try:
        pumps = tuple(Pump(**p) for p in raw["pumps"])
        zones = tuple(Zone(**{**z, "allowed_pumps": tuple(z["allowed_pumps"])}) for z in raw["zones"])
        b = Building(raw=raw, name=raw["name"], tz_offset_h=int(raw.get("timezone_offset_h", 0)),
                     tank=raw["tank"], pumps=pumps, zones=zones, generator=raw.get("generator", {}),
                     default_allocation=raw["default_allocation"], balancer=raw["balancer"],
                     forecast=raw["forecast"], service=raw["service"], synthetic=raw.get("synthetic", {}),
                     pump_by_id={p.id: p for p in pumps}, zone_by_id={z.id: z for z in zones})
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"building.json is missing or has a bad field: {exc}") from exc
    _validate(b)
    return b


def _validate(b: Building) -> None:
    if len(b.pump_by_id) != len(b.pumps) or len(b.zone_by_id) != len(b.zones):
        raise ConfigError("Pump and zone ids must be unique")
    for p in b.pumps:
        if not 0 < p.rated_flow_m3h <= p.max_flow_m3h:
            raise ConfigError(f"{p.id}: need 0 < rated_flow <= max_flow")
        if not p.shutoff_head_m > p.rated_head_m > 0:
            raise ConfigError(f"{p.id}: need shutoff_head > rated_head > 0")
    for z in b.zones:
        if not z.allowed_pumps or any(pid not in b.pump_by_id for pid in z.allowed_pumps):
            raise ConfigError(f"{z.id}: allowed_pumps must list known pumps")
        if z.min_pressure_bar >= z.setpoint_bar:
            raise ConfigError(f"{z.id}: min_pressure must be below setpoint")
        shares = b.default_allocation.get(z.id)
        if not shares:
            raise ConfigError(f"{z.id}: missing from default_allocation")
        if abs(sum(shares.values()) - 1) > 1e-6 or any(pid not in z.allowed_pumps for pid in shares):
            raise ConfigError(f"{z.id}: default shares must sum to 1 over allowed pumps")
    t = b.tank
    if not 0 <= t["dead_volume_m3"] < t["capacity_m3"]:
        raise ConfigError("tank: need 0 <= dead_volume < capacity")
    bal = b.balancer
    if bal["release_pct"] >= bal["crowded_pct"]:
        raise ConfigError("balancer: release_pct must be below crowded_pct (hysteresis)")


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
