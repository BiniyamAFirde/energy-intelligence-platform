# External inference (CLI and API)

This document describes two interfaces to the same external-company
inference service, `energy_platform.forecasting.external`: the CLI
(`scripts/predict_external.py`, external CSVs in, `forecast.csv` out) and
the API (`POST /api/v1/forecast`, JSON in, JSON out). Both let a company
score its own building energy data against the already-trained forecasting
model **without** first loading that company into the BDG2/PostgreSQL
schema, and both call the exact same validation, feature-generation, and
prediction code underneath -- see section 8. This follows on from
`tests/integration/test_forecasting_external_building_inference.py`
(Phase 11), which proved the trained artifact scores an unseen building once
it *is* in the database; this interface removes that requirement entirely.

## 1. What external inference means here

"External inference" means: take a company's own recent hourly meter
readings and building metadata, run them through the exact same
feature-engineering code and the exact same trained
`models/random_forest_v1.joblib` Pipeline the production system uses for
BDG2 buildings, and get back a 24-hour forecast -- with **zero** database
writes, zero rows in `sites`/`buildings`/`sensors`/`energy_measurements`,
and zero changes to the model. It is pure, read-only, offline inference: an
adapter that reshapes external CSVs or JSON requests into the internal shape the existing
pipeline already expects, then calls `.predict()`.

## 2. Required input files

Two CSVs, per building:

- an **energy file** (e.g. `energy.csv`): the building's own recent hourly
  meter readings.
- a **building file** (e.g. `building.csv`): exactly one row of building
  metadata.

Working examples: `examples/external_company/energy.csv` and
`examples/external_company/building.csv`.

This interface scores **one building per run**. Scoring a portfolio means
running the CLI once per building.

## 3. Required columns

**`energy.csv`**

| column | meaning |
|---|---|
| `timestamp` | naive local time, `YYYY-MM-DD HH:MM:SS` (see section 5) |
| `energy_kwh` | hourly electricity consumption, kWh, non-negative |

**`building.csv`** (single row)

| column | meaning |
|---|---|
| `building_code` | any identifier you choose; carried through to the output, never validated against BDG2 |
| `area_sqm` | floor area, m², positive number |
| `number_of_floors` | positive number |
| `occupants` | positive number |
| `primary_use` | building-use category (section 6) |
| `timezone` | IANA timezone name, e.g. `US/Eastern` |

These six metadata fields are all **required** here, even though production
BDG2 ingestion tolerates missing `number_of_floors`/`occupants` (median-
imputed at training time -- `dataset.py`'s `STATIC_NUMERIC_IMPUTED`). That
tolerance exists for BDG2's real, already-known gaps; silently reusing
BDG2's own training medians for an unrelated external building would be
exactly the kind of fabricated value this interface is designed to avoid.
If a value isn't genuinely known, that building isn't a fit for this
interface yet.

## 4. Minimum history requirement

**168 consecutive, gap-free hourly readings**, taken directly from
`features.py`'s `MAX_LOOKBACK_HOURS` (the same constant the production
pipeline uses for `lag_168`/`rolling_168h_mean`) -- not a separately chosen
number. In practice, provide a little more than 168h so a local-midnight
forecast origin actually falls inside the warmed-up window; the bundled
example uses 216h (9 days). Gaps are never filled or fabricated: an
incomplete history is rejected with a clear error, not silently patched.

## 5. Timestamp / timezone requirements

- **Accepted format**: naive local time, `YYYY-MM-DD HH:MM:SS`, no UTC
  offset and no `Z` suffix. A timestamp carrying an explicit offset is
  **rejected**, not silently reinterpreted -- this interface never guesses
  whether a stray offset should override or be ignored.
- **Timezone information**: supplied once, in `building.csv`'s `timezone`
  column (an IANA name), and applied to every row of `energy.csv`. This
  mirrors the exact convention BDG2 ingestion already uses
  (`ingestion/transform.py`'s `normalize_timestamps_to_utc`, reused
  unchanged here): source timestamps are naive local clock time, localized
  and converted to UTC once, not assumed to already be UTC.
