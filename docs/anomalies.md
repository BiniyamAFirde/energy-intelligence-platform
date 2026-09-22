# Anomaly detection (Phase 8)

Every number below is from a real run of `python -m energy_platform.anomalies.evaluate`
and `detect.py` against the loaded 60-building BDG2 subset
(`data/processed/anomaly_evaluation_report.json`, `reports/anomalies/*.png`,
the live `alerts`/`model_versions` tables) -- not estimated or assumed.

## 1. The problem

Answer "which building/time periods show abnormal consumption, why, and how
confident are we" -- not just list strange-looking values. BDG2 has no
real, trustworthy anomaly labels, so this phase builds a controlled,
reproducible **synthetic injection framework** (section 3) to evaluate
against, and four detectors with genuinely different failure modes so
their combination covers more ground than any one alone (section 2).

## 2. Four detectors

| | Method | Leakage-safety mechanism | Fit period |
|---|---|---|---|
| A | Rule-based data quality | No history/model at all -- deterministic thresholds | n/a |
| B | Behavioral (robust z-score) | Causal same-hour-of-day rolling baseline (shift-then-rolling) | n/a (statistical, not fit) |
| C | Forecast residual | Reuses Phase 7's RF model, actual from `energy_measurements` (never `predictions.actual_kwh`) | n/a (reuses Phase 7's TRAIN-fit model) |
| D | Isolation Forest | Genuinely fitted on TRAIN-period data only | 2016-01-01 -- 2017-06-30 |

**A -- data quality** (`detector_data_quality.py`): five deterministic
checks -- `negative_value`, `missing`, `zero_run` (≥3h), `stuck_meter`
(≥30h, see the mis-calibration finding below), `isolated_spike` (≥4x the
local 7h-window median, with non-elevated neighbours). No history needed;
purely a function of the point and its immediate surroundings.

**B -- behavioral** (`detector_behavioral.py`): flags a point against how
that SAME sensor behaves at that SAME hour-of-day historically (grouped by
`(sensor_id, hour_of_day)`, not a blind rolling window across all hours --
comparing 2am to daytime would flag normal diurnal variation). Robust
(median/MAD) rather than mean/std, since the baseline window can itself
contain a few real or injected anomalies. 14-day baseline window.

**C -- forecast residual** (`detector_forecast_residual.py`): `actual -
predicted`, standardized by a causal 72h rolling std of that sensor's own
past residuals. Never retrains the Phase 7 Random Forest; reuses its
existing artifact and the historical predictions Task #14 backfilled
(`generate_predictions_for_range`).

**D -- Isolation Forest** (`detector_isolation_forest.py`): the one
genuinely multivariate detector -- 10 engineered features (raw value,
previous-hour value, 24h/168h rolling stats, Detector B's own z-score as
an input feature, calendar phase) scaled and fed to a scikit-learn
`IsolationForest`, fit on TRAIN-period data only (never validation/test,
where synthetic anomalies get injected). Verified directly, not just by
convention: `test_fitting_on_train_slice_is_unaffected_by_validation_
period_contamination` corrupts the validation portion of the input series
and confirms the fitted scaler/model are byte-identical either way,
because every feature is causal (shift-then-rolling) so a later
corruption cannot reach an earlier row's features.

**Model versioning is intentionally asymmetric.** Only Isolation Forest
gets a `model_versions` row (seed=42, hyperparameters, TRAIN period,
artifact path) -- it is the only detector with an actual training phase.
A/B/C are traced via `method` + `detector_version` on the `alerts` table
instead; forcing them through `model_versions`' NOT NULL train/validation/
test date columns would mean fabricating meaningless date ranges for
something that was never trained.

## 3. Synthetic anomaly injection

`injection.py` builds a controlled substitute for ground truth BDG2
doesn't have. Six types, seeded (`numpy.random.default_rng`), fully
reproducible, and **never** written into `energy_measurements` -- injected
points exist only in an in-memory copy fed to detectors, with the label
recorded in the separate `synthetic_anomalies` table/DataFrame shape
(`sensor_id, ts, anomaly_type, original_value, injected_value, split,
seed`).

