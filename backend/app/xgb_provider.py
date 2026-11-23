"""XGBoost demand provider for the building (trained in notebooks/xgboost_building_flow_15min.ipynb).

It has the same interface as usage.DemandProvider (mean, mean_std, cv, flags, label, forecast),
so the live twin, the load balancer, the scenario lab, the outage outlook and the Usage forecast
all use it without further changes. The model is only loaded and used for prediction here.

Per zone:
- inside the meter history: the model's day-ahead prediction (every lag is at least one day old,
  so a slot's prediction only uses data that existed a day before it);
- after the last reading: recursive, one day at a time; each day's predictions become the lags
  of the following days (the "recursive" mode the notebook evaluated);
- a zone the model was not trained on, a zone with under 7 days of history, a time before the
  model has its 7-day lags, or more than MAX_AHEAD_DAYS past the data: the baseline, flagged.
"""
from __future__ import annotations

import json
import math
import threading
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Building
from .usage import DemandProvider, to_local

MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
MODEL_FILE = MODEL_DIR / "xgb_building_flow.json"
META_FILE = MODEL_DIR / "xgb_building_flow_meta.json"
MAX_AHEAD_DAYS = 60          # recursion drifts towards a smooth profile; past this the baseline takes over
EPOCH = datetime(2000, 1, 1)

# The feature list this module computes, in the notebook's order. A model trained on anything
# else is refused rather than fed mismatched columns.
FEATURES = ["zone_id", "slot", "hour", "day_of_week", "is_weekend", "sin_slot", "cos_slot", "sin_dow", "cos_dow",
            "lag_1d_n", "lag_2d_n", "lag_7d_n", "roll_mean_1d_n", "roll_std_1d_n", "roll_mean_7d_n", "slot_mean_7d_n"]
ROLLS = ["roll_mean_1d", "roll_std_1d", "roll_mean_7d", "slot_mean_7d"]