- **DST handling**: `normalize_timestamps_to_utc`'s existing rules apply
  unchanged --
  - a "fall back" ambiguous local time (occurs twice) cannot be resolved
    automatically. Production BDG2 ingestion silently drops such rows and
    counts them; this interface is stricter and **raises** instead, since
    silently losing hours here could silently invalidate the 168-hour
    completeness requirement without the caller ever finding out.
  - a "spring forward" gap (a local time that never happened) is shifted
    forward to the next valid instant, exactly as in production. If that
    shift causes two distinct local readings to land on the same UTC
    instant, the collision is detected after conversion and **raised** --
    this interface never silently picks one of the two.
- **Output timestamp timezone**: every timestamp in the output CSV
  (`target_ts`, `generated_at`) is **UTC**, matching the `predictions`
  table's convention elsewhere in this project.
- **Forecast origin**: the existing pipeline only ever forecasts from
  **local midnight** (`features.py::filter_origins_to_local_midnight`) --
  this is a hard assumption of the reused feature-generation code, not
  something this adapter changes. If no local-midnight origin exists with a
  full, gap-free 168h history behind it, no forecast can be produced (see
  section 11).

## 6. Supported building metadata

`area_sqm`, `number_of_floors`, `occupants` are validated as positive
numbers only -- the model itself imposes no fixed valid range (see
`docs/PROJECT_REPORT.md`'s generalization-risk discussion for why
extrapolating far outside BDG2's observed building sizes is a real
accuracy risk).

`primary_use` is checked against the categories the loaded artifact's
`OneHotEncoder` actually saw during training (introspected from the
artifact itself, not hardcoded). The encoder was fit with
`handle_unknown="ignore"`, so an unrecognized `primary_use` is **not**
rejected -- consistent with how production `predict.py` already behaves for
any building. Instead, the CLI prints a clear warning and the output still
contains a full 24-hour forecast, understood to be lower-confidence.

## 7. Prediction horizon

Unchanged from production: one forecast **origin** (the most recent
available local midnight), 24 rows, one per **horizon** `1..24`, each
predicting `consumption_kwh` at `origin + horizon` hours -- the same
direct, horizon-as-feature multi-step strategy documented in
`docs/forecasting.md`. This interface does not add, remove, or change any
horizon semantics.

## 8. Example CLI command

```
python scripts/predict_external.py \
    --energy examples/external_company/energy.csv \
    --building examples/external_company/building.csv \
    --output forecast.csv
```

Run from the repo root; equivalently inside the backend container:

```
docker compose run --rm backend python /path/to/scripts/predict_external.py \
    --energy examples/external_company/energy.csv \
    --building examples/external_company/building.csv \
    --output forecast.csv
```

Sample terminal output:

```
Loading model artifact: models/random_forest_v1.joblib (once per run; this may take a while)...
Input building: external_company_001 (primary_use='Office', timezone=US/Eastern)
Observations: 216 hourly readings (2024-06-01 04:00:00+00:00 .. 2024-06-10 03:00:00+00:00)
History duration: 8 days 23:00:00
Forecast origin (local midnight, most recent): 2024-06-09 04:00:00+00:00
Model artifact: models/random_forest_v1.joblib (random_forest v1)
Predictions generated: 24
Output written to: forecast.csv
```

No raw company energy values are ever printed to the terminal -- only
aggregate counts, date ranges, and metadata.

## 9. Example output

`forecast.csv`:

```
building_code,target_ts,horizon,predicted_kwh,model_name,model_version,generated_at
external_company_001,2024-06-09 05:00:00+00:00,1,89.86,random_forest,v1,2024-06-09 04:00:00+00:00
external_company_001,2024-06-09 06:00:00+00:00,2,85.74,random_forest,v1,2024-06-09 04:00:00+00:00
...
external_company_001,2024-06-10 04:00:00+00:00,24,90.74,random_forest,v1,2024-06-09 04:00:00+00:00
```

