# Water Autonomy Twin (v0.2)

Water service continuity during outages, from a single building to Istanbul's 39 districts. Built from `PRD_Water_Autonomy_Twin.md`.

> **SIMULATED DATA. Decision support for operator review.** The app never sends commands to pumps or valves. "Apply" changes only the simulated allocation inside the twin. Only Istanbul's 2022 annual consumption and district population are real inputs, and no real-world accuracy is claimed.

## Run

```powershell
./run.ps1          # Windows; creates backend/.venv on first run
```
```sh
./run.sh           # macOS / Linux / Git Bash
```
Open http://127.0.0.1:8000 (API docs at `/docs`). Add `?lang=tr` for the Turkish UI, or use the EN/TR switch.

On the first start the app converts `water_outage_dummy_db_demo2.xlsx` to `outage.db` (about 40 s). It then runs your trained model over 2022 and evaluates the 48 test scenarios (about a minute). Both results are cached, so later starts take about 20 s before the planner is ready. The building twin is usable immediately.

Tests: `cd backend && .venv/Scripts/python -m pytest -q` runs 48 tests in about 2 minutes. Add `-m "not slow"` to skip the 48-scenario reproduction.

## What is in it

| Tab | What it does | PRD |
|---|---|---|
| **Building** | Live twin drawn as a line map: the tank is the origin, pumps are interchanges and zones are stations. Line width is flow and line colour is the feeding pump. Includes disturbance switches, the load balancer with before/after loads, five safety checks, confidence and operator steps, approve / dismiss / roll back / re-evaluate with an audit log, and live trends. | 8, 9, F2–F6 |
| **Scenario lab** | Normal, pump failure, power outage (with or without generator) and city supply cut. Shows time to threshold per zone (earliest / base / latest), with and without the balancer, plus preparation time, priority demand met, tank and pressure charts, and assumptions. | F3 |
| **Usage & data** | Totals, peak, L/person/day, hourly profile, a 24 h forecast with ±1σ band, and a back-test against "same hour yesterday". Shows the data source, with CSV import and a template. | 10 |
| **Outage planner** | Your XGBoost model plus the MILP. Pick a stored scenario or build an outage; areas are drawn as stations on their tank's line, and unserved areas are ringed in red. Includes the three-policy comparison, tank load vs level, and model evidence (policies, WAPE by duration and hour, actual vs predicted, gain importance, SHAP). | 7, F5.5, F7 |

## The district backbone uses your trained model

`backend/models/xgb_demand.json` and `xgb_demand_meta.json` are loaded as they are; nothing is retrained. The feature build is a line-for-line port of the notebook (`notebooks/xgboost_demand_forecast_colab.ipynb`, cells c07–c13). Predictions use `best_iteration`, as `XGBRegressor.predict` does. The test suite checks that the app reproduces your run:

- hourly predictions match `test_predictions.csv` exactly; hourly test WAPE is **5.38%** (XGBoost) vs **7.10%** (naive)
- window sums match `scenario_demand_forecast.csv`; scenario demand WAPE on the test split is **3.15%**
- Greedy matches the sheet's `greedy_tank_id` in all 393 scenarios
- unserved share on the 48 test scenarios is **Oracle 3.68% · MILP + XGBoost 3.80% · Greedy 4.44%** (4.28% with a 5% buffer)
- WAPE by outage duration matches the PRD 7.8 table

Honest pitch sentence (PRD 7.10): *"Our pipeline runs end to end and, on simulated data calibrated to 2022 consumption, the model beats the simple method at every outage duration."*

## Building twin: worked example (PRD 8.4)

Weekday 12:00 at 150% demand (Building tab → *12:00*, *All zones* 150%). Pump A is at 128% (overloaded) and Pump B at 23%. The balancer proposes moving 15.7 m³/h (59% of Z2) via XV-2. The predicted result is 76% / 76%, all five checks pass, and confidence is high. `tests/test_balancer.py::test_worked_example_8_4` pins these numbers.

## Layout

```
backend/app/        config, hydraulics (single physics step), usage, balancer (pure), simulator, twin, db, main
backend/app/outage/ loader (xlsx → SQLite cache), forecast (your model), allocation (MILP, greedy, real_unserved),
                    explain (gain + TreeSHAP), planner (API service)
backend/data/       building.json, db_mapping.json, outage/ (your workbook and CSVs)
backend/models/     your trained model
frontend/           vanilla HTML/CSS/JS + hand-drawn SVG, self-hosted Overpass font, no CDN
notebooks/          the training record
```

## Assumptions added during the build (flagged, not in the PRD)

- **Low-tank priority reserve** (`building.json → tank.priority_reserve_pct`): below 35% usable tank, priority-3 zones are isolated; below 15%, priority-2 zones too. This makes "who loses water first" reflect priority during a supply cut. Set it to `{}` to disable. Thresholds must come from the building's engineers.
- **Building parameters** (pump curves, friction coefficients, demand shapes) are tuned so that the PRD 8.4 worked example reproduces. They are not from a real building.
- **Planner map** is a diagram, not to scale: distances from the city centre are compressed, like a transit map.
- **The Turkish UI text** was written by the assistant and should be reviewed by a native speaker before the pitch.
