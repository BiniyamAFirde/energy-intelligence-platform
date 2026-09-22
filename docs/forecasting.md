# Day-ahead hourly energy forecasting (Phase 7)

Every number below is from a real run of `python -m energy_platform.forecasting.train`
and `predict.py` against the loaded 60-building BDG2 subset
(`data/processed/forecasting_training_report.json`, `reports/forecasting/*.png`,
the live `model_versions`/`predictions` tables) -- not estimated or assumed.

**Correction note**: an earlier version of this document and the results it
described split train/validation/test by `target_ts` (the timestamp being
predicted). That had a real bug: with one origin producing 24 rows
(h=1..24), an origin within 24h of a split boundary could have some of its
horizons' targets fall before the boundary and others after -- e.g. an
origin at local midnight June 30 has targets spanning June 30 01:00 through
July 1 00:00, so h=24 alone landed in validation while h=1..23 stayed in
train, splitting one 24-hour forecast across two splits. The split now
keys on **`origin_ts`** instead (section 3), guaranteeing every complete
24-hour forecast belongs to exactly one split. The corrected run is what's
reported throughout this document; the results turned out to differ only
slightly from the buggy run (section 8 explains why), but the corrected
split is the methodologically authoritative one regardless.

## 1. The forecasting problem

**Formulation.** For each building, given everything observed up to and
including a forecast *origin* T (a specific hour with a real reading),
predict `consumption_kwh` for T+1, T+2, ..., T+24 -- the entire next 24
hours in one forecast run, not a single next-hour value. This is what makes
it "day-ahead" rather than "one-step-ahead": the model must stay accurate
across the whole horizon, not just h=1, and horizon-by-horizon evaluation
(section 9) is exactly the check that this wasn't accidentally built as a
one-step model in disguise.

**Origins are restricted to local midnight**, one per building per day --
not every hour. This is deliberate on two grounds: it matches how day-ahead
forecasting works in practice (a forecast generated once daily, not
continuously), and every hourly origin would inflate the dataset ~24x for
little statistical benefit (consecutive hourly origins share nearly
identical lag histories) while blowing well past a laptop-sized memory
budget once expanded to (origin, horizon) rows -- discovered directly: the
first implementation OOM-killed the container (section 13).

**Multi-step strategy: direct, with horizon as a feature.** A single model
per algorithm is trained on rows shaped (origin features, horizon,
target = consumption(origin + horizon hours)), horizon ∈ {1..24} included
as an explicit numeric input. This is *not* recursive forecasting (an h=1
prediction is never fed back in as an input for h=2) and *not* 24
independent models -- it's the standard "direct" strategy implemented
sample-efficiently. Calendar features describe the **target** timestamp
(origin + horizon), since calendar information for a known future hour is
legitimately available in advance -- knowing in advance that tomorrow
15:00 is a Tuesday is not a leak. Lag/rolling features describe the
**origin** only, using data at-or-before it exclusively (section 6).

## 2. Data inspection (before any modeling)

- All 60 sensors have exactly 17,540 hourly rows (matches `expected_hourly_grid_size() - 4`,
  the 4 DST-related gaps documented in Phase 4) -- uniform coverage,
  confirmed directly via SQL, not assumed.
- Per-split-window completeness (worst case across all 60 buildings):
  train ≥93.97%, validation ≥95.65%, test ≥96.74%.
- **No buildings were excluded.** All 60 have sufficient history and
  completeness in every split window; this was verified per-building via
  SQL before committing to the split dates, not assumed from the aggregate.

## 3. Train / validation / test split

