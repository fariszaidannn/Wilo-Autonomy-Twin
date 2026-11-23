"""F7.2 / F7.3 Demand forecaster (the user's trained XGBoost model) and window_sum.

Feature engineering is a line-for-line port of notebook cells c07-c13. The model is
only loaded and used for prediction here; training stays in the notebook.
If xgboost or the model file is missing, the forecaster falls back to the naive
"same hour last week" (lag_168) baseline and says so in `label`.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .loader import OutageData, load

MODEL_DIR = Path(__file__).resolve().parents[2] / "models"
MODEL_FILE = MODEL_DIR / "xgb_demand.json"
META_FILE = MODEL_DIR / "xgb_demand_meta.json"

MODEL_LABEL = "XGBoost (trained offline, xgb_demand.json)"
FALLBACK_LABEL = "Baseline: same hour last week (model unavailable)"


def wape(actual, pred) -> float:
    actual, pred = np.asarray(actual, float), np.asarray(pred, float)
    return float(np.abs(actual - pred).sum() / np.abs(actual).sum())


def build_features(data: OutageData, meta: dict) -> pd.DataFrame:
    static, lags = meta["static"], meta["lags"]
    df = data.hourly.merge(data.areas[["area_id"] + static], on="area_id", how="left")
    df["area_id"] = df["area_id"].astype("category")
    df = df.sort_values(["area_id", "timestamp"]).reset_index(drop=True)

    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["sin_hour"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["cos_hour"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["sin_dow"] = np.sin(2 * np.pi * df["day_of_week"] / 7)
    df["cos_dow"] = np.cos(2 * np.pi * df["day_of_week"] / 7)
    df["temp_above18"] = (df["temperature_c"] - 18).clip(lower=0)

    # Every lag is at least 24 h, so all features are known when an outage (<= 24 h) starts.
    g = df.groupby("area_id", observed=True)["usage_m3"]
    df["lag_24"] = g.shift(24)
    df["lag_48"] = g.shift(48)
    df["lag_168"] = g.shift(168)
    s24 = g.shift(24)
    by = s24.groupby(df["area_id"], observed=True)
    df["roll_mean_24"] = by.transform(lambda s: s.rolling(24).mean())
    df["roll_mean_168"] = by.transform(lambda s: s.rolling(168).mean())
    df["roll_std_24"] = by.transform(lambda s: s.rolling(24).std())
    df = df.dropna(subset=["lag_168", "roll_mean_168"]).reset_index(drop=True)

    # area_mean comes from the meta file (computed on train only in the notebook).
    df["area_mean"] = df["area_id"].astype(str).map(meta["area_mean"]).astype(float)
    for c in lags:
        df[c + "_n"] = df[c] / df["area_mean"]
    return df


class Forecaster:
    def __init__(self, data: OutageData, model_file: Path = MODEL_FILE, meta_file: Path = META_FILE):
        self.meta = json.loads(meta_file.read_text(encoding="utf-8"))
        self.features: list[str] = self.meta["features"]
        self.split_date = pd.Timestamp(self.meta["split_date"])
        self.df = build_features(data, self.meta)
        self.booster = None
        self.label = FALLBACK_LABEL
        self.uses_model = False
        try:
            import xgboost as xgb

            booster = xgb.Booster()
            booster.load_model(str(model_file))
            self.booster = booster
            self.label = MODEL_LABEL
            self.uses_model = True
        except Exception as exc:  # missing package or file -> labelled baseline
            self.load_error = str(exc)

        if self.uses_model:
            self.df["pred_m3"] = self.predict_rows(self.df) * self.df["area_mean"].to_numpy()
        else:
            self.df["pred_m3"] = self.df["lag_168"]
        self.df["naive_m3"] = self.df["lag_168"]
        idx = self.df.set_index(["area_id", "timestamp"]).sort_index()
        self.pred = idx["pred_m3"]
        self.actual = idx["usage_m3"]
        self.first_ts = self.df["timestamp"].min()
        self.last_ts = self.df["timestamp"].max()

    def dmatrix(self, rows: pd.DataFrame):
        import xgboost as xgb

        return xgb.DMatrix(rows[self.features], enable_categorical=True)

    @property
    def iteration_range(self) -> tuple[int, int]:
        # XGBRegressor.predict stops at best_iteration; a raw Booster would use every tree.
        return (0, int(self.booster.attr("best_iteration")) + 1)

    def predict_rows(self, rows: pd.DataFrame) -> np.ndarray:
        return self.booster.predict(self.dmatrix(rows), iteration_range=self.iteration_range)

    def test_frame(self) -> pd.DataFrame:
        return self.df[self.df["timestamp"] >= self.split_date]


def window_sum(series: pd.Series, area: str, start: pd.Timestamp, dur: int) -> float:
    """Sum of hourly values for `area` over [start, start + dur - 1 h] (PRD 7.5)."""
    return float(series.loc[(area, slice(start, start + pd.Timedelta(hours=dur - 1)))].sum())


@lru_cache(maxsize=1)
def get_forecaster() -> Forecaster:
    return Forecaster(load())
