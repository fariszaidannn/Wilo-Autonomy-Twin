"""The time bar seeks like a video: going back restores the plant as it was at that minute."""
from datetime import datetime

import pytest

from app import db, usage
from app.synthetic import generate
from app.twin import Twin


@pytest.fixture
def twin(b, tmp_path):
    con = db.connect(tmp_path / "t.db")
    flows = generate(b)[["ts", "zone_id", "flow_m3h", "occupancy", "source"]]
    tw = Twin(b, con, usage.DemandProvider(b, flows))
    tw.control(running=False)
    return tw


def tank(tw):
    return tw.snapshot()["tank"]["volume_m3"]


def test_seek_back_restores_a_full_tank_after_it_ran_empty(twin):
    twin.control(jump_to="08:00")
    full = tank(twin)
    twin.control(city_supply_on=False)          # outage at 08:00
    twin.step(8 * 60)                            # 16:00: the tank has run down to its dead volume
    assert tank(twin) < 10
    twin.control(jump_to="09:00")                # back to one hour into the outage
    s = twin.snapshot()
    assert s["sim_time"].endswith("09:00:00")
    assert tank(twin) > full - 40                # one hour of drain, not empty
    assert s["controls"]["city_supply_on"] is False   # the outage was already on at 09:00
    twin.control(jump_to="07:30")                # before the outage started
    assert twin.snapshot()["controls"]["city_supply_on"] is True
    assert tank(twin) == pytest.approx(twin.timeline[datetime(2026, 11, 23, 7, 30)]["state"].tank_m3, abs=1.0)  # +1 min of flow


def test_playing_forward_after_seeking_back_replays_the_recorded_outage(twin):
    twin.control(jump_to="08:00")
    twin.control(city_supply_on=False)
    twin.step(6 * 60)
    at_14 = tank(twin)
    twin.control(jump_to="10:00")
    twin.step(4 * 60)                            # replay 10:00 -> 14:00
    assert tank(twin) == pytest.approx(at_14)
    assert twin.snapshot()["controls"]["city_supply_on"] is False


def test_changing_the_past_drops_the_recorded_future(twin):
    twin.control(jump_to="08:00")
    twin.control(city_supply_on=False)
    twin.step(6 * 60)
    dry = tank(twin)                             # 14:00 in the outage: nearly empty
    twin.control(jump_to="09:00")
    twin.control(city_supply_on=True)            # supply restored at 09:00 instead
    assert max(twin.timeline) == twin.sim_time
    twin.step(5 * 60)
    assert tank(twin) > dry + 30                 # this new future keeps far more water


def test_seek_forward_simulates_up_to_the_target(twin):
    twin.control(jump_to="08:00")
    twin.control(jump_to="11:00")
    assert twin.snapshot()["sim_time"].endswith("11:00:00")
    assert datetime(2026, 11, 23, 10, 59) in twin.timeline
    assert len([h for h in twin.history if h["t"] < "2026-11-23T11:00"]) > 100   # trends cover the gap