Chronological, non-overlapping, defined in local time (every currently
loaded site is US/Eastern -- see `docs/data_selection.md`) and converted
to UTC once (`dataset.py`'s `TRAIN_START`/`TRAIN_END_EXCL`/`VALIDATION_END_EXCL`/`TEST_END_EXCL`):

| Split | Origin range (local) | Rows after feature engineering + dropna |
|---|---|---|
| Train | 2016-01-01 -- 2017-06-30 | 684,496 |
| Validation | 2017-07-01 -- 2017-09-30 | 124,386 |
| Test | 2017-10-01 -- 2017-12-31 | 117,056 |

**Rows are split by `origin_ts` -- the forecast origin -- not by
`target_ts`.** A row belongs to whichever window its *origin* falls into,
so a complete 24-hour forecast (all horizons h=1..24 from one origin)
always lands entirely in one split, never straddling a boundary (see the
correction note above, and the regression tests in section 13). A row's
features only ever use data at-or-before its own origin regardless of
split (enforced in `features.py` and proven in the leakage test suite), so
this change doesn't alter the leakage guarantee -- it only prevents a
single forecast's 24 rows from being torn across two splits. The test set
was never touched until the single final evaluation pass in `train.py`.

**Row retention after dropna**: train 86.9%, validation 93.9%, test 88.4%
(row counts before dropna: 787,680 / 132,480 / 132,480). The loss is
dominated by the 168-hour lag/rolling warm-up at the very start of train (a
one-time cost) plus missing-reading propagation through 9 required numeric
features -- a row is dropped if *any* required feature is NaN, so the
effective drop rate compounds above any single column's raw missing rate.

## 4. Feature-availability table (the leakage-prevention contract)

| Feature | Computed from | Available at forecast time? | Why |
|---|---|---|---|
| hour, day_of_week, day_of_month, month, is_weekend, *_sin/*_cos | **target** timestamp, local time | YES | Calendar for a known future hour is knowable in advance |
| lag_1 .. lag_168 | **origin**, `consumption(origin - k + 1)` | YES | All ≤ origin, i.e. already observed |
| rolling_24h_mean/std, rolling_168h_mean | **origin**, shifted by 1 before rolling | YES | Explicitly excludes the origin's own value, uses only hours strictly before it |
| area_sqm, primary_use, number_of_floors, occupants | building metadata | YES | Static, known in advance |
| horizon | the forecast request itself | YES | Which of the 24 hours this row predicts |
| year_built, EUI/site_eui/source_eui/energy_star_rating | building metadata | N/A -- not used | Phase 6 found these 13%/0%/0%/0%/0% populated for this subset; not silently imputed as a feature |
| sub_primary_use | building metadata | N/A -- not used | 18 categories across 60 buildings, most with 1-3 members (Phase 7 data check); too fine-grained to generalize |
| weather (any variable, actual or forecast) | -- | **NOT USED** | See section 5 |

## 5. Weather: explicitly excluded from this model

BDG2 provides *observed historical* weather, not a forecast product. Option
A from the phase's decision menu was taken: **no weather features at all**
in this first model, deliberately, to avoid the specific trap of feeding
future observed weather into a day-ahead forecast and calling it valid --
`test_no_weather_columns_anywhere_in_the_feature_set` asserts this holds in
code, not just in this document. Lagged/historical weather (Option B) and
a clearly-labeled oracle experiment using future observed weather as an
upper bound (Option C) are natural follow-ups, not built here, kept out
deliberately to bound this phase's scope. Phase 6 also found weather's
linear correlation with consumption weak (|r| ≤ 0.30) across all three
sites, so excluding it is unlikely to be costing much accuracy relative to
the features that are used.

## 6. Baselines

| Model | Formula | Validation RMSE | Test RMSE |
|---|---|---|---|
| Seasonal naive 24h | forecast(T+h) = consumption(T+h-24) | 77.90 | 85.00 |
| Seasonal naive 168h | forecast(T+h) = consumption(T+h-168) | **65.94** | **77.64** |

The 168h (same hour, same day-of-week, last week) baseline beats the 24h
(same hour, previous day) baseline on both splits -- consistent with Phase
6's finding of a real 14-17% weekday/weekend gap: naively copying
"yesterday" is wrong roughly 2 days out of 7 (Sat/Sun), while "last week,
same day" is right about day-type every time. Every ML model below is
judged against the *stronger* of these two, not the weaker one.

## 7. ML models

**Ridge** (`sklearn.linear_model.Ridge`): alpha selected via 5-fold
expanding-window `TimeSeriesSplit` on train only (grid `[0.1, 1, 10, 100]`;
mean CV RMSE decreased monotonically across the grid, 98.85 → 96.40,
selecting alpha=100 -- the heaviest regularization tested; a wider grid is
a documented limitation, section 12). Fit time: 11.0s.

**Random Forest** (`n_estimators=100, max_depth=20, min_samples_leaf=5`):
a capped, reasonable configuration, not a search. Fit time: **249.6s
(~4.2 min)** -- the dominant cost in the whole pipeline.

**HistGradientBoosting**: library defaults. Fit time: 25.2s.

## 8. Results (comparison table, decided criterion applied mechanically)

**Model-selection criterion, fixed before results were seen**: among
models beating *both* baselines on validation RMSE, select the lowest
validation RMSE.

| Model | Val MAE | Val RMSE | Val R² | Val CV(RMSE)% | Test MAE | Test RMSE | Test R² | Test CV(RMSE)% |
|---|---|---|---|---|---|---|---|---|
| Seasonal naive 24h | 21.55 | 77.90 | 0.949 | 35.3% | 23.54 | 85.00 | 0.938 | 40.0% |
| Seasonal naive 168h | 18.35 | 65.94 | 0.963 | 29.9% | 22.50 | 77.64 | 0.949 | 36.5% |
| Ridge | 39.65 | 74.56 | 0.953 | 33.8% | 37.99 | 70.78 | 0.957 | 33.3% |
| **Random Forest** | **8.46** | **26.08** | **0.994** | **11.8%** | **9.76** | **29.22** | **0.993** | **13.7%** |
| HistGradientBoosting | 13.25 | 30.54 | 0.992 | 13.8% | 14.53 | 32.76 | 0.991 | 15.4% |

**Random Forest achieved the lowest validation RMSE among the evaluated
models that beat both seasonal-naive baselines and was selected for the
final forecasting pipeline.** HistGradientBoosting also beat both
baselines but scored a higher validation RMSE than Random Forest. **Ridge
did not qualify**: its validation RMSE (74.56) is *worse* than the 168h
baseline (65.94), despite being "better than the weaker baseline" --
exactly why the criterion requires beating both, not just one, before a
model is even considered.

**Ridge's error profile is worth a second look, not just its RMSE rank.**
Its MAE (39.65) is *worse* than either baseline's MAE, even though its RMSE
sits between them -- a linear model here has more evenly-spread moderate
error rather than the baselines' occasional large misses. RMSE and MAE can
disagree about which model is "better," and did.

**A high R² is not, by itself, evidence a model is good here.** Every
model in the table, including the two naive baselines, scores R² ≥ 0.94 --
this dataset's strong daily/weekly regularity makes R² easy to score well
on even without real forecasting skill. RMSE/MAE against the baselines is
what actually discriminates model quality; R² alone would have been
misleading.

**The split correction changed these numbers only slightly**: e.g. Random
Forest's validation RMSE moved from 26.01 (buggy target_ts split) to 26.08
(corrected origin_ts split) -- a ~0.3% difference. This makes sense: the
bug only misplaced a small number of rows (origins within 24h of the two
split boundaries, across 60 buildings), a tiny fraction of 684k+ training
rows. The correction was methodologically necessary regardless of its
small practical effect on this particular dataset -- with different
boundary dates or a shorter dataset, the effect could easily have been
larger, and the split should be correct on principle, not just when it
happens not to matter.

## 9. Horizon-by-horizon (Random Forest, validation)

Error is **not** monotonically increasing with horizon, though it broadly
worsens: RMSE rises sharply from 6.38 (h=1) to a plateau of ~30-33 across
h=7-14, then *decreases* through h=15-23 (down to 13.39 at h=23), before
spiking to 44.48 at h=24 -- a 7.0x jump from h=1's error. MAE follows a
much flatter, milder version of the same shape (2.91 → ~10-11 → 6.22 →
11.63), confirming RMSE's swings are driven by a smaller number of large
errors, not a uniform accuracy decline. The h=24 spike coincides with a
maximally long lag_1-to-target gap (predicting "this exact hour, one full
day out") and a slightly smaller sample (n=5,165 vs 5,184 elsewhere, an
edge effect of origin availability). See `reports/forecasting/03_error_by_horizon.png`.

## 10. Per-building / per-site / per-primary_use (Random Forest, validation)

**The pooled R² (0.994) substantially overstates individual building
reliability.** Per-building R² ranges from **-1.06 to 0.966** (median
0.887); **22 of 60 buildings score below 0.8**. The worst case is a
genuine R² paradox, not a bad prediction: buildings with very low
consumption variance can have R² driven sharply negative by a tiny
absolute error, since R² compares against a predict-the-mean baseline.
**CV(RMSE)% is the metric that should be used to compare buildings** (it
ranges a far more sensible 1.0%-28.4% per building); raw R² should not be
used to rank them. HistGradientBoosting is markedly less robust by this
same lens: its worst per-building R² is **-813.9** (vs RF's -1.06) -- one
more reason RF, not HistGB, is the better choice even though their pooled
metrics look close in section 8.

Per-site (CV(RMSE)%, the fair cross-site metric given very different
absolute consumption scales -- Phase 6 established site 1's buildings run
much larger on average): site 1 9.7%, site 2 12.2%, site 3 8.9% -- broadly
comparable once scale is normalized out, even though raw RMSE differs by
~3.7x (54.7 vs 14.8) between the worst and best.

Per primary_use (CV(RMSE)%): Warehouse/storage 6.7% (n=1,847, smallest
category), Education 9.4% (n=43,188, largest), Entertainment 9.3%, Public
services 10.5%, Lodging 13.5%, Office 14.8% (worst, and lowest R² at
0.951), Other 18.4% (n=5,516). No category was small enough to be excluded
under the n≥30 minimum-sample rule (`evaluation.MIN_SAMPLES_FOR_GROUP_METRIC`).

## 11. Predictions written

`predict.py` writes one row per (building, horizon) from each building's
latest available origin. Real run: **1,440 rows** (60 buildings × 24
horizons), **100% satisfy `generated_at < target_ts`** (verified via SQL,
not assumed). Origin = 2017-12-31 00:00 local (the last available
midnight); the final predicted hour extends genuinely beyond the loaded
historical data.

## 12. Limitations (stated plainly, not hidden)

- **Ridge's alpha grid topped out at 100, and CV RMSE was still improving
  at that point** -- a wider grid might find a better-regularized Ridge,
  though it's very unlikely to close the gap to RF/HistGB given how large
  that gap is.
- **No weather features** (section 5) -- a documented scope decision, not
  an oversight; the next natural experiment.
- **Random Forest's artifact is ~550MB** and its fit time (~4.2 min)
  dominates the whole training pipeline; a lighter configuration (fewer
  trees, or swapping to HistGB, which trained in 25s for only slightly
  worse pooled metrics) is a reasonable trade if training cadence needs to
  be fast.
- **One representative-building plot (`02_representative_forecast.png`)
  shows the model completely missing a real step-change** in consumption
  on a specific real day -- picked as literally the first available
  complete example, not cherry-picked either way. It's an honest
  illustration that pooled metrics look excellent while specific real
  forecasts can still miss real events the model has no feature to see
  coming.
- **This is a first model**, evaluated on 6 months of held-out data from
  one specific year; it has not been validated against a second full year
  of unseen data, and building/weather/occupancy patterns could shift in
  ways this dataset can't reveal.

## 13. Reproducibility, performance, and bugs found

- Extraction (fetch + feature engineering + split/clean) for the full
  1,052,400-row dataset: ~48s in the corrected run (fetch + feature
  engineering + split/clean combined) -- measured on every run, not
  assumed once. An earlier version fetched via SQLAlchemy ORM row-by-row
  hydration and took 170s just for the fetch step; rewriting to a
  Core-level bulk query + vectorized pandas joins cut that dramatically.
- Every model/baseline is recorded in `model_versions` (composite PK
  `model_name, model_version`): feature_version, train/validation/test
  date ranges, hyperparameters (JSONB), random_seed, metrics (JSONB),
  artifact_path, created_at. `predictions.model_name/model_version` has a
  real foreign key into this table -- a prediction can never reference a
  model version that wasn't actually recorded.
- Random seed 42 used everywhere stochastic (Ridge, RF, HistGB);
  `TimeSeriesSplit` fold boundaries are recorded in the training report
  for full transparency.
- `predict.py` never retrains -- it loads a saved joblib artifact (or, for
  the seasonal-naive baselines, needs no artifact at all) and only writes
  to the `predictions` table via an idempotent upsert (same ON CONFLICT DO
  UPDATE pattern as Phase 4's ingestion loader).

**Bugs found while building this** (grounding, not marketing):

1. **OOM on the first dataset-build attempt**: expanding every hourly
   origin x 24 horizons produced ~23M+ rows before any filtering.
   Restricting origins to local midnight (section 1) fixed this and is
   also the more correct day-ahead formulation.
2. **`build_feature_frame` dropped `site_id`**: a hard-required-column bug
   only surfaced by a real training run against the full DB (small
   synthetic test fixtures didn't exercise the `by_site` evaluation path);
   fixed, and a regression test added.
3. **170s ORM-hydration fetch**: rewritten to a Core-level bulk query
   returning dicts + vectorized pandas joins.
4. **`occupants`/`number_of_floors` in the hard-required-complete column
   set** would have silently dropped most of the dataset (these are only
   43%/50% populated) -- caught by an integration test expecting nonzero
   usable rows; fixed by moving them to a separately-imputed feature group.
5. **`generated_at = datetime.now()` would have been backwards**: this
   system operates on a fixed 2016-2018 historical dataset, so real
   wall-clock "now" (2026) is *after* every target_ts it could produce --
   the opposite of the required invariant. Fixed to `generated_at =
   origin_ts` (also what the phase's own example implies: an origin-aligned
   hour, not a literal timestamp).
6. **`models/` and `reports/` artifacts landed under `backend/` on the
   host** the first time each was generated, because `/app` was already
   fully bind-mounted to `./backend` and the more specific subpath mount
   hadn't been added yet. Fixed by adding explicit `./models:/app/models`
   and `./reports:/app/reports` mounts; stray files moved to the correct
   location.
7. **Split-by-`target_ts` let a single 24-hour forecast straddle a split
   boundary** (this document's correction note, section 3): an origin
   within 24h of a boundary could have some horizons in one split and the
   rest in the next. Fixed by splitting on `origin_ts`; proven with a unit
   test constructing the exact "23 rows before the boundary, 1 at/after"
   scenario, and an integration test seeding real DB data that spans the
   boundary and confirming no `origin_ts` appears in two splits.
