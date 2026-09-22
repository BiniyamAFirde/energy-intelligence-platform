"""Builds the full labeled forecasting dataset from PostgreSQL via the
existing repository layer (energy/buildings/sensors/sites repositories --
no new ad-hoc SQL), then hands off to features.py for the actual
transformations. This module's job is orchestration: fetch -> regularize
per building -> engineer features -> expand to (origin, horizon) rows ->
split chronologically.

Split boundaries (see docs/forecasting.md for the full data-inspection
justification): every currently-loaded site is US/Eastern (Phase 6), so
split boundaries are defined in that reference timezone and converted to
UTC once here. If a future subset spans multiple timezones, this constant
would need to become genuinely per-site.
"""

from __future__ import annotations

import time

import pandas as pd
from sqlalchemy.orm import Session

from energy_platform.forecasting.features import (
    add_calendar_features,
    add_lag_features,
    add_rolling_features,
    add_target_columns,
    expand_horizons,
    filter_origins_to_local_midnight,
    reindex_to_hourly_grid,
)
from energy_platform.repositories import buildings as buildings_repo
from energy_platform.repositories import energy as energy_repo
from energy_platform.repositories import sites as sites_repo

REFERENCE_TZ = "US/Eastern"

TRAIN_START = pd.Timestamp("2016-01-01 00:00", tz=REFERENCE_TZ).tz_convert("UTC")
TRAIN_END_EXCL = pd.Timestamp("2017-07-01 00:00", tz=REFERENCE_TZ).tz_convert("UTC")
VALIDATION_END_EXCL = pd.Timestamp("2017-10-01 00:00", tz=REFERENCE_TZ).tz_convert("UTC")
TEST_END_EXCL = pd.Timestamp("2018-01-01 00:00", tz=REFERENCE_TZ).tz_convert("UTC")

HORIZONS = range(1, 25)

CALENDAR_FEATURES = [
    "hour", "day_of_week", "day_of_month", "month", "is_weekend",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos", "month_sin", "month_cos",
]
LAG_FEATURES = ["lag_1", "lag_2", "lag_3", "lag_24", "lag_48", "lag_168"]
ROLLING_FEATURES = ["rolling_24h_mean", "rolling_24h_std", "rolling_168h_mean"]
# area_sqm is 100% populated (Phase 6) -- always required. number_of_floors
# (50%) and occupants (43%) are not: requiring them non-NaN would drop most
# buildings from training, not just a few rows. They stay in the feature
# set but are median-imputed downstream (models.py, fit on train only),
# not hard-required here.
STATIC_NUMERIC_REQUIRED = ["area_sqm"]
STATIC_NUMERIC_IMPUTED = ["number_of_floors", "occupants"]
STATIC_CATEGORICAL_FEATURES = ["primary_use"]
HORIZON_FEATURE = ["horizon"]

FEATURE_COLUMNS = (
    CALENDAR_FEATURES + LAG_FEATURES + ROLLING_FEATURES
    + STATIC_NUMERIC_REQUIRED + STATIC_NUMERIC_IMPUTED
    + STATIC_CATEGORICAL_FEATURES + HORIZON_FEATURE
)
REQUIRED_COMPLETE_COLUMNS = (
    CALENDAR_FEATURES + LAG_FEATURES + ROLLING_FEATURES
    + STATIC_NUMERIC_REQUIRED + STATIC_CATEGORICAL_FEATURES + HORIZON_FEATURE
    + ["target_kwh"]
)

# Static metadata excluded and why (Phase 6 findings, not re-derived here):
#   sub_primary_use  -- 18 categories across 60 buildings, most with 1-3
#                        members; too fine-grained to generalize.
#   year_built       -- only 8/60 buildings populated (13%), insufficient.
#   eui/site_eui/source_eui/energy_star_rating -- 0/60 populated.
# Weather: excluded from this model entirely (Option A from the Phase 7
# spec's weather menu) -- see docs/forecasting.md for the full rationale.