| Type | Mechanism | Duration |
|---|---|---|
| spike | value × uniform(3, 6) | 1h |
| drop | value × uniform(0.05, 0.3) | 1h |
| sustained_high | value × uniform(1.6, 2.5) | 6-24h |
| sustained_low | value × uniform(0.2, 0.5) | 6-24h |
| stuck_meter | held at the span's first value | 36-72h |
| zero_consumption | forced to 0.0 | 1-4h |

Injections are placed only in VALIDATION and TEST periods, never TRAIN --
this is what keeps Isolation Forest's fit uncontaminated with *no*
special-casing (there is simply nothing injected in the period it's fit
on). Rejection sampling (`_pick_start_points`) keeps injected spans from
overlapping within a sensor, so every evaluation point has one unambiguous
label.

**A real bug this framework caught.** `stuck_meter`'s duration was
originally 1-4h, shared with `zero_consumption`. The first full evaluation
run showed Detector A flagging **35% of all validation-period points** as
false positives. Investigation (not assumption) traced this to a genuine,
previously-undocumented BDG2 characteristic: ~21 of 60 sensors report
**daily-resolution readings** (one value per calendar day, replicated
across all 24 hours once reindexed to an hourly grid) -- confirmed
directly (`nunique() == 92` over a 92-day window, run lengths of exactly
~24h). `MIN_STUCK_RUN_HOURS=4` could not tell "this sensor fundamentally
reports daily" from "this meter is stuck." Fixed by raising the threshold
to 30h (comfortably above one calendar day) and giving `stuck_meter`
injections their own longer duration (36-72h, since a synthetic injection
shorter than the detection threshold could never be detectable by the
detector it's meant to benchmark). A regression test
(`test_one_calendar_day_flat_run_is_not_flagged_as_stuck_meter`) encodes
this directly. Before the fix: data_quality FPR=35.2%, precision=0.3% on
validation. After: FPR=3.5%, precision=14.4% (section 5).

## 4. Evaluation methodology

Thresholds for the three score-based detectors (B, C, D) are selected by
sweeping candidates and picking the one that maximizes F1 **on VALIDATION-
period injections only**, then **frozen** and applied unchanged to TEST --
TEST metrics never influence the threshold. Detector A has no single
sweepable threshold (five independent rule constants, chosen by domain
reasoning, not by fitting to either split's labels) and is evaluated
identically on both splits.

**Binary evaluation.** A point counts as a positive if it appears anywhere
in that split's synthetic ground truth, regardless of which of the six
injected types produced it; a detector's output counts as a "predicted
positive" if it flagged that point at all, regardless of which
`anomaly_type` it assigned. This tests each detector as a general-purpose
"does this need a look" flag. A secondary per-injected-type recall
breakdown (`04_recall_by_type_heatmap.png`) gives the diagnostic detail
underneath the headline number.

**Class balance/prevalence, reported before any performance claim:**

| Split | Total slots | Injected anomalies | Rate | Affected sensors |
|---|---|---|---|---|
| Validation | 132,480 | 1,241 | 0.94% | 42 |
| Test | 132,540 | 1,343 | 1.01% | 48 |

(`03_class_balance.png` breaks this down by injected type.)

## 5. Results

Frozen thresholds (selected on validation): behavioral |z| ≥ 8.0,
forecast-residual |z| ≥ 3.0, Isolation Forest score ≥ 0.6449.

| Detector | Val P / R / F1 | Test P / R / F1 | Val FPR | Test FPR |
|---|---|---|---|---|
| A: data quality | 0.144 / 0.627 / 0.234 | 0.100 / 0.633 / 0.173 | 3.52% | 5.81% |
| B: behavioral | 0.101 / 0.326 / 0.154 | 0.122 / 0.279 / 0.170 | 2.74% | 2.05% |
| C: forecast residual | 0.129 / 0.289 / 0.179 | 0.114 / 0.233 / 0.153 | 1.84% | 1.86% |
| D: Isolation Forest | 0.076 / 0.072 / 0.074 | 0.048 / 0.071 / 0.057 | 0.83% | 1.43% |

(`01_detector_comparison.png`, `02_threshold_sweep.png`.)

**Best-performing detector, by the criterion decided before results were
seen (highest validation F1): Detector A (data quality), F1=0.234.** This
is not "A is the best detector" in the abstract -- its F1 lead comes
almost entirely from a perfect recall on `stuck_meter` (its own specialty)
and no ability at all on `sustained_high`/`sustained_low` (0.0 recall,
entirely out of scope by design; that is Detector B/C's job). The honest
reading is per-type, not a single ranking:

- **A** is the only detector that reliably catches `stuck_meter` (1.00
  recall) and does reasonably on `spike` (0.67-0.73) and
  `zero_consumption` (0.59-0.72) -- both gaps are fully explained, not
  mysterious: spike recall matches the fraction of injected multipliers
  (uniform 3-6x) that clear A's 4x threshold; zero_consumption recall
  matches the fraction of injected durations (uniform 1-4h) that clear
  its 3h minimum.
- **C** (forecast residual) is the strongest on `spike`/`drop`/
  `zero_consumption` (0.8-1.0) -- exactly the sudden, large deviations an
  ML model conditioned on recent history would be most surprised by.
- **B** (behavioral) is the most balanced across types, including the
  only meaningful signal on `sustained_low` among the statistical
  detectors.
- **D** (Isolation Forest) is the weakest performer on every type. This
  is a genuine, not-hidden finding: its 10-feature multivariate score is
  optimized for joint unusualness, while this benchmark's injected
  anomalies are largely univariate shifts along one axis (the raw value)
  -- exactly what A/B/C are each individually specialized for.

(`04_recall_by_type_heatmap.png` is the complete per-type breakdown.)

## 6. Example anomaly

Sensor 34, 2016-07-18 00:00 UTC: a single-hour reading of 634.58 kWh
against a local 7-hour-window median of 0.08 kWh (7,873x), with both
neighbouring hours normal (0.01 / 0.06 kWh) -- flagged by Detector A as
`isolated_spike`, severity `high`, score 7873.21. `07_example_anomaly.png`
shows the point against its surrounding 24 hours: two more one-hour spikes
are visible nearby (07-17 15:00, 07-18 03:00), consistent with an
intermittent sensor fault rather than one-off noise -- exactly the kind
of concrete, value-derived explanation this system is meant to produce,
not a generic "AI detected an anomaly."

## 7. Real production run

`python -m energy_platform.anomalies.detect` against the full loaded
dataset (all 60 sensors, 2016-01-01 -- 2018-01-01) wrote **99,651 alerts**
(`05_alerts_by_method_severity.png`, `06_alerts_over_time.png`):

| Method | Count | Dominant anomaly_type |
|---|---|---|
| data_quality | 53,217 | stuck_meter (37,823), missing (15,386), isolated_spike (8) |
| behavioral | 24,084 | behavioral_deviation |
| isolation_forest | 17,273 | isolation_forest |
| forecast_residual | 5,077 | high_residual (2,597), low_residual (2,480) |

`negative_value` and `zero_run` both fired **zero** times on real data --
the expected, documented pass for negative_value (BDG2, once loaded, has
no negative readings; unit tests inject synthetic negatives to prove
detection still works) and a genuine, sensible finding for zero_run (no
occupied commercial building in this subset has a real ≥3h zero-
consumption stretch).

## 8. Leakage checks

- **Detector B/D same-hour baseline**: `shift(1)`-then-`rolling`, same
  pattern as `features.add_rolling_features` -- verified directly
  (`test_baseline_only_uses_strictly_earlier_same_hour_observations`
  hand-recomputes the expected window and confirms an exact match).
- **Detector D fit/eval separation**: verified by actually corrupting
  validation-period data and confirming the TRAIN-fit scaler/model are
  unchanged (section 2).
- **Detector C's actual value**: always `energy_measurements`, never the
  unused `predictions.actual_kwh` column (`test_join_uses_energy_
  measurements_actuals_not_predictions_actual_kwh`).
- **Synthetic injection isolation**: injections never touch TRAIN
  (`inject_anomalies` is only ever called with VALIDATION/TEST slices);
  `energy_measurements` itself is never written to by the injection
  framework (verified by `test_injection_never_mutates_the_input_series`).

## 9. Limitations

- **Detector C's production coverage starts 2017-07, not 2016-01.** Task
  #14's prediction backfill was scoped to VALIDATION+TEST (2017-07-01 --
  2018-01-01, 241,680 rows) -- what evaluation needed -- not the full
  TRAIN period. `06_alerts_over_time.png` shows this directly: no green
  (forecast_residual) bars before mid-2017. Extending Detector C's
  production coverage back to 2016 would need one more
  `generate_predictions_for_range` backfill over the TRAIN targets too;
  out of scope for this phase.
- **Precision is low in absolute terms (7-23% at best).** At <1%
  synthetic anomaly prevalence, this is expected of any detector that
  fires more than a handful of times, and the FPR column (0.8-5.8%) is
  the more informative number for judging real-world alert volume.
- **Isolation Forest underperforms the other three on this benchmark**
  (section 5) -- a real characteristic of a multivariate detector
  evaluated against largely univariate injected shifts, not a bug.
- **Detector A's "false positives" against the synthetic-only ground
  truth are dominated by real, pre-existing BDG2 data issues** (37,823
  real stuck-meter hours across the full dataset) that the synthetic
  benchmark has no way to credit, since it only knows about injected
  points. Detector A's true real-world precision is very likely
  understated by the numbers in section 5.
- **Detector B's hour-of-day grouping uses UTC hour, not per-building
  local hour** -- a fixed offset since every currently-loaded site is
  US/Eastern (`dataset.py`); would need to become genuinely per-building
  if a future dataset spans multiple timezones.

## 10. Reproduction

```bash
# 1. Migration (adds alerts.anomaly_type/detector_version/residual + synthetic_anomalies)
alembic upgrade head

# 2. Historical prediction backfill for Detector C (VALIDATION+TEST targets)
python -m energy_platform.forecasting.predict random_forest  # generate_predictions_for_range is called via evaluate.py's own predictions_df load

# 3. Evaluation: fits + registers Isolation Forest, sweeps thresholds on
#    validation, freezes them, evaluates on test, writes
#    data/processed/anomaly_evaluation_report.json (~12-15 min)
python -m energy_platform.anomalies.evaluate

# 4. Production detection run against real (non-injected) data, writes to `alerts` (~6 min)
python -m energy_platform.anomalies.detect

# 5. Plots
python -m energy_platform.anomalies.plots

# 6. Full test suite
pytest tests/ -q
```

## 11. Files changed

- `db/models.py`: `Alert` extended (`anomaly_type`, `detector_version`,
  `residual`, idempotency constraint); new `SyntheticAnomaly` model.
- `alembic/versions/c072cc66140a_*.py`: the migration.
- `anomalies/injection.py`, `detector_data_quality.py`,
  `detector_behavioral.py`, `detector_forecast_residual.py`,
  `detector_isolation_forest.py`, `evaluation.py`, `evaluate.py`,
  `detect.py`, `plots.py`: this phase's core modules.
- `forecasting/dataset.py`, `predict.py`: added
  `build_prediction_request_for_range`/`generate_predictions_for_range`
  (behavior-preserving; existing Phase 7 functions unchanged).
- `repositories/alerts.py`, `repositories/predictions.py`
  (`list_predictions` added).
- `schemas/anomalies.py`, `services/anomaly_service.py`,
  `api/routers/anomalies.py`: `/api/v1/alerts`,
  `/api/v1/alerts/{id}`, `/api/v1/alerts/summary`,
  `/api/v1/buildings/{id}/alerts`.
- Tests: `tests/unit/test_anomaly_injection.py`,
  `test_detector_data_quality.py`, `test_detector_behavioral.py`,
  `test_detector_forecast_residual.py`, `test_detector_isolation_forest.py`,
  `test_evaluation.py`; `tests/integration/test_migration.py` (extended),
  `test_forecasting_predict.py` (extended), `test_alerts_repository.py`,
  `test_api_anomalies.py`, `test_detector_isolation_forest_fit.py`.
