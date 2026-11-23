"""F7.4 / F7.5 Tank allocation MILP, Greedy baseline, and fair evaluation.

solve_milp and real_unserved are ports of notebook cell c32. An assignment is a
tank index per area; -1 means "not served".
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp

from .loader import OutageData

MILP_TIME_LIMIT_S = 30


class Network:
    """Distance, connectivity and priority lookups for the area/tank network."""

    def __init__(self, data: OutageData):
        self.tank_ids = data.tank_ids
        self.dist = data.links.pivot(index="area_id", columns="tank_id", values="distance_km")[self.tank_ids]
        self.conn = data.links.pivot(index="area_id", columns="tank_id", values="connected")[self.tank_ids]
        self.prio = data.areas.set_index("area_id")["priority_weight"]


def solve_milp(net: Network, area_ids, demand, level) -> np.ndarray:
    area_ids = list(area_ids)
    demand = np.asarray(demand, float)
    level = np.asarray(level, float)
    n, m = len(area_ids), len(net.tank_ids)
    if n == 0:
        return np.zeros(0, int)
    D = net.dist.loc[area_ids].to_numpy(float)
    C = net.conn.loc[area_ids].to_numpy(float)
    w = net.prio.loc[area_ids].to_numpy(float)
    # Serving reward weighted by priority dominates; distance only breaks ties.
    c = (demand[:, None] * D / 100 - 1000.0 * w[:, None] * demand[:, None]).ravel() / 1e3
    A1 = np.zeros((n, n * m))
    A2 = np.zeros((m, n * m))
    for i in range(n):
        A1[i, i * m:(i + 1) * m] = 1
        A2[np.arange(m), i * m + np.arange(m)] = demand[i]
    r = milp(c, constraints=[LinearConstraint(A1, -np.inf, 1), LinearConstraint(A2, -np.inf, level)],
             integrality=np.ones(n * m), bounds=Bounds(0, C.ravel()),
             options={"time_limit": MILP_TIME_LIMIT_S})
    if r.x is None:
        return np.full(n, -1)
    x = np.round(r.x.reshape(n, m)).astype(int)
    return np.where(x.sum(1) > 0, x.argmax(1), -1)


def greedy(net: Network, area_ids, demand, level) -> np.ndarray:
    """Largest demand first, to the nearest connected tank that still fits."""
    area_ids = list(area_ids)
    demand = np.asarray(demand, float)
    left = np.asarray(level, float).copy()
    assign = np.full(len(area_ids), -1)
    for i in np.argsort(-demand, kind="stable"):
        a = area_ids[i]
        order = np.argsort(net.dist.loc[a].to_numpy(float), kind="stable")
        for j in order:
            if net.conn.loc[a].iloc[j] == 1 and left[j] >= demand[i]:
                assign[i] = j
                left[j] -= demand[i]
                break
    return assign


def real_unserved(assign, actual, level) -> float:
    """Unserved demand judged on ACTUAL demand (PRD 7.7)."""
    assign = np.asarray(assign)
    actual = np.asarray(actual, float)
    un = actual[assign < 0].sum()
    for j in range(len(level)):
        load = actual[assign == j].sum()
        un += max(0.0, load - level[j])
    return float(un)


def scenario_levels(data: OutageData, scenario_id: str) -> np.ndarray:
    row = data.scenarios.set_index("scenario_id").loc[scenario_id]
    return np.array(json.loads(row["tank_levels_m3"]), float)


def evaluate(net: Network, data: OutageData, sa: pd.DataFrame, split: str = "test",
             buffer: float = 0.0) -> pd.DataFrame:
    """Three policies per scenario: Oracle, MILP + forecast, Greedy (from the sheet)."""
    out = []
    for sid, g in sa[sa["split"] == split].groupby("scenario_id"):
        level = scenario_levels(data, sid)
        ids, act = g["area_id"].tolist(), g["demand_m3"].to_numpy(float)
        fc = g["demand_forecast_m3"].to_numpy(float) * (1 + buffer)
        a_oracle = solve_milp(net, ids, act, level)
        a_fc = solve_milp(net, ids, fc, level)
        a_greedy = np.array([net.tank_ids.index(t) if t != "NONE" else -1 for t in g["greedy_tank_id"]])
        out.append({"scenario_id": sid, "duration_h": int(g["duration_h"].iloc[0]),
                    "total_demand": float(act.sum()),
                    "unserved_oracle": real_unserved(a_oracle, act, level),
                    "unserved_xgb": real_unserved(a_fc, act, level),
                    "unserved_greedy": real_unserved(a_greedy, act, level)})
    return pd.DataFrame(out)
