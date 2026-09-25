# Architecture

## System overview (Phase 9/10)

```
                    React + TypeScript dashboard (frontend/)
                                   |
                          typed API client (src/api/)
                                   |
                                   v
                    FastAPI routers  (read-only HTTP layer)
                                   |
                              Services
                                   |
                            Repositories
                                   |
                              PostgreSQL
                                   ^
                                   |
        ingestion / forecasting-train / forecasting-predict /
        anomaly-evaluate / anomaly-detect   (offline CLI jobs)
```

**Offline jobs vs. online API -- an explicit, deliberate split, not an
oversight.** Everything that writes to the database or produces a model
artifact (BDG2 ingestion, Random Forest training, day-ahead prediction
backfill, Isolation Forest fitting + threshold evaluation, running the
four anomaly detectors) is a `python -m energy_platform.<module>` CLI job,
run manually or on a schedule outside the request/response cycle. The API
and the dashboard that consumes it are **read-only**: no endpoint
triggers ingestion, training, prediction, or detection. This keeps
request latency predictable (a page load is always just SQL reads, never
"wait while a model retrains"), keeps the expensive/slow operations
(the Isolation Forest fit alone takes minutes) out of the request path
entirely, and matches how this system is actually meant to run --
periodic batch jobs feeding a live, fast, read-only dashboard, not
an online learning service.

## Layering (within the online API)

```
HTTP Router  (energy_platform/api/routers/*.py)
    - parses query/path params via FastAPI Query/Path
    - calls exactly one service function
    - returns its result through a Pydantic response_model
    - never imports SQLAlchemy or touches a session's queries directly

Service  (energy_platform/services/*.py)
    - business rules: "does this building exist", "is this date range valid",
      "which granularity was requested"
    - calls one or more repository functions
    - raises NotFoundError / InvalidParameterError (energy_platform/services/exceptions.py)
      rather than HTTP status codes -- services don't know about HTTP

Repository  (energy_platform/repositories/*.py)
    - SQLAlchemy queries only, nothing else
    - returns ORM objects, or plain dicts for aggregate/summary results
      that have no backing ORM class
    - never raises domain exceptions -- a missing row is just `None`

PostgreSQL
```

A request for `GET /api/v1/buildings/1/summary` flows:
`routers/energy.py:get_building_summary` → `services/energy_service.py:get_building_summary`
(resolves the building, resolves its electricity sensor, calls
`repositories/energy.py:summary`, shapes the result) → one aggregate SQL
query → back up through the same chain. The Phase 8 (`/alerts`) and Phase
9 (`/forecast`) endpoints added later follow this exact same chain --
router → service → repository → SQL -- with no exception to the pattern.

## Frontend (Phase 9)

`frontend/`: Vite + React + TypeScript, no server-side rendering, no
authentication (this is a read-only portfolio dashboard, not a multi-user
product). `src/api/` is a typed client -- one file per backend domain
(`sites.ts`, `buildings.ts`, `energy.ts`, `analytics.ts`, `alerts.ts`,
`forecast.ts`), each function's return type mirroring the corresponding
Pydantic schema field-for-field, plus a shared `client.ts` that centralizes
fetch/error handling (`ApiError` carries the backend's own `detail`
message through to the UI rather than a generic "something went wrong").
Five pages (Dashboard, Building Detail, Forecasting, Anomaly Monitoring,
Anomaly Detail), each composing that same typed client with shared
components (`KpiCard`, `TimeSeriesChart`, `CategoryBarChart`, `DataTable`)
-- no page talks to `fetch` directly.

## Errors

Domain exceptions are translated to HTTP responses by two global handlers
registered in `main.py`, not by try/except blocks scattered across routers:

| Exception | HTTP status | When |
|---|---|---|
| `NotFoundError` | 404 | site/building/sensor id doesn't exist, or a building has no energy data yet |
| `InvalidParameterError` | 400 | `start > end`, or an unrecognized `granularity` |
| (FastAPI/Pydantic validation) | 422 | a query param fails its own type/bound check (e.g. `limit` above the max) |

All three shapes return `{"detail": "..."}`, so API consumers handle errors
uniformly regardless of which layer raised them.

## Pagination and limits

Two different limit policies, both centralized in `api/deps.py` so every
router uses the same numbers instead of picking their own:

- **List endpoints** (`/sites`, `/buildings`, `/sensors`): `limit` defaults to
  50, capped at 200. Small tables (tens to low hundreds of rows), so this is
  mostly about consistent API shape, not performance.
