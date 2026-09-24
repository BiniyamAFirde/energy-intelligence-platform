# Deploying to Render

This document is the step-by-step companion to `render.yaml`. It exists
because a Blueprint file alone cannot fully automate this deployment: a
few steps genuinely require a human with Render Dashboard / GitHub access
(creating the GitHub Release asset, resolving the backend↔frontend URL
chicken-and-egg, running a one-time migration+data-import). Nothing in
this document has been executed against a live Render account by an
automated process -- every command below is written to be run by you.

## Architecture

```
GitHub (source + CI/CD source, this repo)
  |
  +--> Render Static Site   (React/Vite build, frontend/dist)
  |
  +--> Render Web Service   (FastAPI, Docker runtime, backend/Dockerfile)
  |         |
  |         v
  +--> Render PostgreSQL    (managed, Frankfurt)
```

The Random Forest model (`models/random_forest_v1.joblib`, ~552MB) is
deliberately **not** in Git history (see `.gitignore`). It is published as
a GitHub Release asset and downloaded once at container start by
`backend/docker_model_fetch.py` (invoked by `backend/docker-entrypoint.sh`
before uvicorn starts) -- see Phase 3 below.

Region: **Frankfurt** for the backend and database (closest Render region
to Italy). The static frontend is served from Render's global CDN
regardless of its configured region, so that choice doesn't affect
frontend latency.

## Prerequisites

- A Render account with a payment method on file (the backend and
  database plans below are paid -- see "Why not free tier" below).
