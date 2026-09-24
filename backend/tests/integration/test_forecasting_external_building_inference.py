"""Proves the existing trained Random Forest artifact (models/random_forest_v1.joblib)
can score a brand-new building that was never part of the 60-building BDG2
training set -- pure inference, no retraining, no new code paths.

This exercises the exact production prediction path end to end:
dataset.build_prediction_request (real feature engineering, DB-driven) ->
predict._predict_with_model (joblib.load + pipeline.predict) ->
predictions_repo.upsert_predictions (real persistence). The synthetic
building is seeded through the same ingestion upsert functions production
ingestion uses (ingestion/loader.py), not a second/parallel implementation,
and lives only inside this test's own rolled-back transaction (see
conftest.db_session) -- nothing here touches real BDG2 data or persists
after the test.
"""

from __future__ import annotations

import datetime as dt
import math
import os
from unittest.mock import patch

import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sqlalchemy import select

from energy_platform.db.models import ModelVersion, Prediction
from energy_platform.forecasting import dataset, predict, train
from energy_platform.ingestion.loader import (
    upsert_buildings,
    upsert_energy_measurements,
    upsert_sensors,
    upsert_sites,
)
from energy_platform.repositories import energy as energy_repo
from energy_platform.repositories import predictions as predictions_repo

MODEL_NAME = "random_forest"
ARTIFACT_PATH = train.MODELS_DIR / f"{MODEL_NAME}_{train.MODEL_VERSION}.joblib"

# Obviously outside BDG2's real 60 buildings -- this company/building/sensor
# has never existed in any data the artifact was trained on.
SITE_CODE = "ExternalCoSite"
BUILDING_CODE = "external_co_b1"

# 216h (9 days) of consecutive hourly history: comfortably above the 168h
# warm-up features.py's lag_168/rolling_168h_mean require, with slack left
# over so at least one local-midnight origin lands inside the window.
N_HOURS = 216


def _seed_model_version(db_session) -> None:
    """The predictions.(model_name, model_version) FK requires a matching
    model_versions row -- mirrors the identical helper already used by
    test_forecasting_predict.py for the same reason."""
    now = dt.datetime(2016, 1, 1, tzinfo=dt.timezone.utc)
    db_session.merge(ModelVersion(
        model_name=MODEL_NAME, model_version=train.MODEL_VERSION, feature_version="v1",
        train_start=now, train_end=now, validation_start=now, validation_end=now,
        test_start=now, test_end=now, hyperparameters={}, metrics={},
    ))
    db_session.flush()