- **Time-series endpoints** (`/energy`, `/energy/aggregate`, `/weather`):
  `limit` defaults to 1000, capped at 5000. A single building has at most
  ~17,544 hourly rows total (its full 2-year history) -- capping below that
  means a client fetching full history must page with `start`/`end` rather
  than pulling the whole table in one unbounded request.

List endpoints also return `total` alongside `items`, so a client can
compute whether more pages exist without a second request.

## Performance

`energy_measurements` and `weather_measurements` have composite primary keys
`(sensor_id, ts)` / `(site_id, ts)` (a Phase 2 design decision, not new in
Phase 5). Both of Phase 5's real query patterns -- "one building's readings
in a date range" and "one building's readings grouped by day/week" -- filter
on the id column first and range/group on `ts`, which is exactly what that
composite index serves. No new indexes were needed; `EXPLAIN ANALYZE`
against the live 1,052,400-row table confirms both use an index seek, not a
sequential scan:

**Time-range query** (`WHERE sensor_id = 1 AND ts BETWEEN ...`):
```
Limit  (actual time=3.379..4.821 rows=697 loops=1)
  -> Index Scan using energy_measurements_pkey on energy_measurements
       Index Cond: ((sensor_id = 1) AND (ts >= ...) AND (ts <= ...))
Execution Time: 4.950 ms
```

**Daily aggregation query** (`WHERE sensor_id = 1 GROUP BY date_trunc('day', ts)`,
no date filter -- the full 2-year history, the worst case for this endpoint):
```
Limit
  -> Sort
       -> HashAggregate
            -> Bitmap Heap Scan on energy_measurements
                 Recheck Cond: (sensor_id = 1)
                 -> Bitmap Index Scan on energy_measurements_pkey
                      Index Cond: (sensor_id = 1)
Execution Time: 54.886 ms
```

54.9ms for a full 17,540-row aggregation is acceptable for a synchronous API
endpoint; if this becomes a bottleneck under real load, the next step would
be a materialized daily-rollup table refreshed by the ingestion pipeline,
not a new index (the index is already doing its job -- the cost here is the
aggregation itself, over every row).

