# Portfolio Guide: explaining this project to a recruiter or professor

A concise reference for talking about this project out loud. Every number
here is repeated from (not invented beyond) `docs/PROJECT_REPORT.md`,
`docs/eda.md`, `docs/forecasting.md`, `docs/anomalies.md`,
`docs/external_inference.md`, and `docs/architecture.md` -- if you want
the full derivation of any claim below, those documents have it.

## 30-second explanation

"I built a full-stack energy analytics platform on real building data:
ingestion, a PostgreSQL database, a FastAPI backend, a day-ahead
electricity forecasting model, four different anomaly detectors, and a
React dashboard -- plus a feature that lets an external company upload its
own data and get a real forecast from the trained model without
retraining it. It runs locally with one command, `docker compose up`, and
has 310 backend tests and 29 frontend tests."

## 1-minute explanation

"The data is real -- 60 buildings, over a million hourly electricity
readings, from the Building Data Genome Project 2 research dataset, not
synthetic. I built an ingestion pipeline that cleans and loads it into
PostgreSQL, handling real messiness like daylight-saving-time timestamp
ambiguity. On top of that I trained a Random Forest to forecast a
building's next 24 hours of consumption -- I specifically designed the
train/validation/test split around the forecast *origin*, not the target
time, because an earlier version of my own split had a subtle leakage bug
I found and fixed. I also built four different anomaly detectors and
evaluated them against a synthetic benchmark since the real data has no
labeled anomalies. All of that is exposed through a read-only FastAPI
backend and a six-page React dashboard. The last piece is an
'external-company inference' feature: someone can upload a completely
different building's data -- one the model has never seen -- and get a
real forecast, proving the model generalizes as a *mechanism* without
retraining, while being explicit that accuracy on genuinely new data
isn't guaranteed the way it is on the evaluated buildings."

## 3-minute explanation

Start with the 1-minute version, then add:

- **Why this dataset**: BDG2 is a real, citable, public research dataset
  (Miller et al., *Scientific Data* 2020) -- not scraped, not synthetic,
  not a toy CSV. The 60-building subset was chosen algorithmically
  (smallest sites first, filtered by data-quality thresholds), documented
  in `docs/data_selection.md`, not hand-picked to look good.
- **The leakage-prevention story** is a real engineering decision worth
  telling in full: the original split was by target timestamp, which let
  a single 24-hour forecast straddle the train/validation boundary. The
  fix was splitting by forecast *origin* instead. This is exactly the
  kind of subtle bug that's easy to miss and expensive in a real ML
  system -- and it's caught by a dedicated leakage test suite, not just
  fixed once and hoped to stay fixed.