24 rows for the one building/origin scored.

## 10. API usage

`POST /api/v1/forecast` is the API equivalent of the CLI: same building
metadata, same energy observations, same validation, same model, same 24
predictions -- just JSON in, JSON out, instead of CSV files. Where the CLI
looks like:

```
CLI: external CSV files -> validate/normalize -> feature generation -> pipeline.predict() -> forecast.csv
```

the API looks like:

```
API: JSON request body -> validate/normalize -> feature generation -> pipeline.predict() -> JSON response
```

Both arrows after "validate/normalize" are the literal same function calls
(`dataset.build_feature_frame`, `pipeline.predict()`) -- see section 8.
Nothing in the forecasting logic differs between the two.

**Request** (`ExternalForecastRequest`, see `/docs` for the full generated
schema):

```json
{
  "building": {
    "building_code": "company_001",
    "area_sqm": 2500,
    "number_of_floors": 4,
    "occupants": 180,
    "primary_use": "Office",
    "timezone": "US/Eastern"
  },
  "energy": [
    {"timestamp": "2024-06-01 00:00:00", "energy_kwh": 82.4},
    {"timestamp": "2024-06-01 01:00:00", "energy_kwh": 79.1},
    ...
  ]
}
```

`energy` accepts at most **8760 observations** (one year of hourly
readings) per request -- a deliberate upper bound (`MAX_ENERGY_OBSERVATIONS`
in `schemas/external_forecast.py`) protecting the process from an
accidental or malicious multi-million-row payload, well above the 168-hour
minimum any real request needs.

**Example call:**

```
curl -X POST http://localhost:8000/api/v1/forecast \
    -H "Content-Type: application/json" \
    -d @request.json
```

**Response** (`ExternalForecastResponse`) -- 200 on success:

```json
{
  "building_code": "company_001",
  "model_name": "random_forest",
  "model_version": "v1",
  "forecast_origin": "2024-06-09T04:00:00Z",
  "predictions": [
    {"target_ts": "2024-06-09T05:00:00Z", "horizon": 1, "predicted_kwh": 89.86},
    ...
    {"target_ts": "2024-06-10T04:00:00Z", "horizon": 24, "predicted_kwh": 90.74}
  ],
  "warnings": []
}
```

No `sensor_id`, `building_id`, or any other internal database identifier is
ever returned -- external companies are not registered in the database, so
there is nothing to return. `warnings` carries the same unrecognized-
`primary_use` notice the CLI prints (section 6); it's an empty list when
there's nothing to flag.

**Validation failures** return **422** with `{"detail": "<the same message
the CLI would print>"}` -- both interfaces raise the identical
`external.ExternalDataError` internally (a global FastAPI exception handler
in `main.py` converts it to the HTTP response), so an invalid request
produces the same complaint whether it came in as a CSV or as JSON. A few
checks (missing required fields, wrong JSON types) are instead caught by
Pydantic itself before reaching that shared validator, and return FastAPI's
own 422 shape -- still a 4xx with a clear message, just phrased by Pydantic
rather than by `external.py`.

