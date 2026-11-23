import sqlite3
from datetime import datetime

import pandas as pd
import pytest

from app import db, usage
from app.config import ConfigError
from app.synthetic import generate


@pytest.fixture(scope="module")
def flows(b):
    return generate(b)[["ts", "zone_id", "flow_m3h", "occupancy", "source"]]


def test_forecast_calibrated_to_worked_example(b, flows):
    p = usage.DemandProvider(b, flows)
    mid = datetime(2026, 11, 23, 12, 0)      # a Monday
    for zid, want in {"Z1": 7.9, "Z2": 17.7, "Z3": 4.6}.items():
        assert p.mean(zid, mid) == pytest.approx(want, rel=0.05)
    assert all(not f for f in p.flags.values())


def test_forecast_band_ordering(b, flows):
    fc = usage.DemandProvider(b, flows).forecast(datetime(2026, 11, 23, 7), hours=24)
    for pt in fc["points"]:
        for z in ("Z1", "Z2", "Z3", "total"):
            assert pt[z]["lo"] <= pt[z]["mean"] <= pt[z]["hi"]
    assert all(b.forecast["cv_min"] <= v <= b.forecast["cv_max"] for v in fc["cv"].values())


def test_fallback_to_synthetic_shape_when_zone_has_no_data(b, flows):
    p = usage.DemandProvider(b, flows[flows["zone_id"] != "Z3"])
    assert p.flags["Z3"] and "synthetic" in p.flags["Z3"][0]
    assert p.mean("Z3", datetime(2026, 11, 23, 7, 30)) > 0


def test_summary(b, flows):
    s = usage.summary(b, flows)
    z = s["zones"]["Z2"]
    assert z["days"] == pytest.approx(56, abs=0.1)
    assert z["avg_m3h"] == pytest.approx(z["total_m3"] / (z["days"] * 24))
    assert len(z["hourly_profile"]) == 24 and len(z["weekday_profile"]) == 7
    assert z["l_per_person_day"] > 0
    assert s["total"]["peak_m3h"] >= max(zz["peak_m3h"] for zz in s["zones"].values())


def test_backtest_beats_same_hour_yesterday(b, flows):
    bt = usage.backtest(b, flows)
    for z in bt["zones"].values():
        assert z["mape"] < 0.05
        assert z["mape"] < z["mape_yesterday"]
        assert 0 <= z["within_1sigma"] <= 1


def test_csv_import_and_header_validation(b, tmp_path):
    con = db.connect(tmp_path / "t.db")
    db.reseed_demo(con, b)
    csv = b"ts,zone_id,flow_m3h,occupancy\n2026-11-01T00:00:00Z,Z1,4.0,30\n2026-11-01T00:15:00Z,Z1,-1,30\n" \
          b"2026-11-01T00:15:00Z,ZX,3,1\nnot-a-time,Z1,2,2\n"
    res = db.import_csv(con, csv, {"Z1", "Z2", "Z3"}, replace_simulated=True)
    assert res["imported_rows"] == 1 and res["removed_simulated_rows"] > 0
    assert len(res["warnings"]) == 3
    left = con.execute("SELECT COUNT(*) FROM usage_readings WHERE zone_id='Z1' AND source='SIMULATED'").fetchone()[0]
    assert left == 0
    with pytest.raises(ValueError):
        db.parse_csv(b"time,zone,flow\n1,2,3\n", {"Z1"})
    with pytest.raises(ValueError):
        db.parse_csv(b"ts,zone_id,flow_m3h,volume_m3\n2026-01-01,Z1,1,1\n", {"Z1"})


def test_csv_volume_converted_to_flow(b):
    csv = b"ts,zone_id,volume_m3\n2026-11-01T00:00:00Z,Z1,1.0\n2026-11-01T00:15:00Z,Z1,1.5\n"
    df, _ = db.parse_csv(csv, {"Z1"})
    assert df["flow_m3h"].tolist() == pytest.approx([4.0, 6.0])


def test_external_db_cumulative_epoch_aliases(tmp_path):
    path = tmp_path / "ext.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE meter (t INTEGER, zone TEXT, reading REAL)")
    t0 = 1_763_000_000
    rows = [(t0 + i * 900, "ZONE-1", 100 + 2.0 * i) for i in range(5)]
    rows.append((t0 + 5 * 900, "ZONE-1", 0.5))           # meter reset: skipped
    con.executemany("INSERT INTO meter VALUES (?,?,?)", rows)
    con.commit()
    con.close()
    repo = db.MappedUsageRepository({"db_path": str(path), "table": "meter", "ts_col": "t", "zone_col": "zone",
                                     "value_col": "reading", "value_kind": "cumulative_m3", "ts_format": "epoch_s",
                                     "zone_aliases": {"ZONE-1": "Z1"}})
    df = repo.fetch_flows()
    assert set(df["zone_id"]) == {"Z1"}
    assert len(df) == 4 and df["flow_m3h"].tolist() == pytest.approx([8.0] * 4)
    assert df["ts"].iloc[0] == pd.Timestamp(t0 + 900, unit="s")
    assert (df["source"] == "MEASURED").all()


def test_unsafe_mapping_rejected():
    with pytest.raises(ConfigError):
        db.MappedUsageRepository({"db_path": "x", "table": "usage; DROP TABLE x", "ts_col": "ts", "zone_col": "z",
                                  "value_col": "v", "value_kind": "flow_m3h", "ts_format": "iso"})


def test_data_label():
    assert db.data_label(pd.Series(["SIMULATED"])) == "SIMULATED"
    assert db.data_label(pd.Series(["MEASURED"])) == "MEASURED"
    assert db.data_label(pd.Series(["MEASURED", "SIMULATED"])) == "MIXED"
