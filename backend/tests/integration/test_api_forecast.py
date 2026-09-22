import datetime as dt
import json

import pandas as pd

from energy_platform.db.models import ModelVersion
from energy_platform.forecasting import train
from energy_platform.ingestion.loader import (
    upsert_buildings,
    upsert_energy_measurements,
    upsert_sensors,
    upsert_sites,
)
from energy_platform.repositories import predictions as predictions_repo

MODEL_NAME = "seasonal_naive_24h"
MODEL_VERSION = "v1"


def _seed_model_version(db_session):
    now = dt.datetime(2016, 1, 1, tzinfo=dt.timezone.utc)
    db_session.merge(ModelVersion(
        model_name=MODEL_NAME, model_version=MODEL_VERSION, feature_version="v1",
        train_start=now, train_end=now, validation_start=now, validation_end=now,
        test_start=now, test_end=now, hyperparameters={}, metrics={},
    ))
    db_session.flush()


def _seed_building(db_session, site_code, building_code, n_hours=48):
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

    ts = pd.date_range("2017-01-01", periods=n_hours, freq="h", tz="UTC")
    df = pd.DataFrame({
        "building_code": [building_code] * n_hours, "ts_utc": ts,
        "consumption_kwh": [10.0 + i for i in range(n_hours)],
    })
    upsert_energy_measurements(db_session, df, {building_code: sensor_ids[building_code]})
    db_session.flush()
    return building_ids[building_code], sensor_ids[building_code]


def _point_report(monkeypatch, tmp_path, selected_model=MODEL_NAME, selection_note="test"):
    report_path = tmp_path / "forecasting_training_report.json"
    report_path.write_text(json.dumps({"selected_model": selected_model, "selection_note": selection_note}))
    monkeypatch.setattr(train, "REPORT_PATH", report_path)
    return report_path


def test_get_building_forecast_joins_actual_and_computes_residual(api_client, db_session, monkeypatch, tmp_path):
    _point_report(monkeypatch, tmp_path)
    building_id, sensor_id = _seed_building(db_session, "ForecastSite1", "forecast_b1")
    _seed_model_version(db_session)

    target_ts = pd.Timestamp("2017-01-01 05:00", tz="UTC").to_pydatetime()  # actual = 10.0 + 5 = 15.0
    predictions_repo.upsert_predictions(db_session, [{
        "sensor_id": sensor_id, "target_ts": target_ts, "generated_at": target_ts,
        "model_name": MODEL_NAME, "model_version": MODEL_VERSION, "horizon": 1, "predicted_kwh": 12.0,
    }])
    db_session.flush()

    r = api_client.get(f"/api/v1/buildings/{building_id}/forecast")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["predicted_kwh"] == 12.0
    assert row["actual_kwh"] == 15.0
    assert row["residual"] == 3.0
    assert row["model_name"] == MODEL_NAME
    assert row["horizon"] == 1


def test_get_building_forecast_actual_kwh_null_when_no_matching_measurement(api_client, db_session, monkeypatch, tmp_path):
    _point_report(monkeypatch, tmp_path)
    building_id, sensor_id = _seed_building(db_session, "ForecastSite2", "forecast_b2", n_hours=5)
    _seed_model_version(db_session)

    # target_ts far beyond the 5 seeded energy hours -- no actual available
    target_ts = pd.Timestamp("2017-01-05 00:00", tz="UTC").to_pydatetime()
    predictions_repo.upsert_predictions(db_session, [{
        "sensor_id": sensor_id, "target_ts": target_ts, "generated_at": target_ts,
        "model_name": MODEL_NAME, "model_version": MODEL_VERSION, "horizon": 1, "predicted_kwh": 9.0,
    }])
    db_session.flush()

    r = api_client.get(f"/api/v1/buildings/{building_id}/forecast")
    assert r.status_code == 200
    row = r.json()[0]
    assert row["actual_kwh"] is None
    assert row["residual"] is None


