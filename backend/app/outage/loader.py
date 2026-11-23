"""F7.1 Outage data loader.

Reads the 7-sheet workbook once (about 40 s) and caches the six data tables in
SQLite next to it, so later starts take about a second. Delete outage.db to rebuild.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "outage"
XLSX = DATA_DIR / "water_outage_dummy_db_demo2.xlsx"
CACHE = DATA_DIR / "outage.db"
SHEETS = ["areas", "tanks", "area_tank_links", "hourly_usage_2022",
          "outage_scenarios", "scenario_assignments"]


@dataclass(frozen=True)
class OutageData:
    areas: pd.DataFrame
    tanks: pd.DataFrame
    links: pd.DataFrame
    hourly: pd.DataFrame
    scenarios: pd.DataFrame
    assignments: pd.DataFrame

    @property
    def tank_ids(self) -> list[str]:
        return self.tanks["tank_id"].tolist()


def _build_cache(xlsx: Path, cache: Path) -> None:
    sheets = pd.read_excel(xlsx, sheet_name=SHEETS)
    tmp = cache.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        for name, df in sheets.items():
            df.to_sql(name, con, index=False)
        con.commit()
    finally:
        con.close()
    tmp.replace(cache)


@lru_cache(maxsize=1)
def load(xlsx: Path = XLSX, cache: Path = CACHE) -> OutageData:
    if not cache.exists():
        if not xlsx.exists():
            raise FileNotFoundError(f"Outage database not found: {xlsx}")
        _build_cache(xlsx, cache)
    con = sqlite3.connect(f"file:{cache.as_posix()}?mode=ro", uri=True)
    try:
        t = {n: pd.read_sql(f"SELECT * FROM {n}", con) for n in SHEETS}
    finally:
        con.close()
    t["hourly_usage_2022"]["timestamp"] = pd.to_datetime(t["hourly_usage_2022"]["timestamp"])
    t["outage_scenarios"]["outage_start"] = pd.to_datetime(t["outage_scenarios"]["outage_start"])
    return OutageData(t["areas"], t["tanks"], t["area_tank_links"], t["hourly_usage_2022"],
                      t["outage_scenarios"], t["scenario_assignments"])
