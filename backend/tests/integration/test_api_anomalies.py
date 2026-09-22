import datetime as dt

import pandas as pd

from energy_platform.ingestion.loader import (
    upsert_buildings,
    upsert_energy_measurements,
    upsert_sensors,
    upsert_sites,
)
from energy_platform.repositories import alerts as alerts_repo


def _seed_sensor(db_session, site_code, building_code, n_hours=5):
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

    ts = pd.date_range("2017-07-01", periods=n_hours, freq="h", tz="UTC")
    energy_df = pd.DataFrame({
        "building_code": [building_code] * n_hours, "ts_utc": ts, "consumption_kwh": [10.0] * n_hours,
    })
    upsert_energy_measurements(db_session, energy_df, {building_code: sensor_ids[building_code]})
    db_session.flush()
    return building_ids[building_code], sensor_ids[building_code]


def _alert_row(sensor_id, ts, method="data_quality", anomaly_type="negative_value", **overrides) -> dict:
    row = {
        "sensor_id": sensor_id, "ts": ts, "method": method, "anomaly_type": anomaly_type,
        "detector_version": "v1", "severity": "high", "score": 5.0,
        "expected_value": 0.0, "actual_value": -5.0, "residual": None,
        "explanation": "test explanation",
    }
    row.update(overrides)
    return row


def test_list_alerts_endpoint(api_client, db_session):
    _, sensor_id = _seed_sensor(db_session, "ApiAlertSite1", "api_alert_b1")
    ts = pd.Timestamp("2017-07-01 00:00", tz="UTC").to_pydatetime()
    alerts_repo.upsert_alerts(db_session, [_alert_row(sensor_id, ts)])
    db_session.flush()

    r = api_client.get("/api/v1/alerts", params={"sensor_id": sensor_id})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["anomaly_type"] == "negative_value"
    assert body["items"][0]["sensor_id"] == sensor_id


def test_list_alerts_filters_by_method_and_severity(api_client, db_session):
    _, sensor_id = _seed_sensor(db_session, "ApiAlertSite2", "api_alert_b2")
    ts = pd.Timestamp("2017-07-01 00:00", tz="UTC").to_pydatetime()
    alerts_repo.upsert_alerts(db_session, [
        _alert_row(sensor_id, ts, method="data_quality", severity="high"),
        _alert_row(sensor_id, ts + dt.timedelta(hours=1), method="behavioral", anomaly_type="behavioral_deviation", severity="low"),
    ])
    db_session.flush()

    r = api_client.get("/api/v1/alerts", params={"sensor_id": sensor_id, "method": "behavioral"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["method"] == "behavioral"

    r = api_client.get("/api/v1/alerts", params={"sensor_id": sensor_id, "severity": "high"})
    assert r.json()["total"] == 1


def test_list_alerts_date_range_filtering(api_client, db_session):
    _, sensor_id = _seed_sensor(db_session, "ApiAlertSite3", "api_alert_b3")
    ts0 = pd.Timestamp("2017-07-01 00:00", tz="UTC").to_pydatetime()
    alerts_repo.upsert_alerts(db_session, [
        _alert_row(sensor_id, ts0),
        _alert_row(sensor_id, ts0 + dt.timedelta(hours=10)),
    ])
    db_session.flush()

    r = api_client.get(
        "/api/v1/alerts",
        params={"sensor_id": sensor_id, "start": "2017-07-01T00:00:00", "end": "2017-07-01T05:00:00"},
    )
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_list_alerts_rejects_start_after_end(api_client):
    r = api_client.get(
        "/api/v1/alerts", params={"start": "2017-07-02T00:00:00", "end": "2017-07-01T00:00:00"}
    )
    assert r.status_code == 400


def test_get_alert_detail_endpoint(api_client, db_session):
    _, sensor_id = _seed_sensor(db_session, "ApiAlertSite4", "api_alert_b4")
    ts = pd.Timestamp("2017-07-01 00:00", tz="UTC").to_pydatetime()
    alerts_repo.upsert_alerts(db_session, [_alert_row(sensor_id, ts, explanation="concrete reason")])
    db_session.flush()

    listed = api_client.get("/api/v1/alerts", params={"sensor_id": sensor_id}).json()
    alert_id = listed["items"][0]["id"]

    r = api_client.get(f"/api/v1/alerts/{alert_id}")
    assert r.status_code == 200
    assert r.json()["explanation"] == "concrete reason"


def test_get_alert_404_for_missing(api_client):
    r = api_client.get("/api/v1/alerts/999999")
    assert r.status_code == 404


def test_get_building_alerts_endpoint(api_client, db_session):
    building_id, sensor_id = _seed_sensor(db_session, "ApiAlertSite5", "api_alert_b5")
    ts = pd.Timestamp("2017-07-01 00:00", tz="UTC").to_pydatetime()
    alerts_repo.upsert_alerts(db_session, [_alert_row(sensor_id, ts)])
    db_session.flush()

    r = api_client.get(f"/api/v1/buildings/{building_id}/alerts")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["sensor_id"] == sensor_id


def test_get_building_alerts_404_for_missing_building(api_client):
    r = api_client.get("/api/v1/buildings/999999/alerts")
    assert r.status_code == 404


def test_alerts_summary_endpoint(api_client, db_session):
    _, sensor_id = _seed_sensor(db_session, "ApiAlertSite7", "api_alert_b7")
    ts = pd.Timestamp("2017-07-01 00:00", tz="UTC").to_pydatetime()
    alerts_repo.upsert_alerts(db_session, [
        _alert_row(sensor_id, ts, method="data_quality"),
        _alert_row(sensor_id, ts + dt.timedelta(hours=1), method="behavioral", anomaly_type="behavioral_deviation"),
    ])
    db_session.flush()

    r = api_client.get("/api/v1/alerts/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_alerts"] >= 2
    assert "counts_by_method" in body and "counts_by_severity" in body


def test_alerts_summary_route_does_not_collide_with_alert_id_route(api_client):
    # /alerts/summary must resolve to the summary endpoint, not a 422 from
    # trying to parse "summary" as an integer alert_id.
    r = api_client.get("/api/v1/alerts/summary")
    assert r.status_code == 200
