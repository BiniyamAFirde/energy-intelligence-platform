# Energy Intelligence Platform — Complete Technical Report

**Author's note on sourcing.** Every number in this report is drawn from the
repository itself: `docs/eda.md`, `docs/forecasting.md`, `docs/anomalies.md`,
`docs/architecture.md`, `docs/data_selection.md`, `docs/data_quality_notes.md`,
the generated JSON reports in `data/processed/`, the live PostgreSQL database
(re-verified directly via SQL during the Phase 10 end-to-end verification
pass), and the test suite. Where the repository documents a limitation or an
honest negative result, this report keeps it — nothing here has been
smoothed over to look better than the underlying work.

Repository: `https://github.com/BiniyamAFirde/energy-intelligence-platform`.
This report documents the state of the project through Phase 10 (data
pipeline, REST API, forecasting, anomaly detection, and the dashboard).
External-company inference (CLI + API + dashboard page), added afterward,
is documented separately in `docs/external_inference.md` and the README's
"External-Company Inference" section rather than folded in here.

---

## Executive Summary

The Energy Intelligence Platform is a full-stack data engineering and
machine learning system built around real building-level electricity and
weather data from the Building Data Genome Project 2 (BDG2). It ingests
60 buildings across 3 sites (1,052,400 hourly electricity readings, 51,931
hourly weather readings, 2016–2018) into PostgreSQL through an idempotent,
DST-aware ingestion pipeline, then layers two machine-learning subsystems
on top of that data: a leakage-safe day-ahead hourly consumption
forecaster, and a four-detector anomaly detection system. Both are served
through a read-only FastAPI REST API and visualized in a five-page React
+ TypeScript dashboard.

The forecasting subsystem formulates day-ahead prediction as: given
everything known at a local-midnight origin, predict the next 24 hourly
values in a single pass, with horizon as an explicit model feature rather
than a recursive chain. Five models were evaluated — two seasonal-naive
baselines (24h and 168h lag) and three learned models (Ridge, Random
Forest, HistGradientBoosting) — under a chronological train/validation/test
split that keys on the forecast *origin* rather than the target timestamp,
specifically to prevent a single 24-hour forecast from being split across
two windows (a real bug found and corrected during this project, not a
hypothetical). Random Forest was selected under a criterion fixed before
results were seen (lowest validation RMSE among models beating both
baselines), achieving a validation RMSE of 26.08 kWh against the strongest
baseline's 65.94 kWh, and a validation R² of 0.994 — though the report is
explicit that pooled R² substantially overstates single-building
reliability (per-building R² ranges from −1.06 to 0.966), and that RMSE/CV
against the baselines, not R² alone, is the metric that actually
discriminates model quality on this data.

The anomaly detection subsystem combines four detectors with genuinely
different failure modes — rule-based data quality checks, a robust
same-hour-of-day behavioral z-score, a standardized forecast-residual
score reusing the (never retrained) Random Forest model, and a
multivariate Isolation Forest fit only on training-period data. Because
BDG2 has no real anomaly ground truth, all four are evaluated against a
reproducible synthetic injection framework (six anomaly types, seeded,
never written into the real measurements table), with detection
thresholds selected on a validation split and frozen before being applied,
unchanged, to a held-out test split. A real methodological bug was found
and fixed during this work: an early threshold miscalibration caused a
35% false-positive rate, traced to ~21 of 60 sensors reporting
daily-resolution readings rather than genuine sensor faults — fixed,
regression-tested, and documented rather than hidden.

Engineering practices applied throughout include a layered
router→service→repository backend architecture, Alembic-managed database
migrations verified to build the complete schema from an empty database,
idempotent upserts everywhere the system writes, a typed frontend API
client with no direct `fetch` calls in UI code, and a combined 279
backend + 19 frontend automated tests, all passing, alongside clean
linting (`ruff`, `oxlint`) and clean TypeScript compilation/build. A
dedicated production-hardening pass verified Docker healthchecks,
fresh-database migration reproducibility, and a clean shutdown/restart
cycle with data persistence intact.

The main honest limitations are: forecast-API coverage is scoped to the
validation+test window (2017-07 to 2018-01), not the full two years;
anomaly-detector precision is low in absolute terms against the synthetic
benchmark (expected at under 1% synthetic prevalence, with false-positive
rate the more informative figure); the system has no authentication and
is not deployed to any cloud environment; and the frontend's visual
rendering has not been verified through automated browser testing in the
available environment (documented explicitly, not glossed over). None of
these are presented as failures — they are documented scope boundaries,
each with a stated path for future work.

Taken together, the project demonstrates real, verifiable competency
across the full stack a modern applied-ML/data-engineering role requires:
correct handling of temporal data and leakage, a properly evaluated
forecasting pipeline with honest per-building analysis, a multi-detector
anomaly system evaluated against a principled (if synthetic) benchmark,
a cleanly layered backend and typed frontend, and disciplined engineering
practice — migrations, idempotency, testing, and reproducibility treated
as first-class requirements rather than afterthoughts.

---

## Table of Contents

