"""XGBoost building-flow provider: same numbers as the notebook, and a labelled per-zone fallback."""
import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from app.synthetic import generate
from app.usage import DemandProvider
from app.xgb_provider import MODEL_DIR, MODEL_FILE, XGBDemandProvider

PRED = MODEL_DIR / "building_flow_test_predictions.csv"
SPLIT = pd.Timestamp("2026-11-16 00:00")   # the notebook's test split (meta split_local)


@pytest.fixture(scope="module")
def flows(b):
    return generate(b)          # identical to the seeded demo database the notebook was trained on


@pytest.fixture(scope="module")
def prov(b, flows):
    return XGBDemandProvider(b, flows)


def slot_values(p, z, times):
    m = p.model_zones[z]
    t0 = pd.Timestamp("2000-01-01") + pd.Timedelta(minutes=m["t0_min"])
    idx = ((times - t0) / pd.Timedelta(minutes=p.step)).astype(int).to_numpy()
    p._extend(z, idx.max() + 2)
    return p.model_zones[z]["vals"][idx]


def test_uses_the_model_for_every_demo_zone(prov):
    assert prov.kind == "xgboost" and set(prov.model_zones) == {"Z1", "Z2", "Z3"}
    assert prov.describe()["model_test"]["wape_day_ahead"] is not None
    assert all("XGBoost" in prov.flags[z][0] for z in prov.model_zones)


def test_day_ahead_matches_the_notebook_exactly(prov):
    nb = pd.read_csv(PRED, parse_dates=["local"])
    for z, g in nb.groupby("zone_id"):
        np.testing.assert_allclose(slot_values(prov, z, g["local"]), g["xgb"].to_numpy(), atol=1e-6)


def test_recursive_matches_the_notebook_exactly(b, flows):
    cut = XGBDemandProvider(b, flows[flows["ts"] + pd.Timedelta(hours=b.tz_offset_h) < SPLIT])
    nb = pd.read_csv(PRED, parse_dates=["local"])
    for z, g in nb.groupby("zone_id"):
        np.testing.assert_allclose(slot_values(cut, z, g["local"]), g["xgb_recursive"].to_numpy(), atol=1e-6)


def test_mean_interpolates_between_slot_centres(prov):
    m = prov.model_zones["Z1"]
    t0 = pd.Timestamp("2000-01-01") + pd.Timedelta(minutes=m["t0_min"])
    i = 2000
    centre = (t0 + pd.Timedelta(minutes=15 * i + 7.5)).to_pydatetime()
    assert prov.mean("Z1", centre) == pytest.approx(m["vals"][i])
    half = centre + pd.Timedelta(minutes=7.5)
    assert prov.mean("Z1", half) == pytest.approx((m["vals"][i] + m["vals"][i + 1]) / 2)
    _, sd = prov.mean_std("Z1", centre)
    assert sd == pytest.approx(prov.meta["sigma_m3h_by_hour"]["Z1"][centre.hour])


def test_runs_recursively_past_the_data_and_then_falls_back(prov, b, flows):
    end = (flows["ts"].max() + pd.Timedelta(hours=b.tz_offset_h)).to_pydatetime()
    for days in (1, 5, 20):
        v = prov.mean("Z2", end + pd.Timedelta(days=days))
        assert np.isfinite(v) and v >= 0
    base = DemandProvider(b, flows)
    far = end + pd.Timedelta(days=200)          # past MAX_AHEAD_DAYS: the baseline answers
    assert prov.mean("Z2", far) == pytest.approx(base.mean("Z2", far))
    first_week = (flows["ts"].min() + pd.Timedelta(hours=b.tz_offset_h + 24)).to_pydatetime()
    assert prov.mean("Z2", first_week) == pytest.approx(base.mean("Z2", first_week))   # no 7-day lags yet


def test_a_zone_the_model_never_saw_uses_the_baseline(b, flows):
    z4 = replace(b.zones[2], id="Z4", name="New floor")
    b4 = replace(b, zones=b.zones + (z4,), zone_by_id={**b.zone_by_id, "Z4": z4},
                 synthetic={**b.synthetic, "zones": {**b.synthetic["zones"], "Z4": b.synthetic["zones"]["Z3"]}})
    f4 = pd.concat([flows, flows[flows.zone_id == "Z3"].assign(zone_id="Z4")], ignore_index=True)
    p = XGBDemandProvider(b4, f4)
    assert p.kind == "mixed" and "Z4" not in p.model_zones and "Z4" in p.fallback
    t = pd.Timestamp("2026-11-23 12:00").to_pydatetime()
    assert p.mean("Z4", t) == pytest.approx(DemandProvider(b4, f4).mean("Z4", t))
    assert "Z4" in p.label and any("baseline" in f for f in p.flags["Z4"])


def test_without_a_usable_model_it_is_the_labelled_baseline(b, flows, tmp_path):
    p = XGBDemandProvider(b, flows, model_file=tmp_path / "missing.json")
    assert p.kind == "baseline" and not p.model_zones and "XGBoost not used" in p.label
    meta = json.loads((MODEL_DIR / "xgb_building_flow_meta.json").read_text(encoding="utf-8"))
    meta["features"] = meta["features"][:-1]
    bad = tmp_path / "meta.json"
    bad.write_text(json.dumps(meta), encoding="utf-8")
    p2 = XGBDemandProvider(b, flows, model_file=MODEL_FILE, meta_file=bad)
    assert p2.kind == "baseline" and "feature" in p2.load_error
