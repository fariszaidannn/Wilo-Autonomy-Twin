"""Usage calculation and the baseline DemandProvider (PRD 10.3).

Forecast mean = mean of the same hour-of-week over the last N weeks, linearly
interpolated between hour centres; band = mean ± sigma. Fallbacks are flagged:
fewer than `min_samples` for an hour-of-week -> hour-of-day mean; no data for a
zone -> the configured synthetic shape.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .config import Building
from .synthetic import shape_value


def to_local(df: pd.DataFrame, b: Building) -> pd.DataFrame:
    out = df.copy()
    out["local"] = out["ts"] + pd.Timedelta(hours=b.tz_offset_h)
    return out


def hourly(df_local: pd.DataFrame) -> pd.DataFrame:
    """Mean flow per zone per local clock hour."""
    h = df_local.assign(hour_start=df_local["local"].dt.floor("h"))
    return h.groupby(["zone_id", "hour_start"], as_index=False)["flow_m3h"].mean()


class DemandProvider:
    """Swappable demand interface. `label` says what produced the numbers."""

    label = "Baseline: hour-of-week mean over the last 8 weeks (±1σ)"

    def __init__(self, b: Building, flows: pd.DataFrame | None, as_of_local: datetime | None = None):
        self.b = b
        fc = b.forecast
        self.flags: dict[str, list[str]] = {z.id: [] for z in b.zones}
        self.stats: dict[str, pd.DataFrame] = {}
        self.hod: dict[str, pd.DataFrame] = {}
        self.cv: dict[str, float] = {}
        if flows is not None and not flows.empty:
            h = hourly(to_local(flows, b))
            if as_of_local is not None:
                h = h[h["hour_start"] < pd.Timestamp(as_of_local)]
            if not h.empty:
                cutoff = h["hour_start"].max() - pd.Timedelta(weeks=fc["history_weeks"])
                h = h[h["hour_start"] > cutoff]
            h = h.assign(how=h["hour_start"].dt.day_of_week * 24 + h["hour_start"].dt.hour,
                         hod=h["hour_start"].dt.hour)
            for zid, g in h.groupby("zone_id"):
                self.stats[zid] = g.groupby("how")["flow_m3h"].agg(["mean", "std", "count"]).reindex(range(168))
                self.hod[zid] = g.groupby("hod")["flow_m3h"].agg(["mean", "std"]).reindex(range(24))
        for z in b.zones:
            st = self.stats.get(z.id)
            if st is None:
                self.flags[z.id].append("no data: using the configured synthetic shape")
                self.cv[z.id] = fc["cv_min"]
                continue
            sparse = int((st["count"].fillna(0) < fc["min_samples"]).sum())
            if sparse:
                self.flags[z.id].append(f"{sparse} hour-of-week slot(s) with < {fc['min_samples']} samples: "
                                        "using the hour-of-day mean")
            ok = st[(st["mean"] > 0.5) & (st["count"] >= 2)]
            ratio = (ok["std"] / ok["mean"]).median() if len(ok) else np.nan
            cv = fc["cv_min"] if np.isnan(ratio) else float(ratio)
            self.cv[z.id] = float(min(fc["cv_max"], max(fc["cv_min"], cv)))

        # Resolve fallbacks once into plain arrays: the twin asks for demand every simulated minute.
        self._mean: dict[str, list[float]] = {}
        self._std: dict[str, list[float]] = {}
        for zid, st in self.stats.items():
            sparse = st["count"].isna() | (st["count"] < fc["min_samples"])
            hod = self.hod[zid].fillna(0.0)
            hods = np.arange(168) % 24
            self._mean[zid] = np.where(sparse, hod["mean"].to_numpy()[hods], st["mean"].to_numpy()).tolist()
            self._std[zid] = np.where(sparse, hod["std"].to_numpy()[hods], st["std"].fillna(0).to_numpy()).tolist()

    def mean_std(self, zid: str, local: datetime) -> tuple[float, float]:
        if zid not in self._mean:
            m = shape_value(self.b, zid, local)
            return m, m * self.cv[zid]
        # Linear interpolation between hour centres (hh:30).
        pos = local - timedelta(minutes=30)
        w = (pos.minute * 60 + pos.second) / 3600
        h0 = pos.weekday() * 24 + pos.hour
        h1 = (h0 + 1) % 168
        mu, sd = self._mean[zid], self._std[zid]
        return (1 - w) * mu[h0] + w * mu[h1], (1 - w) * sd[h0] + w * sd[h1]

    def mean(self, zid: str, local: datetime) -> float:
        return self.mean_std(zid, local)[0]

    def forecast(self, start_local: datetime, hours: int = 24, step_min: int = 30) -> dict:
        pts = []
        t = pd.Timestamp(start_local)
        for i in range(int(hours * 60 / step_min) + 1):
            ti = t + pd.Timedelta(minutes=step_min * i)
            row = {"local": ti.isoformat()}
            tot_m = tot_v = 0.0
            for z in self.b.zones:
                m, s = self.mean_std(z.id, ti)
                row[z.id] = {"mean": m, "lo": max(0.0, m - s), "hi": m + s}
                tot_m += m
                tot_v += s * s
            row["total"] = {"mean": tot_m, "lo": max(0.0, tot_m - tot_v ** 0.5), "hi": tot_m + tot_v ** 0.5}
            pts.append(row)
        return {"label": self.label, "points": pts, "cv": self.cv, "flags": self.flags}


def summary(b: Building, flows: pd.DataFrame) -> dict:
    if flows.empty:
        return {"zones": {}, "total": None}
    df = to_local(flows, b).sort_values(["zone_id", "local"])
    dt_h = df.groupby("zone_id")["local"].diff().dt.total_seconds() / 3600
    dt_h = dt_h.fillna(dt_h.groupby(df["zone_id"]).transform("median")).clip(upper=1.0)
    df["vol"] = df["flow_m3h"] * dt_h
    zones = {}
    for zid, g in df.groupby("zone_id"):
        span_h = (g["local"].max() - g["local"].min()).total_seconds() / 3600 + float(dt_h[g.index].median())
        vol = float(g["vol"].sum())
        days = span_h / 24
        occ = float(g["occupancy"].mean()) if g["occupancy"].notna().any() else None
        i = g["flow_m3h"].idxmax()
        zones[zid] = {
            "total_m3": vol, "avg_m3h": vol / span_h, "days": days,
            "peak_m3h": float(g.loc[i, "flow_m3h"]), "peak_local": g.loc[i, "local"].isoformat(),
            "hourly_profile": g.groupby(g["local"].dt.hour)["flow_m3h"].mean().reindex(range(24)).fillna(0).tolist(),
            "weekday_profile": g.groupby(g["local"].dt.day_of_week)["flow_m3h"].mean().reindex(range(7)).fillna(0).tolist(),
            "avg_occupancy": occ,
            "l_per_person_day": (vol / days * 1000 / occ) if occ else None,
        }
    tot = df.groupby("local")["flow_m3h"].sum()
    total_vol = float(df["vol"].sum())
    span_days = max(z["days"] for z in zones.values())
    return {
        "zones": zones,
        "total": {"total_m3": total_vol, "avg_m3h": total_vol / (span_days * 24), "days": span_days,
                  "peak_m3h": float(tot.max()), "peak_local": tot.idxmax().isoformat(),
                  "m3_per_day": total_vol / span_days,
                  "hourly_profile": tot.groupby(tot.index.hour).mean().reindex(range(24)).fillna(0).tolist()},
        "range": {"first_local": df["local"].min().isoformat(), "last_local": df["local"].max().isoformat()},
    }


def backtest(b: Building, flows: pd.DataFrame, days: int = 7) -> dict:
    """Profile from data before the last `days` days; scored on those days, vs same hour yesterday."""
    if flows.empty:
        return {"zones": {}}
    h = hourly(to_local(flows, b))
    end = h["hour_start"].max() + pd.Timedelta(hours=1)
    split = end - pd.Timedelta(days=days)
    train = flows[flows["ts"] + pd.Timedelta(hours=b.tz_offset_h) < split]
    prov = DemandProvider(b, train)
    out = {}
    for zid, g in h.groupby("zone_id"):
        s = g.set_index("hour_start")["flow_m3h"]
        test = s[s.index >= split]
        if test.empty:
            continue
        pred, sd = zip(*[prov.mean_std(zid, t + pd.Timedelta(minutes=30)) for t in test.index])
        pred, sd = np.array(pred), np.array(sd)
        yest = s.reindex(test.index - pd.Timedelta(days=1)).to_numpy()
        a = test.to_numpy()
        mask = a > 0.5
        ym = ~np.isnan(yest)
        out[zid] = {
            "hours": int(len(a)),
            "mae": float(np.mean(np.abs(a - pred))),
            "mape": float(np.mean(np.abs(a[mask] - pred[mask]) / a[mask])) if mask.any() else None,
            "mae_yesterday": float(np.mean(np.abs(a[ym] - yest[ym]))) if ym.any() else None,
            "mape_yesterday": float(np.mean(np.abs(a[mask & ym] - yest[mask & ym]) / a[mask & ym]))
            if (mask & ym).any() else None,
            "within_1sigma": float(np.mean(np.abs(a - pred) <= sd)),
        }
    return {"days": days, "split_local": split.isoformat(), "label": prov.label, "zones": out}