- **Model selection was principled, not cherry-picked**: two seasonal-
  naive baselines (24h and 168h lookback) plus three real models (Ridge,
  Random Forest, HistGradientBoosting) were all evaluated the same way.
  Random Forest won on validation RMSE among models that beat both
  baselines (26.1 kWh vs. the best baseline's 65.9 kWh) -- the selection
  criterion was fixed *before* looking at results.
- **Anomaly detection has no ground truth in the real data**, so all four
  detectors are evaluated against a synthetic injection framework (six
  anomaly types, seeded, frozen thresholds selected on validation before
  touching test) rather than an unfalsifiable "trust me, it works" claim.
- **A real methodological bug was found and fixed, not hidden**: a
  stuck-meter check initially had a 35% false-positive rate, traced to
  ~21 of 60 sensors reporting daily-resolution readings (not actual meter
  faults) once reindexed to an hourly grid. Documented in
  `docs/anomalies.md` section 3, not smoothed over.
- **External-company inference is the newest, most "production-shaped"
  piece**: a CSV/JSON adapter, a `POST /api/v1/forecast` endpoint, and a
  dashboard page all share exactly one validation/inference
  implementation (`forecasting/external.py`) -- proven by a dedicated
  test suite that spies on `.fit()` to guarantee no retraining ever
  happens and hashes the model artifact before/after to prove it's never
  modified.
- **This is a local-only, portfolio-scoped project on purpose** -- no
  authentication, no cloud hosting, explicitly documented limitations
  rather than an oversold "production-ready" claim.

## Architecture explanation

```
BDG2 dataset -> ingestion (clean, dedupe, DST-aware UTC normalization) -> PostgreSQL
                                                                              |
                                        +-------------------------------------+-------------------------------------+
                                        v                                                                           v
                          Forecasting (train/predict, offline CLI)                              Anomaly detection (evaluate/detect, offline CLI)
                                        |                                                                           |
                                        +-------------------------------------+-------------------------------------+
                                                                              v
                                                              FastAPI (read-only REST API)
                                                                              |
                                                                              v
                                                              React + TypeScript dashboard
```

Key architectural decision: **offline batch jobs vs. online API, strictly
separated**. Ingestion, model training, prediction generation, and
anomaly detection all run as one-off CLI commands against the database.
The API and dashboard are pure read-only consumers of whatever those jobs
already wrote -- no endpoint ever triggers training or detection. This
keeps API latency predictable (always just SQL reads) and matches how
this kind of system would actually run in practice (scheduled batch jobs
feeding a live dashboard).

Layering within the API itself: `router -> service -> repository ->
SQLAlchemy/PostgreSQL`, enforced by convention across every endpoint (see
`docs/architecture.md`).

## Dataset explanation

Building Data Genome Project 2: 3 sites, 60 buildings, 1,052,400 hourly
electricity readings, 51,931 hourly weather readings, 2016-2018. Real
data, not synthetic -- with real messiness (daylight-saving transitions,
missing readings, a handful of sensors reporting at daily rather than
hourly resolution) that the ingestion pipeline has to handle correctly,
not idealized away.

## Forecasting explanation

Day-ahead, direct multi-horizon: given everything known at an origin
hour, predict all 24 of the next hourly values in one pass, with horizon
(1-24) as an explicit input feature rather than a recursive one-step
model or 24 separate models. 25 features: 11 calendar features (from the
target hour, legitimately knowable in advance), 6 lag features, 3 rolling
statistics, 4 static building-metadata features, plus horizon itself. No
weather features (a deliberate, documented choice -- weather correlated
weakly, |r| <= 0.30, with consumption in this dataset's EDA). Random
Forest selected over two seasonal-naive baselines and two other models on
validation RMSE (26.1 kWh vs. 65.9 kWh for the best baseline).

## Leakage prevention explanation

Three specific, testable guarantees: (1) the train/validation/test split
is by forecast *origin*, not target timestamp, so one 24-hour forecast
never straddles a split boundary; (2) lag and rolling features are
computed as shift-then-rolling, so they can never see the current or a
future value; (3) the preprocessing pipeline's imputer/scaler/encoder are
fit only on the training split, never on validation or test data. All
three are enforced by a dedicated leakage test suite, not just asserted
in documentation.

## Anomaly detection explanation

Four detectors with genuinely different failure modes: rule-based data
quality checks, a robust (median/MAD) behavioral z-score, a forecast-
residual check (reusing the trained Random Forest, never retraining it),
and an Isolation Forest (the one genuinely multivariate detector, 10
engineered features, fit on train-period data only). Evaluated against a
synthetic anomaly-injection benchmark since BDG2 has no real labeled
anomalies. Detector A (data quality) had the highest validation F1
(0.234) -- the honest picture is per-anomaly-type performance, not a
single "best detector" ranking (see `docs/anomalies.md` section 5).

## External inference explanation

A separate capability: score a company that was **never** in the training
data, using a CSV of its own recent hourly readings plus a few metadata
fields, against the *same already-trained* Random Forest. Requires at
least 168 consecutive hourly observations (the model's lag/rolling
warm-up window). Available three ways -- a CLI script, a
`POST /api/v1/forecast` endpoint, and a dashboard page -- all backed by
exactly one shared validation/inference implementation, so there's no
risk of the CLI and the API silently disagreeing about what's valid.
Proven to never retrain (a test suite spies on `RandomForestRegressor.fit`
across every code path) and to never modify the model artifact (hash
compared before/after).

## Major engineering decisions

- **Origin-based, not target-based, train/val/test split** (see Leakage
  prevention above) -- a real bug found and fixed, not a decision made
  correctly the first time.
- **Direct multi-horizon forecasting with horizon as a feature**, not
  recursive prediction or 24 independent models -- more sample-efficient,
  avoids recursive error accumulation.
- **Offline batch jobs strictly separated from the online API** -- no
  endpoint ever trains or retrains anything.
- **Idempotent writes everywhere**: ingestion, prediction backfill, and
  alert generation all use `INSERT ... ON CONFLICT DO UPDATE`, so
  re-running any job refreshes rather than duplicates data -- and a
  human's alert-resolution state is deliberately excluded from that
  upsert, so re-running detection can never silently un-resolve
  something a person already triaged.
- **One shared validation/inference implementation** for external
  company data, reused identically by the CLI, the API, and the
  dashboard -- not three parallel, potentially-diverging copies.
- **Explicit, tested non-goals**: no authentication, no cloud deployment,
  no weather features in forecasting -- each a documented decision, not
  an oversight discovered later.

## Testing strategy

**Backend: 310 tests.** Unit tests for pure logic (timestamp
normalization including DST edge cases, feature engineering, evaluation
metrics, each anomaly detector, the synthetic injection framework) plus
integration tests against a real, dedicated PostgreSQL test database --
verified to build correctly from a completely empty database via `alembic
upgrade head` alone. Includes a dedicated forecasting-leakage suite, an
Isolation Forest fit-contamination test that actually corrupts
validation-period data and confirms the train-fit model is unaffected
(rather than asserting it by convention), and a dedicated external-
inference suite that spies on `.fit()` and hashes the model artifact
before/after.

**Frontend: 29 tests** (Vitest + React Testing Library) -- the typed API
client's query building and error handling, formatting utilities,
loading/error/empty state rendering, and the External Forecast page's
full flow (CSV parsing, loading/result/error states, a rendered 24-point
forecast, warning surfacing) with the API call mocked, never hitting a
live backend from a unit test.

## Limitations (say these before being asked)

- Forecast API coverage only spans 2017-07-01 through 2018-01-01 (the
  validation+test window), not the full loaded history.
- Anomaly-detector precision is modest in absolute terms (7-23% at best)
  against the synthetic benchmark -- expected at <1% synthetic anomaly
  prevalence, and partly an artifact of the synthetic-only ground truth
  not crediting real, pre-existing BDG2 data-quality issues Detector A
  also (correctly) flags.
- Isolation Forest underperforms the other three detectors here -- a real
  property of a multivariate detector evaluated against largely
  univariate injected anomalies, not a bug.
- No authentication; not a multi-tenant production service.
- External-company inference proves the mechanism, not guaranteed
  accuracy on an arbitrary company's real distribution.
- Local-only: no cloud deployment, no public URL, by design.

## Likely professor questions and concise answers

**"How do you know there's no data leakage?"** A dedicated test suite
checks it directly: lag/rolling features are asserted to never exceed
their origin's value end-to-end, the split boundary is asserted to never
divide a single origin's 24 target rows across two splits, and the
preprocessing pipeline's fitted state is asserted to come only from
training-split data.

**"Why Random Forest and not a neural network / more complex model?"**
Model selection was empirical, not aesthetic: baselines and three
candidate models were evaluated under an identical, leakage-safe
pipeline, and Random Forest had the lowest validation RMSE among models
that beat both seasonal-naive baselines. A more complex model wasn't
justified by the validation results actually obtained.

**"How do you evaluate anomaly detection without labeled anomalies?"** A
synthetic injection framework: six anomaly types are seeded into a copy
of the data (never written into the real `energy_measurements` table),
detector thresholds are selected on a validation split and then frozen
before ever touching the test split, and precision/recall/F1 are computed
against the known-injected ground truth.

**"What would you do differently / what's the biggest weakness?"** The
anomaly detectors' precision against the synthetic benchmark is
genuinely modest, and I say so directly rather than picking a rosier
metric -- the honest next step would be either a better benchmark
(real near-miss labels from domain experts) or accepting that
univariate injected anomalies structurally favor univariate detectors
over the Isolation Forest.

## Likely recruiter questions and concise answers

**"Is this a real dataset or did you make it up?"** Real, public,
citable research data (BDG2, Miller et al. 2020) -- 60 real buildings,
over a million real hourly readings, not synthetic or scraped.

**"Is this deployed somewhere I can click on?"** No -- it's a local-only
Docker Compose application by design (see README Deployment section);
some cloud-deployment exploration exists in the project's Git history as
an architecture exercise, but nothing is currently hosted.

**"What's the tech stack?"** Python/FastAPI/PostgreSQL/SQLAlchemy/
Alembic/pandas/scikit-learn on the backend; React/TypeScript/Vite/
Recharts on the frontend; Docker Compose to run it all together.

**"How long did this take / how big is it?"** [Answer from your own
experience building it -- this guide doesn't claim a specific timeline
since that wasn't independently measured/logged the way the technical
metrics above were.]

**"Can I see the tests actually pass?"** Yes -- `docker compose run --rm
backend pytest -v` (310 tests) and `cd frontend && npm test` (29 tests)
both run against the real repository, not a cherry-picked subset.
