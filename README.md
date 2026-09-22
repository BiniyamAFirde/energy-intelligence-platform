# Energy Intelligence Platform

A real-world building-energy intelligence platform: BDG2 data ingested into
PostgreSQL, served through a FastAPI backend with day-ahead ML forecasting
and multi-detector anomaly detection, and visualized in a React +
TypeScript dashboard.

> Status: **Phases 1-10 of 13 complete** -- data pipeline, REST API,
> analytics/EDA, leakage-safe day-ahead forecasting, four-detector anomaly
> detection, and the full dashboard are working end to end against real
> data, with a production-hardening pass on top. The React frontend is the
> newest layer (Phase 9); MQTT/IoT simulation and authentication remain
> out of scope. See `docs/architecture.md` for the layered design,
> `docs/eda.md` for what the data actually shows, `docs/forecasting.md`
> for the forecasting formulation and measured results, `docs/anomalies.md`
> for the four detectors and their evaluated performance, and
> `docs/data_selection.md` / `docs/data_quality_notes.md` for how the
> dataset was chosen and cleaned.

## Overview

Ingests real building-level electricity and weather data, stores it in
PostgreSQL behind a typed, read-only REST API, and layers two ML
subsystems on top: a leakage-safe day-ahead consumption forecaster and a
four-detector anomaly-detection system evaluated against a reproducible
synthetic benchmark. A React dashboard consumes that same API to make the
whole thing visually explorable -- consumption trends, building
comparisons, forecast-vs-actual, and anomaly monitoring/detail, all
backed by real data, not mocked responses.

## Architecture

```
BDG2 (Building Data Genome Project 2)
  |
  v
Ingestion / cleaning  (download, validate, dedupe, DST-aware UTC normalization)
  |
  v
PostgreSQL
  |
  +--> Analytics (EDA, comparisons, seasonality profiles)
  |
  +--> Forecasting  (train / predict, offline CLI jobs)
  |         |
  +--> Anomaly Detection  (evaluate / detect, offline CLI jobs)
  |         |
  v         v
     FastAPI  (read-only REST API)
            |
            v
     React + TypeScript Dashboard
```

Ingestion, model training, prediction generation, and anomaly
evaluation/detection all run as **offline CLI jobs** against the
database -- never inside an API request. The API and dashboard are purely
read-only consumers of what those jobs already wrote. See
`docs/architecture.md` for the full layering rationale and why that split
is deliberate.

## Technology

Python 3.11 / FastAPI / PostgreSQL 16 / SQLAlchemy 2.0 / Alembic / pandas
/ scikit-learn / matplotlib &nbsp;&middot;&nbsp; React 19 / TypeScript /
Vite / Recharts / react-router-dom &nbsp;&middot;&nbsp; Docker Compose

## Data