One existing index, `ix_energy_measurements_ts` (on `ts` alone), is **not**
used by any Phase 5 query -- it was added in Phase 2 for a hypothetical
future cross-sensor time-slice query (e.g. "every building's reading at a
given hour," relevant to anomaly detection later) that no current endpoint
performs. It's left in place rather than removed, since removing an index
that's justified by a *documented future need* isn't the same as adding one
that isn't justified by anything.

`buildings.site_id` has an index (`ix_buildings_site_id`, from Phase 2), but
at 60 rows Postgres correctly chooses a sequential scan over it:
```
Seq Scan on buildings  (actual time=1.669..1.705 rows=30 loops=1)
  Filter: (site_id = 3)
Execution Time: 1.806 ms
```
This is expected planner behavior, not a problem -- the index becomes useful
automatically once the table has enough rows to make a scan more expensive
than a seek. No action needed.

## API endpoints (Phase 5)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness |
| GET | `/health/db` | readiness (DB reachable) |
| GET | `/api/v1/sites` | list sites, paginated |
| GET | `/api/v1/sites/{site_id}` | one site |
| GET | `/api/v1/buildings` | list buildings, filter by `site_id`/`primary_use`, paginated |
| GET | `/api/v1/buildings/{building_id}` | one building (full retained metadata) |
| GET | `/api/v1/sensors` | list sensors, filter by `building_id`, paginated |
| GET | `/api/v1/sensors/{sensor_id}` | one sensor |
| GET | `/api/v1/buildings/{building_id}/energy` | hourly readings, `start`/`end`/`limit` |
| GET | `/api/v1/buildings/{building_id}/energy/aggregate` | grouped by `hourly`\|`daily`\|`weekly`, `start`/`end`/`limit` |
| GET | `/api/v1/buildings/{building_id}/summary` | count/mean/min/max/stddev/first/last/missing |
| GET | `/api/v1/sites/{site_id}/weather` | hourly weather, `start`/`end`/`limit` |

### Analytics endpoints (Phase 6)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/buildings/compare` | cross-building table: mean/median/total/peak/std kWh, area, CV; filter by `site_id`/`primary_use` |
| GET | `/api/v1/buildings/{building_id}/analytics/summary` | richer than `/summary`: adds median, total, coefficient of variation |
| GET | `/api/v1/buildings/{building_id}/analytics/profile` | hour-of-day, day-of-week, month, weekday/weekend -- all in the building's local time |
| GET | `/api/v1/buildings/{building_id}/analytics/peaks` | top-N peaks + one peak per calendar month, `top_n` (default 10, max 100) |
| GET | `/api/v1/sites/{site_id}/analytics/weather-energy` | Pearson correlation of site-wide consumption vs. weather, with sample sizes |

**Registration-order gotcha**: `/api/v1/buildings/compare` and
`/api/v1/buildings/{building_id}` are structurally ambiguous to Starlette's
router (both are one path segment after `/buildings/`) -- it matches by
registration order and only coerces the path param's type *after* matching,
so `analytics.router` (which owns `/compare`) must be `include_router`'d
before `buildings.router` in `main.py`, or a request to `/compare` would be
captured by `{building_id}` and rejected with a 422 before ever reaching the
right route. Covered by a regression test
(`test_get_buildings_compare_route_not_shadowed_by_building_id_route`).

**Timezone handling in analytics**: `hour_of_day_profile`, `day_of_week_profile`,
`monthly_profile`, and `weekday_weekend_profile` (`repositories/analytics.py`)
all convert `ts` to the building's site's local time via Postgres
`ts AT TIME ZONE tz_name` before grouping. Grouping on the raw UTC timestamp
would misattribute an occupancy pattern to the wrong local hour -- verified
directly: a reading at `2016-06-15 16:00:00 UTC` groups to local hour 12 for
a `US/Eastern` site and local hour 16 for a `UTC` "site" in the same test,
proving the conversion actually runs rather than silently being a no-op.

### Performance (Phase 6 analytics queries)

Measured against the live 1,052,400-row `energy_measurements` table:

| Query | Time | Plan |
|---|---|---|
| Hour-of-day profile, one sensor | 26ms | Bitmap Index Scan on the composite PK, then HashAggregate |
| Building comparison, site-filtered (~9 buildings) | 353ms | Nested loop + Index Scan per sensor; `percentile_cont` (median) requires a per-group sort, which dominates the cost |
| Building comparison, **unfiltered** (all 60 buildings, full table) | **1.8s** (Phase 6) / **~2.3s** (Phase 10 re-measurement, see below) | Full cross-building aggregation -- no filter exists to make this selective, since the query needs every sensor's data |
| Site weather-energy join | 334ms | Hash Join; `weather_measurements` filtered by `site_id` via Seq Scan (see below) |

**No new indexes added.** The unfiltered building-comparison case is the
one honest bottleneck, but it's inherent to "aggregate across every
sensor" -- no B-tree index changes that; the real fix, if this becomes a
problem under load, is a materialized daily-rollup table refreshed by the
ingestion pipeline (same conclusion as the Phase 5 aggregation note), not an
index. The weather-energy join's Seq Scan on `weather_measurements` is also
correct planner behavior, not a missing index: `site_id` is the leading
column of that table's composite PK, but at ~32% selectivity (16,856 of
52,007 rows for one site) Postgres correctly judges a sequential scan
cheaper than walking the index.

### Performance re-measured (Phase 10)

Re-measured directly via `EXPLAIN ANALYZE` (not wall-clock `curl`, which
is noisy under concurrent local Docker activity -- an initial `curl`-based
pass showed the unfiltered `/buildings/compare` case anywhere from 1.8s to
10s across repeated runs, which briefly looked like a real regression
worth investigating; two separate direct `EXPLAIN ANALYZE` runs before and
after refreshing table statistics (`ANALYZE`) showed an essentially
identical plan and cost either time (~2.3s execution both times) -- the
`curl` variance was transient host load from this session's own
concurrent activity, not a backend issue, and refreshing statistics
changed nothing material). Current numbers, all endpoints:

| Endpoint (worst realistic case) | Time |
|---|---|
| `/alerts` (default limit) | ~0.2s |
| `/alerts/summary` (full-table aggregate) | ~0.25s |
| `/buildings/{id}/energy` (max limit, 5000 rows) | ~0.2-1.2s |
| `/buildings/{id}/energy/aggregate` | ~0.05-0.1s |
| `/buildings/{id}/forecast` (max limit) | ~0.4-0.5s |
| `/buildings/{id}/alerts` (max limit) | ~0.03-0.2s |
| `/buildings/compare`, unfiltered (all 60 buildings) | **~2.3s** |