def load_raw_frame(session: Session) -> pd.DataFrame:
    """One row per (building, hour): energy reading + static building
    metadata + the building's site timezone, needed for local-time
    calendar features. Joins are done with pandas .map()/.merge() (vectorized,
    C-level) rather than a Python for-loop over ~1M measurement rows --
    that loop was the original, much slower implementation."""
    all_buildings, _ = buildings_repo.list_buildings(session, limit=1000, offset=0)
    all_sites, _ = sites_repo.list_sites(session, limit=1000, offset=0)
    tz_by_site = {s.site_id: s.timezone for s in all_sites}

    meta_df = pd.DataFrame([
        {
            "building_id": b.building_id,
            "site_id": b.site_id,
            "tz_name": tz_by_site[b.site_id],
            "area_sqm": float(b.area_sqm) if b.area_sqm is not None else None,
            "primary_use": b.primary_use,
            "number_of_floors": float(b.number_of_floors) if b.number_of_floors is not None else None,
            "occupants": float(b.occupants) if b.occupants is not None else None,
        }
        for b in all_buildings
    ])

    from energy_platform.repositories import sensors as sensors_repo
    all_sensors, _ = sensors_repo.list_sensors(session, limit=1000, offset=0)
    sensor_building_df = pd.DataFrame([
        {"sensor_id": s.sensor_id, "building_id": s.building_id}
        for s in all_sensors if s.meter_type == "electricity"
    ])

    measurements = energy_repo.list_measurements_for_sensors(
        session, sensor_building_df["sensor_id"].tolist()
    )
    df = pd.DataFrame(measurements)
    df["consumption_kwh"] = df["consumption_kwh"].astype(float)

    df = df.merge(sensor_building_df, on="sensor_id", how="left")
    df = df.merge(meta_df, on="building_id", how="left")
    return df


def build_feature_frame(raw_df: pd.DataFrame) -> pd.DataFrame:
    """raw_df: output of load_raw_frame. Returns the long-format
    (origin, horizon) dataset with all engineered features and the target,
    NOT yet split and NOT yet dropna'd -- see split_dataset /
    drop_incomplete_rows."""
    static_cols = ["building_id", "site_id", "tz_name", "area_sqm", "primary_use", "number_of_floors", "occupants"]
    static_by_sensor = raw_df.drop_duplicates("sensor_id").set_index("sensor_id")[static_cols]

    series = raw_df[["sensor_id", "ts", "consumption_kwh"]].sort_values(["sensor_id", "ts"])
    series = reindex_to_hourly_grid(series, "ts", group_col="sensor_id")
    # Lag/rolling/target-shift all need the *complete* hourly grid to be
    # positionally correct -- computed before restricting to daily origins.
    series = add_lag_features(series, "sensor_id", "consumption_kwh")
    series = add_rolling_features(series, "sensor_id", "consumption_kwh")
    series = add_target_columns(series, "sensor_id", "consumption_kwh", horizons=HORIZONS)

    # NOW restrict to one origin per building per day (local midnight) --
    # per-tz, since (in principle, if not currently) different buildings
    # can have different site timezones. Done before expand_horizons so the
    # 24x row multiplication only ever applies to actual forecast origins,
    # not every hour (see filter_origins_to_local_midnight's docstring for
    # why: this is also what keeps the expansion laptop-sized).
    series = series.merge(
        static_by_sensor[["tz_name"]], left_on="sensor_id", right_index=True
    )
    origin_frames = [
        filter_origins_to_local_midnight(group, "ts", tz_name)
        for tz_name, group in series.groupby("tz_name")
    ]
    origins = pd.concat(origin_frames, ignore_index=True).drop(columns=["tz_name"])

    long_df = expand_horizons(origins, "ts", horizons=HORIZONS)
    long_df = long_df.rename(columns={"ts": "origin_ts"})

    long_df = long_df.join(static_by_sensor, on="sensor_id")

    # Calendar features describe the TARGET timestamp (legitimately known
    # in advance), grouped by each building's own site timezone.
    calendar_frames = []
    for tz_name, group in long_df.groupby("tz_name"):
        calendar_frames.append(add_calendar_features(group, "target_ts", tz_name))
    long_df = pd.concat(calendar_frames, ignore_index=True)

    return long_df