[Building Data Genome Project 2](https://github.com/buds-lab/building-data-genome-project-2)
(CC BY-SA 4.0; cite Miller et al., *Scientific Data* 7:368, 2020), verified
directly against the official repo, not the Kaggle mirror. Subset
selection is algorithmic and documented, not hand-picked -- see
`docs/data_selection.md`.

Currently loaded: **3 sites, 60 buildings, 1,052,400 hourly electricity
readings, 51,931 hourly weather readings** (2016-2017), all timestamps
normalized to UTC with explicit per-site-timezone DST handling
(`docs/data_quality_notes.md`).

## Quickstart

```bash
cp .env.example .env                 # first time only
docker compose up -d db
python3 scripts/download_data.py     # idempotent, ~187MB, SHA256-verified against BDG2
docker compose build backend
docker compose run --rm backend alembic upgrade head
docker compose run --rm backend python -m energy_platform.ingestion.pipeline   # ~5.5 min
docker compose up -d --build                                                  # backend + frontend
```

- API: `http://localhost:8000` -- interactive docs at `/docs` (Swagger) or `/redoc`
- Dashboard: `http://localhost:5173`

The dashboard's KPIs and charts will be empty until forecasting and
anomaly detection have been run at least once (see their sections below)
-- ingestion alone populates sites/buildings/energy/weather.

## API

Read-only REST API over the ingested data. Full endpoint table in
`docs/architecture.md`; highlights:

```bash
# List sites
curl http://localhost:8000/api/v1/sites

# Buildings at a site
curl "http://localhost:8000/api/v1/buildings?site_id=3&limit=2"

# A building's hourly consumption
curl "http://localhost:8000/api/v1/buildings/1/energy?limit=3"
# [{"ts":"2016-01-01T05:00:00Z","consumption_kwh":7.1667}, ...]

# Daily aggregated consumption (for charting)
curl "http://localhost:8000/api/v1/buildings/1/energy/aggregate?granularity=daily&limit=3"
# [{"period_start":"2016-01-01T00:00:00Z","total_kwh":134.8499,"mean_kwh":7.097,"observation_count":19}, ...]

# Summary statistics
curl "http://localhost:8000/api/v1/buildings/1/summary"

# 404 for a nonexistent resource
curl -i http://localhost:8000/api/v1/buildings/999999   # HTTP/1.1 404, {"detail":"building 999999 not found"}
```

### Analytics

```bash
# Compare all buildings: mean/median/total/peak kWh, area, coefficient of variation
curl "http://localhost:8000/api/v1/buildings/compare?site_id=1"

# A building's seasonality: hour-of-day / day-of-week / month / weekday-vs-weekend, all in local time
curl "http://localhost:8000/api/v1/buildings/1/analytics/profile"
```

Reproducible EDA report (JSON + 8 plots) straight from the database:

```bash
docker compose run --rm backend python -m energy_platform.analytics.report
```

Findings from the actual loaded BDG2 subset (not generic energy-domain
claims) are written up in `docs/eda.md` -- e.g. a clear occupancy-driven
hourly profile (peak/trough ratio ~1.33), a 14-17% weekday premium,
`area_sqm` correlating strongly with consumption (r=0.85), and
consistently weak (|r|<=0.30) linear weather correlation across all three
sites.

## Forecasting

Day-ahead hourly electricity forecasting: given everything known up to an
origin hour, predict the next 24 hourly values in one pass (horizon as an
explicit feature, not a recursive 24-step chain). Chronological
train(2016-01..2017-06)/validation(2017-07..09)/test(2017-10..12) split by
forecast **origin**, not target timestamp, so a single 24-hour forecast
never straddles a split boundary -- an earlier version of this project
split by target and had exactly that bug, corrected and documented in
`docs/forecasting.md`.

Seasonal-naive baselines (24h/168h) vs. Ridge (TimeSeriesSplit-tuned) vs.
Random Forest vs. HistGradientBoosting, plus a dedicated leakage test
suite (lag/rolling features never see future data, scalers fit on
training data only, prediction timestamps always precede their targets).
**Random Forest had the lowest validation RMSE among models that beat
both baselines and was selected** -- cutting RMSE from the best baseline's
65.9 kWh to 26.1 kWh (validation). Full results, the feature-availability
table, and honest limitations (including where the model fails) are in
`docs/forecasting.md`.

```bash
docker compose run --rm backend python -m energy_platform.forecasting.train    # ~6 min (RF fit dominates)
docker compose run --rm backend python -m energy_platform.forecasting.predict  # writes next 24h/building to `predictions`
docker compose run --rm backend python -m energy_platform.forecasting.plots
```

```bash
# Forecast vs. actual for one building, joined against real energy_measurements
curl "http://localhost:8000/api/v1/buildings/1/forecast?limit=5"
```

## Anomaly Detection

Four detectors, each with a genuinely different failure mode:

- **A -- Data quality**: rule-based (negative values, missing readings,
  zero-consumption runs, stuck meters, isolated spikes)
- **B -- Behavioral**: robust (median/MAD) z-score against that sensor's
  own same-hour-of-day history
- **C -- Forecast residual**: reuses Phase 7's Random Forest model,
  standardized `actual - predicted`; never retrains
- **D -- Isolation Forest**: the one genuinely multivariate detector, 10
  engineered features, fit on TRAIN-period data only

BDG2 has no real anomaly ground truth, so all four are evaluated against
a reproducible **synthetic injection framework** (six anomaly types,
seeded, never written into `energy_measurements`), with thresholds
selected on validation and **frozen** before being applied to test.
**Detector A had the highest validation F1 (0.234)**, though the honest
picture is per-anomaly-type, not a single ranking -- see
`docs/anomalies.md` section 5.

One real methodological bug was found and fixed along the way, not
papered over: a stuck-meter check's threshold initially produced a 35%
false-positive rate on real data, traced to ~21 of 60 BDG2 sensors
reporting daily-resolution readings (not meter faults) once reindexed to
an hourly grid -- fixed, regression-tested, and documented in
`docs/anomalies.md` section 3.

```bash
docker compose run --rm backend python -m energy_platform.anomalies.evaluate  # ~12-15 min: fits Isolation Forest, selects+freezes thresholds
docker compose run --rm backend python -m energy_platform.anomalies.detect    # ~6 min: runs all 4 detectors against real data, writes to `alerts`
docker compose run --rm backend python -m energy_platform.anomalies.plots
```

```bash
# List alerts, filterable by sensor/method/anomaly_type/severity/resolved/date-range
curl "http://localhost:8000/api/v1/alerts?method=behavioral&limit=3"

# Counts by method/anomaly_type/severity
curl "http://localhost:8000/api/v1/alerts/summary"
```

## Dashboard

React + TypeScript, five pages, all consuming the real API above (no mock
data anywhere in the frontend):

1. **Dashboard** -- portfolio KPIs, energy trend (hourly/daily/weekly/
   monthly), building ranking, hourly consumption profile, anomaly
   breakdowns by detector/severity, monthly anomaly trend
2. **Building Detail** -- metadata, KPIs, date-ranged historical
   consumption, hourly profile, recent anomalies for that building
3. **Forecasting** -- forecast vs. actual (visually distinct, dashed vs.
   solid), explicitly labeled as historical/backtested rather than live,
   horizon and residual per point, methodology summary
4. **Anomaly Monitoring** -- fully filterable alert table (building,
   detector, anomaly type, severity, resolved state, date range),
   severity/detector distributions, monthly timeline
5. **Anomaly Detail** -- full alert metadata, the real backend-generated
   explanation text (not generic UI copy), and a surrounding-consumption
   chart with the flagged point highlighted

## Testing

```bash
# Backend
docker compose run --rm backend pytest -v

# Frontend
cd frontend && npm run build && npm test
```

**Backend: 279 tests.** Unit tests for pure logic (download integrity,
timestamp normalization incl. DST edge cases, site/building selection,
parameter validation, statistics, feature engineering, seasonal-naive
baselines, evaluation metrics, the four anomaly detectors, the synthetic
injection framework, anomaly-evaluation metric math) plus integration
tests against a real, dedicated `energy_platform_test` PostgreSQL
database -- verified to build correctly from a completely fresh database
via `alembic upgrade head` alone, not assumed from a developer's existing
schema (repository queries, service logic, full API request/response
behavior, a dedicated forecasting-leakage suite, and an Isolation Forest
fit-contamination test that actually corrupts validation-period data and
confirms the TRAIN-fit model is unaffected, rather than asserting it by
convention).

**Frontend: 19 tests** (Vitest + React Testing Library) -- a focused set,
not exhaustive coverage: the typed API client's query building and error
handling, the pure formatting utilities, and loading/error/empty state
rendering (including that an `ApiError`'s backend-provided detail message
is shown in preference to a generic one).

## Running locally

```bash
docker compose up -d --build     # db + backend + frontend
docker compose ps                # confirm all three are healthy
```

`docker-compose.yml` wires health checks so the frontend only starts once
the backend is actually serving requests (not just "container started").
Backend and frontend both bind-mount their source for live reload during
development.

## Project structure

```
backend/src/energy_platform/
  db/             SQLAlchemy models + session
  ingestion/      download, validate, clean, select subset, load
  repositories/   DB queries only
  services/       business logic, validation, exceptions, statistics
  schemas/        Pydantic API contracts
  api/routers/    HTTP layer only
  analytics/      CLI EDA report + plots
  forecasting/    features, dataset, baselines, models, evaluation, train, predict, plots
  anomalies/      injection, 4 detectors, evaluation, evaluate, detect, plots
backend/tests/{unit,integration}/
backend/alembic/  migrations
frontend/src/
  api/            typed API client, one file per backend domain
  components/     shared UI (KpiCard, charts, DataTable, badges, state views)
  hooks/          useApi, useAnomalyMonthlyTrend
  pages/          Dashboard, BuildingDetail, Forecasting, AnomalyMonitoring, AnomalyDetail
scripts/          download_data.py, run_ingestion.py
data/{raw,processed,samples}/
models/           trained model artifacts (gitignored, regenerable via train.py / anomalies.evaluate)
reports/{eda,forecasting,anomalies}/  generated plots (gitignored, regenerable)
docs/
```

Layering is `router -> service -> repository -> SQLAlchemy/PostgreSQL`,
enforced by convention and reviewed in each phase -- see
`docs/architecture.md` for the full rationale, including the deliberate
split between offline batch jobs (ingestion/training/detection) and the
read-only online API.

## Engineering highlights

- **Temporal leakage prevention**, enforced by a dedicated test suite, not
  just claimed: forecast features split by origin (not target) so a
  single 24-hour forecast never straddles train/validation/test; lag and
  rolling features are shift-then-rolling so they can never see the
  current or a future value; scalers/encoders fit on training data only.
- **Idempotent everything that writes**: ingestion, prediction backfill,
  and alert generation all use `INSERT ... ON CONFLICT DO UPDATE` keyed on
  the natural uniqueness of the data, so re-running any job refreshes
  rather than duplicates -- and, for alerts specifically, a re-detection
  run can never silently reset a human's `resolved` triage state, since
  that column is deliberately excluded from the upsert's update set.
- **Database migrations, not a hand-shaped schema**: verified in this
  phase to build the complete, correct schema (10 tables, all indexes,
  constraints, and foreign keys) from `alembic upgrade head` against a
  genuinely empty database.
- **Model versioning is asymmetric on purpose**: only Isolation Forest (a
  genuinely fitted model) gets a `model_versions` row with seed,
  hyperparameters, and fit period; the three deterministic/statistical
  anomaly detectors are traced via `method` + `detector_version` instead,
  rather than fabricating meaningless training date ranges for something
  that was never trained.
- **Anomaly traceability**: every alert carries a concrete, value-derived
  explanation string generated by the detector itself (e.g. "634.58 kWh
  is 7873x the local median of 0.08 kWh"), not a generic "anomaly
  detected" message.
- **Reproducibility**: every number in `docs/*.md` comes from a real run
  of the corresponding CLI job against the loaded database -- not
  estimated, not fabricated -- and every generated report/plot is
  regenerable from source, not checked in as static output.

## Limitations

Kept honest rather than smoothed over:

- Forecast API coverage (`/buildings/{id}/forecast`) only spans
  2017-07-01 through 2018-01-01 -- the prediction backfill was scoped to
  the validation+test window Phase 8's evaluation needed, not the full
  2016-2018 history.
- Anomaly-detector precision is low in absolute terms (7-23% at best)
  against the synthetic benchmark; that's expected at <1% synthetic
  anomaly prevalence, and the false-positive-rate numbers in
  `docs/anomalies.md` are the more informative figures. A meaningful
  fraction of Detector A's "false positives" against the synthetic-only
  ground truth are real, pre-existing BDG2 data-quality issues the
  benchmark has no way to credit it for.
- Isolation Forest underperforms the other three detectors on this
  benchmark -- a real property of a multivariate detector evaluated
  against largely univariate injected anomalies, not a bug.
- No authentication -- this is a portfolio/demo application with a
  read-only API, not a multi-tenant production service.
- The unfiltered `/buildings/compare` endpoint (~2.3s) remains the one
  measurably expensive query; a materialized rollup table is the known
  fix if this ever needs to be faster, deliberately not built until it's
  actually needed.
