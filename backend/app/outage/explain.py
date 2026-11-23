"""F7.6 Explainability: gain importance and SHAP values.

SHAP comes from XGBoost's own TreeSHAP (`pred_contribs=True`), so the `shap`
package is not needed. SHAP shows what the model uses, not a real-world cause.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .forecast import Forecaster

SAMPLE_ROWS = 3000
SEED = 42


def importance_gain(fc: Forecaster, top: int = 15) -> list[dict]:
    if not fc.uses_model:
        return []
    s = pd.Series(fc.booster.get_score(importance_type="gain"))
    s = (s / s.sum()).sort_values(ascending=False).head(top)
    return [{"feature": k, "share": float(v)} for k, v in s.items()]


def shap_summary(fc: Forecaster, top: int = 10, points: int = 250) -> list[dict]:
    """Mean |SHAP| per feature plus a small sample of (value rank, shap) points for a beeswarm."""
    if not fc.uses_model:
        return []
    sample = fc.test_frame().sample(SAMPLE_ROWS, random_state=SEED)
    contrib = fc.booster.predict(fc.dmatrix(sample), pred_contribs=True,
                                 iteration_range=fc.iteration_range)[:, :-1]  # last column = bias
    mean_abs = np.abs(contrib).mean(0)
    order = np.argsort(-mean_abs)[:top]
    rng = np.random.default_rng(SEED)
    pick = rng.choice(len(sample), size=min(points, len(sample)), replace=False)
    out = []
    for j in order:
        name = fc.features[j]
        col = sample[name]
        rank = (col.cat.codes if name == "area_id" else col).rank(pct=True).to_numpy()
        out.append({
            "feature": name,
            "mean_abs_shap": float(mean_abs[j]),
            "points": [[round(float(rank[i]), 3), round(float(contrib[i, j]), 4)] for i in pick],
        })
    return out
