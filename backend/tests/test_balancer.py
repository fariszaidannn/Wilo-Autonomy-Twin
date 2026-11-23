import pytest

from app import balancer as bl
from app import hydraulics as hy
from conftest import BUSY, CV

CROWD_A = {"PA": True, "PB": False}


def plan(b, state, demand=BUSY, alloc=None, crowded=CROWD_A, **kw):
    return bl.plan(b, state, hy.Inputs(demand, alloc or b.default_allocation, **kw), CV, crowded)


def test_no_plan_when_balanced(b, state):
    r = plan(b, state, {"Z1": 4, "Z2": 4, "Z3": 4}, crowded={"PA": False, "PB": False})
    assert r["status"] == "balanced" and "moves" not in r


def test_worked_example_8_4(b, state):
    r = plan(b, state)
    assert r["status"] == "recommendation_ready"
    assert r["loads_before"]["PA"]["load_pct"] == pytest.approx(128, abs=1)
    assert r["loads_before"]["PB"]["load_pct"] == pytest.approx(23, abs=1)
    (m,) = r["moves"]
    assert (m["zone"], m["from"], m["to"], m["crossover_valve"]) == ("Z2", "PA", "PB", "XV-2")
    assert m["m3h"] == pytest.approx(15.7, abs=0.2)
    assert m["fraction_of_zone"] == pytest.approx(0.59, abs=0.01)
    assert r["loads_after"]["PA"]["load_pct"] == pytest.approx(76, abs=1)
    assert r["loads_after"]["PB"]["load_pct"] == pytest.approx(76, abs=1)
    assert r["unserved_before_m3h"] > 1 and r["unserved_after_m3h"] == pytest.approx(0, abs=1e-6)
    assert all(c["passed"] for c in r["safety_checks"]) and len(r["safety_checks"]) == 5
    assert r["confidence"] == "high"
    assert r["requires_operator_approval"] is True and r["controls_real_pumps"] is False


def test_moves_least_critical_zone_first(b, state):
    r = plan(b, state)
    assert all(m["zone"] != "Z1" for m in r["moves"])


def test_keeps_every_zone_in_service_and_matches_physics(b, state):
    r = plan(b, state)
    _, res = hy.step(b, state, hy.Inputs(BUSY, r["allocation_after"]), 1 / 60)
    for z in b.zones:
        zr = res["zones"][z.id]
        assert zr["pump_pressure_bar"] >= z.min_pressure_bar + b.balancer["pressure_margin_bar"]
        assert res["pumps"]["PA"]["load_pct"] == pytest.approx(r["loads_after"]["PA"]["load_pct"])


def test_respects_allowed_pumps(b, state):
    raw = dict(b.raw)
    raw["zones"] = [dict(z, allowed_pumps=["PA"]) if z["id"] in ("Z1", "Z2") else z for z in raw["zones"]]
    from app.config import parse_building

    b2 = parse_building(raw)
    r = plan(b2, state)
    assert r["status"] == "crowded_no_safe_move"


def test_ramp_limit_caps_one_move(b, state):
    r = plan(b, state)
    for m in r["moves"]:
        assert m["m3h"] <= b.balancer["ramp_max_fraction"] * BUSY[m["zone"]] + 1e-6


def test_single_pump_cannot_balance_and_reports_shortfall(b, state):
    r = plan(b, state, pump_failed=frozenset({"PB"}), crowded={"PA": True})
    assert r["status"] == "cannot_balance"
    assert r["shortfall_m3h"] == pytest.approx(sum(BUSY.values()) - 36.0)
    assert "capacity" in r["headline"]


def test_no_receiver_when_both_busy(b, state):
    alloc = {"Z1": {"PA": 1.0}, "Z2": {"PA": 0.5, "PB": 0.5}, "Z3": {"PB": 1.0}}
    demand = {"Z1": 13.0, "Z2": 28.0, "Z3": 12.0}
    r = plan(b, state, demand, alloc, crowded={"PA": True, "PB": True})
    assert r["status"] == "crowded_no_safe_move"


def test_hydraulic_veto_blocks_unsafe_move_then_critical_zone_is_last_resort(b, state):
    # Z3 needs 2.4 bar: the large Z2 move would drop Z3 to ~2.2 bar on Pump B, so it is vetoed.
    # Only then may the critical zone move (PRD 8.5), in a smaller, ramp-limited step that passes.
    raw = dict(b.raw)
    raw["zones"] = [dict(z, min_pressure_bar=2.4) if z["id"] == "Z3" else z for z in raw["zones"]]
    from app.config import parse_building

    b2 = parse_building(raw)
    r = plan(b2, hy.initial_state(b2))
    assert any("Z2" in msg for msg in r["blocked"])
    assert [m["zone"] for m in r["moves"]] == ["Z1"]
    assert all(c["passed"] for c in r["safety_checks"])


def test_everything_vetoed_reports_no_safe_move(b, state):
    raw = dict(b.raw)
    raw["zones"] = [dict(z, min_pressure_bar=2.45) if z["id"] == "Z3" else z for z in raw["zones"]]
    raw["balancer"] = dict(raw["balancer"], pressure_margin_bar=0.2)
    from app.config import parse_building

    b2 = parse_building(raw)
    r = plan(b2, hy.initial_state(b2))
    assert r["status"] == "crowded_no_safe_move" and r["blocked"]
    assert "capacity problem" in r["reason"]