def _seed_external_building(db_session) -> tuple[int, int]:
    """Seeds one synthetic company's building through the real ingestion
    upsert functions (not a test-only reimplementation), with full,
    schema-compatible metadata: a valid primary_use, area_sqm,
    number_of_floors, and occupants."""
    site_ids = upsert_sites(db_session, [SITE_CODE], {SITE_CODE: "US/Eastern"})
    metadata_df = pd.DataFrame([{
        "building_id": BUILDING_CODE, "site_id": SITE_CODE, "primaryspaceusage": "Office",
        "sqm": 4200.0, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": 5.0, "occupants": 120.0, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, [BUILDING_CODE], site_ids)
    sensor_ids = upsert_sensors(db_session, building_ids)
    db_session.flush()

    ts = pd.date_range(dataset.TRAIN_START, periods=N_HOURS, freq="h", tz="UTC")
    # A non-degenerate synthetic daily/weekly pattern (not a flat constant)
    # so the lag/rolling features carry real signal.
    values = [
        80.0 + 20.0 * math.sin(2 * math.pi * (i % 24) / 24) + (15.0 if (i // 24) % 7 < 5 else 0.0)
        for i in range(N_HOURS)
    ]
    long_df = pd.DataFrame({
        "building_code": [BUILDING_CODE] * N_HOURS, "ts_utc": ts, "consumption_kwh": values,
    })
    upsert_energy_measurements(db_session, long_df, {BUILDING_CODE: sensor_ids[BUILDING_CODE]})
    db_session.flush()
    return building_ids[BUILDING_CODE], sensor_ids[BUILDING_CODE]


def test_random_forest_artifact_scores_a_building_outside_the_training_set(db_session):
    assert ARTIFACT_PATH.exists(), (
        f"{ARTIFACT_PATH} not found -- run `python -m energy_platform.forecasting.train` "
        "first (outside this test; this test never trains)."
    )
    artifact_stat_before = os.stat(ARTIFACT_PATH)

    # (1) a new synthetic site/building/sensor, outside BDG2's 60 buildings
    building_id, sensor_id = _seed_external_building(db_session)
    assert building_id is not None
    assert sensor_id is not None

    # (2) at least 168 consecutive hourly readings exist for it
    measurements = energy_repo.list_measurements_for_sensors(db_session, [sensor_id])
    assert len(measurements) == N_HOURS
    assert N_HOURS >= 168

    _seed_model_version(db_session)

    # (3) feature generation succeeds, via the real unmodified pipeline
    # (dataset.build_prediction_request -> features.py) -- not a manually
    # constructed feature row.
    request_df = dataset.build_prediction_request(db_session)
    building_request = request_df[request_df["sensor_id"] == sensor_id]
    assert len(building_request) > 0, "no valid forecast origin was produced for the synthetic building"
    assert not building_request[dataset.FEATURE_COLUMNS].isna().any().any(), (
        "feature generation left NaNs in the model's required input columns"
    )

    # (4) no training occurs: .fit() must never be called on this path.
    # (5) + (6) the existing saved artifact is loaded (joblib.load spied,
    # wrapping the real implementation) and the pipeline's .predict() is
    # what actually produces the scores.
    with (
        patch.object(
            RandomForestRegressor, "fit",
            side_effect=AssertionError("RandomForestRegressor.fit() was called during an inference-only test"),
        ) as mock_fit,
        patch("energy_platform.forecasting.predict.joblib.load", wraps=predict.joblib.load) as mock_load,
    ):
        rows = predict.generate_predictions(db_session, MODEL_NAME)
    mock_fit.assert_not_called()
    assert mock_load.called, "predict.py did not load a saved model artifact"
    assert str(mock_load.call_args[0][0]) == str(ARTIFACT_PATH)

    # (7) at least one prediction was generated for the unseen building
    building_rows = [r for r in rows if r["sensor_id"] == sensor_id]
    assert len(building_rows) > 0
    assert sorted(r["horizon"] for r in building_rows) == list(range(1, 25))

    # (8) predictions are finite, real numbers
    for r in building_rows:
        assert isinstance(r["predicted_kwh"], float)
        assert math.isfinite(r["predicted_kwh"])

    # (9) predictions belong to the synthetic building/sensor, and respect
    # the same temporal invariant test_forecasting_leakage.py enforces at
    # the feature level (generated_at, the forecast origin, always precedes
    # target_ts) -- re-checked here end to end for this specific unseen
    # building's predictions.
    for r in building_rows:
        assert r["sensor_id"] == sensor_id
        assert r["generated_at"] < r["target_ts"]

    # (10) this building was never part of random_forest_v1.joblib's
    # training data: it was seeded fresh, inside this test's own rolled-
    # back transaction, with a building_code (external_co_b1) that does not
    # exist among BDG2's real 60 buildings the artifact was fit on.

    # persistence, exactly matching predict.main()'s production behavior
    n_written = predictions_repo.upsert_predictions(db_session, rows)
    db_session.flush()
    assert n_written == len(rows)
    persisted = db_session.execute(
        select(Prediction).where(Prediction.sensor_id == sensor_id)
    ).scalars().all()
    assert len(persisted) == 24
    assert all(p.model_name == MODEL_NAME for p in persisted)

    # (11) the production model artifact itself was never modified
    artifact_stat_after = os.stat(ARTIFACT_PATH)
    assert artifact_stat_before.st_mtime == artifact_stat_after.st_mtime
    assert artifact_stat_before.st_size == artifact_stat_after.st_size
