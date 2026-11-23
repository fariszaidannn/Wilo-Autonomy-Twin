"""Outage outlook (Building tab bento): operator isolation, reserve stage and the live outlook."""
import copy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import hydraulics as hy
from app import simulator
from app.config import DEFAULT_BUILDING_FILE, check_raw
from app.main import AppState, create_app

DEMAND = {"Z1": 5.0, "Z2": 8.0, "Z3": 6.0}


def inputs(b, **kw):
    return hy.Inputs(DEMAND, {z: dict(s) for z, s in b.default_allocation.items()}, city_supply_on=False, **kw)


def test_isolated_zone_gets_no_pump_water_and_saves_tank(b, state):
    s_open, r_open = hy.step(b, state, inputs(b), 1 / 60)
    s_iso, r_iso = hy.step(b, state, inputs(b, isolated=frozenset({"Z3"})), 1 / 60)
    z3 = r_iso["zones"]["Z3"]
    assert z3["isolated"] and z3["fed_by"] == {} and z3["pump_served_m3h"] == 0
    assert not r_open["zones"]["Z3"]["isolated"]
    assert r_iso["zones"]["Z1"]["served_m3h"] == pytest.approx(DEMAND["Z1"])
    assert s_iso.tank_m3 - s_open.tank_m3 == pytest.approx(DEMAND["Z3"] / 60)


def test_first_reserve_stage_is_the_highest_percentage(b):
    assert simulator.first_reserve_stage(b) == {"priority_from": 3, "below_pct": 35.0, "zones": ["Z3"]}


def test_reserve_stage_never_isolates_the_critical_zone(b):
    everyone = replace(b, tank={**b.tank, "priority_reserve_pct": {"1": 50, "3": 20}})
    assert simulator.first_reserve_stage(everyone)["zones"] == ["Z2", "Z3"]   # Z1 (priority 1) is kept
    all_critical = replace(b, zones=tuple(replace(z, priority=1) for z in b.zones))
    assert simulator.first_reserve_stage(all_critical) is None
    none = replace(b, tank={**b.tank, "priority_reserve_pct": {}})
    assert simulator.first_reserve_stage(none) is None


@pytest.fixture(scope="module")
def app_state(tmp_path_factory):
    d = tmp_path_factory.mktemp("outlook")
    bpath = d / "building.json"
    bpath.write_text(DEFAULT_BUILDING_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    return AppState(start_clock=False, warm_outage=False, db_path=d / "o.db", building_path=bpath)


def test_city_cut_outlook_is_consistent(app_state):
    s = app_state
    r = simulator.outlook(s.b, s.twin.provider, s.twin.scenario_start(), "city_supply_cut", 12)
    assert r["detection_min"] == 5 and not r["already_active"]
    tt = {z["zone"]: z["threshold_min"] for z in r["zones"]}
    # Reserve stages isolate Z3 (35 %) then Z2 (15 %); Z1 runs until the tank is at dead volume.
    assert tt["Z3"]["expected"] < tt["Z2"]["expected"] < tt["Z1"]["expected"]
    for v in tt.values():   # more demand never buys time
        assert v["worst"] <= v["expected"] <= v["best"]
    lead = r["lead"]["expected"]
    assert lead["zone"] == "Z3" and lead["lead_min"] == tt["Z3"]["expected"] - 5
    p = r["priority"]
    assert p["zones"] == ["Z1"]
    assert p["no_action"]["worst"] <= p["no_action"]["expected"] <= p["no_action"]["best"]
    assert p["mitigated"]["expected"] > p["no_action"]["expected"]   # isolating Z3 early saves water for Z1
    assert r["mitigation"]["zones"] == ["Z3"]
    assert r["controls_real_pumps"] is False and r["requires_operator_approval"] is True


def test_outlook_counts_from_now_when_the_outage_is_already_on(app_state):
    start = app_state.twin.scenario_start()
    start["controls"]["city_supply_on"] = False
    r = simulator.outlook(app_state.b, app_state.twin.provider, start, "city_supply_cut", 6)
    assert r["already_active"] and r["detection_min"] == 0
    assert r["lead"]["expected"]["lead_min"] == r["lead"]["expected"]["breach_min"]


def test_power_cut_uses_the_live_generator_setting(app_state):
    start = app_state.twin.scenario_start()
    with_gen = simulator.outlook(app_state.b, app_state.twin.provider, start, "power_outage", 6)
    start["controls"]["generator_available"] = False
    no_gen = simulator.outlook(app_state.b, app_state.twin.provider, start, "power_outage", 6)
    assert with_gen["generator"] is True and no_gen["generator"] is False
    # Without a generator every pump stops at once: every zone loses service within minutes.
    assert all(z["threshold_min"]["expected"] is not None and z["threshold_min"]["expected"] < 30 for z in no_gen["zones"])
    assert no_gen["priority"]["no_action"]["expected"] < with_gen["priority"]["no_action"]["expected"]


@pytest.fixture
def client(tmp_path):
    bpath = tmp_path / "building.json"
    bpath.write_text(DEFAULT_BUILDING_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    with TestClient(create_app(start_clock=False, warm_outage=False, db_path=tmp_path / "api.db",
                               building_path=bpath)) as c:
        yield c


def test_outlook_endpoint_validates_and_follows_the_controls(client):
    assert client.get("/api/outlook?event=meteor").status_code == 422
    assert client.get("/api/outlook?window_h=7").status_code == 422
    a = client.get("/api/outlook?event=city_supply_cut&window_h=6").json()
    assert client.get("/api/outlook?event=city_supply_cut&window_h=6").json() == a   # cached, same answer
    client.post("/api/twin/control", json={"demand_mult": 1.5})
    b = client.get("/api/outlook?event=city_supply_cut&window_h=6").json()
    z1 = lambda r: next(z for z in r["zones"] if z["zone"] == "Z1")["threshold_min"]["expected"]
    assert z1(b) < z1(a)   # more demand drains the tank sooner: the cache did not serve a stale answer


def test_detection_delay_is_validated():
    raw = copy.deepcopy(__import__("json").loads(DEFAULT_BUILDING_FILE.read_text(encoding="utf-8")))
    raw["service"]["detection_delay_min"] = -1
    errors, _, _ = check_raw(raw)
    assert any("detection delay" in e for e in errors)