def test_get_building_forecast_date_range_filtering(api_client, db_session, monkeypatch, tmp_path):
    _point_report(monkeypatch, tmp_path)
    building_id, sensor_id = _seed_building(db_session, "ForecastSite3", "forecast_b3")
    _seed_model_version(db_session)

    ts0 = pd.Timestamp("2017-01-01 00:00", tz="UTC").to_pydatetime()
    predictions_repo.upsert_predictions(db_session, [
        {"sensor_id": sensor_id, "target_ts": ts0, "generated_at": ts0, "model_name": MODEL_NAME,
         "model_version": MODEL_VERSION, "horizon": 1, "predicted_kwh": 1.0},
        {"sensor_id": sensor_id, "target_ts": ts0 + dt.timedelta(hours=20), "generated_at": ts0,
         "model_name": MODEL_NAME, "model_version": MODEL_VERSION, "horizon": 21, "predicted_kwh": 2.0},
    ])
    db_session.flush()

    r = api_client.get(
        f"/api/v1/buildings/{building_id}/forecast",
        params={"start": "2017-01-01T00:00:00", "end": "2017-01-01T05:00:00"},
    )
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_get_building_forecast_empty_list_when_no_predictions(api_client, db_session, monkeypatch, tmp_path):
    _point_report(monkeypatch, tmp_path)
    building_id, _ = _seed_building(db_session, "ForecastSite4", "forecast_b4", n_hours=3)

    r = api_client.get(f"/api/v1/buildings/{building_id}/forecast")
    assert r.status_code == 200
    assert r.json() == []


def test_get_building_forecast_404_for_missing_building(api_client, monkeypatch, tmp_path):
    _point_report(monkeypatch, tmp_path)
    r = api_client.get("/api/v1/buildings/999999/forecast")
    assert r.status_code == 404


def test_get_building_forecast_404_when_no_training_report(api_client, db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(train, "REPORT_PATH", tmp_path / "does_not_exist.json")
    building_id, _ = _seed_building(db_session, "ForecastSite5", "forecast_b5", n_hours=3)

    r = api_client.get(f"/api/v1/buildings/{building_id}/forecast")
    assert r.status_code == 404
    assert "trained" in r.json()["detail"]


def test_get_building_forecast_404_when_no_model_beat_baselines(api_client, db_session, monkeypatch, tmp_path):
    _point_report(monkeypatch, tmp_path, selected_model=None, selection_note="nothing beat the baselines")
    building_id, _ = _seed_building(db_session, "ForecastSite6", "forecast_b6", n_hours=3)

    r = api_client.get(f"/api/v1/buildings/{building_id}/forecast")
    assert r.status_code == 404


def test_get_building_forecast_generated_at_always_precedes_target_ts(api_client, db_session, monkeypatch, tmp_path):
    """Phase 10 Step 6's explicit guarantee: a forecast's generated_at (the
    origin it was made from) must always be strictly before the target_ts
    it predicts -- otherwise this would be describing a forecast as having
    been made after the fact, which is exactly the "historical predictions
    silently look like live/instant forecasts" failure mode this endpoint
    must not have. Seeds rows spanning several horizons (1h and 19h ahead
    of the same origin) so this isn't just checking a single coincidental
    pair."""
    _point_report(monkeypatch, tmp_path)
    building_id, sensor_id = _seed_building(db_session, "ForecastSite7", "forecast_b7")
    _seed_model_version(db_session)

    origin = pd.Timestamp("2017-01-01 04:00", tz="UTC").to_pydatetime()
    predictions_repo.upsert_predictions(db_session, [
        {
            "sensor_id": sensor_id, "target_ts": origin + dt.timedelta(hours=1), "generated_at": origin,
            "model_name": MODEL_NAME, "model_version": MODEL_VERSION, "horizon": 1, "predicted_kwh": 12.0,
        },
        {
            "sensor_id": sensor_id, "target_ts": origin + dt.timedelta(hours=19), "generated_at": origin,
            "model_name": MODEL_NAME, "model_version": MODEL_VERSION, "horizon": 19, "predicted_kwh": 13.0,
        },
    ])
    db_session.flush()

    r = api_client.get(f"/api/v1/buildings/{building_id}/forecast")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    for row in rows:
        generated_at = dt.datetime.fromisoformat(row["generated_at"].replace("Z", "+00:00"))
        target_ts = dt.datetime.fromisoformat(row["target_ts"].replace("Z", "+00:00"))
        assert generated_at < target_ts, f"generated_at {generated_at} was not before target_ts {target_ts}"
