# Deploying PostgreSQL to Supabase

Companion to `docs/deployment_cloud_run.md`. Nothing here has been applied
to a live Supabase project by an automated process -- creating the
project and running the migration/import requires a Supabase account,
which this environment does not have.

## 1. Create the Supabase project

1. https://supabase.com/dashboard -> **New project**.
2. **Region: choose a European region** -- Supabase offers several
   (e.g. `eu-central-1` / Frankfurt, `eu-west-1` / Ireland, `eu-west-2` /
   London at the time of writing); pick whichever is currently offered
   and closest to Italy in the project-creation dropdown, since Supabase's
   exact region list can change -- don't assume the list below this
   sentence is exhaustive or current.
3. Set a strong database password when prompted -- **this becomes part of
   your `DATABASE_URL` and must never be committed to Git** (see Phase 10
   security notes in the README).
4. Wait for provisioning (a couple of minutes).

## 2. Get the connection string(s)

Project -> **Connect** (or Settings -> Database) shows several connection
strings. Supabase's pooler (Supavisor) has two relevant modes -- use the
right one for the right job:

| Use | Mode | Port | Why |
|---|---|---|---|
| Alembic migrations, `pg_restore` | **Session** (direct or pooler session mode) | `5432` | DDL and long-lived transactional work need a stable, non-multiplexed connection; transaction-mode pooling can break mid-migration. |
| The running Cloud Run app's `DATABASE_URL` | **Transaction** pooler | `6543` | Cloud Run can scale to multiple instances/connections; the transaction pooler multiplexes many short-lived app connections without exhausting Postgres's direct connection limit -- Supabase's own recommendation for serverless/scaling clients. |

Both look like:

```
postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:<5432|6543>/postgres
```

This project's `energy_platform.config.Settings.database_url` is read by
SQLAlchemy with the `postgresql+psycopg2://` driver prefix (see
`backend/src/energy_platform/config.py` and `backend/alembic/env.py`, both
unchanged by this deployment work) -- and needs `sslmode=require` appended,
since Supabase requires SSL:

```
postgresql+psycopg2://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require    # migrations/restore
postgresql+psycopg2://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require    # Cloud Run DATABASE_URL
```

Never paste the real password into this repo, a commit, or a GitHub
issue -- treat it exactly like the local `.env`'s `POSTGRES_PASSWORD`
(gitignored, per-environment).

## 3. Create the schema

```bash
cd backend
DATABASE_URL="postgresql+psycopg2://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require" \
    alembic upgrade head
```

This runs against the **session-mode** connection (port 5432) -- DDL via
the transaction pooler (6543) is not reliable. Verify:

```bash
DATABASE_URL="...same session-mode URL..." alembic current
# expect: c072cc66140a (head)
```

## 4. Measure the local database before exporting (don't assume)

```bash
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "SELECT pg_size_pretty(pg_database_size(current_database()));"
```

Measured in this environment (re-checked, not assumed from an earlier
session): **308 MB**, with per-table breakdown:

| Table | Size (incl. indexes) |
|---|---|
| `energy_measurements` | 188 MB |
| `predictions` | 56 MB |
| `alerts` | 45 MB |
| `weather_measurements` | 12 MB |
| everything else (`buildings`, `sites`, `sensors`, `model_versions`, `ingestion_runs`, `alembic_version`, `synthetic_anomalies`) | <200 KB combined |

Row counts:

```bash
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

Expected: 3 sites, 60 buildings, 60 sensors, 1,052,400 energy
measurements, 51,931 weather measurements, 6 model_versions, 241,680
predictions, 99,651 alerts.

**308 MB is well under Supabase Free's 500MB *database-size* limit** --
but that 308MB figure is Postgres's on-disk size (data + indexes + page
overhead) for the *local* database, not automatically the size Supabase
will report after import. Step 6 below re-measures on Supabase directly
rather than assuming the two numbers match exactly (index bloat, page
layout, and Postgres version/extension differences can shift the number
either direction by a meaningful amount).

## 5. Export and import (pg_dump / pg_restore)

```bash
# Export -- custom format (compressed, supports selective/parallel restore)
mkdir -p /tmp/energy_platform_export        # NOT inside the repo -- never committed
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --format=custom --no-owner --no-privileges \
    --file=/tmp/dump.custom
docker compose cp db:/tmp/dump.custom /tmp/energy_platform_export/dump.custom
ls -lh /tmp/energy_platform_export/dump.custom   # check size before proceeding

