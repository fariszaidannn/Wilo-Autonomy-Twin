import copy
import json

import pytest

from app import hydraulics as hy
from app.config import BUILDING_FILE, ConfigError, parse_building
from conftest import BUSY

DT = 1 / 60


def run(b, state, demand, **kw):
    return hy.step(b, state, hy.Inputs(demand, kw.pop("allocation", b.default_allocation), **kw), DT)


def test_tank_mass_balance(b, state):
    demand = {"Z1": 5.0, "Z2": 6.0, "Z3": 4.0}
    new, res = run(b, state, demand)
    q_in, q_out = res["tank"]["inflow_m3h"], res["tank"]["outflow_m3h"]
    assert new.tank_m3 == pytest.approx(state.tank_m3 + (q_in - q_out) * DT)
    assert q_out == pytest.approx(sum(demand.values()))


def test_step_is_pure_dry_run(b, state):
    before = copy.deepcopy(state)
    run(b, state, BUSY)
    assert state == before


def test_pressure_capped_at_setpoint_and_falls_with_flow(b, state):
    _, low = run(b, state, {"Z1": 2.0, "Z2": 2.0, "Z3": 2.0})
    for z in b.zones:
        assert low["zones"][z.id]["pressure_bar"] <= z.setpoint_bar + 1e-9
    _, high = run(b, state, BUSY)
    assert high["zones"]["Z2"]["pump_pressure_bar"] < low["zones"]["Z2"]["pump_pressure_bar"]


def test_overload_curtails_lowest_priority_first(b, state):
    # Everything on Pump A at 45 m3/h > 36 max: Z3 (priority 3) is cut before Z2, Z1 untouched.
    alloc = {"Z1": {"PA": 1.0}, "Z2": {"PA": 1.0}, "Z3": {"PA": 1.0}}
    _, res = run(b, state, BUSY, allocation=alloc)
    z = res["zones"]
    assert res["pumps"]["PA"]["flow_m3h"] == pytest.approx(36.0)
    assert z["Z1"]["pump_served_m3h"] == pytest.approx(11.8)
    assert z["Z3"]["pump_served_m3h"] == pytest.approx(0.0)
    assert z["Z2"]["pump_served_m3h"] == pytest.approx(36.0 - 11.8)


def test_failover_moves_share_to_allowed_pump(b, state):
    _, res = run(b, state, {"Z1": 4, "Z2": 4, "Z3": 4}, pump_failed=frozenset({"PB"}))
    assert res["allocation_effective"]["Z3"] == {"PA": 1.0}
    assert res["pumps"]["PA"]["flow_m3h"] == pytest.approx(12.0)
    assert res["pumps"]["PB"]["flow_m3h"] == 0


def test_generator_covers_only_listed_pumps(b, state):
    _, res = run(b, state, {"Z1": 4, "Z2": 4, "Z3": 4}, grid_on=False, generator_available=True)
    assert res["power_source"] == "generator"
    assert res["pumps"]["PA"]["powered"] and not res["pumps"]["PB"]["powered"]
    assert res["pumps"]["PA"]["flow_m3h"] == pytest.approx(12.0)
    new, _ = run(b, state, {"Z1": 4, "Z2": 4, "Z3": 4}, grid_on=False, generator_available=True)
    assert new.fuel_h == pytest.approx(state.fuel_h - DT)


def test_no_power_no_generator_zones_fail_after_buffer(b, state):
    s = state
    demand = {"Z1": 6.0, "Z2": 6.0, "Z3": 6.0}
    s, res = run(b, s, demand, grid_on=False, generator_available=False)
    assert res["zones"]["Z1"]["in_service"]          # vessel bridges the first minute
    for _ in range(10):
        s, res = run(b, s, demand, grid_on=False, generator_available=False)
    assert all(not z["in_service"] and z["breached"] for z in res["zones"].values())


def test_breach_declared_after_two_minutes(b, state):
    s = state
    alloc = {"Z1": {"PA": 1.0}, "Z2": {"PA": 1.0}, "Z3": {"PA": 1.0}}
    flags = []
    for _ in range(4):
        s, res = run(b, s, BUSY, allocation=alloc)
        flags.append((res["zones"]["Z3"]["in_service"], res["zones"]["Z3"]["breached"]))
    first_out = next(i for i, (ins, _) in enumerate(flags) if not ins)
    first_breach = next(i for i, (_, br) in enumerate(flags) if br)
    assert first_breach == first_out + 1


def test_city_cut_drains_tank_and_reserve_sheds_low_priority(b):
    s = hy.initial_state(b, tank_m3=40.0)
    demand = {"Z1": 8.0, "Z2": 10.0, "Z3": 6.0}
    s, res = run(b, s, demand, city_supply_on=False)
    assert res["tank"]["inflow_m3h"] == 0 and s.tank_m3 < 40.0
    assert res["zones"]["Z3"]["shed_low_tank"] and not res["zones"]["Z1"]["shed_low_tank"]


def test_config_validation_rejects_bad_allocation():
    raw = json.loads(BUILDING_FILE.read_text(encoding="utf-8"))
    raw["default_allocation"]["Z1"] = {"PA": 0.5}
    with pytest.raises(ConfigError):
        parse_building(raw)
    raw = json.loads(BUILDING_FILE.read_text(encoding="utf-8"))
    raw["zones"][0]["allowed_pumps"] = ["PX"]
    with pytest.raises(ConfigError):
        parse_building(raw)