**Model loading**: the ~552MB Pipeline is loaded exactly **once**, at
application startup (`main.py`'s `lifespan`), via a process-level cached
singleton (`external.get_cached_pipeline()`), and reused for every request
through `app.state.forecast_pipeline` -- never re-deserialized per request.
If the artifact can't be loaded, the application **fails to start**
(no silent fallback). See section 13 for the full CLI/API/deployment
relationship.

## 11. Important limitations

- **One building per run/request.** No batch/multi-building support yet on
  either interface -- documented scope, not an oversight.
- **No weather features.** The model was trained without weather inputs at
  all (see `docs/forecasting.md`); this adapter cannot and does not
  compensate for a company's climate being different from BDG2's three
  sites.
- **`primary_use` vocabulary is fixed to BDG2's training categories.** An
  unrecognized value degrades silently at the model level (section 6);
  this adapter's only mitigation is surfacing a warning, not fixing the
  underlying limitation.
- **No accuracy guarantee on out-of-distribution buildings.** Nothing here
  changes the generalization risks already identified in
  `docs/PROJECT_REPORT.md` (building-size extrapolation, occupancy-pattern
  differences, no per-building confidence signal, etc.) -- this interface
  proves the *mechanism* works, not that the *numbers* will be as accurate
  as they are for BDG2 buildings.
- **Stricter validation than production ingestion in a few places**
  (DST-ambiguous timestamps, negative values, incomplete metadata) --
  intentional, and documented in section 5/3 above, but worth knowing if
  you're comparing behavior against `ingestion/loader.py`.
- **~552MB artifact load time.** `joblib.load` of `random_forest_v1.joblib`
  takes tens of seconds; both interfaces load it once per process (the CLI
  once per invocation, the API once at application startup), never per
  prediction.
- **No authentication on the API.** `POST /api/v1/forecast` is open to
  anyone who can reach the server -- explicitly out of scope for this
  phase (section 13).

## 12. Inference vs. retraining

This interface **never** calls `.fit()` or `.fit_transform()` on anything,
and never modifies `models/random_forest_v1.joblib`. Every prediction is a
pure forward pass through the Pipeline `train.py` already fit on BDG2 data.
Scoring a new company's building does not teach the model anything about
that building, and running this CLI a thousand times against a thousand
different companies would leave the artifact byte-for-byte identical --
verified directly by `tests/unit/test_external_inference.py`'s and
`tests/integration/test_api_external_forecast.py`'s artifact-hash/mtime
checks and `RandomForestRegressor.fit` spies. The API's own model-loading
strategy (section 10) makes the same guarantee explicit at the process
level: `joblib.load` runs once at startup, so no request -- the first or
the ten-thousandth -- ever triggers training or reloading.

## 13. Why this is not yet a *deployed, production* service

Both interfaces exist now (CLI and API), but neither is deployed as a
production service yet -- deliberately out of scope for this phase:

- **No authentication or authorization** on `POST /api/v1/forecast` --
  anyone reaching the server can call it.
- **No rate limiting or request queueing** -- a burst of large requests
  could still be slow (bounded by the 8760-observation cap, section 10,
  but not throttled).
- **No cloud deployment.** The API runs in the same local Docker Compose
  setup as the rest of this project -- no managed hosting, no autoscaling,
  no model registry/versioned rollout. Docker access to the model artifact
  itself works today because `docker-compose.yml`'s `backend` service
  already bind-mounts the repo-root `models/` directory to `/app/models`
  (`- ./models:/app/models`); `train.MODELS_DIR = Path("models")` resolves
  against the container's `/app` working directory, landing on that mount.
  This was already true before this phase (Phase 7's `predict.py` relies
  on the same mount) and needed no change here. It does mean the artifact
  is **not** baked into the Docker image itself (`models/*` is gitignored,
  section "Do NOT commit the large trained model" above) -- a real
  deployment without this local bind mount (e.g. a cloud container with no
  access to this machine's filesystem) would need its own way to supply
  the artifact (object storage, a volume populated by a training/release
  pipeline, etc.), which is exactly the kind of cloud model-storage
  question this phase explicitly leaves for later.
- **Single model, no A/B or shadow-scoring path.** `model_name` is always
  `"random_forest"`; there is no mechanism to route a request to a
  different model version.

The architecture is deliberately shaped so adding these later doesn't
require touching the inference logic itself: auth would sit in FastAPI
middleware/dependencies in front of the existing router, rate limiting in
front of that, and a cloud deployment would package the same Docker image
with the model artifact available via a real volume or object-storage
mount instead of a local bind mount -- none of that changes
`forecasting/external.py`, the router, or the service layer described
above.