# Import into Supabase -- against the SESSION-mode connection (port 5432),
# same reasoning as the schema-creation step above
pg_restore --no-owner --no-privileges \
    --dbname="postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require" \
    /tmp/energy_platform_export/dump.custom
```

If `pg_restore` complains that `alembic_version` (or any table) already
exists from step 3's `alembic upgrade head`, either add `--clean
--if-exists` to `pg_restore`'s flags, or skip step 3 entirely and let the
restore create the whole schema from the dump alone (the dump includes
everything Alembic would have created).

**Delete the local export immediately after a successful, verified
import** -- it is a full data dump and must never be committed:

```bash
rm -rf /tmp/energy_platform_export /tmp/dump.custom
```

### If `pg_restore` has problems (fallback, documented rather than improvised)

1. **Foreign key / ordering errors**: `pg_restore`'s custom format
   already orders objects correctly by default; if you hit an error here,
   it usually means step 3's `alembic upgrade head` partially created
   conflicting objects -- restore into a genuinely empty Supabase database
   instead (drop and recreate the project's public schema, or use a fresh
   project) rather than fighting a partial-state mismatch.
2. **Connection drops mid-restore** (large dump over a slow/unstable
   link): re-run with `--single-transaction` so a failure rolls back
   cleanly instead of leaving a half-imported database, or split the
   restore table-by-table with `pg_restore -t <table>` for the largest
   tables (`energy_measurements`, `predictions`, `alerts`) so a failure
   only requires re-importing one table, not the whole dump.
3. **Dump is much larger than expected**: do not improvise. Two smaller,
   reliable alternatives, in order of preference: (a) dump only what the
   deployed app actually needs to demo well -- `sites`, `buildings`,
   `sensors`, `energy_measurements`, `weather_measurements`,
   `model_versions`, and a recent slice of `predictions`/`alerts` (e.g.
   the last 90 days) via `pg_dump -t <table>` per table, or a `WHERE`
   clause during export; or (b) re-run the ingestion/forecasting/anomaly-
   detection pipeline directly against
   `DATABASE_URL=<supabase session URL>` instead of moving a local dump
   at all (slower -- tens of minutes, see the README's per-job timings --
   but produces the exact same data without ever creating a large local
   file to move). If neither is workable, stop and report the specific
   blocker rather than guessing at a fix.

## 6. Verify database size on Supabase AFTER import (measured, not assumed)

```bash
psql "postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require" \
    -c "SELECT pg_size_pretty(pg_database_size(current_database()));"
```

Also visible in the Supabase Dashboard -> Project Settings -> Database ->
"Database size", or Reports -> Database. **Confirm this number is below
500MB before considering the migration complete** -- if the imported size
comes back meaningfully higher than the local 308MB figure, investigate
before treating the migration as done (don't assume it's fine because the
compressed dump was small).

Then re-run the row-count query from step 4 against Supabase and confirm
every number matches exactly.

## 7. What happens approaching/exceeding the 500MB limit

Supabase Free enforces the 500MB database-size limit by restricting
further writes once it's reached (existing data remains readable, but
`INSERT`/`UPDATE` operations that would grow the database further start
failing) rather than silently deleting data. If this dataset's 308MB ever
grows close to that ceiling (e.g. from re-running ingestion with a larger
BDG2 subset, or accumulating many more `predictions`/`alerts` rows over
time), the options are: delete/archive older `predictions`/`alerts` rows
(the two largest, most append-heavy tables), or upgrade to a paid
Supabase plan. At the current 308MB with no ongoing growth process writing
to this database automatically, there is meaningful headroom (~62% of the
limit still free), but this is not a "solved forever" guarantee if usage
patterns change.

**Separately, and just as operationally important for a portfolio
demo**: Supabase free projects **pause after one week of inactivity** and
must be manually unpaused from the Dashboard (a click, not a data-loss
event -- the data itself isn't deleted, but the API/database becomes
unreachable until unpaused). If this deployment goes quiet for a week,
expect `/health/db` to start failing until someone visits the Supabase
Dashboard and resumes the project.

## 8. Never commit the dump

`/tmp/energy_platform_export/` (or wherever you export to) must be
**outside** this repository's working directory. If you ever export
*inside* the repo by mistake, confirm `.gitignore` would catch it or
delete it manually before `git add` -- a `*.dump`/`*.custom` pattern is
not currently in `.gitignore` specifically because dumps are expected to
never be created inside the repo tree in the first place; do not rely on
gitignore as the only safeguard here.
