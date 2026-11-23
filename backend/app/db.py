"""SQLite store, mapping-driven UsageRepository and CSV import (PRD section 10).

An external database is always opened read-only. Table and column names from the
mapping are validated as plain identifiers; values are always bound parameters.
"""
from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .config import DATA_DIR, Building, validate_mapping

DB_FILE = DATA_DIR / "water_twin.db"
MAX_CSV_BYTES = 5_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_readings (ts TEXT, zone_id TEXT, flow_m3h REAL, volume_m3 REAL,
                                           occupancy REAL, source TEXT);
CREATE INDEX IF NOT EXISTS ix_usage ON usage_readings(zone_id, ts);
CREATE TABLE IF NOT EXISTS pump_readings (ts TEXT, pump_id TEXT, status TEXT, flow_m3h REAL,
                                          pressure_bar REAL, power_kw REAL, source TEXT);
CREATE TABLE IF NOT EXISTS tank_readings (ts TEXT, tank_id TEXT, level_pct REAL, volume_m3 REAL, source TEXT);
CREATE TABLE IF NOT EXISTS lb_actions (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, sim_time TEXT,
                                       action TEXT, plan_id TEXT, operator TEXT, note TEXT, plan_json TEXT);
"""


def connect(path: Path = DB_FILE) -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False)
    con.executescript(SCHEMA)
    return con


def _iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")


def insert_usage(con: sqlite3.Connection, df: pd.DataFrame) -> int:
    rows = [(_iso(r.ts), r.zone_id, float(r.flow_m3h),
             None if pd.isna(r.volume_m3) else float(r.volume_m3),
             None if pd.isna(r.occupancy) else float(r.occupancy), r.source) for r in df.itertuples()]
    con.executemany("INSERT INTO usage_readings VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    return len(rows)


def reseed_demo(con: sqlite3.Connection, b: Building) -> int:
    from .synthetic import generate

    con.execute("DELETE FROM usage_readings")
    return insert_usage(con, generate(b))


def ensure_seeded(con: sqlite3.Connection, b: Building) -> None:
    if con.execute("SELECT COUNT(*) FROM usage_readings").fetchone()[0] == 0:
        reseed_demo(con, b)


# ---------- conversion shared by every source ----------
def values_to_flows(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    """df has ts (UTC), zone_id, value. Returns ts, zone_id, flow_m3h (+ any extra columns)."""
    df = df.sort_values(["zone_id", "ts"]).reset_index(drop=True)
    if kind == "flow_m3h":
        return df.rename(columns={"value": "flow_m3h"})
    dt_h = df.groupby("zone_id")["ts"].diff().dt.total_seconds() / 3600
    if kind == "volume_m3":
        # Interval volume is reported at the interval end: q = v / dt. First row uses the median dt.
        med = dt_h.groupby(df["zone_id"]).transform("median")
        dt_h = dt_h.fillna(med)
        df["flow_m3h"] = df["value"] / dt_h
        return df.drop(columns="value").dropna(subset=["flow_m3h"])
    # cumulative meter: q = (c_t - c_t-1) / dt; negative differences (meter reset) are skipped
    dc = df.groupby("zone_id")["value"].diff()
    df["flow_m3h"] = dc / dt_h
    keep = dc.notna() & (dc >= 0) & (dt_h > 0)
    return df[keep].drop(columns="value").reset_index(drop=True)


class UsageRepository:
    """Interface: fetch_flows() -> DataFrame[ts (UTC), zone_id, flow_m3h, occupancy, source]."""

    label = "internal"

    def fetch_flows(self) -> pd.DataFrame:  # pragma: no cover - interface
        raise NotImplementedError


class SqliteUsageRepository(UsageRepository):
    label = "internal SQLite (data/water_twin.db)"

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def fetch_flows(self) -> pd.DataFrame:
        df = pd.read_sql("SELECT ts, zone_id, flow_m3h, occupancy, source FROM usage_readings", self.con)
        df["ts"] = pd.to_datetime(df["ts"])
        return df


class MappedUsageRepository(UsageRepository):
    """Reads your own SQLite file through db_mapping.json, read-only."""

    def __init__(self, mapping: dict, base_dir: Path = DATA_DIR):
        self.m = validate_mapping(dict(mapping))
        p = Path(self.m["db_path"])
        self.path = p if p.is_absolute() else (base_dir / p)
        self.label = f"external SQLite {self.path.name} (read-only, via db_mapping.json)"

    def fetch_flows(self) -> pd.DataFrame:
        m = self.m
        cols = [m["ts_col"], m["zone_col"], m["value_col"]] + ([m["occupancy_col"]] if m.get("occupancy_col") else [])
        sql = f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{m["table"]}"'
        con = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        try:
            raw = pd.read_sql(sql, con)
        finally:
            con.close()
        df = pd.DataFrame({"ts": raw[m["ts_col"]], "zone_id": raw[m["zone_col"]].astype(str),
                           "value": raw[m["value_col"]].astype(float)})
        df["occupancy"] = raw[m["occupancy_col"]].astype(float) if m.get("occupancy_col") else np.nan
        fmt = m["ts_format"]
        if fmt == "iso":
            ts = pd.to_datetime(df["ts"], utc=True)
        else:
            ts = pd.to_datetime(df["ts"].astype("int64"), unit="s" if fmt == "epoch_s" else "ms", utc=True)
        df["ts"] = ts.dt.tz_convert(None)
        aliases = m.get("zone_aliases") or {}
        df["zone_id"] = df["zone_id"].map(lambda z: aliases.get(z, z))
        out = values_to_flows(df, m["value_kind"])
        out["source"] = "MEASURED"
        return out[["ts", "zone_id", "flow_m3h", "occupancy", "source"]]


def make_repository(con: sqlite3.Connection, mapping: dict) -> UsageRepository:
    if mapping.get("enabled"):
        return MappedUsageRepository(mapping)
    return SqliteUsageRepository(con)


# ---------- CSV import ----------
def parse_csv(content: bytes, zone_ids: set[str]) -> tuple[pd.DataFrame, list[str]]:
    """Columns: ts, zone_id, flow_m3h OR volume_m3, optional occupancy, source. Raises ValueError."""
    if len(content) > MAX_CSV_BYTES:
        raise ValueError(f"CSV larger than {MAX_CSV_BYTES // 1_000_000} MB")
    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception as exc:
        raise ValueError(f"Could not read CSV: {exc}") from exc
    df.columns = [c.strip().lower() for c in df.columns]
    missing = {"ts", "zone_id"} - set(df.columns)
    if missing:
        raise ValueError(f"Missing column(s): {', '.join(sorted(missing))}")
    has_flow, has_vol = "flow_m3h" in df.columns, "volume_m3" in df.columns
    if has_flow == has_vol:
        raise ValueError("Give exactly one of flow_m3h or volume_m3")
    warnings = []
    ts = pd.to_datetime(df["ts"], errors="coerce", utc=True)
    bad_ts = int(ts.isna().sum())
    if bad_ts:
        warnings.append(f"{bad_ts} row(s) with an unreadable timestamp were skipped")
    vcol = "flow_m3h" if has_flow else "volume_m3"
    val = pd.to_numeric(df[vcol], errors="coerce")
    out = pd.DataFrame({"ts": ts.dt.tz_convert(None), "zone_id": df["zone_id"].astype(str).str.strip(), "value": val})
    out["occupancy"] = pd.to_numeric(df["occupancy"], errors="coerce") if "occupancy" in df.columns else np.nan
    src = df["source"].astype(str).str.upper().str.strip() if "source" in df.columns else "MEASURED"
    out["source"] = src
    out.loc[~out["source"].isin(["MEASURED", "SIMULATED"]), "source"] = "MEASURED"
    out = out.dropna(subset=["ts"])
    neg = int((out["value"] < 0).sum()) + int(out["value"].isna().sum())
    if neg:
        warnings.append(f"{neg} row(s) with a missing or negative value were skipped")
    out = out[out["value"].notna() & (out["value"] >= 0)]
    unknown = sorted(set(out["zone_id"]) - zone_ids)
    if unknown:
        warnings.append(f"Unknown zone id(s) skipped: {', '.join(unknown[:10])}")
        out = out[out["zone_id"].isin(zone_ids)]
    dups = int(out.duplicated(["ts", "zone_id"]).sum())
    if dups:
        warnings.append(f"{dups} duplicate timestamp(s) per zone were dropped")
        out = out.drop_duplicates(["ts", "zone_id"])
    if out.empty:
        raise ValueError("No valid rows after validation")
    flows = values_to_flows(out, "flow_m3h" if has_flow else "volume_m3")
    flows["volume_m3"] = np.nan
    return flows[["ts", "zone_id", "flow_m3h", "volume_m3", "occupancy", "source"]], warnings


def import_csv(con: sqlite3.Connection, content: bytes, zone_ids: set[str], replace_simulated: bool) -> dict:
    df, warnings = parse_csv(content, zone_ids)
    removed = 0
    if replace_simulated:
        for z in df["zone_id"].unique():
            removed += con.execute("DELETE FROM usage_readings WHERE source='SIMULATED' AND zone_id=?", (z,)).rowcount
    n = insert_usage(con, df)
    return {"imported_rows": n, "removed_simulated_rows": removed, "warnings": warnings,
            "zones": sorted(df["zone_id"].unique().tolist())}


def template_csv(zone_ids: list[str]) -> str:
    lines = ["ts,zone_id,flow_m3h,occupancy,source"]
    for z in zone_ids:
        lines += [f"2026-11-01T00:00:00Z,{z},4.2,35,MEASURED", f"2026-11-01T00:15:00Z,{z},4.0,35,MEASURED"]
    return "\n".join(lines) + "\n"


def data_label(sources: pd.Series) -> str:
    s = set(sources.unique())
    if s == {"MEASURED"}:
        return "MEASURED"
    if "MEASURED" in s:
        return "MIXED"
    return "SIMULATED"


# ---------- audit log ----------
def log_action(con: sqlite3.Connection, sim_time: datetime, action: str, plan_id: str | None,
               operator: str, note: str, plan: dict | None) -> dict:
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = con.execute("INSERT INTO lb_actions (created_at, sim_time, action, plan_id, operator, note, plan_json) "
                      "VALUES (?,?,?,?,?,?,?)",
                      (created, _iso(sim_time), action, plan_id, operator, note, json.dumps(plan) if plan else None))
    con.commit()
    return {"id": cur.lastrowid, "created_at": created, "sim_time": _iso(sim_time), "action": action,
            "plan_id": plan_id, "operator": operator, "note": note}


def read_log(con: sqlite3.Connection, limit: int = 50, with_plan: bool = False) -> list[dict]:
    rows = con.execute("SELECT id, created_at, sim_time, action, plan_id, operator, note, plan_json "
                       "FROM lb_actions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    keys = ["id", "created_at", "sim_time", "action", "plan_id", "operator", "note"]
    out = []
    for r in rows:
        d = dict(zip(keys, r[:7]))
        if with_plan and r[7]:
            d["plan"] = json.loads(r[7])
        out.append(d)
    return out
