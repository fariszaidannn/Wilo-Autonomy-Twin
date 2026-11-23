"""Seeded demo usage (every row labelled SIMULATED). Shapes live in building.json -> synthetic."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .config import Building

# The twin's clock starts here (local time, a Monday). Demo history ends at this moment.
SIM_START = datetime(2026, 11, 23, 7, 0)


def shape_value(b: Building, zone_id: str, local: datetime) -> float:
    """Configured mean flow (m3/h) for a zone at a local time, interpolated between hour centres."""
    zc = b.synthetic["zones"][zone_id]
    pos = local.hour + local.minute / 60 - 0.5
    h0 = int(np.floor(pos)) % 24
    h1 = (h0 + 1) % 24
    w = pos - np.floor(pos)
    t0 = local - timedelta(hours=w)
    t1 = t0 + timedelta(hours=1)

    def at(h, t):
        f = zc["weekend_factor"] if t.weekday() >= 5 else 1.0
        return zc["shape"][h] * zc["peak_m3h"] * f

    return (1 - w) * at(h0, t0) + w * at(h1, t1)


def generate(b: Building, end_local: datetime = SIM_START) -> pd.DataFrame:
    s = b.synthetic
    rng = np.random.default_rng(s["seed"])
    step = timedelta(minutes=s["interval_min"])
    n = int(timedelta(weeks=s["weeks"]) / step)
    times = [end_local - step * (n - i) for i in range(n)]
    rows = []
    for z in b.zones:
        days = {}
        ar = 0.0
        for t in times:
            d = t.date()
            if d not in days:
                days[d] = rng.normal(1.0, 0.02)
            ar = 0.6 * ar + rng.normal(0, 0.025)
            mid = t + step / 2
            q = max(0.0, shape_value(b, z.id, mid) * days[d] * (1 + ar))
            occ_frac = 0.3 + 0.7 * shape_value(b, z.id, mid) / s["zones"][z.id]["peak_m3h"]
            rows.append((t - timedelta(hours=b.tz_offset_h), z.id, q, q * step.total_seconds() / 3600,
                         round(z.occupancy * min(1.0, occ_frac)), "SIMULATED"))
    return pd.DataFrame(rows, columns=["ts", "zone_id", "flow_m3h", "volume_m3", "occupancy", "source"])
