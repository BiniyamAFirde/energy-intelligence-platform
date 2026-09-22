import datetime as dt

import pandas as pd
from sqlalchemy import select

from energy_platform.db.models import ModelVersion, Prediction
from energy_platform.forecasting.predict import generate_predictions, generate_predictions_for_range
from energy_platform.ingestion.loader import (
    upsert_buildings,
    upsert_energy_measurements,
    upsert_sensors,
    upsert_sites,
)
from energy_platform.repositories import predictions as predictions_repo


def _seed_model_version(db_session, model_name="seasonal_naive_24h", model_version="v1"):
    """In production, train.py always records a model_versions row before
    predict.py ever runs against that (name, version) -- the FK from
    predictions requires it. Mirrored here for tests that call
    upsert_predictions directly without running the full train.py."""
    now = dt.datetime(2016, 1, 1, tzinfo=dt.timezone.utc)
    db_session.merge(ModelVersion(
        model_name=model_name, model_version=model_version, feature_version="v1",
        train_start=now, train_end=now, validation_start=now, validation_end=now,
        test_start=now, test_end=now, hyperparameters={}, metrics={},
    ))
    db_session.flush()


def _seed_building(db_session, site_code, building_code, n_hours=250):
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

    from energy_platform.forecasting import dataset
    ts = pd.date_range(dataset.TRAIN_START, periods=n_hours, freq="h", tz="UTC")
    values = [10.0 + (i % 24) for i in range(n_hours)]
    df = pd.DataFrame({"building_code": [building_code] * n_hours, "ts_utc": ts, "consumption_kwh": values})
    upsert_energy_measurements(db_session, df, {building_code: sensor_ids[building_code]})
    db_session.flush()
    return sensor_ids[building_code]


def test_generate_predictions_produces_24_rows_per_building(db_session):
    sensor_id = _seed_building(db_session, "PredictSite", "predict_b1")
    rows = generate_predictions(db_session, "seasonal_naive_24h")
    building_rows = [r for r in rows if r["sensor_id"] == sensor_id]
    assert len(building_rows) == 24
    assert sorted(r["horizon"] for r in building_rows) == list(range(1, 25))


def test_predictions_persist_to_database(db_session):
    _seed_model_version(db_session)
    _seed_building(db_session, "PredictSite2", "predict_b2")
    rows = generate_predictions(db_session, "seasonal_naive_24h")
    n_written = predictions_repo.upsert_predictions(db_session, rows)
    db_session.flush()

    assert n_written == len(rows)
    db_rows = db_session.execute(select(Prediction)).scalars().all()
    assert len(db_rows) == len(rows)
    assert all(r.model_name == "seasonal_naive_24h" for r in db_rows)


def test_predictions_upsert_is_idempotent_and_refreshes_generated_at(db_session):
    _seed_model_version(db_session)
    _seed_building(db_session, "PredictSite3", "predict_b3")
    rows = generate_predictions(db_session, "seasonal_naive_24h")

    predictions_repo.upsert_predictions(db_session, rows)
    db_session.flush()
    first_count = len(db_session.execute(select(Prediction)).scalars().all())

    # re-run with a later generated_at -- must update in place, not duplicate
    rows2 = [dict(r, generated_at=r["generated_at"] + dt.timedelta(hours=1)) for r in rows]
    predictions_repo.upsert_predictions(db_session, rows2)
    db_session.flush()

    db_rows = db_session.execute(select(Prediction)).scalars().all()
    assert len(db_rows) == first_count  # updated in place, not duplicated
    assert all(r.generated_at == rows2[0]["generated_at"] for r in db_rows)


# --- Phase 8: historical backfill (generate_predictions_for_range) ---