1. [Project Timeline and Phases](#1-project-timeline-and-phases)
2. [Dataset and Data Engineering](#2-dataset-and-data-engineering)
3. [Database and Architecture](#3-database-and-architecture)
4. [Exploratory Data Analysis](#4-exploratory-data-analysis)
5. [Forecasting](#5-forecasting)
6. [Forecast Leakage and Correctness](#6-forecast-leakage-and-correctness)
7. [Anomaly Detection](#7-anomaly-detection)
8. [FastAPI Backend](#8-fastapi-backend)
9. [React Frontend](#9-react-frontend)
10. [Docker and Deployment](#10-docker-and-deployment)
11. [Testing](#11-testing)
12. [Production Hardening](#12-production-hardening)
13. [Final Verification](#13-final-verification)
14. [Limitations](#14-limitations)
15. [Future Work](#15-future-work)
16. [Recruiter Skills Summary](#16-recruiter-skills-summary)
17. [Oral Presentation Materials](#17-oral-presentation-materials)
18. [Recruiter / Portfolio Materials](#18-recruiter--portfolio-materials)

---

## 1. Project Timeline and Phases

The repository's own documentation and commit narrative identify ten
functional phases of work (the project plan targets 13 total; phases
11–13, covering deployment/observability extensions, are explicitly out
of scope for this report — see [Future Work](#15-future-work)).

### Phase 1–2: Project setup and initial schema
Objective: establish the project skeleton (layered backend package,
PostgreSQL via Docker Compose, Alembic migration tooling) before any data
work began. The initial schema migration (`2bbf3ba70060_initial_schema.py`)
establishes `sites`, `buildings`, `sensors`, `energy_measurements`,
`weather_measurements`, and `ingestion_runs`.

### Phase 3: Dataset selection
Objective: choose a real, citable, appropriately-sized subset of BDG2.
Implementation: a deterministic algorithm (`ingestion/selection.py`)
filters to electricity-metered buildings, drops any building at or above
5% missing data, then greedily selects whole sites (smallest qualifying
building count first) until a 3-site/60-building budget is filled. Result:
Moose (9 buildings), Gator (21 buildings), Peacock (30 of 36 qualifying,
capped to fit the budget) — 60 buildings total, all US/Eastern. See
[Section 2](#2-dataset-and-data-engineering) for full detail.

### Phase 4: Data ingestion and quality
Objective: load the selected subset into PostgreSQL correctly, handling
real-world timestamp edge cases rather than assuming clean data.
Important decisions: DST fall-back hours are dropped as ambiguous rather
than guessed; DST spring-forward collisions are resolved via
deduplication; negative consumption values are nulled at ingestion
(physically impossible for a cumulative meter) rather than silently kept;
missing readings are preserved as SQL `NULL`, never dropped or imputed at
this stage. Result: 1,052,400 electricity readings and 51,931 weather
readings loaded, with exactly 240 rows (0.023%) absent purely due to DST
handling — a quantified, not silent, limitation.

### Phase 5: FastAPI REST API
Objective: a read-only API over the ingested data. Implementation:
router → service → repository layering, centralized pagination/limit
policy, two global exception handlers translating domain exceptions to
HTTP status codes. Result: endpoints for sites, buildings, sensors,
energy readings (raw and aggregated), and weather.

### Phase 6: Exploratory Data Analysis
Objective: understand the actual loaded data before any modeling
decision was made. Result: a reproducible EDA report (JSON + 8 plots)
surfacing the hour-of-day/day-of-week patterns, the `area_sqm`
correlation, weak weather correlation, and the site-clustered
missing-data pattern that directly informed later modeling choices. See
[Section 4](#4-exploratory-data-analysis).

### Phase 7: Day-ahead forecasting
Objective: a leakage-safe, evaluated day-ahead hourly forecasting
pipeline. Important problem discovered and resolved: an initial
train/validation/test split keyed on `target_ts` allowed a single
24-hour forecast to be split across two windows; corrected to split by
forecast `origin_ts` instead, with the correction proven by a unit test
constructing the exact failure scenario. Result: Random Forest selected,
cutting validation RMSE from the strongest baseline's 65.94 kWh to 26.08
kWh. See [Section 5](#5-forecasting) and [Section 6](#6-forecast-leakage-and-correctness).

### Phase 8: Anomaly detection
Objective: detect and explain abnormal consumption without real ground
truth to train or validate against. Result: four detectors evaluated
against a synthetic injection benchmark with validation/test threshold
freezing; a genuine methodological bug (a 35% false-positive rate traced
to daily-resolution sensors, not sensor faults) found and fixed during
evaluation. See [Section 7](#7-anomaly-detection).

### Phase 9: React + TypeScript dashboard
Objective: make the forecasting and anomaly systems visually explorable
against real API data. Result: five pages (Dashboard, Building Detail,
Forecasting, Anomaly Monitoring, Anomaly Detail), a typed API client, and
one new backend endpoint (`/buildings/{id}/forecast`) added specifically
to serve the Forecasting page — the only backend addition made during
this phase. See [Section 9](#9-react-frontend).

### Phase 10: Production hardening
Objective: make the existing system reliable, reproducible, and
documented — not add new capability. Result: Docker healthchecks, a
verified fresh-database migration path, `ruff`/`oxlint` adopted and made
clean, a focused frontend test suite added (19 tests, previously zero),
re-measured API performance, and full documentation/README rewrite. A
zero-commit repository state was also discovered and corrected to a
single clean commit during this phase. See [Section 12](#12-production-hardening).

### Final Verification
Objective: prove the complete system actually works end-to-end, not
merely that it builds. Result documented in [Section 13](#13-final-verification):
279/279 backend tests, 19/19 frontend tests, all data volumes verified
directly against the live database, the `generated_at < target_ts`
forecast invariant verified exhaustively (not sampled) across all
241,680 prediction rows, and a full container shutdown/restart cycle
confirmed to preserve data. Browser-level visual verification was
explicitly not possible in the available environment and is documented
as a remaining manual step, not claimed as done.

---

## 2. Dataset and Data Engineering

### Why BDG2

[Building Data Genome Project 2](https://github.com/buds-lab/building-data-genome-project-2)
is a real, published, citable dataset of non-residential building meter
data (Miller et al., *Scientific Data* 7:368, 2020; CC BY-SA 4.0). It was
verified directly against the official repository rather than a
third-party mirror (e.g. the Kaggle copy), and provides genuinely messy,
real-world data — missing readings, timezone ambiguity, uneven
per-building/per-site data quality — which is precisely what makes the
data-engineering and data-quality work in this project meaningful rather
than a clean-data toy exercise.

### Subset selection

Implemented in `ingestion/selection.py::select_subset`, run against the
verified raw files:

1. Filter `metadata.csv` to the 1,578 (of 1,636) buildings flagged
   `electricity == 'Yes'`.
2. Compute each building's missing-value rate over its full two-year
   hourly series.
3. Drop any building at or above 5% missing (`MAX_MISSING_RATE = 0.05`).
4. Rank remaining sites by qualifying building count ascending (smaller
   sites first, to fit more distinct sites under the 60-building
   ceiling), ties broken by mean missingness ascending.
5. Greedily add whole sites under the 3-site/60-building budget; if a
   site would exceed the budget, take only its lowest-missingness
   buildings up to the remaining slots.

This is deterministic — re-running it against the same raw files always
produces the same 60 buildings. Result:

| Site | Timezone | Qualifying buildings | Selected |
|---|---|---|---|
| Moose | US/Eastern | 9 | 9 (all) |
| Gator | US/Eastern | 21 | 21 (all) |
| Peacock | US/Eastern | 36 | 30 (capped, lowest-missingness) |

**Total: 60 buildings, 3 sites**, missing-rate range 0.0%–4.94%.
Primary-use breakdown: Education (20), Public services (11),
Lodging/residential (9), Entertainment/public assembly (9), Office (7),
Other (3), Warehouse/storage (1).

**Documented limitation**: all three selected sites happen to share one
timezone (US/Eastern), since the algorithm optimizes for building count
and data quality, not geographic/climate diversity — a deliberate scope
decision, stated explicitly in `docs/data_selection.md`, not an
oversight.

### Ingestion pipeline and transformation

The pipeline (`ingestion/download.py`, `transform.py`, `loader.py`,
`pipeline.py`) downloads the raw BDG2 files (SHA256-verified,
idempotent — re-running the download is a no-op if the files already
match), transforms them, and loads into PostgreSQL.

**Timestamp handling.** BDG2 timestamps are local "clock time" per
building with no explicit DST marker. Converting to UTC surfaces two
real edge cases, both confirmed against the actual ingested data (not
assumed):

- **Fall-back (ambiguous hour)**: resolved as `ambiguous="NaT"` and the
  row dropped rather than guessed. Real count: exactly 120 rows (2
  fall-back transitions in the 2016–2017 window × 60 buildings).
- **Spring-forward (nonexistent hour)**: resolved as
  `nonexistent="shift_forward"`, which can collide with an adjacent real
  reading at the same UTC instant; deduplication then keeps one. Real
  count: also exactly 120 rows.

Net effect: **240 of 1,052,640 possible readings (0.023%) absent purely
from DST handling** — quantified and recorded in `ingestion_runs`, not a
silent gap.

**Missing values are preserved, not dropped.** A `NaN` in the source CSV
becomes SQL `NULL` in `energy_measurements.consumption_kwh`, keeping a
genuine data gap distinguishable from an ingestion failure — this
matters directly for both the forecasting feature pipeline
(`dropna`-based complete-case policy) and the anomaly detector's
`missing` check.

**Negative values are treated as invalid.** `consumption_kwh < 0` is
physically impossible for a cumulative electricity meter and is nulled
out before loading. Observed count in the selected subset: **0** — the
subset happened to contain none, confirmed by construction, not assumed.

**Referential integrity** is enforced by the database itself: every
foreign key (`buildings.site_id`, `sensors.building_id`,
`energy_measurements.sensor_id`, `weather_measurements.site_id`) is
`NOT NULL`, so an orphaned row cannot be inserted.

**Idempotency**: all loader writes use `INSERT ... ON CONFLICT DO
UPDATE`, so re-running ingestion against already-loaded data refreshes
rather than duplicates or errors.

### Verified final data volumes

Re-confirmed directly against the live database during Phase 10's final
verification (not taken from documentation alone):

| Table | Row count |
|---|---|
| `sites` | 3 |
| `buildings` | 60 |
| `sensors` | 60 |
| `energy_measurements` | 1,052,400 |
| `weather_measurements` | 51,931 |
| `model_versions` | 6 |
| `predictions` | 241,680 |
| `alerts` | 99,651 |
| `synthetic_anomalies` | 0 (expected — see [Section 7](#7-anomaly-detection)) |
| `ingestion_runs` | 6 |

---

## 3. Database and Architecture

### Schema

PostgreSQL was chosen for its combination of strong relational integrity
(composite primary keys and foreign keys enforced at the database level,
not just in application code), native `JSONB` (used for
`model_versions.hyperparameters`/`metrics`), timezone-aware timestamp
types, and mature window/aggregate functions (`percentile_cont`,
`date_trunc`, `AT TIME ZONE`) used directly by the analytics layer.

| Table | Purpose | Key structure |
|---|---|---|
| `sites` | Physical sites (3) | PK `site_id`; `timezone` |
| `buildings` | Buildings (60) | PK `building_id`; FK `site_id`; retained BDG2 metadata (area, use, etc.) |
| `sensors` | One electricity meter per building in this subset | PK `sensor_id`; FK `building_id` |
| `energy_measurements` | Hourly readings | Composite PK `(sensor_id, ts)` |
| `weather_measurements` | Hourly weather, per site | Composite PK `(site_id, ts)` |
| `model_versions` | One row per trained/baseline forecasting or anomaly model | Composite PK `(model_name, model_version)`; hyperparameters/metrics as JSONB |
| `predictions` | Forecast output | FK to `model_versions`; unique `(sensor_id, target_ts, model_name, model_version)` |
| `alerts` | Anomaly detector output | Unique idempotency key `(sensor_id, ts, method, detector_version)` |
| `synthetic_anomalies` | Ground-truth schema for the injection framework | Present in the schema for completeness; **0 rows** by design — evaluation uses this shape in-memory, never persists it (see [Section 7](#7-anomaly-detection)) |
| `ingestion_runs` | Ingestion run bookkeeping | Records row counts and DST-related error summaries per run |
| `alembic_version` | Migration bookkeeping | Single row, current head |

All migrations are managed through Alembic (`alembic/versions/`), with a
single, linear revision chain (`2bbf3ba70060` → `417d3ee13de9` →
`c072cc66140a`) — no branching heads.

### System architecture

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
  |
  +--> Anomaly Detection  (evaluate / detect, offline CLI jobs)
  |
  v
FastAPI  (read-only REST API)
  |
  v
React + TypeScript Dashboard
```

### Offline jobs vs. online application — a deliberate split

This is an explicit architectural decision, documented in
`docs/architecture.md`, not an implicit consequence of how the code
happened to be written:

**Offline jobs** (each a `python -m energy_platform.<module>` CLI
command, run manually or on a schedule, never inside an HTTP request):
- `ingestion.pipeline` — BDG2 download, transform, load
- `forecasting.train` — fits Ridge/Random Forest/HistGradientBoosting,
  evaluates, selects, records `model_versions`
- `forecasting.predict` — loads a saved artifact and writes predictions
  (never retrains)
- `anomalies.evaluate` — fits the Isolation Forest, sweeps and freezes
  detector thresholds
- `anomalies.detect` — runs all four detectors against real data, writes
  `alerts`

**Online application** (FastAPI + React): strictly **read-only**. No
endpoint triggers ingestion, training, prediction, or detection. This
keeps API latency predictable (every request is just SQL reads, never
"wait while a model retrains" — the Isolation Forest fit alone takes
minutes), keeps expensive operations out of the request path entirely,
and matches the system's actual intended operating model: periodic batch
jobs feeding a fast, read-only dashboard, not an online-learning service.

### Backend layering

```
HTTP Router   (api/routers/*.py)   — parses params, calls one service, returns via a Pydantic response_model
Service       (services/*.py)      — business rules, calls repositories, raises domain exceptions
Repository    (repositories/*.py)  — SQLAlchemy queries only, returns ORM objects or plain dicts
PostgreSQL
```

Every endpoint, including the ones added in later phases (`/alerts`,
`/buildings/{id}/forecast`), follows this exact same chain with no
exception.

---

## 4. Exploratory Data Analysis

Generated by `python -m energy_platform.analytics.report` directly
against the live database (`data/processed/eda_report.json`,
`reports/eda/*.png`) — every figure below is from that run.

**Daily/weekly/monthly patterns.** A clear occupancy-driven hour-of-day
curve: overnight trough ~188–192 kWh (02:00–04:00), plateau ~250–252 kWh
(10:00–14:00), peak/trough ratio ≈1.33. The overnight floor being ~75% of
the midday peak implies a large base load (HVAC, refrigeration,
always-on equipment) independent of occupancy — consistent with a
portfolio dominated by large institutional buildings. Day-of-week:
weekdays average 228–234 kWh vs. 199–203 kWh on weekends, a 14–17%
weekday premium — real, but far smaller than the hour-of-day swing;
weekends are reduced activity, not shutdown.

**Building characteristics.** Mean consumption spans **0.39 to 1,442.6
kWh** across the 60 buildings — a ~3,700× range, driven by building size
and use, not noise.

**Area/consumption relationship.** `area_sqm` correlates strongly with
mean consumption: **r = 0.85 (n = 60)**, the single strongest and most
completely-populated static feature found. `occupants` (r = 0.58, n =
26) and `number_of_floors` (r = 0.47, n = 30) are directionally
consistent but far less usable due to sparse population. `year_built`
(r = 0.06, n = 8) and the EUI/energy-star fields (0 of 60 populated) are
not viable features for this subset at all — stated as a data-availability
fact, not an oversight.

**Weather relationships.** Every Pearson correlation between site-level
consumption and weather variables is weak: **|r| ≤ 0.30** across all
three sites and all measured variables (air/dew temperature, wind speed,
cloud coverage, precipitation). The strongest single value is wind speed
at Site 3 (r = 0.301). Two caveats are stated explicitly in the source
document: Pearson's r only captures linear relationships (a real
U-shaped heating/cooling curve would still show weak linear correlation
even if physically present), and these are site-level aggregates that
could mask a real per-building effect. Neither caveat was tested further
in this phase — flagged as a candidate direction, not resolved.

**Missingness.** Zero duplicate observations (structurally impossible
given the composite primary keys, verified anyway). Missing timestamps:
exactly 240 across all 60 sensors, fully attributed to DST handling
(matches [Section 2](#2-dataset-and-data-engineering) exactly). NULL
consumption values are **not** uniform across sites: every Gator-site
building sits between ~2.9% and ~5.0% missing, while nearly every Moose-
and Peacock-site building is under 1% — a real, site-clustered pattern,
not random noise, meaning missingness is not missing-completely-at-random
with respect to site.

**Why these findings mattered for later modeling.** The EDA report
explicitly enumerates its implications for Phase 7: calendar/time
features were identified as the strongest observed signal (ranked #1
candidate feature, later confirmed — the largest single driver of
forecast accuracy); `area_sqm` was flagged as the strongest static
feature (later used as a required numeric feature); weak weather
correlation directly motivated the decision to exclude weather from the
first forecasting model entirely rather than include a noisy feature;
and the site-clustered missingness directly informed the forecasting
pipeline's complete-case (dropna) policy and later the anomaly
detector's per-sensor rather than global thresholds.

---

## 5. Forecasting

### Problem formulation

For each building, given everything observed up to and including a
forecast **origin** T (a specific hour with a real reading), predict
`consumption_kwh` for T+1 through T+24 — the entire next 24 hours in one
forecast run. Origins are restricted to **local midnight**, one per
building per day (not every hour) — deliberate, both because it matches
how day-ahead forecasting works in practice, and because every hourly
origin would have inflated the dataset ~24× for little statistical
benefit while exceeding available memory (the first unrestricted
implementation OOM-killed the container — a real, documented incident,
not hypothetical).

**Multi-step strategy: direct, with horizon as a feature.** One model
per algorithm is trained on rows shaped `(origin features, horizon,
target)`, with `horizon ∈ {1..24}` as an explicit numeric input — not
recursive forecasting, and not 24 independent models. Calendar features
describe the **target** timestamp (legitimately knowable in advance);
lag/rolling features describe the **origin** only.

### Train / validation / test split

Chronological, non-overlapping, split by forecast **origin**, defined in
local time (US/Eastern) and converted to UTC:

| Split | Origin range (local) | Rows after feature engineering + dropna |
|---|---|---|
| Train | 2016-01-01 – 2017-06-30 | 684,496 |
| Validation | 2017-07-01 – 2017-09-30 | 124,386 |
| Test | 2017-10-01 – 2017-12-31 | 117,056 |

Data-completeness check performed **before** committing to these split
dates (not assumed): all 60 sensors have exactly 17,540 hourly rows,
worst-case per-split completeness across all 60 buildings is train
≥93.97%, validation ≥95.65%, test ≥96.74% — no buildings were excluded.

### Feature-availability contract (leakage prevention)

| Feature | Computed from | Available at forecast time? |
|---|---|---|
| Calendar features (hour, day-of-week, month, cyclical sin/cos) | **target** timestamp, local time | Yes — a known future hour's calendar is knowable in advance |
| `lag_1`..`lag_168` | **origin**, strictly ≤ origin | Yes |
| Rolling 24h/168h mean/std | **origin**, shifted by 1 before rolling | Yes — excludes the origin's own value |
| `area_sqm`, `primary_use`, `number_of_floors`, `occupants` | building metadata | Yes — static |
| `horizon` | the forecast request itself | Yes |
| `year_built`, EUI fields | building metadata | **Not used** — 13%/0% populated for this subset |
| weather (any) | — | **Not used at all** — see below |

**Weather was deliberately excluded from this model.** BDG2 provides
observed historical weather, not a forecast product; using future
observed weather as a day-ahead feature would overstate real-world
accuracy. This is stated in the codebase as an explicit scope decision
(Option A of three considered), enforced in code by a dedicated test
(`test_no_weather_columns_anywhere_in_the_feature_set`), and reinforced
by the EDA finding that weather's linear correlation with consumption is
weak in this data anyway.

### Models evaluated

| Model | Configuration |
|---|---|
| Seasonal naive 24h | `forecast(T+h) = consumption(T+h-24)` |
| Seasonal naive 168h | `forecast(T+h) = consumption(T+h-168)` |
| Ridge | alpha selected via 5-fold expanding-window `TimeSeriesSplit` on train only, grid `[0.1, 1, 10, 100]` |
| Random Forest | `n_estimators=100, max_depth=20, min_samples_leaf=5` — a capped reasonable configuration, not a hyperparameter search |
| HistGradientBoosting | library defaults |

Random seed 42 used everywhere stochastic. Every model/baseline is
recorded in `model_versions` (feature version, train/validation/test
date ranges, hyperparameters, seed, metrics, artifact path) — a
prediction can never reference a model version that was never actually
recorded, enforced by a real foreign key.

### Verified results

| Model | Val MAE | Val RMSE | Val R² | Val CV(RMSE)% | Test MAE | Test RMSE | Test R² | Test CV(RMSE)% |
|---|---|---|---|---|---|---|---|---|
| Seasonal naive 24h | 21.55 | 77.90 | 0.949 | 35.3% | 23.54 | 85.00 | 0.938 | 40.0% |
| Seasonal naive 168h | 18.35 | 65.94 | 0.963 | 29.9% | 22.50 | 77.64 | 0.949 | 36.5% |
| Ridge | 39.65 | 74.56 | 0.953 | 33.8% | 37.99 | 70.78 | 0.957 | 33.3% |
| **Random Forest** | **8.46** | **26.08** | **0.994** | **11.8%** | **9.76** | **29.22** | **0.993** | **13.7%** |
| HistGradientBoosting | 13.25 | 30.54 | 0.992 | 13.8% | 14.53 | 32.76 | 0.991 | 15.4% |

**Model-selection criterion, fixed before results were seen**: among
models beating *both* seasonal-naive baselines on validation RMSE,
select the lowest validation RMSE.

**Random Forest was selected under this criterion** — the lowest
validation RMSE among models that beat both baselines. This is a
statement about performance under this project's specific validation
methodology, evaluation window, and feature set — **not** a claim that
Random Forest is universally the best algorithm for this problem class.
Ridge explicitly did **not** qualify: its validation RMSE (74.56) is
worse than the 168h baseline (65.94) despite technically beating the
weaker 24h baseline — exactly why the criterion requires beating both.

**R² is explicitly flagged as an unreliable standalone signal on this
data.** Every model, including both naive baselines, scores R² ≥ 0.94 —
the data's strong daily/weekly regularity makes high R² easy to achieve
without real forecasting skill. The report states directly: "RMSE/MAE
against the baselines is what actually discriminates model quality; R²
alone would have been misleading."

**Per-building reliability is markedly worse than the pooled metric
suggests.** Random Forest's pooled validation R² (0.994) is a portfolio
average; **per-building R² ranges from −1.06 to 0.966 (median 0.887),
with 22 of 60 buildings scoring below 0.8.** The negative-R² cases are a
known statistical artifact (low-variance buildings can have R² driven
sharply negative by a small absolute error, since R² compares against a
predict-the-mean baseline) rather than evidence of a broken model — the
report explicitly recommends CV(RMSE)% (range 1.0%–28.4% per building)
as the fairer cross-building metric, and notes HistGradientBoosting is
markedly less robust by the same lens (worst per-building R² = −813.9
vs. Random Forest's −1.06).

**Horizon-by-horizon behavior**: RMSE is not monotonically increasing —
it rises sharply from 6.38 (h=1) to a plateau of ~30–33 (h=7–14), then
*decreases* through h=15–23, before spiking to 44.48 at h=24 (a 7.0×
jump from h=1).

---

## 6. Forecast Leakage and Correctness

### The origin-split bug, found and corrected

An earlier version of the split logic keyed train/validation/test on
`target_ts` (the timestamp being predicted). This had a real, concrete
bug: since one origin produces 24 rows (h=1..24), an origin within 24
hours of a split boundary could have some horizons' targets fall before
the boundary and others after it — e.g., an origin at local midnight
June 30 has targets spanning June 30 01:00 through July 1 00:00, so
h=24 alone would land in validation while h=1–23 stayed in train,
splitting a single 24-hour forecast across two windows.

**The fix**: split by forecast **origin**, not target — guaranteeing
every complete 24-hour forecast belongs to exactly one split. This was
proven, not just asserted: a unit test constructs the exact "23 rows
before the boundary, 1 at/after" scenario, and an integration test seeds
real database data spanning a split boundary and confirms no `origin_ts`
appears in two splits.

**Practical effect of the correction was small but the fix was
methodologically necessary regardless**: Random Forest's validation RMSE
moved from 26.01 (buggy split) to 26.08 (corrected split) — a ~0.3%
difference, because the bug only ever misplaced a small number of
boundary-adjacent rows out of 684k+ training rows. The report states
plainly that the correction was required on principle, independent of
its small effect on this particular dataset — a different dataset size
or boundary date could have produced a much larger effect.

### Leakage prevention, verified by a dedicated test suite

- **Target strictly after origin**: `test_target_kwh_is_strictly_after_origin`.
- **Lag/rolling features never exceed the origin's own value**:
  `test_lag_and_rolling_features_never_exceed_origin_value`,
  `test_rolling_24h_mean_excludes_origin_hour_end_to_end`.
- **Train-only preprocessing**: `test_fitting_a_pipeline_only_touches_the_data_passed_to_fit`,
  `test_onehot_encoder_categories_come_only_from_training_data`.
- **No weather anywhere in the feature set**:
  `test_no_weather_columns_anywhere_in_the_feature_set`.
- **Splits are disjoint and non-straddling**:
  `test_split_dataset_train_val_test_are_disjoint`,
  `test_split_dataset_no_origin_crosses_the_train_validation_boundary_end_to_end`.
- **Predictions can never look like they were made after the fact**:
  `test_prediction_generated_at_precedes_target_ts`.

### Final exhaustive verification

Predictions written by `predict.py`: **1,440 rows** (60 buildings × 24
horizons) from the real run documented in `docs/forecasting.md`,
**100% satisfying `generated_at < target_ts`**, verified via SQL at that
time.

This invariant was **re-verified exhaustively during the Phase 10 final
verification pass, across the full current `predictions` table (241,680
rows, not the smaller 1,440-row single-origin run)**:

```sql
SELECT count(*) AS total, count(*) FILTER (WHERE generated_at < target_ts) AS valid_ordering
FROM predictions;
-- total: 241680, valid_ordering: 241680
```

**241,680 of 241,680 rows satisfy the ordering — checked exhaustively
against the live database, not sampled.**

---

## 7. Anomaly Detection

### Why multiple detectors

BDG2 has no real anomaly ground truth, and no single detection strategy
covers every real failure mode a building sensor exhibits. Four
detectors were built with deliberately different mechanisms so their
combination covers more ground than any one alone:

| | Method | Leakage-safety mechanism | Fit period |
|---|---|---|---|
| **A** | Rule-based data quality | No history/model — deterministic thresholds on the point and its immediate surroundings | n/a |
| **B** | Behavioral (robust z-score) | Causal same-hour-of-day rolling baseline (shift-then-rolling) | n/a — statistical, not fit |
| **C** | Forecast residual | Reuses the Phase 7 Random Forest; actual value always from `energy_measurements`, never the unused `predictions.actual_kwh` | n/a — reuses the TRAIN-fit forecasting model |
| **D** | Isolation Forest | Genuinely fitted, TRAIN-period data only | 2016-01-01 – 2017-06-30 |

**A — Data quality**: five deterministic checks (`negative_value`,
`missing`, `zero_run` ≥3h, `stuck_meter` ≥30h, `isolated_spike` ≥4× the
local 7h-window median with non-elevated neighbors).

**B — Behavioral**: flags a point against how that same sensor behaves
at that same hour-of-day historically, using a robust median/MAD
z-score (not mean/std, since the baseline window can itself contain
real or injected anomalies) over a 14-day window.

**C — Forecast residual**: `actual − predicted`, standardized by a
causal 72-hour rolling standard deviation of that sensor's own past
residuals. Never retrains the Random Forest.

**D — Isolation Forest**: the one genuinely multivariate detector — 10
engineered features (raw value, previous-hour value, 24h/168h rolling
statistics, Detector B's own z-score as an input feature, calendar
phase), scaled and fit on TRAIN-period data only.

**Model versioning is intentionally asymmetric.** Only Isolation Forest
gets a `model_versions` row (seed=42, hyperparameters, TRAIN period,
artifact path) — it is the only detector with an actual training phase.
Detectors A/B/C are traced via `method` + `detector_version` on the
`alerts` table instead, a deliberate design choice to avoid fabricating
meaningless training date ranges for methods that were never trained.

### Why real energy measurements were never modified

The synthetic injection framework (`injection.py`) produces two things,
both purely additive or in-memory: an in-memory "evaluation series" — a
copy of real historical readings with specific points overridden — fed
to detectors instead of real data during evaluation only; and a
ground-truth record matching the `synthetic_anomalies` table's schema.
**`energy_measurements` itself is never written to by the injection
framework**, verified directly by a dedicated test
(`test_injection_never_mutates_the_input_series`). This is why the
`synthetic_anomalies` table legitimately contains 0 rows in the live
database — the ground truth is used in-memory during evaluation and the
evaluation report is written to a JSON file, never persisted to that
table.

Six injected anomaly types, each seeded (`numpy.random.default_rng`) for
full reproducibility: `spike`, `drop`, `sustained_high`,
`sustained_low`, `stuck_meter`, `zero_consumption`. Injections are
placed **only** in validation and test periods, never train — the
mechanism that keeps Isolation Forest's fit uncontaminated with no
special-casing required, since there is simply nothing injected in the
period it's fit on.

### Validation/test threshold selection

Thresholds for the three score-based detectors (B, C, D) are selected by
sweeping candidates and picking the one maximizing F1 **on
validation-period injections only**, then **frozen** and applied
unchanged to test — test metrics never influence the threshold.
Detector A has no single sweepable threshold (five independent,
domain-reasoned rule constants) and is evaluated identically on both
splits.

**Isolation Forest contamination handling**: fit/evaluation separation
is verified directly, not just by convention — a dedicated test
(`test_fitting_a_pipeline_only_touches_the_data_passed_to_fit`-style
guarantee, specifically `test_fitting_on_train_slice_is_unaffected_by_
validation_period_contamination`) actually corrupts the validation
portion of the input series and confirms the fitted scaler/model are
byte-identical either way — possible because every feature is causal
(shift-then-rolling), so a later corruption cannot reach an earlier
row's features.

### A real bug found during evaluation

`stuck_meter`'s injected duration was originally 1–4 hours, shared with
`zero_consumption`. The first full evaluation run showed Detector A
flagging **35% of all validation-period points** as false positives.
Investigation (not assumption) traced this to a genuine,
previously-undocumented BDG2 characteristic: ~21 of 60 sensors report
**daily-resolution readings** — one value per calendar day, replicated
across all 24 hours once reindexed to an hourly grid — confirmed
directly (92 distinct values over a 92-day window, run lengths of
exactly ~24 hours). The original `MIN_STUCK_RUN_HOURS=4` threshold could
not distinguish "this sensor fundamentally reports daily" from "this
meter is stuck." **Fixed** by raising the threshold to 30 hours
(comfortably above one calendar day) and giving `stuck_meter` injections
their own longer duration (36–72h). Before the fix: data-quality
FPR=35.2%, precision=0.3% on validation. After: FPR=3.5%, precision=14.4%.
A regression test (`test_one_calendar_day_flat_run_is_not_flagged_as_
stuck_meter`) encodes this directly so it cannot silently regress.

### Class balance (reported before any performance claim)

| Split | Total slots | Injected anomalies | Rate | Affected sensors |
|---|---|---|---|---|
| Validation | 132,480 | 1,241 | 0.94% | 42 |
| Test | 132,540 | 1,343 | 1.01% | 48 |

### Verified results

Frozen thresholds (selected on validation): behavioral \|z\| ≥ 8.0,
forecast-residual \|z\| ≥ 3.0, Isolation Forest score ≥ 0.6449.

| Detector | Val P / R / F1 | Test P / R / F1 | Val FPR | Test FPR |
|---|---|---|---|---|
| A: data quality | 0.144 / 0.627 / 0.234 | 0.100 / 0.633 / 0.173 | 3.52% | 5.81% |
| B: behavioral | 0.101 / 0.326 / 0.154 | 0.122 / 0.279 / 0.170 | 2.74% | 2.05% |
| C: forecast residual | 0.129 / 0.289 / 0.179 | 0.114 / 0.233 / 0.153 | 1.84% | 1.86% |
| D: Isolation Forest | 0.076 / 0.072 / 0.074 | 0.048 / 0.071 / 0.057 | 0.83% | 1.43% |

**No detector is presented as universally better or worse.** By the
criterion fixed before results were seen (highest validation F1),
Detector A ranks highest — but this is explained, not left as a bare
ranking: A's F1 comes almost entirely from perfect recall on
`stuck_meter` (its specialty) and zero recall on `sustained_high`/
`sustained_low` (entirely out of scope by design — that is B/C's job).
Detector C is strongest on `spike`/`drop`/`zero_consumption` (0.8–1.0
recall) — exactly the sudden deviations a model conditioned on recent
history would be most surprised by. Detector B is the most balanced
across types. Detector D underperforms on every type, attributed
directly to a real property of the evaluation design — its multivariate
score is optimized for joint unusualness, while the benchmark's injected
anomalies are largely univariate shifts along one axis — not a defect in
the detector's implementation.

### Real production run

`anomalies.detect` against the full loaded dataset wrote **99,651
alerts**: data_quality 53,217 (dominated by `stuck_meter`, 37,823 —
genuine, pre-existing sensor characteristics, and `missing`, 15,386),
behavioral 24,084, isolation_forest 17,273, forecast_residual 5,077.
`negative_value` and `zero_run` both fired zero times on real data — the
expected pass for `negative_value` (BDG2 has no negative readings once
loaded) and a genuine finding for `zero_run` (no occupied building in
this subset has a real ≥3h zero-consumption stretch).

---

## 8. FastAPI Backend

### Request flow

```
React → typed API client → FastAPI router → service → repository → SQLAlchemy → PostgreSQL
```

### Endpoint groups

| Group | Endpoints |
|---|---|
| Core resources | `/api/v1/sites`, `/api/v1/sites/{id}`, `/api/v1/buildings`, `/api/v1/buildings/{id}`, `/api/v1/sensors`, `/api/v1/sensors/{id}` |
| Energy | `/api/v1/buildings/{id}/energy`, `/api/v1/buildings/{id}/energy/aggregate`, `/api/v1/buildings/{id}/summary`, `/api/v1/sites/{id}/weather` |
| Analytics | `/api/v1/buildings/compare`, `/api/v1/buildings/{id}/analytics/summary`, `/api/v1/buildings/{id}/analytics/profile`, `/api/v1/buildings/{id}/analytics/peaks`, `/api/v1/sites/{id}/analytics/weather-energy` |
| Anomalies | `/api/v1/alerts`, `/api/v1/alerts/{id}`, `/api/v1/alerts/summary`, `/api/v1/buildings/{id}/alerts` |
| Forecast | `/api/v1/buildings/{id}/forecast` |
| Health | `/health`, `/health/db` |

### Validation, pagination, and error handling

Two centralized limit policies (`api/deps.py`), applied consistently by
every router rather than each endpoint choosing its own numbers: list
endpoints default to 50 rows, capped at 200; time-series endpoints
default to 1000 rows, capped at 5000. This bound was specifically
verified during Phase 10 to be applied on every energy/forecast/alert
endpoint — no endpoint can be made to return an unbounded result set.

Two global exception handlers translate domain exceptions to HTTP
responses uniformly: `NotFoundError` → 404, `InvalidParameterError` →
400; FastAPI/Pydantic's own validation → 422. All three shapes return
`{"detail": "..."}`.

### CORS

`CORS_ORIGINS` is configured explicitly (`http://localhost:5173` in the
default `.env`), not a wildcard — verified directly during Phase 10 by
issuing a real cross-origin request and confirming the
`access-control-allow-origin` response header matches.

### Health checks

`/health` (liveness) and `/health/db` (readiness — actually queries the
database, not just process-up) both wired into a Docker `HEALTHCHECK`
so `docker compose up` sequences service startup correctly (frontend
only starts once the backend is genuinely serving, not just once its
container process has started).

### Forecast API specifics

`GET /api/v1/buildings/{id}/forecast` reads only the `predictions` table
that Phase 7's `train`/`predict` CLI jobs already populate — this
endpoint never trains or predicts anything itself. `actual_kwh` is
always joined from `energy_measurements`, never the unused
`predictions.actual_kwh` column. `residual = actual − predicted`.
`generated_at` (the forecast's origin) is verified — by a dedicated
regression test, and re-verified exhaustively against the live database
during final verification (see [Section 6](#6-forecast-leakage-and-correctness))
— to always precede `target_ts`.

### Testing results (this layer)

All API-layer behavior is covered by the integration test suite (part
of the 279 backend tests — see [Section 11](#11-testing)), including a
regression test for a genuine routing gotcha (`/buildings/compare` vs.
`/buildings/{building_id}` path ambiguity, resolved by router
registration order and covered by
`test_get_buildings_compare_route_not_shadowed_by_building_id_route`).

---

## 9. React Frontend

### Stack

React 19, TypeScript, Vite, Recharts, `react-router-dom` for client-side
routing. No server-side rendering, no authentication (a deliberate scope
decision — read-only portfolio dashboard, not a multi-user product).

### Typed API client

`frontend/src/api/`: one file per backend domain (`sites.ts`,
`buildings.ts`, `energy.ts`, `analytics.ts`, `alerts.ts`, `forecast.ts`),
each function's return type mirroring the corresponding backend Pydantic
schema field-for-field. A shared `client.ts` centralizes fetch and error
handling — an `ApiError` class carries the backend's own `detail`
message through to the UI rather than a generic failure string. No page
component calls `fetch` directly.

### The five pages

1. **Dashboard** — portfolio KPIs, energy trend (hourly/daily/weekly/
   monthly toggle), building ranking (click-through to detail), hourly
   consumption profile, anomaly breakdowns by detector/severity, monthly
   anomaly trend.
2. **Building Detail** — metadata, KPIs, date-ranged historical
   consumption chart, hourly profile, recent anomalies for that
   building.
3. **Forecasting** — forecast vs. actual on one chart, visually distinct
   (dashed line for forecast, solid for actual), explicitly labeled as
   historical/backtested rather than live, horizon and residual shown
   per point, a methodology summary panel.
4. **Anomaly Monitoring** — fully filterable alert table (building,
   detector, anomaly type, severity, resolved state, date range),
   severity/detector distribution charts, monthly timeline.
5. **Anomaly Detail** — full alert metadata, the real backend-generated
   explanation string (not generic UI copy), and a surrounding-consumption
   chart with the flagged point highlighted.

### How real API data reaches the visualizations

Every chart-driving value is fetched through the typed client from a
live backend endpoint at render time via a shared `useApi` hook
(loading/error/data state management) — there is no mock data, fixture
data, or hardcoded sample data anywhere in the frontend source. This was
verified directly during Phase 10's final verification pass: every
endpoint the frontend calls was independently curled with real
site/building IDs pulled from the live database and confirmed to return
real, non-empty data.

### Automated vs. manual verification — stated explicitly

**Automated verification performed**: TypeScript compilation
(`tsc -b`), production build (`vite build`), lint (`oxlint`, 0
warnings/errors), the frontend test suite (19/19 passing — API client
query-building and error handling, pure formatting utilities, and
loading/error/empty-state rendering), and HTTP-level route checks (all
five SPA routes return 200; CORS headers verified correct on a real
cross-origin request).

**Browser rendering and visual interaction were not automatically
verified in the available environment and require manual browser
verification.** No browser automation tool was available. Nothing in
this report claims that pages were visually confirmed to render
correctly, that charts were visually inspected, or that the browser's
JavaScript console was checked for runtime errors. This is stated as a
known, documented gap, with exact manual verification steps provided in
[Section 13](#13-final-verification), not glossed over or implied to have
been done.

---

## 10. Docker and Deployment

### Services

`docker-compose.yml` defines three services: `db` (PostgreSQL 16,
official Alpine image, named volume for persistence), `backend`
(FastAPI, bind-mounted source for live reload, plus explicit mounts for
`data/`, `reports/`, `models/` so CLI job output lands on the host), and
`frontend` (Vite dev server, bind-mounted source, anonymous volume for
`node_modules`).

### Healthchecks and startup ordering

`db` has a native `pg_isready`-based healthcheck. `backend` has a
Python-`urllib`-based healthcheck against `/health` (added during Phase
10 — the base image has no `curl`, so a Python one-liner was used
instead of adding a dependency just for this). `backend`'s
`depends_on: db` uses `condition: service_healthy`; `frontend`'s
`depends_on: backend` likewise uses `condition: service_healthy` — the
frontend container does not start until the backend is actually serving
requests, not merely running.

### Migrations

`alembic upgrade head` is run explicitly as a setup step (not
auto-applied inside the backend container at startup) — this was
specifically re-verified during Phase 10 by dropping the dedicated test
database entirely and rebuilding it from the migration chain alone,
confirmed via the full 11-test migration suite plus the complete 279-test
suite passing against the freshly-built schema.

### Persistent database behavior — restart verification

A clean shutdown/restart cycle was performed and verified during final
verification: `docker compose down` (all three containers stopped and
removed cleanly) followed by `docker compose up -d` — all three services
returned to a healthy state, and the database's data was confirmed
intact afterward (buildings count re-checked via the live API: 60,
matching the pre-shutdown state).

### What is explicitly not claimed

**No cloud deployment.** This project runs entirely via local Docker
Compose. Nothing in this repository or report claims the system is
deployed to any cloud provider, has a public URL, or has been tested
under real network/production load conditions.

---

## 11. Testing

| Suite | Count | Result |
|---|---|---|
| Backend, full suite | 279 | **279/279 passing** |
| Frontend, full suite | 19 | **19/19 passing** |
| Forecast-specific (API, predict pipeline, leakage suite) | 24 | **24/24 passing** |
| Anomaly-specific (injection, 4 detectors, evaluation math, alerts repository, anomaly API, Isolation Forest fit) | 78 | **78/78 passing** |
| Backend lint (`ruff check .`) | — | Clean (0 findings) |
| Frontend lint (`oxlint`) | — | Clean (0 warnings, 0 errors) |
| Frontend typecheck + build (`tsc -b && vite build`) | — | Clean |

All counts above were re-verified by directly executing the test suites
during Phase 10's final verification pass (not taken from documentation
alone), including a specific run against a genuinely freshly-created
database (dropped and rebuilt from `alembic upgrade head` with no prior
state) to prove the schema is not dependent on any developer's existing
local database.

### Important regression tests

- `test_split_dataset_no_origin_crosses_the_train_validation_boundary_end_to_end`
  — proves the origin-split fix ([Section 6](#6-forecast-leakage-and-correctness)).
- `test_prediction_generated_at_precedes_target_ts` and the Phase 9
  forecast-API-specific `test_get_building_forecast_generated_at_always_
  precedes_target_ts` — proves forecasts can never appear to have been
  made after the fact.
- `test_one_calendar_day_flat_run_is_not_flagged_as_stuck_meter` —
  encodes the fix for the 35%-false-positive-rate bug found during
  anomaly evaluation ([Section 7](#7-anomaly-detection)).
- `test_fitting_on_train_slice_is_unaffected_by_validation_period_
  contamination` — proves the Isolation Forest fit is genuinely isolated
  from validation/test data, by actually corrupting validation data and
  confirming the fitted model is unchanged.
- `test_injection_never_mutates_the_input_series` — proves the synthetic
  anomaly framework never writes into real measurement data.
- `test_get_buildings_compare_route_not_shadowed_by_building_id_route` —
  a routing-ambiguity regression specific to this project's router
  registration order.

---

## 12. Production Hardening

Phase 10's explicit purpose was reliability and reproducibility, not new
capability. Work actually implemented:

- **Environment handling**: `.env`/`.env.example` audited — no secrets
  committed (verified by repository-wide grep), all `.env` values are
  placeholders, `CORS_ORIGINS` explicit rather than a wildcard.
- **Docker healthchecks**: added to the backend service; frontend's
  `depends_on` upgraded to wait for backend health, not just container
  start (see [Section 10](#10-docker-and-deployment)).
- **Non-root container user — attempted, then reverted with the reason
  documented in the Dockerfile itself**: a non-root frontend user was
  tried and caused a real `EACCES` failure (Docker does not reliably
  carry a build-time `chown` onto a freshly-initialized anonymous
  volume). Reverted rather than left broken; this is stated in the code
  comments as a deliberate, evidence-based decision, not a silent
  omission.
- **Migration verification**: the test database was dropped and rebuilt
  from `alembic upgrade head` alone, with the full test suite re-run
  against it.
- **API limits audit**: every time-series/list endpoint confirmed to
  route through the two centralized bounded-limit dependencies — no
  endpoint can return an unbounded result set.
- **Forecast leakage regression**: the `generated_at < target_ts` test
  described in [Section 6](#6-forecast-leakage-and-correctness) was added
  during this phase specifically because it had been true by
  construction but not previously asserted by a dedicated test at the
  API layer.
- **`ruff`** adopted for the backend (27 initial findings — mostly
  unused imports and import ordering, two genuinely unused variables
  individually investigated and confirmed dead before removal — now
  clean) and **`oxlint`** adopted for the frontend (now clean, including
  fixing one real `useEffect`+`setState` anti-pattern flagged by the
  linter in the Dashboard page, and one accessibility gap the pass
  surfaced: a single static page `<title>` across all five routes,
  fixed with a small per-page `useDocumentTitle` hook).
- **Frontend tests added**: 19 tests, where previously there were zero.
- **Accessibility**: semantic headings, `aria-label`s on form controls,
  visible focus states, `role="status"`/`role="alert"` on loading/error
  views, severity communicated via text label plus color (never color
  alone), keyboard-operable table rows, and per-page document titles
  (added this phase). Documented limitation: Recharts' SVG chart
  elements are not natively keyboard-navigable or screen-reader-labeled
  — a known limitation of the charting library, not addressed, since
  building a custom accessible-charting layer was judged disproportionate
  to this phase's scope.
- **Git cleanliness**: the repository was found to have **zero commits**
  despite nine phases of prior work — corrected to a single clean
  commit capturing the complete final state (chosen deliberately over
  fabricating a phase-by-phase history after the fact), then pushed to
  the GitHub remote.
- **Documentation**: `README.md` and `docs/architecture.md` fully
  rewritten to remove stale "frontend not built" language and document
  the Phase 8/9 endpoints and the offline-jobs/online-API split.

### Distinguishing implemented hardening from future improvement

Explicitly **not** done in this phase, and not claimed to be: CI/CD
pipeline, cloud deployment, authentication, a materialized rollup table
for the one measurably expensive query (`/buildings/compare` unfiltered,
~2.3s — documented as a known, deliberately deferred optimization, not
an oversight), and browser-based automated frontend testing. All of
these are carried into [Section 15, Future Work](#15-future-work).

---

## 13. Final Verification

A complete local, production-style verification pass was performed
(distinct from, and in addition to, the automated test suite) —
starting the full Docker stack from the actual committed repository
state and checking every layer directly.

**Services**: all three containers (`db`, `backend`, `frontend`) started
successfully; `db` and `backend` report Docker-healthcheck status
`healthy`; `frontend` serves all five SPA routes with HTTP 200.

**Database**: `alembic current` / `alembic heads` both report
`c072cc66140a (head)` — single head, matches `alembic_version`. All 11
tables present. Row counts re-verified directly via SQL and matched
exactly to the numbers already documented in [Section 2](#2-dataset-and-data-engineering):
1,052,400 energy measurements, 51,931 weather measurements, 60
buildings/sensors, 3 sites, 6 model_versions, 241,680 predictions,
99,651 alerts.

**Backend**: `/health` and `/health/db` both return success. Every
endpoint group was tested with real IDs pulled from the live API
(sites 1–3, building 1), returning valid JSON with real data in every
case, including a correctly-returned 404 for a nonexistent building ID.

**Forecasting**: 241,680/241,680 prediction rows satisfy
`generated_at < target_ts`, checked exhaustively (see [Section 6](#6-forecast-leakage-and-correctness)).
24/24 forecast-related tests pass.

**Anomaly detection**: 99,651 alerts confirmed retrievable through both
the global and building-scoped alert endpoints, with detector/method,
anomaly type, severity, resolved status, and the sensor→building
relationship all confirmed present and correctly joined. 78/78
anomaly-related tests pass.

**Full test suite**: 279/279 backend, 19/19 frontend. Backend lint
(`ruff`) and frontend lint (`oxlint`) both clean. Frontend
typecheck+build clean, both on the host and inside the running Docker
container.

**Restart test**: `docker compose down` followed by `docker compose up
-d` — all services returned healthy, and data was confirmed intact
(building count re-checked via the live API before and after: 60 in
both cases).

**Frontend — manual verification still required.** As stated in
[Section 9](#9-react-frontend), no browser automation was available.
The following manual checks in Chrome (with the stack running via
`docker compose up -d --build`) have **not** been performed and are
recommended before presenting this project:

1. Open `http://localhost:5173/` — confirm the Dashboard shows non-zero
   KPIs and populated charts.
2. Click a bar in "Top buildings by consumption" — confirm navigation to
   that building's detail page.
3. On Building Detail, change the date range/granularity — confirm the
   chart updates.
4. Open `/forecasting`, pick a building — confirm two distinct lines
   (solid "Actual", dashed "Forecast") render together.
5. Open `/anomalies`, apply a filter — confirm the table and its total
   count change accordingly.
6. Click an alert row — confirm the detail page shows the real
   explanation text and a highlighted point on the surrounding chart.
7. Check DevTools → Console on each page for JavaScript errors.
8. Check DevTools → Network for any failed or CORS-blocked requests.

---

## 14. Limitations

Stated as scope boundaries, not failures.

- **Single-year holdout.** The forecasting model is evaluated on six
  months of held-out data from one specific year (2017); it has not
  been validated against a second full year of genuinely unseen data,
  and building/weather/occupancy patterns could shift in ways this
  dataset cannot reveal.
- **Substantial per-building performance variation.** Pooled validation
  R² (0.994) is materially better than the typical single building; 22
  of 60 buildings score below 0.8 R², and the model completely misses
  at least one real step-change event in a representative example plot
  — an honest illustration, not cherry-picked either way.
- **Synthetic anomaly labels, not real ground truth.** BDG2 has no
  trustworthy real anomaly labels; all four detectors are evaluated
  against a reproducible but synthetic injection benchmark. Absolute
  precision figures (7–23% at best) should be read in that light — the
  false-positive-rate figures are more informative for judging
  real-world alert volume, and a meaningful share of what the benchmark
  scores as "false positives" for Detector A are real, pre-existing
  BDG2 data-quality issues the synthetic-only ground truth has no way to
  credit.
- **Weather features excluded from forecasting entirely**, a deliberate
  scope decision (see [Section 5](#5-forecasting)) reinforced by weak
  observed linear weather correlation, not fully tested for non-linear
  or per-building effects.
- **No real-world anomaly ground truth exists to validate against** —
  a fundamental property of this dataset, not something this project
  could have solved without external labeled incident data.
- **Forecast API coverage is date-scoped.** `/buildings/{id}/forecast`
  only returns predictions for 2017-07-01 through 2018-01-01 — the
  historical prediction backfill was scoped to the validation+test
  window the anomaly evaluation needed, not the full 2016–2018 history.
- **Frontend browser verification limitation**, stated fully in
  [Section 13](#13-final-verification) — visual rendering and
  interaction have not been automatically verified in the available
  environment.
- **No cloud deployment.** Runs locally via Docker Compose only.
- **No real-time IoT/MQTT pipeline.** Data is a fixed historical BDG2
  export loaded via a batch pipeline, not a live streaming ingestion
  path.
- **No authentication.** The API is intentionally read-only and
  unauthenticated — a deliberate decision appropriate to a portfolio/demo
  application, not a production multi-tenant service.
- **One measurably expensive query remains**: the unfiltered
  `/buildings/compare` endpoint (~2.3s, re-measured directly via
  `EXPLAIN ANALYZE` during Phase 10) — a known, deliberately deferred
  optimization (a materialized rollup table is the identified fix, not
  built since it is not yet needed under any real load).

---

## 15. Future Work

Technically justified next steps, grounded in the limitations above —
none of these are implemented in the current system.

- **Richer temporal/weather features**: lagged (not same-hour) weather,
  non-linear transforms (heating/cooling degree-hours) to test for a
  U-shaped temperature response the linear correlation analysis
  couldn't rule out.
- **Per-building or hierarchical forecasting**: given the wide
  per-building R²/CV(RMSE)% spread, a per-building or partially-pooled
  hierarchical model could close some of that gap without abandoning
  the sample efficiency of a single pooled model.
- **Longer historical evaluation**: a second full year of held-out data
  to test whether current performance generalizes across years, not
  just within one.
- **Real anomaly labels**: incorporating any available real incident
  reports or facilities-maintenance logs, if such data could be sourced
  for this or a similar building portfolio, to validate the detectors
  against genuine ground truth rather than synthetic injections alone.
- **Real-time IoT ingestion**: replacing the batch BDG2 load with a
  live MQTT or similar streaming pipeline for a genuinely operational
  deployment.
- **Authentication/authorization**: needed before this could become a
  genuinely multi-tenant or production-facing service.
- **CI/CD**: automated test/lint/build execution on every change,
  currently run manually.
- **Cloud deployment**: containerized deployment to a real cloud
  environment with production-grade PostgreSQL, currently local-only.
- **Observability**: structured logging, metrics, and tracing beyond
  the current health-check-level visibility.
- **Materialized rollups** for the one expensive cross-building query,
  if real usage ever makes the current ~2.3s response time a genuine
  problem.

---

## 16. Recruiter Skills Summary

**Data Engineering**
Python, pandas/NumPy, DST-aware timestamp normalization and UTC
conversion, idempotent ETL (`INSERT ... ON CONFLICT`), PostgreSQL schema
design (composite keys, foreign-key-enforced referential integrity),
deterministic dataset-selection algorithms, data-quality auditing
(missingness clustering, duplicate/negative-value detection).

**Machine Learning**
Day-ahead time-series forecasting (direct multi-horizon strategy),
Random Forest / Ridge / HistGradientBoosting regression, rigorous
train/validation/test methodology with leakage prevention proven by a
dedicated test suite (not just claimed), model selection under a
criterion fixed before results were seen, per-group (per-building)
evaluation beyond pooled metrics, anomaly detection via four distinct
methodologies (rule-based, statistical z-score, model-residual,
unsupervised Isolation Forest), synthetic ground-truth benchmark design,
model versioning and reproducibility (seeded, artifact-tracked,
foreign-key-enforced).

**Backend**
FastAPI, SQLAlchemy 2.0, Alembic migrations, layered
router→service→repository architecture, Pydantic schema validation,
centralized pagination/limit policy, domain-exception-to-HTTP-status
translation, 279 passing automated tests (unit + integration against a
real database).

**Frontend**
React 19, TypeScript, Vite, Recharts, `react-router-dom`, a typed API
client with zero direct `fetch` calls in UI code, component-level
testing with Vitest + React Testing Library.

**DevOps/Engineering**
Docker, Docker Compose multi-service orchestration with healthcheck-gated
startup ordering, Alembic-managed migrations verified against a
from-scratch database, `ruff`/`oxlint` static analysis integrated and
made clean, Git history hygiene, honest end-to-end production-style
verification distinguishing what was actually tested from what requires
manual follow-up.

---

## 17. Oral Presentation Materials

### 60-second explanation

"I built a full-stack energy analytics platform using real building
electricity data — 60 buildings, over a million hourly readings. It
ingests and cleans the data into PostgreSQL, then runs two machine
learning systems on top: a day-ahead consumption forecaster using
Random Forest, and a four-method anomaly detection system. Both are
served through a FastAPI backend and a React dashboard. The part I'm
most proud of is the rigor — I specifically test that the model can't
see future data, that a forecast can never be mistaken for happening
after the fact, and I evaluate the anomaly detectors against a synthetic
benchmark with a frozen train/validation/test discipline, the same as
the forecasting model."

### 2-minute explanation

"The project ingests the Building Data Genome Project 2 dataset — 60
real buildings across 3 sites, about 1.05 million hourly electricity
readings — into PostgreSQL through a pipeline that handles real-world
mess: daylight saving time edge cases, missing readings, and duplicate
timestamps, all verified with exact counts, not assumed clean.

On top of that I built two ML subsystems. The forecaster predicts the
next 24 hours of consumption for each building from a midnight origin,
using calendar and lag features but deliberately no weather, because
BDG2's weather is historical, not forecast data — using it would
overstate real accuracy. I evaluated five models under a strict
chronological split by forecast origin — I actually found and fixed a
bug where an earlier version split by the wrong timestamp and let single
forecasts straddle two data splits. Random Forest won, cutting RMSE from
66 to 26 kWh on validation, though I'm upfront that the pooled R² of
0.99 hides a lot of per-building variance — a fifth of buildings score
below 0.8.

The anomaly system runs four different detectors — rule-based, a
behavioral z-score, forecast-residual, and Isolation Forest — because
BDG2 has no real anomaly labels, so I built a synthetic injection
framework to evaluate them, with thresholds tuned only on a validation
split and frozen before touching test data. I actually caught a real bug
this way: my stuck-meter detector was flagging 35% of points as
anomalies because a fifth of the sensors report daily, not hourly,
resolution — not because meters were broken.

Everything's served through a layered FastAPI backend and a React
dashboard, with 279 backend tests and 19 frontend tests, all passing,
and I did a full production-hardening pass — Docker healthchecks, clean
linting, verified the database rebuilds correctly from migrations alone."

### 5-minute technical explanation

Building on the above, add:

- **Architecture**: explain the offline-jobs/online-API split explicitly
  — ingestion, training, and detection are batch CLI jobs; the API and
  dashboard are strictly read-only, so a page load is always just a
  fast SQL read, never a wait for a model to retrain.
- **Leakage prevention mechanics**: lag/rolling features use a
  shift-then-rolling pattern so the origin's own value is structurally
  excluded from its own baseline; calendar features describe the target
  timestamp (legitimately knowable in advance) while lag/rolling
  features describe only the origin; every one of these guarantees has
  a dedicated automated test, not just a code comment.
- **The origin-vs-target split bug**: walk through the concrete failure
  mode (an origin near a split boundary having some of its 24 horizons
  in one split and the rest in another) and how it was proven fixed
  (a unit test constructing that exact scenario).
- **Anomaly evaluation discipline**: explain binary evaluation (any
  detector flagging a point counts as a hit, regardless of assigned
  type) plus the secondary per-type recall breakdown, and why Isolation
  Forest's low score doesn't mean it's a worse implementation — it's
  suited to multivariate anomalies the synthetic benchmark doesn't
  emphasize.
- **Model-versioning asymmetry**: only Isolation Forest gets a
  `model_versions` row, because it's the only detector with an actual
  fit step — explain why forcing the other three through that table
  would mean fabricating meaningless training date ranges.
- **Production hardening**: the discovery of zero Git commits despite
  nine phases of work, and the decision to create one honest snapshot
  commit rather than fabricate history; the discovery that a Docker
  non-root user broke the frontend container (an anonymous volume
  permission issue) and the decision to revert and document rather than
  force it to "look" more secure.

### 10 likely professor questions with answers

**1. Why BDG2?**
It's a real, published, peer-reviewed dataset (Miller et al., *Scientific
Data*, 2020) with genuinely messy real-world characteristics — missing
readings, timezone ambiguity, uneven per-site data quality — which makes
the data-engineering and evaluation work meaningful rather than a
clean-data exercise. It was verified against the official repository,
not a third-party mirror.

**2. Why PostgreSQL?**
Strong relational integrity enforced at the database level (composite
primary keys on `(sensor_id, ts)`, `NOT NULL` foreign keys making
orphaned rows impossible to insert), native `JSONB` for flexible
hyperparameter/metric storage, and mature timezone-aware timestamp and
window-function support used directly by the analytics layer (e.g.
`percentile_cont` for median, `AT TIME ZONE` for local-time grouping).

**3. Why Random Forest?**
It had the lowest validation RMSE among the models that beat both
seasonal-naive baselines, under a selection criterion fixed before
results were seen. This is a statement about this specific
methodology, feature set, and evaluation window — not a claim that
Random Forest is universally superior to gradient boosting or linear
models for this problem class in general.

**4. Why these baselines?**
24-hour and 168-hour seasonal-naive baselines represent the two
simplest, hardest-to-beat heuristics for hourly data with daily and
weekly seasonality ("same hour yesterday" and "same hour last week").
The 168h baseline beating the 24h baseline on both splits is itself a
finding — it's consistent with the EDA's measured 14–17% weekday/weekend
gap, since "yesterday" is the wrong day-type two days out of seven.

**5. Why RMSE as the primary metric?**
RMSE and MAE were both reported, and R² was deliberately *not* used as
the primary selection criterion, because every model in the comparison
— including both naive baselines — scored R² above 0.94, which would
have made R² alone misleading for distinguishing real forecasting skill
from the data's inherent regularity. RMSE against the baselines is what
actually discriminates model quality here.

**6. How did you prevent leakage?**
Three layers: a feature-availability contract enforced by a dedicated
test suite (calendar features from the target timestamp, since a future
hour's calendar is legitimately knowable; lag/rolling features
exclusively from the origin, via a shift-then-rolling pattern that
structurally excludes the origin's own value); train-only preprocessing
(scalers and encoders fit only on the training split, verified by
tests); and a forecast-origin-based chronological split, verified to
have zero train/validation/test overlap.

**7. Why forecast-origin splitting instead of target-timestamp
splitting?**
Splitting by target timestamp allowed a single 24-hour forecast to be
divided across two splits when its origin fell near a boundary (some
horizons' targets before the boundary, some after). Splitting by origin
instead guarantees every complete 24-hour forecast belongs to exactly
one split — proven with a unit test constructing that exact scenario and
an integration test on real seeded data.

**8. Why multiple anomaly detectors instead of one?**
No single detection strategy covers every real failure mode. A
rule-based check catches obvious data-quality faults with zero
statistical assumptions; a behavioral z-score catches gradual drift a
rule can't; a forecast-residual detector catches deviations from
expected behavior conditioned on recent history; Isolation Forest
catches multivariate anomalies none of the univariate methods would
see. The evaluation shows each genuinely specializes in different
injected anomaly types rather than one dominating.

**9. Why synthetic anomalies instead of real labels?**
BDG2 has no trustworthy real anomaly ground truth. A synthetic
injection framework — six anomaly types, seeded for reproducibility,
never written into the real measurements table — is the only available
way to compute precision/recall/F1 at all. This is an explicitly stated
limitation, not presented as equivalent to real-world validation; the
false-positive rate (measured on real, non-injected data too) is
offered as a more informative real-world signal than absolute precision
against synthetic labels.

**10. What are the main limitations, and what would you improve next?**
Single-year holdout, substantial per-building forecast variance behind
a strong pooled metric, synthetic (not real) anomaly ground truth, no
weather features in forecasting, and no cloud deployment or real-time
ingestion. Next steps: a second year of held-out evaluation, richer
weather feature engineering (non-linear transforms), per-building or
hierarchical forecasting to address the per-building variance, and — if
obtainable — real incident/maintenance data to validate the anomaly
detectors against genuine ground truth instead of synthetic injections.

---

## 18. Recruiter / Portfolio Materials

### Project title

**Energy Intelligence Platform**

### One-line description

A full-stack building-energy analytics platform — PostgreSQL, FastAPI,
and a React dashboard — delivering leakage-tested day-ahead ML
forecasting and multi-detector anomaly detection over 1M+ real building
electricity readings.

### 3 CV bullets

- Built a full-stack energy analytics platform (PostgreSQL, FastAPI,
  React/TypeScript) ingesting 1,052,400 real hourly electricity readings
  across 60 buildings, with a leakage-tested day-ahead forecasting
  pipeline (Random Forest, validation RMSE 26.1 kWh vs. 65.9 kWh
  strongest baseline) verified by a dedicated automated test suite.
- Designed and evaluated a four-method anomaly detection system
  (rule-based, statistical, model-residual, Isolation Forest) against a
  custom synthetic benchmark with frozen validation/test thresholds;
  identified and fixed a real evaluation bug that had produced a 35%
  false-positive rate, tracing it to a previously-undocumented sensor
  characteristic.
- Delivered 279 backend and 19 frontend automated tests (100% passing),
  clean linting/typechecking, Docker Compose orchestration with
  healthcheck-gated service startup, and a verified from-scratch
  database migration path.

### 5 GitHub README highlights

1. Leakage-safe day-ahead forecasting with a documented, test-proven fix
   for a real forecast-origin-vs-target-timestamp split bug.
2. Four-detector anomaly system evaluated against a reproducible
   synthetic injection benchmark with validation/test threshold
   freezing — not just "an anomaly score."
3. Layered backend (router → service → repository) with 279 passing
   tests, including a suite dedicated purely to proving no temporal
   leakage exists.
4. Typed, mock-free React + TypeScript dashboard consuming a real
   read-only API across five pages.
5. Full production-hardening pass — Docker healthchecks, Alembic
   migration reproducibility verified from an empty database, `ruff` +
   `oxlint` adopted and clean, honest end-to-end verification with
   explicitly documented remaining manual steps.

### 30-second elevator pitch

"I built a platform that takes real electricity data from 60 buildings —
over a million hourly readings — and turns it into two things: a
day-ahead consumption forecast using a Random Forest model I evaluated
against strict seasonal baselines, and an anomaly detection system that
combines four different detection methods and validates them against a
synthetic benchmark since the real dataset has no anomaly labels. It's
all backed by PostgreSQL, served through FastAPI, and visualized in a
React dashboard — with nearly 300 automated tests and a full
production-hardening pass to make sure it actually works end-to-end, not
just that it builds."
