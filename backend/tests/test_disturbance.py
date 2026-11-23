"""Disturbance length: a switched-on disturbance runs for the chosen length, then switches back."""
import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_BUILDING_FILE
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    bpath = tmp_path / "building.json"
    bpath.write_text(DEFAULT_BUILDING_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    with TestClient(create_app(start_clock=False, warm_outage=False, db_path=tmp_path / "d.db",
                               building_path=bpath)) as c:
        c.post("/api/twin/control", json={"running": False})
        yield c


def ctl(client, **kw):
    return client.post("/api/twin/control", json=kw).json()


def step(client, n):
    return client.post("/api/twin/step", json={"minutes": n}).json()


def length(client, key, minutes):
    return client.post("/api/twin/disturbance", json={"key": key, "duration_min": minutes})


def test_switching_on_starts_a_disturbance_without_an_end(client):
    s = ctl(client, city_supply_on=False)
    assert s["disturbances"] == [{"key": "city", "start": s["sim_time"], "end": None, "active": True}]


def test_it_switches_back_exactly_after_the_chosen_length(client):
    ctl(client, city_supply_on=False)
    s = length(client, "city", 60).json()
    assert s["disturbances"][0]["end"][11:16] == "08:00"
    s = step(client, 59)
    assert s["controls"]["city_supply_on"] is False and s["tank"]["inflow_m3h"] == 0
    s = step(client, 1)
    assert s["sim_time"][11:16] == "08:00"
    assert s["controls"]["city_supply_on"] is True and s["tank"]["inflow_m3h"] > 0
    assert s["disturbances"][0]["active"] is False


def test_seeking_back_replays_it_and_its_end(client):
    ctl(client, pump_failed=["PB"])
    length(client, "pump:PB", 60)
    step(client, 120)
    s = ctl(client, jump_to="07:30")
    assert s["controls"]["pump_failed"] == ["PB"] and s["disturbances"][0]["active"]
    s = step(client, 30)
    assert s["sim_time"][11:16] == "08:00" and s["controls"]["pump_failed"] == []


def test_switching_off_by_hand_ends_it_now(client):
    ctl(client, grid_on=False)
    step(client, 12)
    s = ctl(client, grid_on=True)
    (d,) = s["disturbances"]
    assert not d["active"] and d["end"] == s["sim_time"]
    assert length(client, "grid", 60).status_code == 409


def test_a_length_that_has_already_passed_ends_it_at_once(client):
    ctl(client, generator_available=False)
    step(client, 90)
    s = length(client, "generator", 60).json()
    assert s["controls"]["generator_available"] is True and not s["disturbances"][0]["active"]


def test_changing_the_length_moves_the_end(client):
    ctl(client, city_supply_on=False)
    length(client, "city", 720)
    s = length(client, "city", 240).json()
    assert s["disturbances"][0]["end"][11:16] == "11:00"


def test_request_validation(client):
    assert length(client, "meteor", 60).status_code == 422
    assert length(client, "city", 0).status_code == 422