def test_generate_predictions_for_range_covers_multiple_origins(db_session):
    from energy_platform.forecasting import dataset

    sensor_id = _seed_building(db_session, "BackfillSite", "backfill_b1", n_hours=250)
    # 250h of history starting at TRAIN_START gives midnight origins at day
    # 7 (168h warmup satisfied) through day 9 (250h - 24h horizon) -> 3 origins
    start = dataset.TRAIN_START
    end_excl = dataset.TRAIN_START + pd.Timedelta(days=30)

    rows = generate_predictions_for_range(db_session, "seasonal_naive_24h", start, end_excl)
    building_rows = [r for r in rows if r["sensor_id"] == sensor_id]
    origins = {r["generated_at"] for r in building_rows}  # generated_at == origin_ts
    assert len(origins) >= 2, "expected predictions from more than one origin"
    assert len(building_rows) == len(origins) * 24


def test_generate_predictions_for_range_excludes_origins_outside_range(db_session):
    from energy_platform.forecasting import dataset

    sensor_id = _seed_building(db_session, "BackfillSite2", "backfill_b2", n_hours=250)
    first_valid_origin = dataset.TRAIN_START + pd.Timedelta(hours=168)  # first origin with full lag_168

    # a one-day window containing exactly one origin
    rows = generate_predictions_for_range(
        db_session, "seasonal_naive_24h", first_valid_origin, first_valid_origin + pd.Timedelta(days=1)
    )
    building_rows = [r for r in rows if r["sensor_id"] == sensor_id]
    assert len(building_rows) == 24
    assert all(r["generated_at"] == first_valid_origin.to_pydatetime() for r in building_rows)


def test_generate_predictions_for_range_does_not_touch_existing_latest_origin_predictions(db_session):
    """The backfill must be purely additive: predictions for the *latest*
    origin (what generate_predictions/main() writes in normal operation)
    must be untouched by a separate historical-range backfill run."""
    _seed_model_version(db_session)
    _seed_building(db_session, "BackfillSite3", "backfill_b3", n_hours=250)
    from energy_platform.forecasting import dataset

    latest_rows = generate_predictions(db_session, "seasonal_naive_24h")
    predictions_repo.upsert_predictions(db_session, latest_rows)
    db_session.flush()
    latest_count = len(db_session.execute(select(Prediction)).scalars().all())

    backfill_rows = generate_predictions_for_range(
        db_session, "seasonal_naive_24h", dataset.TRAIN_START, dataset.TRAIN_START + pd.Timedelta(hours=200)
    )
    predictions_repo.upsert_predictions(db_session, backfill_rows)
    db_session.flush()

    total_count = len(db_session.execute(select(Prediction)).scalars().all())
    assert total_count == latest_count + len(backfill_rows)  # purely additive, no overlap/overwrite


# --- Phase 8: predictions_repo.list_predictions (feeds Detector C) ---

def test_list_predictions_returns_rows_for_requested_model_only(db_session):
    _seed_model_version(db_session, model_name="seasonal_naive_24h", model_version="v1")
    _seed_model_version(db_session, model_name="seasonal_naive_168h", model_version="v1")
    _seed_building(db_session, "ListPredSite", "list_pred_b1")

    rows_24h = generate_predictions(db_session, "seasonal_naive_24h")
    rows_168h = generate_predictions(db_session, "seasonal_naive_168h")
    predictions_repo.upsert_predictions(db_session, rows_24h)
    predictions_repo.upsert_predictions(db_session, rows_168h)
    db_session.flush()

    listed = predictions_repo.list_predictions(db_session, "seasonal_naive_24h", "v1")
    assert len(listed) == len(rows_24h)
    assert {"sensor_id", "target_ts", "predicted_kwh"} <= set(listed[0].keys())
    assert "actual_kwh" not in listed[0]
    # predicted_kwh comes back as decimal.Decimal (raw NUMERIC column, same as
    # energy.list_measurements_for_sensors) -- callers doing float arithmetic
    # on it (e.g. Detector C's residual computation) must cast explicitly,
    # same as dataset.load_raw_frame does for consumption_kwh. Documented
    # here so that requirement doesn't get silently lost.
    import decimal
    assert isinstance(listed[0]["predicted_kwh"], decimal.Decimal)
