"""F7.7 Outage planner service used by the API.

A plan is built from the FORECAST demand and scored on the ACTUAL demand (PRD 7.7).
Nothing is executed: the result is a proposal for operator review.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from . import allocation as al
from .explain import importance_gain, shap_summary
from .forecast import Forecaster, get_forecaster, wape, window_sum
from .loader import DATA_DIR, OutageData, load

POLICIES = ("milp_xgb", "oracle", "greedy")
DURATIONS = (4, 6, 8, 12, 24)
DEFAULT_LEVEL_FRACTION = 0.475  # mean of U(0.25, 0.70) used by the scenario generator

LIMITS = [
    "All data is synthetic. Only 2022 annual consumption and district population are real inputs.",
    "Tanks, coordinates, distances, connectivity, hourly profiles, temperature, holidays and scenarios are simulation assumptions, not İSKİ operational data.",
    "Whole-area assignment only: no pipes, pressure or hydraulic loss. An area is never split across tanks.",
    "Tank level is used as available volume; the minimum fill level is not enforced (as in the notebook).",
    "One year of data; the test months (Nov and Dec) do not include summer.",
    "48 test scenarios: 3.80% vs 3.68% is a small gap, not 'the same'.",
    "Train-split scenarios are predicted in-sample; only test-split numbers are results.",
    "Greedy is computed with actual demand, so the comparison with it is conservative.",
]


class Planner:
    def __init__(self, data: OutageData, fc: Forecaster):
        self.data = data
        self.fc = fc
        self.net = al.Network(data)
        self.areas = data.areas.set_index("area_id")
        self.tanks = data.tanks.set_index("tank_id")
        self.scen = data.scenarios.set_index("scenario_id")
        # Scenario demand per area, built in the app (identical to scenario_demand_forecast.csv).
        meta = data.scenarios[["scenario_id", "outage_start", "duration_h", "split"]]
        sa = data.assignments.merge(meta, on="scenario_id")
        sa = sa[sa["outage_start"] >= fc.first_ts].copy()
        sa["demand_forecast_m3"] = [window_sum(fc.pred, a, s, d)
                                    for a, s, d in zip(sa.area_id, sa.outage_start, sa.duration_h)]
        self.sa = sa

    # ---------- listing ----------
    def scenarios(self) -> list[dict]:
        sids = set(self.sa["scenario_id"])
        rows = self.data.scenarios[self.data.scenarios["scenario_id"].isin(sids)]
        return [{
            "scenario_id": r.scenario_id, "split": r.split,
            "outage_start": r.outage_start.isoformat(), "duration_h": int(r.duration_h),
            "n_areas_affected": int(r.n_areas_affected), "total_demand_m3": float(r.total_demand_m3),
            "total_tank_available_m3": float(r.total_tank_available_m3), "all_served": bool(r.all_served),
        } for r in rows.itertuples()]

    def network(self) -> dict:
        a = self.data.areas
        t = self.data.tanks
        links = self.data.links[self.data.links["connected"] == 1]
        return {
            "areas": [{"area_id": r.area_id, "district": r.district, "lat": r.lat_approx, "lon": r.lon_approx,
                       "population": int(r.population_2022), "priority_weight": float(r.priority_weight),
                       "priority_class": r.priority_class, "hospitals": int(r.hospitals)} for r in a.itertuples()],
            "tanks": [{"tank_id": r.tank_id, "lat": r.lat_approx, "lon": r.lon_approx,
                       "capacity_m3": int(r.capacity_m3), "min_fill_pct": float(r.min_fill_pct),
                       "pump_station_m3h": int(r.pump_station_m3h),
                       "default_level_m3": round(r.capacity_m3 * DEFAULT_LEVEL_FRACTION)} for r in t.itertuples()],
            "links": [{"area_id": r.area_id, "tank_id": r.tank_id, "distance_km": float(r.distance_km)}
                      for r in links.itertuples()],
            "time_range": {"first": self.fc.first_ts.isoformat(), "last": self.fc.last_ts.isoformat()},
        }

    # ---------- planning ----------
    def plan(self, scenario_id: str | None = None, outage_start: str | None = None,
             duration_h: int | None = None, area_ids: list[str] | None = None,
             tank_levels_m3: list[float] | None = None, policy: str = "milp_xgb") -> dict:
        if policy not in POLICIES:
            raise ValueError(f"policy must be one of {POLICIES}")
        if scenario_id and area_ids is None and tank_levels_m3 is None:
            # A stored scenario is deterministic; the stock-short ones take seconds to solve.
            return {**self._stored_plan(scenario_id), "policy": policy,
                    **self._chosen(self._stored_plan(scenario_id), policy)}
        return self._plan(scenario_id, outage_start, duration_h, area_ids, tank_levels_m3, policy)

    @lru_cache(maxsize=64)
    def _stored_plan(self, scenario_id: str) -> dict:
        return self._plan(scenario_id, None, None, None, None, "milp_xgb")

    def _chosen(self, base: dict, policy: str) -> dict:
        """Re-point per-area / per-tank fields at another policy without solving again."""
        areas = []
        for a in base["areas"]:
            tid = a["by_policy"][policy]
            j = self.net.tank_ids.index(tid) if tid else -1
            areas.append({**a, "tank_id": tid,
                          "distance_km": float(self.net.dist.loc[a["area_id"]].iloc[j]) if j >= 0 else None})
        tanks = []
        for tk in base["tanks"]:
            mine = [a for a in areas if a["tank_id"] == tk["tank_id"]]
            act = sum(a["actual_m3"] for a in mine)
            tanks.append({**tk, "assigned_forecast_m3": sum(a["forecast_m3"] for a in mine),
                          "assigned_actual_m3": act, "overdraw_m3": max(0.0, act - tk["level_m3"])})
        return {"areas": areas, "tanks": tanks}

    def _plan(self, scenario_id, outage_start, duration_h, area_ids, tank_levels_m3, policy) -> dict:
        split = "custom"
        if scenario_id:
            if scenario_id not in set(self.sa["scenario_id"]):
                raise KeyError(f"Unknown scenario {scenario_id}")
            g = self.sa[self.sa["scenario_id"] == scenario_id]
            start = g["outage_start"].iloc[0]
            dur = int(g["duration_h"].iloc[0])
            split = g["split"].iloc[0]
            ids = g["area_id"].tolist() if area_ids is None else list(area_ids)
            level = al.scenario_levels(self.data, scenario_id) if tank_levels_m3 is None else None
        else:
            if outage_start is None or duration_h is None or not area_ids:
                raise ValueError("Give scenario_id, or outage_start, duration_h and area_ids")
            start, dur, ids, level = pd.Timestamp(outage_start).floor("h"), int(duration_h), list(area_ids), None
        if level is None:
            if tank_levels_m3 is None:
                level = self.data.tanks["capacity_m3"].to_numpy(float) * DEFAULT_LEVEL_FRACTION
            else:
                level = np.asarray(tank_levels_m3, float)
        self._check(start, dur, ids, level)

        fc = np.array([window_sum(self.fc.pred, a, start, dur) for a in ids])
        act = np.array([window_sum(self.fc.actual, a, start, dur) for a in ids])
        assigns = {
            "milp_xgb": al.solve_milp(self.net, ids, fc, level),
            "oracle": al.solve_milp(self.net, ids, act, level),
            "greedy": al.greedy(self.net, ids, act, level),
        }
        total = float(act.sum())
        comparison = {p: {"unserved_m3": al.real_unserved(a, act, level),
                          "unserved_share": al.real_unserved(a, act, level) / total if total else 0.0,
                          "areas_not_served": int((a < 0).sum())} for p, a in assigns.items()}
        chosen = assigns[policy]
        tid = self.net.tank_ids
        rows = []
        for i, a in enumerate(ids):
            j = int(chosen[i])
            ar = self.areas.loc[a]
            rows.append({
                "area_id": a, "district": ar["district"], "priority_class": ar["priority_class"],
                "priority_weight": float(ar["priority_weight"]),
                "forecast_m3": float(fc[i]), "actual_m3": float(act[i]),
                "tank_id": tid[j] if j >= 0 else None,
                "distance_km": float(self.net.dist.loc[a].iloc[j]) if j >= 0 else None,
                "by_policy": {p: (tid[int(v[i])] if v[i] >= 0 else None) for p, v in assigns.items()},
            })
        tanks = []
        for j, t in enumerate(tid):
            load_fc = float(fc[chosen == j].sum())
            load_act = float(act[chosen == j].sum())
            tanks.append({"tank_id": t, "level_m3": float(level[j]),
                          "capacity_m3": float(self.tanks.loc[t, "capacity_m3"]),
                          "assigned_forecast_m3": load_fc, "assigned_actual_m3": load_act,
                          "overdraw_m3": max(0.0, load_act - float(level[j]))})
        return {
            "scenario_id": scenario_id, "split": split, "policy": policy,
            "outage_start": start.isoformat(), "duration_h": dur,
            "forecast_model": self.fc.label, "forecast_uses_model": self.fc.uses_model,
            "forecast_wape": wape(act, fc) if total else None,
            "in_sample": split == "train" or (split == "custom" and start < self.fc.split_date),
            "total_forecast_m3": float(fc.sum()), "total_actual_m3": total,
            "total_level_m3": float(level.sum()),
            "areas": rows, "tanks": tanks, "comparison": comparison,
            "requires_operator_approval": True, "controls_real_pumps": False,
            "data_label": "SIMULATED", "limits": LIMITS,
        }

    def _check(self, start, dur, ids, level):
        if not 1 <= dur <= 24:
            raise ValueError("duration_h must be between 1 and 24 (all lags are at least 24 h)")
        if start < self.fc.first_ts or start + pd.Timedelta(hours=dur - 1) > self.fc.last_ts:
            raise ValueError(f"Outage must lie within {self.fc.first_ts:%Y-%m-%d %H:%M} and "
                             f"{self.fc.last_ts:%Y-%m-%d %H:%M} (the simulated 2022 history)")
        unknown = [a for a in ids if a not in self.areas.index]
        if unknown:
            raise ValueError(f"Unknown areas: {unknown}")
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate area ids")
        if len(level) != len(self.net.tank_ids) or (level < 0).any():
            raise ValueError(f"tank_levels_m3 needs {len(self.net.tank_ids)} non-negative values")
        cap = self.data.tanks["capacity_m3"].to_numpy(float)
        if (level > cap + 1e-6).any():
            raise ValueError("A tank level exceeds its capacity")

    # ---------- evaluation ----------
    @lru_cache(maxsize=1)
    def evaluation(self) -> dict:
        policies = {}
        for buf in (0.0, 0.05):
            ev = al.evaluate(self.net, self.data, self.sa, "test", buf)
            tot = ev["total_demand"].sum()
            policies[f"{buf:.2f}"] = {
                "oracle": ev["unserved_oracle"].sum() / tot,
                "milp_xgb": ev["unserved_xgb"].sum() / tot,
                "greedy": ev["unserved_greedy"].sum() / tot,
                "n_scenarios": int(len(ev)),
            }
        test = self.fc.test_frame()
        by_dur = []
        split = self.fc.split_date
        for dur in DURATIONS:
            t = test.assign(block=((test["timestamp"] - split) / pd.Timedelta(hours=dur)).astype(int))
            agg = t.groupby(["area_id", "block"], observed=True)[["usage_m3", "pred_m3", "naive_m3"]].sum()
            by_dur.append({"duration_h": dur, "wape_model": wape(agg.usage_m3, agg.pred_m3),
                           "wape_naive": wape(agg.usage_m3, agg.naive_m3)})
        by_hour = []
        for h, d in test.groupby("hour"):
            by_hour.append({"hour": int(h), "wape_model": wape(d.usage_m3, d.pred_m3),
                            "wape_naive": wape(d.usage_m3, d.naive_m3)})
        per_area = []
        for a, d in test.groupby("area_id", observed=True):
            per_area.append({"area_id": str(a), "district": self.areas.loc[str(a), "district"],
                             "wape_model": wape(d.usage_m3, d.pred_m3), "wape_naive": wape(d.usage_m3, d.naive_m3)})
        per_area.sort(key=lambda r: -r["wape_model"])
        scen_wape = {sp: {"wape": wape(x.demand_m3, x.demand_forecast_m3), "n_scenarios": int(x.scenario_id.nunique())}
                     for sp, x in self.sa.groupby("split")}
        return {
            "forecast_model": self.fc.label, "forecast_uses_model": self.fc.uses_model,
            "hourly_test": {"wape_model": wape(test.usage_m3, test.pred_m3),
                            "wape_naive": wape(test.usage_m3, test.naive_m3),
                            "mae_model": float((test.usage_m3 - test.pred_m3).abs().mean()),
                            "mae_naive": float((test.usage_m3 - test.naive_m3).abs().mean())},
            "wape_by_duration": by_dur, "wape_by_hour": by_hour, "per_area": per_area,
            "scenario_demand_wape": scen_wape, "policies_by_buffer": policies,
            "final_policy": "milp_xgb with 0% buffer (5% buffer tested and rejected)",
            "importance_gain": importance_gain(self.fc), "shap": shap_summary(self.fc),
            "data_label": "SIMULATED", "limits": LIMITS,
        }

    def series(self, area_id: str, start: str, hours: int = 168) -> dict:
        """Hourly actual vs predicted vs naive for one area, for the actual-vs-predicted chart."""
        s = pd.Timestamp(start)
        d = self.fc.df[(self.fc.df["area_id"] == area_id) & (self.fc.df["timestamp"] >= s)].head(hours)
        return {"area_id": area_id, "district": self.areas.loc[area_id, "district"],
                "points": [{"ts": r.timestamp.isoformat(), "actual": r.usage_m3, "pred": r.pred_m3,
                            "naive": r.naive_m3} for r in d.itertuples()]}


@lru_cache(maxsize=1)
def get_planner() -> Planner:
    return Planner(load(), get_forecaster())


__all__ = ["Planner", "get_planner", "POLICIES", "DATA_DIR"]