The unfiltered building-comparison case remains, as originally documented
in Phase 6, the one genuinely expensive endpoint -- a full cross-building
aggregation with a per-group median (`percentile_cont`, which needs a
per-group sort) over 1M+ rows, currently planned as a Nested Loop +
per-sensor Index Scan rather than the Parallel Seq Scan seen when this was
first measured in Phase 6 (a Postgres planner choice between two
similarly-costed strategies, not something this project's schema or query
controls directly). Every other endpoint responds in well under half a
second. No index or query change is justified by this: the conclusion is
identical to Phase 6's -- a materialized daily-rollup table is the correct
fix *if* this ever becomes a real bottleneck under load, not a smaller
patch now.

Interactive docs: `http://localhost:8000/docs` (Swagger UI) or `/redoc`.

## API endpoints (Phase 8: anomaly detection)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/alerts` | list alerts, filter by `sensor_id`/`method`/`anomaly_type`/`severity`/`resolved`/`start`/`end`, paginated |
| GET | `/api/v1/alerts/{alert_id}` | one alert (full detail: score, actual/expected/residual, explanation) |
| GET | `/api/v1/alerts/summary` | total/unresolved counts + breakdowns by method/anomaly_type/severity |
| GET | `/api/v1/buildings/{building_id}/alerts` | one building's alerts, same filters as `/alerts` |

Alerts are written only by the offline `anomalies.detect` CLI job (an
idempotent upsert keyed on `(sensor_id, ts, method, detector_version)` --
re-running detection refreshes a point's score/severity/explanation but
never resets `resolved`, so nothing in the read API or a re-detection run
can silently erase a human's triage state). See `docs/anomalies.md` for
the four detectors and their evaluated performance.

## API endpoints (Phase 9: forecasting)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/buildings/{building_id}/forecast` | day-ahead predictions for one building, joined against real `energy_measurements` for `actual_kwh`/`residual`; `start`/`end`/`limit` |

Reads only the `predictions` table Phase 7's `train`/`predict` CLI jobs
already populate -- this endpoint never trains or predicts anything
itself. `actual_kwh` always comes from `energy_measurements`, never the
unused `predictions.actual_kwh` column (the same principle Phase 8's
forecast-residual detector established); `residual = actual - predicted`;
`generated_at` (the forecast's origin) is verified to always precede
`target_ts` (the point predicted) by a dedicated regression test, so a
historical, backtested prediction can never be mistaken for a live one
(the origin is always strictly earlier).

## Production deployment architecture

Everything above describes the application; this section describes where
it runs when not local. Full procedure: `docs/deployment_cloud_run.md`
(backend), `docs/deployment_supabase.md` (database),
`docs/deployment_frontend.md` (frontend hosting options).

```
GitHub (main)
  |  push triggers a Cloud Build trigger (GitHub-connected, OAuth-based --
  |  no credential file ever stored in this repo)
  v
Cloud Build
  |  builds backend/Dockerfile (unchanged from local Docker Compose)
  v
Google Cloud Run              Supabase PostgreSQL
(Docker runtime, same          (managed, EU region)
 backend/Dockerfile as
 local Docker Compose,
 >=2GB RAM, min-instances 0)
      |
      v
GitHub Release asset
(random_forest_v1.joblib,
 downloaded + SHA256-verified
 once at container start --
 never in Git history)

Static frontend host (Cloudflare Pages recommended; GitHub Pages
documented but needs a BrowserRouter/subpath workaround -- see
docs/deployment_frontend.md), auto-deployed from the same GitHub repo.
```

The backend container is the *same* `backend/Dockerfile` used by
`docker compose up` locally -- not a second, deployment-specific image.
The only additions are (a) an entrypoint step
(`backend/docker-entrypoint.sh` + `docker_model_fetch.py`) that fetches
the model artifact if it isn't already present on disk -- a no-op locally,
since the host bind mount already provides it -- and (b) the Dockerfile's
default `CMD` reading the platform's `PORT` env var via a shell-form
command, which `docker-compose.yml`'s own `command:` override (hardcoded
to the container-internal port 8000) bypasses entirely for local
development. No forecasting, anomaly-detection, or API behavior changed
for deployment.

`DATABASE_URL` and `CORS_ORIGINS` are environment-driven in both
environments already (`energy_platform.config.Settings`) -- deployment
only means setting their values to Supabase's connection string and the
deployed frontend's URL instead of `localhost`, never a code change. Same
for the frontend's API base URL (`VITE_API_BASE_URL`, already
environment-driven since the external-inference dashboard page was added --
see `frontend/src/api/client.ts`).
