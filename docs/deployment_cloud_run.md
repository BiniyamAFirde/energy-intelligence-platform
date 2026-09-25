# Deploying the backend to Google Cloud Run

This is the step-by-step companion for deploying the existing FastAPI
backend (`backend/Dockerfile`, unchanged in structure -- see "What stays
the same" below) to Google Cloud Run. Nothing in this document has been
executed against a live Google Cloud project by an automated process --
every command here is written for you to run, since it requires a Google
Cloud account, billing enabled, and IAM permissions this environment does
not have.

## What stays the same (Phase 2 of the task this doc supports)

Cloud Run runs the **exact same container image** `docker compose up`
builds locally, unmodified in behavior:

- `backend/Dockerfile` -- same `COPY`/`pip install` steps.
- `backend/docker-entrypoint.sh` + `backend/docker_model_fetch.py` --
  same model-fetch-if-missing logic (see their docstrings). Locally, the
  model is already on disk via the host bind mount; on Cloud Run, there's
  no host filesystem, so this step downloads it from the GitHub Release
  asset described below and SHA256-verifies it before uvicorn's existing,
  **unchanged** lifespan model-loading code (`energy_platform.forecasting
  .external.get_cached_pipeline`) ever runs. No retraining, no `.fit()`
  call, anywhere in this path.
- Uvicorn binds `0.0.0.0` and reads `$PORT` already (the Dockerfile's
  `CMD ["sh", "-c", "uvicorn ... --port ${PORT:-8000}"]`) -- Cloud Run
  sets `PORT` to whatever port you configure the service to accept
  traffic on, so this needs no further change.
- `/health`, `/health/db`, `/docs`, `/openapi.json`, every `/api/v1/*`
  route including `POST /api/v1/forecast` -- all unchanged; Cloud Run is
  just a place to run the same container.
- `DATABASE_URL` and `CORS_ORIGINS` are already environment-driven
  (`energy_platform.config.Settings`) -- deployment only means setting
  their values, never a code change.
- The model artifact stays outside Git history, exactly as before (see
  "Model delivery" below) -- this document does not change that strategy,
  only which platform downloads it.

## Model delivery (unchanged strategy, carried over from the Render prep)

| | Value |
|---|---|
| Tag | `model-v1` |
| Asset | `random_forest_v1.joblib` |
| SHA256 | `2b859538307206b4516ff23c984878b1051a05cc910480ec9bdcd27ee83421a8` |
| Download URL | `https://github.com/BiniyamAFirde/energy-intelligence-platform/releases/download/model-v1/random_forest_v1.joblib` |

If this release doesn't exist yet:

```bash
gh release create model-v1 \
    models/random_forest_v1.joblib \
    --title "Random Forest model v1" \
    --notes "Trained artifact for energy_platform.forecasting.train MODEL_VERSION='v1'. SHA256: 2b859538307206b4516ff23c984878b1051a05cc910480ec9bdcd27ee83421a8. Not part of Git history -- see .gitignore."
```

Or via the GitHub web UI: go to
`https://github.com/BiniyamAFirde/energy-intelligence-platform/releases/new`,
tag `model-v1` (target `main`), title "Random Forest model v1", paste the
same notes text as the `--notes` string above, drag
`models/random_forest_v1.joblib` into the "Attach binaries" area, and
publish. The mechanism is identical regardless of which compute platform
ends up downloading the asset -- this release only needs to be created
once, not once per deployment target.

`MODEL_ARTIFACT_URL` (Cloud Run env var, set below) points at that URL.
`MODEL_ARTIFACT_SHA256` defaults to the value above inside
`docker_model_fetch.py` even if the env var isn't set.

## Prerequisites

- A Google Cloud project with billing enabled (see "Cost" below -- Cloud
  Run has a real always-free allowance, but this is not a claim of zero
  cost).
- `gcloud` CLI installed and authenticated (`gcloud auth login`), or use
  the Cloud Console entirely in-browser -- both work, this doc shows
  `gcloud` commands for precision/reproducibility.
- APIs enabled on the project: `run.googleapis.com`,
  `cloudbuild.googleapis.com`, `artifactregistry.googleapis.com`.

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
```

## Region

**`europe-west8` (Milan)** -- confirmed available as a Cloud Run region at
Tier 1 pricing (no premium over other EU regions) as of this writing.
Closest Cloud Run region to Italy. If Milan is ever unavailable on your
project/quota, `europe-west1` (Belgium) or `europe-west3` (Frankfurt) are
the next-closest well-established EU alternatives -- verify current
availability with `gcloud run regions list` before committing to one.

```bash
gcloud run regions list --format="table(name)" | grep europe
```

## Resource configuration

| Setting | Value | Why |
|---|---|---|
| Memory | `2Gi` (minimum) | The model is ~552MB on disk; deserialized into a fitted `RandomForestRegressor`'s tree structures, plus the Python/uvicorn/pandas process itself, needs comfortably more than that. Do not use less. |
| CPU | `1` | Sufficient for a single model's `.predict()` calls; revisit only if latency under load becomes a real, measured problem. |
| Min instances | `0` | No idle cost between visits -- appropriate for a portfolio demo, at the cost of a cold start (model download + load) on the first request after a scale-to-zero. |
| Max instances | `1` | Keeps this from ever running (and being billed for) more than one instance -- deliberate for a low-traffic demo, not a production scaling posture. |
| Concurrency | `4` (conservative) | Each `POST /api/v1/forecast` call runs real `RandomForestRegressor.predict()` work; keeping concurrent requests per instance low avoids many simultaneous heavy inferences contending for the single vCPU. Cloud Run's default is 80 -- explicitly lower this. |
| Authentication | Public (`--allow-unauthenticated`) | This is a portfolio demo with a read-only API plus one inference endpoint -- see README Limitations for the (deliberate, documented) absence of authentication. |
| Startup probe | see below | Must comfortably exceed the model download + load time. |

### Startup timing and the health check

Cloud Run auto-configures a TCP startup probe on the container's port with
`timeoutSeconds: 240, periodSeconds: 240, failureThreshold: 1` by default
(i.e. ~240s/4 minutes before giving up) if you don't set one explicitly.
The local, cold-filesystem `joblib.load` of this same model measured 103s
in `docker-compose.yml`'s own healthcheck tuning -- Cloud Run adds a model
**download** step on top of that (not present locally, where the file is
already on disk via the bind mount), so the total cold-start budget needed
is somewhat larger than the local number alone suggests. Rather than
relying on the default exactly matching that margin, configure an explicit,
more generous startup probe:

```bash
--startup-probe tcpSocket.port=8080,initialDelaySeconds=0,timeoutSeconds=240,periodSeconds=240,failureThreshold=2
```

This doubles the default's total budget (~480s/8 minutes across two
attempts) without weakening the check itself -- it still fails a
genuinely broken deploy, just gives a slow-but-working cold start enough
room. **Do not remove or shrink this startup allowance merely to make
deploys look faster** -- the model load is real, necessary work, not
overhead to optimize away.

## Deploy

### First deploy (manual, from your machine, to establish the service)

```bash
cd backend
gcloud run deploy energy-platform-backend \
    --source . \
    --region europe-west8 \
    --memory 2Gi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 1 \
    --concurrency 4 \
    --allow-unauthenticated \
    --startup-probe tcpSocket.port=8080,initialDelaySeconds=0,timeoutSeconds=240,periodSeconds=240,failureThreshold=2 \
    --set-env-vars ENV=production,LOG_LEVEL=INFO,JWT_ALGORITHM=HS256,ACCESS_TOKEN_EXPIRE_MINUTES=60,MODEL_ARTIFACT_SHA256=2b859538307206b4516ff23c984878b1051a05cc910480ec9bdcd27ee83421a8 \
    --set-env-vars MODEL_ARTIFACT_URL=https://github.com/BiniyamAFirde/energy-intelligence-platform/releases/download/model-v1/random_forest_v1.joblib
```

`--source .` tells Cloud Run to build `backend/Dockerfile` via Cloud
Build automatically (no local Docker daemon required on your Mac -- the
build happens in Google's cloud, matching the task's "usable online
without Docker running on my Mac" goal). `gcloud` will prompt to enable
any not-yet-enabled API the first time.

**`DATABASE_URL` and `CORS_ORIGINS` are deliberately not in the command
above** -- set them as real secrets/values once Supabase (Phase 4) and
the frontend URL (Phase 8) exist, via:

```bash
gcloud run services update energy-platform-backend \
    --region europe-west8 \
    --set-env-vars DATABASE_URL="postgresql+psycopg2://...supabase connection string...",CORS_ORIGINS="https://your-frontend-url"
```

Prefer Secret Manager over a plain env var for `DATABASE_URL` specifically
(it contains a password):

```bash
echo -n "postgresql+psycopg2://...supabase connection string..." | \
    gcloud secrets create database-url --data-file=-
gcloud run services update energy-platform-backend \
    --region europe-west8 \
    --set-secrets DATABASE_URL=database-url:latest
```

Also grant the Cloud Run service's runtime service account
`roles/secretmanager.secretAccessor` on that secret (the deploy command
will print the exact `gcloud secrets add-iam-policy-binding` command to
run if it's missing).

### Continuous deployment from GitHub (Phase 7)

Cloud Run supports building and deploying automatically on every push,
without any credential file ever touching this repository or your
machine's disk:

1. Cloud Console -> Cloud Run -> your service -> **Set up Continuous
   Deployment**.
2. Choose **Cloud Build** (GitHub-only) or **Developer Connect**
   (GitHub/GitLab/Bitbucket -- functionally equivalent for a GitHub repo).
   Authenticate via the **Cloud Build GitHub App** -- an OAuth-based
   repository connection, not a downloaded key.
3. Repository: `BiniyamAFirde/energy-intelligence-platform`. Branch:
   `main`.
4. Build type: **Dockerfile**. Source location: `backend/Dockerfile`.
   Build context directory: `backend` (Cloud Run's GitHub-connect flow
   supports a Dockerfile in a monorepo subdirectory directly -- no
   repo-root Dockerfile trick needed).
5. Save. Cloud Run creates a Cloud Build trigger; from then on:

   ```
   git push origin main
     -> Cloud Build trigger fires
     -> backend/Dockerfile builds in the cloud (no local Docker needed)
     -> image pushed to Artifact Registry
     -> new Cloud Run revision deployed automatically
   ```

   A new revision inherits the previous revision's memory/CPU/min-max-
   instances/concurrency/startup-probe/env-var configuration -- the
   settings from the first manual deploy above carry forward
   automatically; you don't need to repeat them on every push.

**Required Google Cloud IAM setup** (one-time, done by you in the
Console/`gcloud`, never stored in this repo): the Cloud Build service
account needs `Cloud Run Admin` and `Service Account User` roles; your own
user account needs `Cloud Build Editor`, `Artifact Registry Admin`, and
`Cloud Run Developer` (or broader project-level roles that include them).
The Console's "Set up Continuous Deployment" wizard offers to grant these
automatically if it detects they're missing.

**Never commit a service-account JSON key file.** This flow doesn't
produce one -- the GitHub connection is OAuth-based, and Cloud Build's
own service account is Google-managed. If you ever see a `.json` key file
appear in this repo, that's a sign something was configured incorrectly;
remove it and rotate the key immediately (see docs -- Phase 10 audit
below still applies going forward).

## Cost -- read before enabling billing

Cloud Run has a real, genuinely free **"always free" monthly allowance**:
180,000 vCPU-seconds, 360,000 GiB-seconds of memory, and 2 million
requests, resetting every calendar month, per billing account. At 1 vCPU
/ 2GiB, that's roughly 50 hours/month of continuous full-resource usage
covered for free -- and with `min-instances: 0`, this service **only
consumes vCPU/memory-seconds while actually handling a request**, not
while idle, so a low-traffic portfolio demo can very plausibly stay
within the free allowance most months.

**This is not a guarantee of zero cost.** Google Cloud billing must be
enabled to deploy at all (Cloud Run requires a billing-enabled project
even to use the free tier), and usage beyond the monthly allowance --
heavier traffic, more cold starts than expected, or simply the request
volume growing -- **will incur real charges**. Set a budget alert:

```bash
gcloud billing budgets create --billing-account=YOUR_BILLING_ACCOUNT_ID \
    --display-name="energy-platform-budget" \
    --budget-amount=5USD \
    --threshold-rule=percent=0.5 --threshold-rule=percent=1.0
```

## Verify

```bash
BACKEND_URL=$(gcloud run services describe energy-platform-backend --region europe-west8 --format='value(status.url)')
curl -s "$BACKEND_URL/health"
curl -s "$BACKEND_URL/health/db"
curl -sI "$BACKEND_URL/docs" | head -1
curl -s "$BACKEND_URL/api/v1/sites"
```

An empty/failing `/health/db` at this point is expected until Supabase
(Phase 4) exists and `DATABASE_URL` points at it.