def add_features(df: pd.DataFrame, step_min: int, zones: list[str], zone_mean: dict) -> pd.DataFrame:
    """Exactly the notebook's add_features. df: local, zone_id, flow_m3h on a gap-free 15-min grid per
    zone (NaN = not known)."""
    D = 24 * 60 // step_min
    lag_steps = {"lag_1d": D, "lag_2d": 2 * D, "lag_7d": 7 * D}
    lags = list(lag_steps) + ROLLS
    df = df[["local", "zone_id", "flow_m3h"]].copy()
    df["zone_id"] = df["zone_id"].astype(str)
    df = df.sort_values(["zone_id", "local"]).reset_index(drop=True)
    t = df["local"]
    df["slot"] = t.dt.hour * (60 // step_min) + t.dt.minute // step_min
    df["hour"] = t.dt.hour
    df["day_of_week"] = t.dt.day_of_week
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["sin_slot"] = np.sin(2 * np.pi * df["slot"] / D)
    df["cos_slot"] = np.cos(2 * np.pi * df["slot"] / D)
    df["sin_dow"] = np.sin(2 * np.pi * df["day_of_week"] / 7)
    df["cos_dow"] = np.cos(2 * np.pi * df["day_of_week"] / 7)

    g = df.groupby("zone_id")["flow_m3h"]
    for name, k in lag_steps.items():
        df[name] = g.shift(k)
    df["slot_mean_7d"] = pd.concat([g.shift(k * D) for k in range(1, 8)], axis=1).mean(axis=1)
    gs = g.shift(D).groupby(df["zone_id"])
    df["roll_mean_1d"] = gs.transform(lambda s: s.rolling(D, min_periods=D // 2).mean())
    df["roll_std_1d"] = gs.transform(lambda s: s.rolling(D, min_periods=D // 2).std())
    df["roll_mean_7d"] = gs.transform(lambda s: s.rolling(7 * D, min_periods=7 * D // 2).mean())

    df["zone_mean"] = df["zone_id"].map(zone_mean).astype(float)
    for c in lags:
        df[c + "_n"] = df[c] / df["zone_mean"]
    df["zone_id"] = pd.Categorical(df["zone_id"], categories=zones)
    return df


def flow_grid(flows: pd.DataFrame, b: Building, step_min: int, zone_ids) -> pd.DataFrame:
    """Readings on a gap-free local 15-min grid per zone, as in the notebook (missing slot = NaN)."""
    freq = f"{step_min}min"
    loc = to_local(flows[flows["zone_id"].isin(list(zone_ids))], b)
    frames = []
    for z, g in loc.groupby("zone_id"):
        s = g.assign(local=g["local"].dt.floor(freq)).groupby("local")["flow_m3h"].mean()
        grid = pd.date_range(s.index.min(), s.index.max(), freq=freq)
        frames.append(pd.DataFrame({"local": grid, "zone_id": z, "flow_m3h": s.reindex(grid).to_numpy()}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["local", "zone_id", "flow_m3h"])


class XGBDemandProvider(DemandProvider):
    """XGBoost where the model knows the zone and has history; the baseline everywhere else."""

    def __init__(self, b: Building, flows: pd.DataFrame | None, model_file: Path = MODEL_FILE,
                 meta_file: Path = META_FILE):
        super().__init__(b, flows)          # the baseline: per-zone fallback and the fallback if loading fails
        self.baseline_label = DemandProvider.label
        self.model_zones: dict[str, dict] = {}
        self.fallback: dict[str, str] = {}
        self.load_error: str | None = None
        self._lock = threading.Lock()
        try:
            self._load(b, flows, Path(model_file), Path(meta_file))
        except Exception as exc:            # missing package, file or incompatible model -> labelled baseline
            self.load_error = f"{type(exc).__name__}: {exc}"
            self.model_zones = {}
        self.kind = ("xgboost" if self.model_zones and not self.fallback else
                     "mixed" if self.model_zones else "baseline")
        self._set_label()

    # ---------- loading ----------
    def _load(self, b, flows, model_file, meta_file):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        step = int(meta["step_min"])
        D = 24 * 60 // step
        if meta["features"] != FEATURES or meta["lag_steps"] != {"lag_1d": D, "lag_2d": 2 * D, "lag_7d": 7 * D}:
            raise ValueError("the model's features do not match this app's feature builder; retrain with the notebook")
        import xgboost as xgb

        booster = xgb.Booster()
        booster.load_model(str(model_file))
        best = booster.attr("best_iteration")
        self._booster = booster
        self._xgb = xgb
        self._range = (0, int(best if best is not None else meta["best_iteration"]) + 1)
        self.meta = meta
        self.step = step
        self.D = D
        self.cats = list(meta["categories"]["zone_id"])
        self.zone_mean = {z: float(v) for z, v in meta["zone_mean"].items()}
        self.tz_note = None if int(meta.get("tz_offset_h", b.tz_offset_h)) == b.tz_offset_h else (
            f"model trained on UTC+{meta['tz_offset_h']}, building clock is UTC+{b.tz_offset_h}")

        building = [z.id for z in b.zones]
        for z in building:
            if z not in self.cats:
                self.fallback[z] = f"the model was not trained on {z} (it knows {', '.join(self.cats)}); retrain"
        usable = [z for z in building if z in self.cats]
        if flows is None or flows.empty or not usable:
            for z in usable:
                self.fallback[z] = "no meter data to compute the model's lags"
            return
        grid = flow_grid(flows, b, step, usable)
        for z in usable:
            n = int(grid.loc[grid["zone_id"] == z, "flow_m3h"].notna().sum())
            if n <= 7 * D:
                self.fallback[z] = f"under 7 days of readings ({n} slots); the model needs 7-day lags"
        usable = [z for z in usable if z not in self.fallback]
        if not usable:
            return
        grid = grid[grid["zone_id"].isin(usable)]
        feats = add_features(grid, step, self.cats, self.zone_mean)
        pred = self._predict(feats)
        pred[feats["lag_7d"].isna().to_numpy()] = np.nan     # the model was trained only on rows with lag_7d
        feats["pred"] = pred
        for z in usable:
            f = feats[feats["zone_id"] == z]
            hist = grid[grid["zone_id"] == z]
            last = hist.loc[hist["flow_m3h"].notna(), "local"].max()
            t0 = f["local"].iloc[0]
            known = hist[(hist["local"] <= last) & (hist["local"] > last - pd.Timedelta(days=9))]
            vals = f.loc[f["local"] <= last, "pred"].to_numpy(float)
            self.model_zones[z] = {
                "t0_min": (t0.to_pydatetime() - EPOCH).total_seconds() / 60,
                "vals": vals, "known": known[["local", "zone_id", "flow_m3h"]].reset_index(drop=True),
                "next": last + pd.Timedelta(minutes=step), "last": last,
                "max_slots": len(vals) + MAX_AHEAD_DAYS * D,
                "sigma": [float(x) for x in meta["sigma_m3h_by_hour"][z]],
            }
            self.cv[z] = float(meta["cv"][z])
            self.flags[z] = [f"XGBoost: day-ahead inside the meter data (to {last:%d %b %H:%M}), "
                             "recursive after it" + (f"; {self.tz_note}" if self.tz_note else "")]
            self._extend(z, len(vals) + 3 * D)              # covers a 48 h scenario from the end of the data

    def _predict(self, rows: pd.DataFrame) -> np.ndarray:
        dm = self._xgb.DMatrix(rows[FEATURES], enable_categorical=True)
        return np.clip(self._booster.predict(dm, iteration_range=self._range) * rows["zone_mean"].to_numpy(), 0, None)

    def _extend(self, z: str, need_slots: int):
        """Predict whole days after the data until `need_slots` slots exist (thread-safe)."""
        m = self.model_zones[z]
        with self._lock:
            while len(m["vals"]) < min(need_slots, m["max_slots"]):
                times = pd.date_range(m["next"], periods=self.D, freq=f"{self.step}min")
                block = pd.DataFrame({"local": times, "zone_id": z, "flow_m3h": np.nan})
                f = add_features(pd.concat([m["known"], block], ignore_index=True), self.step, self.cats,
                                 self.zone_mean)
                rows = f.iloc[-self.D:]
                p = self._predict(rows)
                block["flow_m3h"] = p
                m["known"] = pd.concat([m["known"], block], ignore_index=True).iloc[-9 * self.D:].reset_index(drop=True)
                m["vals"] = np.concatenate([m["vals"], p])   # replaced whole, so readers never see half a day
                m["next"] = times[-1] + pd.Timedelta(minutes=self.step)

    # ---------- the provider interface ----------
    def mean_std(self, zid: str, local: datetime) -> tuple[float, float]:
        m = self.model_zones.get(zid)
        if m is None:
            return super().mean_std(zid, local)
        # Slot values are means over [start, start + 15 min); interpolate between slot centres.
        pos = ((local - EPOCH).total_seconds() / 60 - m["t0_min"] - self.step / 2) / self.step
        i = math.floor(pos)
        if i < 0:
            return super().mean_std(zid, local)
        if i + 1 >= len(m["vals"]):
            self._extend(zid, i + 2 + self.D)
            if i + 1 >= len(m["vals"]):                      # beyond MAX_AHEAD_DAYS
                return super().mean_std(zid, local)
        vals = m["vals"]
        v0, v1 = vals[i], vals[i + 1]
        if np.isnan(v0) or np.isnan(v1):                     # first week of the data: no 7-day lags yet
            return super().mean_std(zid, local)
        w = pos - i
        return (1 - w) * float(v0) + w * float(v1), m["sigma"][local.hour]

    def describe(self) -> dict:
        test = None
        mt = (getattr(self, "meta", None) or {}).get("metrics_test") or {}
        w = {k: v.get("WAPE 15 menit") for k, v in mt.items()}
        pick = lambda word: next((v for k, v in w.items() if word in k.lower()), None)
        if self.model_zones and w:
            test = {"wape_day_ahead": pick("day-ahead"), "wape_recursive": pick("rekursif") or pick("recursive"),
                    "wape_baseline": pick("baseline"), "range": self.meta.get("test_range_local")}
        return {"kind": self.kind, "model_zones": list(self.model_zones), "fallback_zones": dict(self.fallback),
                "model_test": test}

    def _set_label(self):
        if self.kind == "xgboost":
            self.label = "XGBoost 15-min model (xgb_building_flow.json), recursive after the last reading; band ±1σ of its errors"
        elif self.kind == "mixed":
            self.label = (f"XGBoost for {', '.join(self.model_zones)}; baseline for {', '.join(self.fallback)} "
                          "(hour-of-week mean over the last 8 weeks, ±1σ)")
        else:
            why = self.load_error or "; ".join(sorted(set(self.fallback.values()))) or "no model"
            self.label = f"{self.baseline_label} (XGBoost not used: {why})"
        for z, why in self.fallback.items():
            self.flags.setdefault(z, []).append(f"baseline: {why}")


def make_provider(b: Building, flows: pd.DataFrame | None) -> DemandProvider:
    return XGBDemandProvider(b, flows)
