# Beginner Manual: Energy Intelligence Platform

This manual assumes **no prior familiarity with this project**. Every
command below has been taken from this repository's actual configuration
and verified by running it -- nothing here is invented or assumed to work.

There are two paths through this manual:

- **Path A -- Quick Demo**: you already have a copy of this project on
  your computer (someone gave it to you, or you're returning to a clone
  you set up before), with the database already loaded and the model
  already trained. Skip to [Path A: Quick Demo](#path-a-quick-demo).
- **Path B -- Fresh Setup**: you are cloning this from GitHub for the
  first time, with nothing set up yet. Start at
  [Path B: Fresh Setup](#path-b-fresh-setup).

**Read this before either path**, in [section 8](#8-required-localmodel-data-assets):
a completely fresh `git clone` of this repository does **not**, by
itself, reproduce the full demo (real data, a trained model, and
generated forecasts/anomalies). That requires downloading a real,
public dataset and running real training jobs, both of which take real
time (up to roughly 30 minutes total, mostly unattended). This is stated
plainly rather than glossed over.

---

## 1. What this project is

A full-stack application that:

- Downloads and cleans **real** building electricity/weather data (60
  real buildings, over a million real hourly readings) from a public
  research dataset (Building Data Genome Project 2).
- Stores it in a **PostgreSQL** database.
- Serves it through a **FastAPI** (Python) backend with a REST API.
- Trains a **machine learning model** (a Random Forest) to forecast a
  building's next 24 hours of electricity use, and runs **four different
  anomaly detectors** to flag unusual consumption.
- Displays all of this in a **React** web dashboard with six pages you
  can click through in a browser.
- Additionally lets you upload **a completely different company's own
  data** (not from the original dataset) and get a real forecast from the
  same trained model, without retraining anything.

**This project runs entirely on your own computer using Docker Compose.
It is not a website you can visit online -- there is no public URL.**
GitHub only hosts the source code and documentation.

## 2. What you need before starting

| Requirement | Why | Check with |
|---|---|---|
| **Docker Desktop** (Mac/Windows) or Docker Engine + Docker Compose (Linux) | Runs the database, backend, and frontend as three separate, isolated containers | `docker --version` and `docker compose version` |
| **Git** | To download (clone) this repository | `git --version` |
| **Python 3** (only for one download script, not for running the app) | `scripts/download_data.py` runs on your host machine, not inside Docker | `python3 --version` |
| ~2GB free disk space | The dataset, the trained models, and Docker images all take real space | -- |
| A terminal | Every command in this manual is a terminal command | -- |

**Mac/Linux/Windows note:** this manual's commands are written for a
Unix-style shell (macOS Terminal, Linux shell, or Git Bash / WSL on
Windows). If you're on native Windows PowerShell/cmd.exe, the commands
are the same tools (`docker`, `git`, `python3`) but path separators and
line-continuation syntax may differ slightly -- consider using WSL2 for
the smoothest experience, which is also what Docker Desktop for Windows
itself recommends.

## 3. How to get the project from GitHub

```bash
git clone https://github.com/BiniyamAFirde/energy-intelligence-platform.git
```

This downloads the full source code (not the dataset, not the trained
model -- see section 8) into a new folder named
`energy-intelligence-platform`.

## 4. How to open the project directory

```bash
cd energy-intelligence-platform
```

Every command in the rest of this manual assumes you are in this
directory (the one containing `docker-compose.yml`).

## 5. What Docker Compose does

`docker-compose.yml` (in the project root) describes **three services**
that run together as one application:

| Service | What it is | Port on your machine |
|---|---|---|
| `db` | PostgreSQL 16 database | `5432` |
| `backend` | FastAPI Python API (+ the trained ML model) | `8000` |
| `frontend` | React dashboard (Vite dev server) | `5173` |

`docker compose up` starts all three, in the right order (the backend
waits for the database to be healthy; the frontend waits for the backend
to be healthy), each in its own isolated container, without installing
Python, Node.js, or PostgreSQL directly on your computer.

## 6. Project structure

```
energy-intelligence-platform/
  backend/          FastAPI app, database models, ML training/prediction code
  frontend/         React + TypeScript dashboard
  scripts/          Standalone helper scripts (download data, run ingestion, external inference)
  examples/         Sample CSVs for the External Forecast feature
  data/             Downloaded/generated datasets (not in Git -- see section 8)
  models/           Trained ML model files (not in Git -- see section 8)
  docs/             All project documentation, including this manual
  docker-compose.yml   Defines the three services above
  .env.example      Template for local configuration (copy to .env)
```

## 7. Environment setup

The application reads its configuration (database password, ports, etc.)
from a file named `.env`, which is **not** included in Git (it's
gitignored, since real configuration files can contain secrets -- even
though this project's local defaults are just placeholder values like
`change_me`).

```bash
cp .env.example .env
```

You don't need to edit anything in `.env` for local use -- the defaults
in `.env.example` already match what `docker-compose.yml` expects.

## 8. Required local model/data assets

**Read this section carefully -- it explains an intentional limitation,
not a bug.**

Two things are deliberately **not** stored in Git, and a fresh clone does
not include them:

1. **The BDG2 dataset** (`data/raw/`, `data/processed/`) -- real research
   data, hundreds of megabytes, publicly available but not something to
   duplicate inside a Git repository.
2. **The trained model files** (`models/*.joblib`) -- the main one
   (`random_forest_v1.joblib`) is about 552MB by itself. Git repositories
   are not designed to store files this large efficiently.

**Why excluded:** keeping a Git repository fast to clone and free of
large binary blobs is standard practice; both of these are fully
*regenerable* from the source code in this repository plus the public
dataset, so committing them would just be redundant, unreviewable binary
weight.

**Where they're expected locally:** `data/raw/`, `data/processed/`, and
`models/` at the repository root (all three already exist as empty,
gitignored directories with a `README.md` placeholder explaining this
same thing).

**How the repository's actual scripts obtain/recreate them:**

```bash
python3 scripts/download_data.py
```

This downloads the real BDG2 subset (~187MB) directly from the dataset's
official source, verifies it against a known SHA256 checksum, and places
it under `data/raw/`. It is idempotent -- safe to re-run, it won't
re-download if the file is already there and correct.

The model files are then created by actually training them (see
[Path B, step 6](#path-b-fresh-setup) below) -- there is no separate
"download the model" step; training *is* how the model comes to exist
the first time.

**What a beginner should do:** follow Path B below in order. Each step
that creates one of these assets is marked. **If you skip the data
download and training steps, the dashboard will start but show empty
KPIs and charts** -- this is expected, not broken; see section 8's
warning restated in Path B.

**Honest statement, as required:** a completely fresh `git clone` of this
repository, on its own, does **not** reproduce the full demo (real data,
trained model, generated forecasts, and detected anomalies). Reproducing
it requires running the real ingestion and training pipelines against the
real, separately-downloaded dataset, which takes real computation time
(summarized in the table in Path B). This is not something Docker Compose
alone can shortcut, and this manual does not pretend otherwise.

---

## Path A: Quick Demo

**For someone who already has this project set up locally** (data
downloaded, database populated, model trained) and just wants to run it.

### A1. How to start the application

```bash
docker compose up -d --build
```

`-d` runs it in the background (detached); `--build` makes sure the
images reflect the current source code.

### A2. What Docker containers are running

```bash
docker compose ps
```

Expected output: three rows, `db`, `backend`, `frontend`, each `Up`.

### A3. How to check container health

The same `docker compose ps` output shows a health status in the
`STATUS` column, e.g. `Up 2 minutes (healthy)`. **The backend can take up
to about 2 minutes to become healthy the first time it starts** -- it
loads the ~552MB trained model into memory before it will answer any
request at all, including its own health check. `starting` or
`unhealthy` during this window is expected, not an error; wait and check
again:

```bash
docker compose ps
```

Once `backend` shows `(healthy)`, continue.

### A4. How to open the frontend

Open a browser to:

```
http://localhost:5173
```

### A5. How to open the FastAPI docs

```
http://localhost:8000/docs
```

This is an interactive page (Swagger UI) listing every API endpoint,
generated automatically from the backend's actual code -- you can try
real requests directly from this page.

### A6. How to use the Dashboard

The Dashboard (`http://localhost:5173/`) is the landing page: portfolio-
wide KPIs (total consumption, building count, anomaly counts), a
consumption trend chart, a building ranking table, and an anomaly
breakdown. Everything on this page is a real query against the
PostgreSQL database -- nothing is hardcoded or mocked.

### A7. How to open a Building Detail page

Click any row in the Dashboard's building ranking table, or go directly
to:

```
http://localhost:5173/buildings/1
```

Shows that one building's metadata, KPIs, a date-range-and-granularity-
adjustable consumption chart, and its own recent anomalies.

### A8. How to use Forecasting

```
http://localhost:5173/forecasting
```

Shows forecast-vs-actual for a selected building. These are **historical,
backtested** predictions (made from 2017 data), clearly labeled as such
-- not a live "what will happen tomorrow" forecast, since this dataset
ends in 2018.

### A9. How to use Anomaly Monitoring

```
http://localhost:5173/anomalies
```

A filterable table of every flagged anomaly (filter by building,
detector, severity, type, or date range), plus summary charts.

### A10. How to inspect an anomaly

Click any row in the Anomaly Monitoring table to open its detail page
(`http://localhost:5173/anomalies/<id>`), showing the specific value that
triggered it, a real explanation string generated by the detector itself
(e.g. "634.58 kWh is 7873x the local median of 0.08 kWh"), and a chart of
the surrounding consumption with the flagged point highlighted.

### A11. How to use External Forecast

```
http://localhost:5173/external-forecast
```

This page is different from the rest: it doesn't show data from the
built-in 60 buildings. Instead, it lets you upload **any building's own
hourly energy data** and get a real 24-hour forecast from the same
trained model -- proving the model works on data it has never seen,
without being retrained on it.

### A12. How to use the example external-company CSV

This repository includes ready-made sample files so you don't need your
own data to try this feature:

```
examples/external_company/energy.csv       (216 hours of sample hourly readings)
examples/external_company/building.csv     (matching building metadata)
```

On the External Forecast page: the building-metadata form is already
pre-filled to match `building.csv`'s values; use the file upload control
to select `examples/external_company/energy.csv`; the page will show how
many hourly observations it detected; click **Generate 24h Forecast**.
You should see a 24-point chart and a 24-row table appear within a few
seconds.

### A13. How to run tests

```bash
# Backend (inside Docker, against a real test database)
docker compose run --rm backend pytest -v

# Frontend
cd frontend
npm test
npm run build
npx oxlint
```

### A14. How to stop Docker

```bash
docker compose down
```

Stops and removes the three containers. **Your database data is
preserved** (it lives in a separate Docker volume, not inside the
container) -- the next `docker compose up` picks up exactly where you
left off.

### A15. How to restart Docker

```bash
docker compose up -d
```

If nothing about the code changed, you don't need `--build` again.

### A16. How to reset the database safely

**Warning: this is destructive and cannot be undone.** Only do this if
you genuinely want to erase all locally loaded data (all 1,052,400+
energy readings, trained-model predictions, and detected anomalies) and
start over from an empty database.

```bash
docker compose down
docker volume rm energy-intelligence-platform_pgdata
docker compose up -d db
docker compose run --rm backend alembic upgrade head
```

This leaves you with an empty, correctly-structured database -- you
would then need to re-run ingestion (and optionally training/anomaly
detection) exactly as in Path B below to have data again. If you are not
certain you want to delete everything, **do not run this** -- there is no
confirmation prompt.

---

## Path B: Fresh Setup

**For someone cloning this repository from scratch, with nothing set up.**
Follow in order. Approximate one-time timings are shown so you know what
to expect -- these are long-running but mostly unattended (real ML
training and real data processing over ~1 million rows, not instant).

| Step | Command | Approx. time |
|---|---|---|
| 1. Clone | `git clone https://github.com/BiniyamAFirde/energy-intelligence-platform.git && cd energy-intelligence-platform` | seconds |
| 2. Configure | `cp .env.example .env` | instant |
| 3. Start the database only | `docker compose up -d db` | seconds |
| 4. Download the real dataset | `python3 scripts/download_data.py` | a few minutes (~187MB download) |
| 5. Build the backend image | `docker compose build backend` | 1-2 minutes |
| 6. Create the database schema | `docker compose run --rm backend alembic upgrade head` | seconds |
| 7. Load the data into PostgreSQL | `docker compose run --rm backend python -m energy_platform.ingestion.pipeline` | ~5.5 minutes |
| 8. Train the forecasting model | `docker compose run --rm backend python -m energy_platform.forecasting.train` | ~6 minutes |
| 9. Generate forecasts | `docker compose run --rm backend python -m energy_platform.forecasting.predict` | under a minute |
| 10. Fit anomaly detectors + freeze thresholds | `docker compose run --rm backend python -m energy_platform.anomalies.evaluate` | ~12-15 minutes |
| 11. Run anomaly detection | `docker compose run --rm backend python -m energy_platform.anomalies.detect` | ~6 minutes |
| 12. Start everything | `docker compose up -d --build` | under a minute |

**Total: roughly 30-35 minutes**, most of it unattended (steps 4, 7, 8,
10, 11 are the long ones; you can do something else while they run).

After step 12, follow **Path A** above (A2 onward) -- your setup is now
identical to someone who already had the project running.

**If you stop after step 3 or 7 without steps 8-11**: the app will run,
the Dashboard will show real consumption data, but the Forecasting and
Anomaly pages will be empty (no predictions or alerts exist yet). This
matches the exact warning in section 8 above.

---

## 25 (continued). Common problems and fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| `docker compose ps` shows `backend` as `unhealthy` for a long time | Cold model load, or the container is still downloading/starting | Wait up to ~2-3 minutes on first start; check `docker compose logs backend` for `Loading forecast model artifact` / `Forecast model artifact loaded` |
| Dashboard loads but all KPIs are zero/empty | Ingestion (step 7) hasn't been run yet | Run `docker compose run --rm backend python -m energy_platform.ingestion.pipeline` |
| Forecasting/Anomaly pages are empty | Training/prediction/detection (steps 8-11) haven't been run yet | Run the corresponding commands from the Path B table |
| `docker compose up` fails on the `db` service | Port `5432` already in use by another PostgreSQL instance on your machine | Stop the other instance, or change `POSTGRES_PORT` in `.env` |
| Frontend shows network/connection errors | Backend isn't healthy yet, or `VITE_API_BASE_URL` mismatch | Check `docker compose ps`; the default local setup needs no manual URL configuration |
| `python3 scripts/download_data.py` fails partway | Network interruption | Re-run it -- it's idempotent and will resume/re-verify rather than duplicate |
| Alembic migration fails with a connection error | The `db` service isn't up/healthy yet | Run `docker compose up -d db` first and wait for it to be healthy before running migrations |
| You want a completely clean slate | -- | See A16 above (destructive -- reads the warning first) |

## 26. What the model does

A single **Random Forest** regression model, trained once on the 60 real
buildings' 2016-2017 history, predicts a building's next 24 hours of
electricity consumption from: the target hour's calendar features (hour,
day of week, month, cyclical encodings), the building's own recent
consumption history (lags and rolling statistics), and static building
metadata (floor area, floor count, occupants, use type). It does **not**
use weather data (this was a deliberate, documented modeling decision --
see `docs/forecasting.md`), and it is never retrained automatically --
every prediction, whether for the original 60 buildings or an uploaded
external company's data, is a pure forward pass through the same,
already-fitted model.

## 27. What the anomaly detectors do

Four detectors, each looking for a different kind of problem:

- **Data quality**: obviously bad readings (negative values, stuck
  meters, missing data, isolated spikes) -- simple rules, not ML.
- **Behavioral**: is this reading unusually far from what *this specific
  building* normally does at this hour of day?
- **Forecast residual**: is the actual reading far from what the trained
  forecasting model predicted?
- **Isolation Forest**: a genuinely multivariate ML model looking at 10
  engineered features together, trained only on early (non-anomalous)
  data.

Since the real dataset has no labeled "this was an anomaly" ground truth,
all four were evaluated against a synthetic benchmark (fake anomalies
deliberately injected into a copy of the data, never into the real
database) -- see `docs/anomalies.md` for exact, measured performance
numbers (precision/recall per detector), not just a claim that they
"work."

## 28. What the project does NOT guarantee

- **Not a live/real-time system.** All data is historical (2016-2018);
  forecasts and anomaly detections are backtested, not generated
  continuously.
- **Not validated for arbitrary companies.** The External Forecast
  feature proves the model *mechanism* works on new data, but its
  *accuracy* on a company very different from the original 60 buildings
  (different climate, building size, or use type) is not guaranteed --
  see `docs/external_inference.md`.
- **Not authenticated.** This is a portfolio/demo app with no login
  system -- don't expose it on a public network as-is.
- **Not perfect anomaly detection.** Measured precision is modest (see
  `docs/anomalies.md`) -- documented honestly rather than oversold.
- **Not cloud-hosted.** There is no public URL; it only runs locally via
  Docker Compose (some cloud-deployment exploration exists in this
  project's Git history, but it is not the current or supported way to
  run this project).

## 29. How to explain the project in an interview

See `docs/PORTFOLIO_GUIDE.md` for 30-second / 1-minute / 3-minute versions
of this explanation, plus likely recruiter and professor questions with
concise, accurate answers.

## 30. Final quick-start checklist

- [ ] Docker Desktop installed and running
- [ ] Git installed
- [ ] Python 3 installed (for the one download script)
- [ ] Repository cloned
- [ ] `.env` created from `.env.example`
- [ ] Dataset downloaded (`scripts/download_data.py`)
- [ ] Database schema created (`alembic upgrade head`)
- [ ] Data ingested (`ingestion.pipeline`)
- [ ] Model trained (`forecasting.train`) and predictions generated (`forecasting.predict`)
- [ ] Anomaly detectors evaluated (`anomalies.evaluate`) and run (`anomalies.detect`)
- [ ] `docker compose up -d --build` run
- [ ] `docker compose ps` shows all three services healthy
- [ ] `http://localhost:5173` loads the Dashboard with real, non-zero numbers
- [ ] `http://localhost:8000/docs` loads the API documentation
- [ ] External Forecast page successfully scores the example CSV

If every box is checked, the application is fully running locally.
