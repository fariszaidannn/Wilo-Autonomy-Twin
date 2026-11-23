import copy

import pytest
from fastapi.testclient import TestClient

from app.config import BUILDING_FILE
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    bpath = tmp_path / "building.json"
    bpath.write_text(BUILDING_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    with TestClient(create_app(start_clock=False, warm_outage=False, db_path=tmp_path / "api.db",
                               building_path=bpath)) as c:
        yield c


def busy(client):
    client.post("/api/twin/control", json={"jump_to": "12:00", "demand_mult": 1.5, "running": False})
    return client.post("/api/twin/step", json={"minutes": 10}).json()


def test_state_never_controls_real_pumps(client):
    s = client.get("/api/state").json()
    assert s["controls_real_pumps"] is False and s["requires_operator_approval"] is True
    assert s["loadbalancer"]["controls_real_pumps"] is False
    assert {p["id"] for p in s["pumps"]} == {"PA", "PB"}


def test_full_balancer_flow(client):
    s = busy(client)
    lb = s["loadbalancer"]
    assert lb["status"] == "recommendation_ready"
    rec = lb["recommendation"]
    assert rec["moves"][0]["zone"] == "Z2"

    stale = client.post("/api/loadbalancer/apply", json={"plan_id": "LB-stale", "operator": "ayse"})
    assert stale.status_code == 409

    ok = client.post("/api/loadbalancer/apply", json={"plan_id": rec["plan_id"], "operator": "ayse", "note": "peak"})
    assert ok.status_code == 200
    after = ok.json()["state"]
    loads = [p["load_pct"] for p in after["pumps"]]
    assert max(loads) <= 85
    assert after["loadbalancer"]["status"] == "settling" and after["loadbalancer"]["can_rollback"]

    log = client.get("/api/loadbalancer/log").json()["entries"]
    assert log[0]["action"] == "apply" and log[0]["operator"] == "ayse" and log[0]["plan"]["moves"]

    rb = client.post("/api/loadbalancer/rollback", json={"operator": "ayse", "note": "test"})
    assert rb.status_code == 200
    assert rb.json()["state"]["zones"][1]["allocation"] == {"PA": 1.0}
    assert client.post("/api/loadbalancer/rollback", json={"operator": "ayse"}).status_code == 409


def test_dismiss_pauses_advice(client):
    rec = busy(client)["loadbalancer"]["recommendation"]
    r = client.post("/api/loadbalancer/dismiss", json={"plan_id": rec["plan_id"], "operator": "ops"})
    assert r.status_code == 200
    assert r.json()["state"]["loadbalancer"]["recommendation"] is None
    assert client.get("/api/loadbalancer/log").json()["entries"][0]["action"] == "dismiss"


def test_failing_a_pump_makes_plan_stale(client):
    rec = busy(client)["loadbalancer"]["recommendation"]
    client.post("/api/twin/control", json={"pump_failed": ["PB"]})
    assert client.post("/api/loadbalancer/apply", json={"plan_id": rec["plan_id"]}).status_code == 409
    assert client.get("/api/loadbalancer").json()["status"] == "cannot_balance"


def test_scenario_run_and_usage_endpoints(client):
    r = client.post("/api/scenarios/run", json={"scenario": "city_supply_cut", "horizon_h": 6}).json()
    assert r["controls_real_pumps"] is False and r["assumptions"] and len(r["zones"]) == 3
    assert set(r["zones"][0]["time_to_threshold_min"]) == {"best", "base", "worst"}
    assert client.post("/api/scenarios/run", json={"scenario": "meteor"}).status_code == 422
    assert client.get("/api/usage/summary").json()["zones"]
    assert len(client.get("/api/usage/forecast").json()["points"]) > 24
    assert client.get("/api/usage/accuracy").json()["zones"]
    st = client.get("/api/data/status").json()
    assert st["label"] == "SIMULATED"
    assert client.get("/api/data/template.csv").text.startswith("ts,zone_id")


def test_csv_upload_switches_label_to_mixed(client):
    csv = client.get("/api/data/template.csv").content
    r = client.post("/api/data/import-csv", files={"file": ("u.csv", csv, "text/csv")})
    assert r.status_code == 200 and r.json()["imported_rows"] == 6
    assert client.get("/api/data/status").json()["label"] == "MIXED"
    bad = client.post("/api/data/import-csv", files={"file": ("u.csv", b"a,b\n1,2\n", "text/csv")})
    assert bad.status_code == 422


def test_outage_endpoints_report_unavailable_when_not_loaded(client):
    assert client.get("/api/outage/status").json()["status"] == "loading"
    assert client.get("/api/outage/scenarios").status_code == 503


def edited_topology(cfg):
    """Demo building plus a third pump PC and a fourth zone Z4 fed by PC (backup PB)."""
    raw = copy.deepcopy(cfg)
    raw["pumps"].append({"id": "PC", "name": "Booster Pump C", "rated_flow_m3h": 20, "max_flow_m3h": 24,
                         "shutoff_head_m": 70, "rated_head_m": 55, "efficiency": 0.6})
    raw["zones"].append({"id": "Z4", "name": "Rooftop sports hall", "priority": 3, "elevation_m": 34,
                         "min_pressure_bar": 1.2, "setpoint_bar": 2.2, "min_flow_ratio": 0.9, "friction_k": 0.05,
                         "buffer_m3": 0.3, "allowed_pumps": ["PC", "PB"], "crossover_valve": "XV-4", "occupancy": 120})
    raw["default_allocation"]["Z4"] = {"PC": 1.0}
    raw["synthetic"]["zones"]["Z4"] = {"peak_m3h": 6, "profile": "classroom"}
    return raw


def test_edit_topology_applies_everywhere(client):
    cfg = client.get("/api/config").json()
    raw = edited_topology(cfg)
    v = client.post("/api/config/validate", json=raw).json()
    assert v["ok"] and v["errors"] == []
    r = client.post("/api/config", json=raw)
    assert r.status_code == 200
    s = client.get("/api/state").json()
    assert [p["id"] for p in s["pumps"]] == ["PA", "PB", "PC"]
    assert [z["id"] for z in s["zones"]] == ["Z1", "Z2", "Z3", "Z4"]
    s = client.post("/api/twin/step", json={"minutes": 30}).json()
    z4 = next(z for z in s["zones"] if z["id"] == "Z4")
    assert z4["demand_m3h"] > 0 and z4["fed_by"].get("PC", 0) > 0
    sc = client.post("/api/scenarios/run", json={"scenario": "pump_failure", "pump": "PC", "horizon_h": 2}).json()
    assert {z["zone"] for z in sc["zones"]} == {"Z1", "Z2", "Z3", "Z4"}
    assert "Z4" in client.get("/api/usage/summary").json()["zones"]   # demo data regenerated
    # removing a zone and a pump works too
    raw2 = client.get("/api/config").json()
    raw2["zones"] = [z for z in raw2["zones"] if z["id"] != "Z3"]
    raw2["default_allocation"].pop("Z3")
    assert client.post("/api/config", json=raw2).status_code == 200
    assert [z["id"] for z in client.get("/api/state").json()["zones"]] == ["Z1", "Z2", "Z4"]


def test_invalid_topology_rejected_and_warnings(client):
    raw = client.get("/api/config").json()
    bad = copy.deepcopy(raw)
    bad["zones"][0]["allowed_pumps"] = []
    bad["zones"][1]["min_pressure_bar"] = 9
    r = client.post("/api/config", json=bad)
    assert r.status_code == 422 and len(r.json()["detail"]) >= 2
    assert [z["id"] for z in client.get("/api/state").json()["zones"]] == ["Z1", "Z2", "Z3"]   # unchanged
    high = copy.deepcopy(raw)
    high["zones"][2]["elevation_m"] = 70
    v = client.post("/api/config/validate", json=high).json()
    assert v["ok"] and any("can never reach" in w for w in v["warnings"])


def test_restore_demo_building(client):
    raw = edited_topology(client.get("/api/config").json())
    client.post("/api/config", json=raw)
    r = client.post("/api/config/restore")
    assert r.status_code == 200
    assert [p["id"] for p in client.get("/api/state").json()["pumps"]] == ["PA", "PB"]
