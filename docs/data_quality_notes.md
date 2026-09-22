# Data quality notes from Phase 4 ingestion

## DST timezone handling (discovered while testing, not assumed)

BDG2 timestamps are local "clock time" per building, not explicitly marked
for daylight saving. Converting to UTC (`ingestion/transform.py`) surfaces
two distinct DST edge cases, confirmed against the real ingested data:

- **Fall-back (ambiguous hour)**: on the day clocks fall back, one local
  clock time occurs twice. We resolve this as `ambiguous="NaT"` and drop the
  row rather than guess which occurrence it was. Real count: exactly 120
  rows across the 60 selected buildings (2 fall-back transitions in the
  2016-2017 window x 60 buildings = 120 -- confirmed by the math, not just
  observed).
- **Spring-forward (nonexistent hour)**: on the day clocks spring forward,
  one local clock time never occurs. We resolve this as
  `nonexistent="shift_forward"`, which can cause the shifted timestamp to
  collide with an adjacent real reading at the same UTC instant. Our
  deduplication step then keeps one and drops the other. Real count: also
  exactly 120 rows (2 spring-forward transitions x 60 buildings), confirmed
  by re-deriving the number rather than assuming the two counts matching
  was coincidental.

Net effect: 240 of 1,052,640 possible readings (0.023%) are absent from the
loaded data purely due to DST handling, on top of whatever was already
`NaN` in the source. This is a known, quantified limitation, not a silent
one -- both counts are recorded in `ingestion_runs.error_summary` for every
run.

## Missing values are preserved, not dropped

A `NaN` reading in `electricity_cleaned.csv` is loaded as `NULL` in
`energy_measurements.consumption_kwh`, not skipped. This keeps a genuine
data gap distinguishable from a gap in ingestion -- important later for the
anomaly detector and for imputation choices in the ML feature pipeline.

## Negative values

`consumption_kwh` values below zero are treated as invalid and nulled out
before loading (a real building cannot have negative electricity
consumption). Observed count in the selected subset: 0.

## Referential integrity

Every foreign key (`buildings.site_id`, `sensors.building_id`,
`energy_measurements.sensor_id`, `weather_measurements.site_id`, ...) is a
`NOT NULL` PostgreSQL constraint, so an orphaned measurement is impossible
to insert rather than something that needs to be checked for afterwards.

## NULL consumption values cluster by site, not randomly (Phase 6 finding)

Phase 4 quantified DST-related row *gaps* (240 rows, above) but didn't
break down the pre-existing `NULL` value rate by building. Phase 6's EDA
report (`docs/eda.md` section 7, `reports/eda/08_missing_data_overview.png`)
found it isn't uniform: every Gator-site building has between ~2.9% and
~5.0% of its readings `NULL`, while nearly every Moose- and Peacock-site
building has under 1%. This means missingness is **not** missing-completely-
at-random (MCAR) with respect to site -- a fact that matters for any
imputation strategy in Phase 7 (a global fill rate would understate the gap
at Gator and overstate it elsewhere) and is worth carrying into the
anomaly-detection phase too (a naive "too many missing readings" threshold
would flag most of Gator and almost none of the other two sites).

## Weather completeness varies enormously by column, and by site (Phase 6 finding)

Dataset-wide null rates by column: `air_temp_c` 0.03%, `dew_temp_c`/`wind_speed`
~0.1%, `sea_lvl_pressure` 1.3%, `wind_direction` 3.9%, `precip_1hr_mm` 29.3%,
`cloud_coverage` 58.4%, `precip_6hr_mm` 92.1%. The last two are too sparse
dataset-wide to be reliable model inputs without heavy imputation or
exclusion. Completeness also varies *by site*, not just by column: Site 1
(Moose)'s `cloud_coverage` is 100% NULL (zero usable readings), while Sites
2 and 3 have partial coverage (~56% and ~66% respectively) -- confirmed by
computing the weather-energy correlation per site and finding zero usable
pairs specifically at Site 1.
