"""Outage planner: the app must reproduce the user's notebook run (PRD 7.8) from the saved model."""
import numpy as np
import pandas as pd
import pytest

from app.outage import allocation as al
from app.outage.forecast import window_sum, wape
from app.outage.loader import DATA_DIR
from app.outage.planner import get_planner


@pytest.fixture(scope="module")
def p():
    return get_planner()


@pytest.fixture(scope="module")
def notebook_scen():
    return pd.read_csv(DATA_DIR / "scenario_demand_forecast.csv", parse_dates=["outage_start"])


def test_model_loaded_not_fallback(p):
    assert p.fc.uses_model and "XGBoost" in p.fc.label


def test_predictions_match_notebook(p):
    nb = pd.read_csv(DATA_DIR / "test_predictions.csv", parse_dates=["timestamp"])
    app = p.fc.test_frame()[["timestamp", "area_id", "pred_m3", "naive_m3"]].assign(
        area_id=lambda x: x["area_id"].astype(str))
    m = nb.merge(app, on=["timestamp", "area_id"], suffixes=("_nb", "_app"))
    assert len(m) == len(nb) == 39 * 1464
    assert np.allclose(m["pred_m3_app"], m["pred_m3_nb"], rtol=1e-4)
    assert np.allclose(m["naive_m3_app"], m["naive_m3_nb"])


def test_window_sums_match_notebook(p, notebook_scen):
    mine = p.sa.set_index(["scenario_id", "area_id"])["demand_forecast_m3"]
    theirs = notebook_scen.set_index(["scenario_id", "area_id"])["demand_forecast_m3"]
    assert len(mine) == len(theirs) == 8999
    assert np.allclose(mine.loc[theirs.index], theirs, rtol=1e-4)
    act = [window_sum(p.fc.actual, a, s, d) for a, s, d in
           zip(notebook_scen.area_id, notebook_scen.outage_start, notebook_scen.duration_h)]
    assert np.allclose(act, notebook_scen["demand_m3"], rtol=1e-3)


def test_window_sum_boundaries(p):
    a, s = "A05", pd.Timestamp("2022-11-10 06:00")
    one = p.fc.actual.loc[(a, s)]
    assert window_sum(p.fc.actual, a, s, 1) == pytest.approx(one)
    four = sum(p.fc.actual.loc[(a, s + pd.Timedelta(hours=h))] for h in range(4))
    assert window_sum(p.fc.actual, a, s, 4) == pytest.approx(four)


def test_greedy_matches_sheet(p):
    bad = 0
    for sid, g in p.sa.groupby("scenario_id"):
        a = al.greedy(p.net, g.area_id.tolist(), g.demand_m3.to_numpy(), al.scenario_levels(p.data, sid))
        ids = [p.net.tank_ids[j] if j >= 0 else "NONE" for j in a]
        bad += ids != g.greedy_tank_id.tolist()
    assert bad == 0


def test_milp_respects_connectivity_and_tank_levels(p):
    for sid in ("S380", "S001", "S232"):
        g = p.sa[p.sa.scenario_id == sid]
        lv = al.scenario_levels(p.data, sid)
        ids, d = g.area_id.tolist(), g.demand_m3.to_numpy()
        a = al.solve_milp(p.net, ids, d, lv)
        for i, j in enumerate(a):
            if j >= 0:
                assert p.net.conn.loc[ids[i]].iloc[j] == 1
        for j in range(len(lv)):
            assert d[a == j].sum() <= lv[j] + 1e-6


def test_unservable_area_left_unserved(p):
    ids = ["A16"]
    lv = np.full(len(p.net.tank_ids), 10.0)
    assert al.solve_milp(p.net, ids, np.array([5000.0]), lv).tolist() == [-1]


def test_real_unserved_hand_case():
    assign = np.array([0, 0, 1, -1])
    actual = np.array([60.0, 50.0, 30.0, 20.0])
    level = np.array([100.0, 40.0])
    # 20 not served + max(0, 110 - 100) on tank 0 + 0 on tank 1
    assert al.real_unserved(assign, actual, level) == pytest.approx(30.0)


def test_plan_api_shape_and_validation(p):
    r = p.plan(outage_start="2022-12-10 08:00", duration_h=6, area_ids=["A01", "A16", "A21"])
    assert r["controls_real_pumps"] is False and r["requires_operator_approval"] is True
    assert {a["area_id"] for a in r["areas"]} == {"A01", "A16", "A21"}
    assert set(r["comparison"]) == {"milp_xgb", "oracle", "greedy"}
    with pytest.raises(ValueError):
        p.plan(outage_start="2022-01-02 00:00", duration_h=6, area_ids=["A01"])   # inside the dropped first week
    with pytest.raises(ValueError):
        p.plan(outage_start="2022-12-10 08:00", duration_h=30, area_ids=["A01"])


def test_stored_plan_policy_switch_is_consistent(p):
    for pol in ("milp_xgb", "oracle", "greedy"):
        r = p.plan(scenario_id="S380", policy=pol)
        assert r["policy"] == pol
        assert all(a["tank_id"] == a["by_policy"][pol] for a in r["areas"])
        for tk in r["tanks"]:
            want = sum(a["actual_m3"] for a in r["areas"] if a["tank_id"] == tk["tank_id"])
            assert tk["assigned_actual_m3"] == pytest.approx(want)


@pytest.mark.slow
def test_evaluation_reproduces_section_7_8(p):
    ev = p.evaluation()
    b0, b5 = ev["policies_by_buffer"]["0.00"], ev["policies_by_buffer"]["0.05"]
    assert b0["n_scenarios"] == 48
    assert (round(100 * b0["oracle"], 2), round(100 * b0["milp_xgb"], 2), round(100 * b0["greedy"], 2)) == \
        (3.68, 3.80, 4.44)
    assert round(100 * b5["milp_xgb"], 2) == 4.28
    want = {4: (4.20, 5.50), 6: (3.75, 4.91), 8: (3.39, 4.43), 12: (2.99, 3.85), 24: (2.31, 2.86)}
    for row in ev["wape_by_duration"]:
        m, n = want[row["duration_h"]]
        assert round(100 * row["wape_model"], 2) == pytest.approx(m, abs=0.01)
        assert round(100 * row["wape_naive"], 2) == pytest.approx(n, abs=0.01)
    assert round(100 * ev["scenario_demand_wape"]["test"]["wape"], 2) == 3.15
    assert ev["importance_gain"][0]["feature"] == "lag_168_n"
    assert wape([1, 1], [1, 1]) == 0