def split_dataset(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Splits by origin_ts -- the forecast origin, not the target being
    predicted. This is the corrected design (previously split by
    target_ts): with one origin producing 24 rows (h=1..24), a target_ts
    split could put the same origin's h=1..23 in one split and h=24 in the
    next whenever the origin fell within 24h of a boundary -- e.g. an
    origin at local midnight June 30 has targets June 30 01:00..July 1
    00:00, so h=24 alone would land on the validation side of a July 1
    boundary while h=1..23 stayed in train. Splitting by origin_ts instead
    guarantees every one of an origin's 24 rows -- the complete day-ahead
    forecast -- lands in exactly one split, never straddling a boundary.

    This does not change the leakage guarantee: a row's FEATURES still only
    ever use data at-or-before its own origin (enforced in features.py),
    independent of which split that origin belongs to. A test-period row's
    lag/rolling features may legitimately draw on training- or validation-
    period actuals -- that's just using real available history, not
    leakage; see docs/forecasting.md and the dedicated leakage test suite."""
    train = df[(df["origin_ts"] >= TRAIN_START) & (df["origin_ts"] < TRAIN_END_EXCL)]
    val = df[(df["origin_ts"] >= TRAIN_END_EXCL) & (df["origin_ts"] < VALIDATION_END_EXCL)]
    test = df[(df["origin_ts"] >= VALIDATION_END_EXCL) & (df["origin_ts"] < TEST_END_EXCL)]
    return train.copy(), val.copy(), test.copy()


def drop_incomplete_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Complete-case policy applied uniformly across all models (Ridge and
    RandomForestRegressor can't handle NaN; HistGradientBoostingRegressor
    could, but using a different effective training set per model would
    confound the model comparison). Drops rows missing any feature
    (warm-up period, or a NaN lag/rolling pulled from a missing reading)
    or missing the target itself."""
    return df.dropna(subset=REQUIRED_COMPLETE_COLUMNS).reset_index(drop=True)


def build_training_dataset(session: Session) -> dict:
    """End-to-end: fetch -> engineer -> split -> drop incomplete rows.
    Returns a dict with train/val/test DataFrames plus timing and row-count
    metadata (Phase 7 requires measuring extraction time, not just running it)."""
    t0 = time.time()
    raw_df = load_raw_frame(session)
    t1 = time.time()
    hourly_series = reindex_to_hourly_grid(
        raw_df[["sensor_id", "ts", "consumption_kwh"]].sort_values(["sensor_id", "ts"]),
        "ts", group_col="sensor_id",
    )
    feature_df = build_feature_frame(raw_df)
    t2 = time.time()
    train_raw, val_raw, test_raw = split_dataset(feature_df)
    train = drop_incomplete_rows(train_raw)
    val = drop_incomplete_rows(val_raw)
    test = drop_incomplete_rows(test_raw)
    t3 = time.time()

    return {
        "train": train, "validation": val, "test": test,
        "hourly_series": hourly_series,  # for baselines.py, which needs arbitrary lookback offsets
        "timing_seconds": {"fetch": t1 - t0, "feature_engineering": t2 - t1, "split_and_clean": t3 - t2, "total": t3 - t0},
        "row_counts": {
            "raw_measurements": len(raw_df),
            "train_before_dropna": len(train_raw), "train_after_dropna": len(train),
            "validation_before_dropna": len(val_raw), "validation_after_dropna": len(val),
            "test_before_dropna": len(test_raw), "test_after_dropna": len(test),
        },
        "split_dates": {
            "train_start": TRAIN_START, "train_end_excl": TRAIN_END_EXCL,
            "validation_end_excl": VALIDATION_END_EXCL, "test_end_excl": TEST_END_EXCL,
        },
    }


def _prediction_ready_features(session: Session) -> pd.DataFrame:
    """Shared by build_prediction_request (latest origin only) and
    build_prediction_request_for_range (Phase 8's historical backfill,
    below): the same feature-engineering path used for training, with the
    target NOT required to be present (prediction's whole point is that
    the target usually isn't observed yet)."""
    raw_df = load_raw_frame(session)
    feature_df = build_feature_frame(raw_df)
    required = [c for c in REQUIRED_COMPLETE_COLUMNS if c != "target_kwh"]
    return feature_df.dropna(subset=required)


def build_prediction_request(session: Session) -> pd.DataFrame:
    """One row per (building, horizon) from each building's *latest*
    available local-midnight origin -- the actual "day ahead" prediction
    request: given everything known up to now, forecast the next 24 hours.
    target_kwh in the result is typically NaN (the whole point of
    prediction is that it isn't observed yet) and is never used -- only
    the FEATURE_COLUMNS are fed to the model."""
    feature_df = _prediction_ready_features(session)
    latest_origin = feature_df.groupby("sensor_id")["origin_ts"].transform("max")
    return feature_df[feature_df["origin_ts"] == latest_origin].reset_index(drop=True)


def build_prediction_request_for_range(
    session: Session, origin_start: pd.Timestamp, origin_end_excl: pd.Timestamp
) -> pd.DataFrame:
    """Backfill variant (Phase 8): every origin in [origin_start,
    origin_end_excl) rather than only the latest. Used by the forecast-
    residual anomaly detector, which needs prediction coverage over a full
    historical period -- the live `predictions` table from Phase 7 only
    ever covers the single most-recent origin per building. Uses the exact
    same feature-generation path as training; does NOT retrain anything,
    and the caller is responsible for loading an already-fitted model
    artifact to score these rows."""
    feature_df = _prediction_ready_features(session)
    mask = (feature_df["origin_ts"] >= origin_start) & (feature_df["origin_ts"] < origin_end_excl)
    return feature_df[mask].reset_index(drop=True)
