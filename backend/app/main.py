"""REST API + static frontend. Interactive docs at /docs.

Every response that carries advice states requires_operator_approval: true and
controls_real_pumps: false. There is no endpoint that commands real equipment.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, simulator, usage
from .config import BUILDING_FILE, DEFAULT_BUILDING_FILE, ConfigError, check_raw, load_building, load_mapping
from .twin import StalePlan, Twin
from .xgb_provider import make_provider

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
EVAL_CACHE = Path(__file__).resolve().parents[1] / "data" / "outage" / "cache" / "evaluation.json"


class Control(BaseModel):
    pump_failed: list[str] | None = None
    grid_on: bool | None = None
    generator_available: bool | None = None
    city_supply_on: bool | None = None
    demand_mult: float | None = Field(None, ge=0, le=2.5)
    zone_mult: dict[str, float] | None = None
    speed: float | None = Field(None, gt=0, le=120)
    running: bool | None = None
    jump_to: str | None = Field(None, pattern=r"^\d{1,2}:\d{2}$")
    jump_day: str | None = Field(None, pattern="^(weekday|weekend)$")


class StepBody(BaseModel):
    minutes: int = Field(1, ge=1, le=1440)


class Decision(BaseModel):
    plan_id: str | None = None
    operator: str = Field("operator", min_length=1, max_length=60)
    note: str = Field("", max_length=500)


class ScenarioBody(BaseModel):
    scenario: str = Field(pattern="^(normal|pump_failure|power_outage|city_supply_cut)$")
    horizon_h: float = Field(12, ge=1, le=48)
    pump: str | None = None
    generator: bool = False


class PlanBody(BaseModel):
    scenario_id: str | None = None
    outage_start: str | None = None
    duration_h: int | None = Field(None, ge=1, le=24)
    area_ids: list[str] | None = None
    tank_levels_m3: list[float] | None = None
    policy: str = Field("milp_xgb", pattern="^(milp_xgb|oracle|greedy)$")


class AppState:
    def __init__(self, start_clock: bool = True, warm_outage: bool = True, db_path: Path = db.DB_FILE,
                 building_path: Path = BUILDING_FILE):
        self.building_path = Path(building_path)
        self.start_clock = start_clock
        self.config_lock = threading.Lock()
        self.b = load_building(self.building_path)
        self.con = db.connect(db_path)
        db.ensure_seeded(self.con, self.b)
        self.mapping = load_mapping()
        self.repo = db.make_repository(self.con, self.mapping)
        self.flows = self.repo.fetch_flows()
        self.provider = make_provider(self.b, self.flows)
        self.twin = Twin(self.b, self.con, self.provider)
        if start_clock:
            self.twin.start()
        self.outage = {"status": "loading", "message": "Loading outage database and model…",
                       "planner": None, "evaluation": None, "error": None}
        if warm_outage:
            threading.Thread(target=self._warm_outage, daemon=True, name="outage-warmup").start()

    def reload_usage(self):
        self.repo = db.make_repository(self.con, self.mapping)
        self.flows = self.repo.fetch_flows()
        self.provider = make_provider(self.b, self.flows)
        self.twin.set_provider(self.provider)

    def apply_building(self, raw: dict) -> list[str]:
        """Validate, back up the old file, save, and rebuild everything that depends on the topology."""
        errors, warnings, b = check_raw(raw)
        if errors:
            raise ConfigError("; ".join(errors), errors)
        with self.config_lock:
            backup = self.building_path.with_name("building.backup.json")
            if self.building_path.exists():
                backup.write_text(self.building_path.read_text(encoding="utf-8"), encoding="utf-8")
            self.building_path.write_text(json.dumps(b.raw, indent=2, ensure_ascii=False), encoding="utf-8")
            self.b = b
            # Demo (simulated) usage is regenerated for the new zones; measured data is kept as it is.
            if self.flows.empty or db.data_label(self.flows["source"]) == "SIMULATED":
                db.reseed_demo(self.con, b)
            self.repo = db.make_repository(self.con, self.mapping)
            self.flows = self.repo.fetch_flows()
            self.provider = make_provider(b, self.flows)
            old = self.twin
            self.twin = Twin(b, self.con, self.provider)
            old.stop()
            if self.start_clock:
                self.twin.start()
        return warnings

    def _warm_outage(self):
        try:
            from .outage.forecast import MODEL_FILE
            from .outage.loader import CACHE, XLSX
            from .outage.planner import get_planner

            t = time.time()
            self.outage["message"] = ("Building the outage cache from the workbook (first start only, about 40 s)…"
                                      if not CACHE.exists() else "Loading outage data and running the model…")
            p = get_planner()
            self.outage.update(planner=p, status="ready", message=f"Ready in {time.time() - t:.0f} s")
            key = f"{MODEL_FILE.stat().st_mtime_ns}-{XLSX.stat().st_size}"
            cached = json.loads(EVAL_CACHE.read_text(encoding="utf-8")) if EVAL_CACHE.exists() else {}
            if cached.get("cache_key") == key:
                self.outage["evaluation"] = cached
            else:
                self.outage["eval_message"] = "Running the 48-scenario test evaluation (about a minute)…"
                ev = p.evaluation()
                ev["cache_key"] = key
                EVAL_CACHE.parent.mkdir(parents=True, exist_ok=True)
                EVAL_CACHE.write_text(json.dumps(ev), encoding="utf-8")
                self.outage["evaluation"] = ev
            # Warm the two stock-short test scenarios (slow MILPs) so the planner opens instantly.
            for sid in ("S391", "S380"):
                p.plan(scenario_id=sid)
        except Exception as exc:   # surface the reason in the UI instead of failing the app
            if self.outage["planner"] is None:
                self.outage.update(status="error", error=f"{type(exc).__name__}: {exc}",
                                   message="The outage planner is unavailable.")
            else:
                self.outage["eval_message"] = f"Evaluation failed: {type(exc).__name__}: {exc}"


def create_app(start_clock: bool = True, warm_outage: bool = True, db_path: Path = db.DB_FILE,
               building_path: Path = BUILDING_FILE) -> FastAPI:
    holder: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        holder["s"] = AppState(start_clock, warm_outage, db_path, building_path)
        yield
        holder["s"].twin.stop()

    app = FastAPI(title="Water Autonomy Twin", version="0.2",
                  description="SIMULATED decision support. Never controls pumps or valves.", lifespan=lifespan)

    def S() -> AppState:
        return holder["s"]

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        if int(request.headers.get("content-length") or 0) > db.MAX_CSV_BYTES + 100_000:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
        return await call_next(request)

    # ---------- twin ----------
    @app.get("/api/state")
    def state():
        return S().twin.snapshot()

    @app.get("/api/history")
    def history(last: int = 240):
        h = list(S().twin.history)
        return {"samples": h[-max(1, min(last, 1440)):]}


    @app.post("/api/twin/control")
    def control(c: Control):
        return S().twin.control(**c.model_dump())


    @app.post("/api/twin/step")
    def step(body: StepBody):
        return S().twin.step(body.minutes)

    @app.post("/api/twin/reset")
    def reset():
        S().twin.reset()
        return S().twin.snapshot()

    # ---------- load balancer ----------

    @app.get("/api/loadbalancer")
    def lb():
        return S().twin.balancer_view()

    @app.post("/api/loadbalancer/evaluate")
    def lb_eval():
        return S().twin.evaluate_now()

    @app.post("/api/loadbalancer/apply")
    def lb_apply(d: Decision):
        if not d.plan_id:
            raise HTTPException(422, "plan_id is required")
        try:
            return {"logged": S().twin.apply(d.plan_id, d.operator, d.note), "state": S().twin.snapshot()}
        except StalePlan as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/loadbalancer/dismiss")
    def lb_dismiss(d: Decision):
        try:
            return {"logged": S().twin.dismiss(d.plan_id, d.operator, d.note), "state": S().twin.snapshot()}
        except StalePlan as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/loadbalancer/rollback")
    def lb_rollback(d: Decision):
        try:
            return {"logged": S().twin.rollback(d.operator, d.note), "state": S().twin.snapshot()}
        except StalePlan as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/loadbalancer/log")
    def lb_log(limit: int = 100):
        return {"entries": db.read_log(S().con, max(1, min(limit, 500)), with_plan=True)}

    # ---------- scenario lab ----------

    @app.post("/api/scenarios/run")
    def run_scenario(body: ScenarioBody):
        s = S()
        if body.pump and body.pump not in s.b.pump_by_id:
            raise HTTPException(422, "Unknown pump")
        return simulator.run(s.b, s.twin.provider, s.twin.scenario_start(), body.scenario, body.horizon_h,
                             {"pump": body.pump, "generator": body.generator})

    outlook_cache: dict = {}
    outlook_lock = threading.Lock()

    @app.get("/api/outlook")
    def outage_outlook(event: str = "city_supply_cut", window_h: int = 12):
        """Outage outlook for the Building tab, recomputed from the live twin state."""
        if event not in simulator.OUTLOOK_EVENTS:
            raise HTTPException(422, f"event must be one of {list(simulator.OUTLOOK_EVENTS)}")
        if window_h not in (6, 12, 24):
            raise HTTPException(422, "window_h must be 6, 12 or 24")
        s = S()
        twin, prov = s.twin, s.twin.provider
        start = twin.scenario_start()
        ctl = {k: v for k, v in start["controls"].items() if k not in ("speed", "running")}
        # Everything the result depends on: building, demand model, the plant state and the event.
        key = (id(twin.b), id(prov), event, window_h, start["sim_time"], repr(start["state"]),
               json.dumps(ctl, sort_keys=True), json.dumps(start["allocation"], sort_keys=True))
        with outlook_lock:
            hit = outlook_cache.get(key)
        if hit is not None:
            return hit[2]
        result = simulator.outlook(twin.b, prov, start, event, window_h)
        with outlook_lock:
            if len(outlook_cache) >= 16:
                outlook_cache.pop(next(iter(outlook_cache)))
            # Holding the building and provider keeps their id() from being reused while cached.
            outlook_cache[key] = (twin.b, prov, result)
        return result

    # ---------- usage and data ----------
    @app.get("/api/usage/summary")
    def usage_summary():
        return usage.summary(S().b, S().flows)

    @app.get("/api/usage/forecast")
    def usage_forecast(hours: int = 24):
        s = S()
        return s.provider.forecast(s.twin.sim_time, max(1, min(hours, 72)))

    @app.get("/api/usage/accuracy")
    def usage_accuracy():
        return usage.backtest(S().b, S().flows)

    @app.get("/api/data/status")
    def data_status():
        s = S()
        f = s.flows
        per_zone = {z: {"rows": int(len(g)), "sources": sorted(g["source"].unique().tolist())}
                    for z, g in f.groupby("zone_id")} if not f.empty else {}
        return {"label": db.data_label(f["source"]) if not f.empty else "NONE", "rows": int(len(f)),
                "first_utc": f["ts"].min().isoformat() if not f.empty else None,
                "last_utc": f["ts"].max().isoformat() if not f.empty else None,
                "repository": s.repo.label, "mapping_enabled": bool(s.mapping.get("enabled")),
                "per_zone": per_zone, "flags": s.provider.flags, "demand_label": s.provider.label}

    @app.post("/api/data/import-csv")
    async def import_csv(file: UploadFile = File(...), replace_simulated: bool = Form(False)):
        s = S()
        if s.mapping.get("enabled"):
            raise HTTPException(409, "An external database is mapped (read-only); import is disabled.")
        content = await file.read()
        try:
            res = db.import_csv(s.con, content, set(s.b.zone_by_id), replace_simulated)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        s.reload_usage()
        return res

    @app.post("/api/data/reseed-demo")
    def reseed():
        s = S()
        n = db.reseed_demo(s.con, s.b)
        s.reload_usage()
        return {"rows": n, "label": "SIMULATED"}

    @app.get("/api/data/template.csv")
    def template():
        return PlainTextResponse(db.template_csv([z.id for z in S().b.zones]), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=usage_template.csv"})

    @app.get("/api/config")
    def config():
        return S().b.raw

    # ---------- water-system editor (topology and connections) ----------
    @app.post("/api/config/validate")
    def config_validate(raw: dict):
        errors, warnings, _ = check_raw(raw)
        return {"ok": not errors, "errors": errors, "warnings": warnings}

    @app.post("/api/config")
    def config_save(raw: dict):
        try:
            warnings = S().apply_building(raw)
        except ConfigError as exc:
            raise HTTPException(422, exc.args[1] if len(exc.args) > 1 else [str(exc)])
        return {"ok": True, "warnings": warnings, "config": S().b.raw}

    @app.post("/api/config/restore")
    def config_restore():
        if not DEFAULT_BUILDING_FILE.exists():
            raise HTTPException(404, "The demo building file is missing.")
        warnings = S().apply_building(json.loads(DEFAULT_BUILDING_FILE.read_text(encoding="utf-8")))
        return {"ok": True, "warnings": warnings, "config": S().b.raw}

    # ---------- outage planner (district) ----------
    def planner():
        o = S().outage
        if o["status"] != "ready":
            raise HTTPException(503, o["error"] or o["message"])
        return o["planner"]

    @app.get("/api/outage/status")
    def outage_status():
        o = S().outage
        return {"status": o["status"], "message": o["message"], "error": o["error"],
                "evaluation_ready": o["evaluation"] is not None, "eval_message": o.get("eval_message")}

    @app.get("/api/outage/scenarios")
    def outage_scenarios():
        return {"scenarios": planner().scenarios()}

    @app.get("/api/outage/network")
    def outage_network():
        return planner().network()


    @app.post("/api/outage/plan")
    def outage_plan(body: PlanBody):
        p = planner()
        try:
            return p.plan(**body.model_dump())
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'"))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.get("/api/outage/evaluation")
    def outage_evaluation():
        planner()
        ev = S().outage["evaluation"]
        if ev is None:
            raise HTTPException(503, S().outage.get("eval_message") or "Evaluation is still running.")
        return ev

    @app.get("/api/outage/series")
    def outage_series(area_id: str = "A15", start: str = "2022-11-01", hours: int = 168):
        p = planner()
        if area_id not in p.areas.index:
            raise HTTPException(404, "Unknown area")
        return p.series(area_id, start, max(24, min(hours, 24 * 31)))

    # ---------- frontend ----------
    if FRONTEND.exists():
        app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(FRONTEND / "index.html")

    return app


app = create_app()