- This repository pushed to GitHub and connected to your Render account.
- `git`, and either the [GitHub CLI](https://cli.github.com/) (`gh`) or
  browser access to GitHub, for Phase 3.
- `psql`/`pg_dump` (ships with PostgreSQL; already available via
  `docker compose exec db ...` if you don't have a local install) for
  Phase 5's data migration.

### Why not Render's free tier

- **Backend (free/512MB)**: the model artifact alone is ~552MB once
  deserialized in memory (a fitted `RandomForestRegressor`'s in-memory
  tree structures are larger than its on-disk pickle size), which alone
  exceeds a 512MB instance. The Blueprint uses `1c-2g` (1 vCPU / 2GB RAM)
  -- verify this plan id is still current in the Render Dashboard before
  applying; Render's compute plan id list can change over time.
- **Postgres (free)**: Render's free Postgres instances **expire and are
  deleted after 30 days**. That's incompatible with a persistent portfolio
  demo. The Blueprint uses a small paid plan instead (see Phase 4).

## Phase 3 (of the main task): model delivery via GitHub Release

**Proposed release/tag/asset strategy** (shown here before creation, per
the task's own instruction):

| | Value |
|---|---|
| Tag | `model-v1` |
| Release title | `Random Forest model v1` |
| Release notes | "Trained artifact for `energy_platform.forecasting.train` MODEL_VERSION='v1', selected model 'random_forest'. SHA256: `2b859538307206b4516ff23c984878b1051a05cc910480ec9bdcd27ee83421a8`. Not part of Git history -- see .gitignore and docs/deployment_render.md." |
| Asset | `random_forest_v1.joblib` (the file itself, ~552MB / 527MiB) |
| Resulting download URL | `https://github.com/BiniyamAFirde/energy-intelligence-platform/releases/download/model-v1/random_forest_v1.joblib` |

This repository is public, so that URL is downloadable with a plain
`GET` request -- no GitHub token needed at deploy time.

**GitHub CLI was not installed/authenticated in the environment this was
prepared in** (`gh --version` → command not found), so the release was
**not** created automatically. This section is the exact procedure to run
it yourself.

### Option A: GitHub CLI

```bash
# from the repo root, with the model present at models/random_forest_v1.joblib
gh auth login          # if not already authenticated
gh release create model-v1 \
    models/random_forest_v1.joblib \
    --title "Random Forest model v1" \
    --notes "Trained artifact for energy_platform.forecasting.train MODEL_VERSION='v1', selected model 'random_forest'. SHA256: 2b859538307206b4516ff23c984878b1051a05cc910480ec9bdcd27ee83421a8. Not part of Git history -- see .gitignore and docs/deployment_render.md."
```

### Option B: GitHub web UI

1. Go to `https://github.com/BiniyamAFirde/energy-intelligence-platform/releases/new`.
2. Tag: `model-v1` (target: `main`).
3. Title: `Random Forest model v1`.
4. Body: paste the release-notes text from the table above.
5. Drag `models/random_forest_v1.joblib` into the "Attach binaries" area.
6. Publish release.

### After the release exists

Verify the published asset matches what's expected before pointing Render
at it:

```bash
curl -sL -o /tmp/downloaded.joblib \
    https://github.com/BiniyamAFirde/energy-intelligence-platform/releases/download/model-v1/random_forest_v1.joblib
sha256sum /tmp/downloaded.joblib   # or: shasum -a 256 /tmp/downloaded.joblib
# expected: 2b859538307206b4516ff23c984878b1051a05cc910480ec9bdcd27ee83421a8
rm /tmp/downloaded.joblib
```

Then set `MODEL_ARTIFACT_URL` to that download URL on the backend service
(Phase 4 below) -- it's marked `sync: false` in `render.yaml`, meaning
Render will prompt you to enter it in the Dashboard rather than reading it
from the Blueprint file.

## Phase 4: create the Render resources

### Option A: Blueprint (recommended)

1. Render Dashboard → **New** → **Blueprint**.
2. Connect the `BiniyamAFirde/energy-intelligence-platform` GitHub repo.
3. Render detects `render.yaml` at the repo root and proposes the three
   resources below. Review, then **Apply**.
4. Render will pause on any `sync: false` variable (`MODEL_ARTIFACT_URL`)
   and prompt you for a value -- paste the GitHub Release asset URL from
   Phase 3.

### Option B: manual Dashboard setup (if you prefer not to use Blueprints)

Create, in this order (the backend needs the database's connection
string; the frontend needs the backend's URL):

1. **PostgreSQL** -- New → PostgreSQL. Region: Frankfurt. Plan: smallest
   non-free/non-expiring tier. Name: `energy-platform-db`, database:
   `energy_platform`, user: `energy_user`.
2. **Backend web service** -- New → Web Service → Docker runtime →
   this repo, root `backend/`. Region: Frankfurt. Plan: `1c-2g` (or
   another ≥2GB plan). Health check path: `/health`. Environment
   variables: see the table in Phase 4.D below.
3. **Frontend static site** -- New → Static Site → this repo, root
   `frontend/`. Build command: `npm install && npm run build`. Publish
   directory: `dist`. Add a rewrite rule `/*` → `/index.html`.
   Environment variable: `VITE_API_BASE_URL` = the backend's Render URL
   from step 2.

### Resolving the backend ↔ frontend URL chicken-and-egg

Render assigns each service's `*.onrender.com` URL only once it exists.
`render.yaml` ships with **placeholder** URLs for `CORS_ORIGINS` (backend)
and `VITE_API_BASE_URL` (frontend) because neither service's real URL is
known until after the other is created. After both services exist:

1. Note the backend's actual URL, e.g. `https://energy-platform-backend.onrender.com`.
2. Note the frontend's actual URL, e.g. `https://energy-platform-frontend.onrender.com`.
3. Backend service → Environment → set `CORS_ORIGINS` to the frontend's
   real URL → save (triggers a redeploy).
4. Frontend service → Environment → set `VITE_API_BASE_URL` to the
   backend's real URL → save (triggers a **rebuild**, not just a restart --
   Vite bakes this in at build time).

### D. Backend environment variables

| Key | Source | Notes |
|---|---|---|
| `DATABASE_URL` | `fromDatabase` (Blueprint) or copy from the Postgres service's "Connections" tab | Never hardcode |
| `ENV` | `production` | |
| `LOG_LEVEL` | `INFO` | |
| `CORS_ORIGINS` | frontend's real Render URL | see above -- two-step resolution |
| `JWT_SECRET_KEY` | `generateValue: true` (Blueprint) or a random string you set | Declared by `Settings` but not read by any active auth code path (no authentication in this app) -- still required because it has no default |
| `JWT_ALGORITHM` | `HS256` | |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | |
| `MODEL_ARTIFACT_URL` | the GitHub Release asset URL from Phase 3 | `sync: false` -- set manually |
| `MODEL_ARTIFACT_SHA256` | `2b859538307206b4516ff23c984878b1051a05cc910480ec9bdcd27ee83421a8` | public checksum, not a secret |
| `PORT` | set automatically by Render | do not set manually |

## Phase 5: database migration strategy

### What's being migrated (measured locally, not assumed)

```bash
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "SELECT pg_size_pretty(pg_database_size(current_database()));"
# -> 308 MB

docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
SELECT 'sites' t, count(*) FROM sites
UNION ALL SELECT 'buildings', count(*) FROM buildings
UNION ALL SELECT 'sensors', count(*) FROM sensors
UNION ALL SELECT 'energy_measurements', count(*) FROM energy_measurements
UNION ALL SELECT 'weather_measurements', count(*) FROM weather_measurements
UNION ALL SELECT 'model_versions', count(*) FROM model_versions
UNION ALL SELECT 'predictions', count(*) FROM predictions
UNION ALL SELECT 'alerts', count(*) FROM alerts;"
```

Measured result (this environment): 3 sites, 60 buildings, 60 sensors,
1,052,400 energy measurements, 51,931 weather measurements, 6
model_versions, 241,680 predictions, 99,651 alerts. 11 tables total
(including `alembic_version`, `ingestion_runs`, `synthetic_anomalies`).
308MB comfortably fits Render's smallest paid Postgres tier.

### Procedure

**1. Let Render create an empty database**, then point Alembic at it and
create the schema (no data yet):

```bash
# From your machine, with Render's backend service's DATABASE_URL copied
# from its Dashboard "Environment" tab (or the Postgres service's
# "Connections" tab -- use the "External Database URL" for a connection
# from outside Render's network):
export RENDER_DATABASE_URL="postgresql://energy_user:...@....frankfurt-postgres.render.com/energy_platform"

cd backend
DATABASE_URL="$RENDER_DATABASE_URL" alembic upgrade head
```

(Alembic reads `DATABASE_URL` via `energy_platform.config.settings` --
see `backend/alembic/env.py` -- so overriding the env var for this one
command is enough; it does not touch your local `.env`.)

Alternatively, run this from the Render Dashboard's **Shell** tab on the
backend service itself (it already has `DATABASE_URL` set correctly in
its own environment):

```bash
alembic upgrade head
```

**2. Export the local demo database** (custom format -- compressed,
supports parallel restore, and is the `pg_restore`-native format):

```bash
mkdir -p /tmp/energy_platform_export   # NOT inside the repo -- never committed
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --format=custom --no-owner --no-privileges \
    --file=/tmp/dump.custom
docker compose cp db:/tmp/dump.custom /tmp/energy_platform_export/dump.custom
ls -lh /tmp/energy_platform_export/dump.custom
```

Check the resulting file size before proceeding. A compressed dump of
this dataset is expected to be well under 100MB (the 308MB figure above
is Postgres's on-disk size including indexes and page overhead, not the
compressed logical dump size) -- if it comes out dramatically larger than
that, **stop and re-read the "If the dump is too large" note below rather
than improvising.**

**3. Restore into Render Postgres:**

```bash
pg_restore --no-owner --no-privileges \
    --dbname="$RENDER_DATABASE_URL" \
    /tmp/energy_platform_export/dump.custom
```

`alembic_version` is part of the dump too, so this also confirms Render's
schema matches what Alembic already created in step 1 (if `pg_restore`
complains about `alembic_version` already existing, that's expected --
add `--clean --if-exists` to `pg_restore`'s flags, or simply skip step 1
and let the restore create the whole schema from the dump alone).

**4. Verify** -- rerun the exact count query from "What's being migrated"
above, with `$RENDER_DATABASE_URL` instead of the local connection, and
confirm every number matches.

**5. Delete the local export** (it's a full data dump -- never commit it):

```bash
rm -rf /tmp/energy_platform_export /tmp/dump.custom
```

### If the dump is too large for a direct restore

Do not improvise a workaround. The smallest reliable alternative, in
order of preference:

1. **Dump only what the deployed app actually needs to demo well**:
   `sites`, `buildings`, `sensors`, `energy_measurements`,
   `weather_measurements`, `model_versions`, and a recent slice of
   `predictions`/`alerts` (e.g. the last 90 days) via `pg_dump -t
   <table>` per table, or a `WHERE` clause during export. This is a
   materially smaller dataset while keeping the dashboard/forecast/
   anomaly pages fully functional.
2. **Re-run the ingestion pipeline directly against Render Postgres**
   instead of dumping/restoring: `DATABASE_URL="$RENDER_DATABASE_URL"
   python -m energy_platform.ingestion.pipeline`, then
   `forecasting.train` / `forecasting.predict` /
   `anomalies.evaluate` / `anomalies.detect` in sequence -- slower
   (the full pipeline takes tens of minutes, see the README's per-job
   timings) but produces the exact same data without ever creating a
   large local file to move.
3. If neither is workable, stop and report the specific blocker (file
   size, transfer time, or a Render connection limit) rather than
   guessing at a fix.

## Phase 6: verify the deployment

```bash
BACKEND_URL="https://energy-platform-backend.onrender.com"   # your real URL

curl -s "$BACKEND_URL/health"
curl -s "$BACKEND_URL/health/db"
curl -sI "$BACKEND_URL/docs" | head -1
curl -s "$BACKEND_URL/api/v1/sites"
curl -s "$BACKEND_URL/api/v1/buildings?limit=2"

# External-company inference against the hosted API, using the bundled example:
python3 - <<'EOF'
import json, pandas as pd, urllib.request
energy = pd.read_csv("examples/external_company/energy.csv")
building = pd.read_csv("examples/external_company/building.csv").iloc[0].to_dict()
payload = json.dumps({"building": building, "energy": energy.to_dict(orient="records")}).encode()
req = urllib.request.Request("REPLACE_WITH_BACKEND_URL/api/v1/forecast", data=payload,
                              headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req) as resp:
    body = json.load(resp)
    print("predictions:", len(body["predictions"]))
EOF
```

Then open the frontend's Render URL in a browser, confirm the Dashboard
loads real data, and confirm `/external-forecast` successfully calls the
hosted `POST /api/v1/forecast` (Network tab should show a request to your
backend's `*.onrender.com` URL, not `localhost`).

## Notes on cold starts

The backend's health check has a generous `start_period`/initial grace
window locally (180s in `docker-compose.yml`) because a cold-filesystem
`joblib.load` of the 552MB model measured 103s on the development
machine. On Render, the equivalent grace is controlled by the platform's
own deploy health-check timeout (not a `render.yaml` field at the time of
writing) -- if a deploy is ever marked failed/unhealthy prematurely,
check the service's logs for how far `docker_model_fetch.py` /
`Loading forecast model artifact` / `Forecast model artifact loaded` got
before the platform gave up, and consider whether the plan's disk/network
speed needs a larger instance rather than assuming a code bug.
