"""Dedicated leakage tests -- as important as the models themselves (Phase
7 spec). Each test targets one specific way this pipeline could cheat:

1. training features never contain future target values
2. rolling features are shifted correctly (excl. the origin's own row)
3. validation/test data are never used during training
4. scaler/encoder fitting occurs only on training data
5. future observed weather is never used in the main day-ahead model
6. prediction generated_at < target_ts always holds

(1) and (2) are also covered at the unit level in test_forecasting_features.py
against small hand-built series; this file re-proves them end-to-end
through the real dataset-building pipeline against real (seeded) DB data,
which is a meaningfully different and stronger guarantee -- the unit tests
could pass while the orchestration in dataset.py still wired something up
wrong.
"""

import numpy as np
import pandas as pd
import pytest

from energy_platform.forecasting import dataset, models
from energy_platform.ingestion.loader import (
    upsert_buildings,
    upsert_energy_measurements,
    upsert_sensors,
    upsert_sites,
)


def _seed_building(db_session, site_code, building_code, n_hours):
    site_ids = upsert_sites(db_session, [site_code], {site_code: "US/Eastern"})
    metadata_df = pd.DataFrame([{
        "building_id": building_code, "site_id": site_code, "primaryspaceusage": "Office",
        "sqm": 150.0, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": None, "occupants": None, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, [building_code], site_ids)
    sensor_ids = upsert_sensors(db_session, building_ids)
    db_session.flush()

    ts = pd.date_range(dataset.TRAIN_START, periods=n_hours, freq="h", tz="UTC")
    rng = np.random.default_rng(7)
    # A strictly increasing series: if any feature ever equals a *future*
    # value relative to its own origin, this construction makes that
    # detectable (future values are always numerically larger).
    values = np.arange(n_hours, dtype=float) + rng.normal(0, 0.01, n_hours)
    df = pd.DataFrame({"building_code": [building_code] * n_hours, "ts_utc": ts, "consumption_kwh": values})
    upsert_energy_measurements(db_session, df, {building_code: sensor_ids[building_code]})
    db_session.flush()
    return sensor_ids[building_code]


# --- 1. training features never contain future target values ---

def test_lag_and_rolling_features_never_exceed_origin_value(db_session):
    """With a strictly-increasing series, every lag/rolling feature value
    at origin T must be <= consumption(T) itself -- if any feature reflects
    data after T, this monotonic construction makes it read too high."""
    sensor_id = _seed_building(db_session, "LeakSite1", "leak_b1", n_hours=250)

    raw = dataset.load_raw_frame(db_session)
    feat = dataset.build_feature_frame(raw)
    rows = feat[feat.sensor_id == sensor_id]

    origin_value = rows["lag_1"]  # lag_1 = consumption(origin) itself
    for col in dataset.LAG_FEATURES + dataset.ROLLING_FEATURES:
        # NaN is expected for early-history warm-up rows (e.g. lag_2 at the
        # very first origin, before any prior hour exists) -- not a
        # leakage signal either way, so excluded from this comparison.
        comparable = rows[[col]].assign(origin=origin_value).dropna()
        assert (comparable[col] <= comparable["origin"] + 1e-6).all(), f"{col} exceeds the origin's own value"


def test_target_kwh_is_strictly_after_origin(db_session):
    sensor_id = _seed_building(db_session, "LeakSite2", "leak_b2", n_hours=250)
    raw = dataset.load_raw_frame(db_session)
    feat = dataset.build_feature_frame(raw)
    rows = feat[feat.sensor_id == sensor_id]
    assert (rows["target_ts"] > rows["origin_ts"]).all()
    assert (rows["target_ts"] == rows["origin_ts"] + pd.to_timedelta(rows["horizon"], unit="h")).all()


# --- 2. rolling features shifted correctly (end-to-end, real pipeline) ---

def test_rolling_24h_mean_excludes_origin_hour_end_to_end(db_session):
    sensor_id = _seed_building(db_session, "LeakSite3", "leak_b3", n_hours=250)
    raw = dataset.load_raw_frame(db_session)
    feat = dataset.build_feature_frame(raw)
    row = feat[(feat.sensor_id == sensor_id) & (feat.horizon == 1)].iloc[10]

    # Independently recompute the expected rolling mean from the raw
    # series, using only hours strictly before the origin.
    raw_series = raw[raw.sensor_id == sensor_id].sort_values("ts")
    window = raw_series[
        (raw_series.ts < row["origin_ts"]) & (raw_series.ts >= row["origin_ts"] - pd.Timedelta(hours=24))
    ]
    assert row["rolling_24h_mean"] == pytest.approx(window["consumption_kwh"].mean())


# --- 3. validation/test data never used during training ---

def test_split_dataset_train_val_test_are_disjoint(db_session):
    _seed_building(db_session, "LeakSite4", "leak_b4", n_hours=250)
    result = dataset.build_training_dataset(db_session)
    train_ts = set(result["train"]["target_ts"])
    val_ts = set(result["validation"]["target_ts"])
    test_ts = set(result["test"]["target_ts"])
    assert train_ts.isdisjoint(val_ts)
    assert train_ts.isdisjoint(test_ts)
    assert val_ts.isdisjoint(test_ts)


def test_split_dataset_no_origin_crosses_the_train_validation_boundary_end_to_end(db_session):
    """Regression test for the origin_ts split correction: seeds real data
    that spans the train/validation boundary and confirms, through the
    actual DB pipeline (not just synthetic frames), that no origin_ts
    appears on both sides -- the exact bug the user identified (an origin
    near midnight June 30 splitting its 24 horizons across train/val)."""
    site_ids = upsert_sites(db_session, ["BoundarySite"], {"BoundarySite": "US/Eastern"})
    metadata_df = pd.DataFrame([{
        "building_id": "boundary_b1", "site_id": "BoundarySite", "primaryspaceusage": "Office",
        "sqm": 150.0, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": None, "occupants": None, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, ["boundary_b1"], site_ids)
    sensor_ids = upsert_sensors(db_session, building_ids)
    db_session.flush()

    # 200h before the TRAIN/VALIDATION boundary through 200h after it --
    # enough prior history (>168h) for at least one origin on each side.
    start = dataset.TRAIN_END_EXCL - pd.Timedelta(hours=200)
    ts = pd.date_range(start, periods=400, freq="h", tz="UTC")
    values = list(range(400))
    df = pd.DataFrame({"building_code": ["boundary_b1"] * 400, "ts_utc": ts, "consumption_kwh": values})
    upsert_energy_measurements(db_session, df, {"boundary_b1": sensor_ids["boundary_b1"]})
    db_session.flush()

    result = dataset.build_training_dataset(db_session)
    train_origins = set(result["train"]["origin_ts"])
    val_origins = set(result["validation"]["origin_ts"])
    assert len(train_origins) > 0 and len(val_origins) > 0, "test needs origins on both sides to be meaningful"
    assert train_origins.isdisjoint(val_origins)

    # and no single origin contributed rows to both splits
    train_keys = set(zip(result["train"]["sensor_id"], result["train"]["origin_ts"]))
    val_keys = set(zip(result["validation"]["sensor_id"], result["validation"]["origin_ts"]))
    assert train_keys.isdisjoint(val_keys)


def test_fitting_a_pipeline_only_touches_the_data_passed_to_fit():
    """A model.fit(X_train, y_train) call cannot see X_val/X_test -- this
    is guaranteed by Python call semantics, but asserted explicitly here:
    if train.py were ever changed to accidentally concatenate splits
    before fit(), this test documents exactly what must stay true."""
    from energy_platform.forecasting.dataset import FEATURE_COLUMNS

    rng = np.random.default_rng(0)
    X_train = pd.DataFrame({c: rng.normal(size=50) for c in FEATURE_COLUMNS if c != "primary_use"})
    X_train["primary_use"] = "Office"
    y_train = X_train["lag_1"] * 2

    pipeline = models.build_ridge_pipeline()
    pipeline.fit(X_train, y_train)
    # The imputer's stored per-feature median must come only from
    # X_train -- fit() was never called with anything else.
    imputer = pipeline.named_steps["preprocess"].named_transformers_["numeric"].named_steps["impute"]
    idx = models.NUMERIC_FEATURES.index("lag_1")
    assert imputer.statistics_[idx] == pytest.approx(X_train["lag_1"].median())


# --- 4. scaler/encoder fitting occurs only on training data ---

def test_onehot_encoder_categories_come_only_from_training_data():
    from energy_platform.forecasting.dataset import FEATURE_COLUMNS

    rng = np.random.default_rng(2)
    X_train = pd.DataFrame({c: rng.normal(size=50) for c in FEATURE_COLUMNS if c != "primary_use"})
    X_train["primary_use"] = "OnlyThisCategoryInTrain"
    y_train = pd.Series(rng.normal(size=50))

    pipeline = models.build_ridge_pipeline()
    pipeline.fit(X_train, y_train)

    encoder = pipeline.named_steps["preprocess"].named_transformers_["categorical"].named_steps["onehot"]
    categories = list(encoder.categories_[0])
    assert categories == ["OnlyThisCategoryInTrain"]


# --- 5. future observed weather never used in the main day-ahead model ---

def test_no_weather_columns_anywhere_in_the_feature_set():
    weather_markers = ("weather", "temp", "wind", "cloud", "precip", "pressure")
    all_columns = [c.lower() for c in dataset.FEATURE_COLUMNS]
    for marker in weather_markers:
        matches = [c for c in all_columns if marker in c]
        assert matches == [], f"found weather-related column(s) in FEATURE_COLUMNS: {matches}"


# --- 6. prediction generated_at < target_ts always holds ---

def test_prediction_generated_at_precedes_target_ts(db_session):
    """The concrete evidence that a prediction was produced before its
    target occurred, not fit to data that includes it."""
    from energy_platform.forecasting.predict import generate_predictions

    _seed_building(db_session, "LeakSite5", "leak_b5", n_hours=250)
    rows = generate_predictions(db_session, "seasonal_naive_24h")
    assert len(rows) > 0
    for row in rows:
        assert row["generated_at"] < row["target_ts"]
