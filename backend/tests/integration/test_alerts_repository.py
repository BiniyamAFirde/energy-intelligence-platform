import datetime as dt

import pandas as pd
from sqlalchemy import select

from energy_platform.db.models import Alert
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
        "building_code": [building_code] * n_hours, "ts_utc": ts,
        "consumption_kwh": [10.0] * n_hours,
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


def test_upsert_alerts_persists_rows(db_session):
    _, sensor_id = _seed_sensor(db_session, "AlertRepoSite1", "alert_repo_b1")
    ts = pd.Timestamp("2017-07-01 00:00", tz="UTC").to_pydatetime()
    n = alerts_repo.upsert_alerts(db_session, [_alert_row(sensor_id, ts)])
    db_session.flush()

    assert n == 1
    rows = db_session.execute(select(Alert).where(Alert.sensor_id == sensor_id)).scalars().all()
    assert len(rows) == 1
    assert rows[0].anomaly_type == "negative_value"
    assert rows[0].resolved is False  # column default


def test_upsert_alerts_is_idempotent_on_sensor_ts_method_detector_version(db_session):
    _, sensor_id = _seed_sensor(db_session, "AlertRepoSite2", "alert_repo_b2")
    ts = pd.Timestamp("2017-07-01 00:00", tz="UTC").to_pydatetime()

    alerts_repo.upsert_alerts(db_session, [_alert_row(sensor_id, ts, score=5.0)])
    db_session.flush()
    alerts_repo.upsert_alerts(db_session, [_alert_row(sensor_id, ts, score=9.0)])
    db_session.flush()

    rows = db_session.execute(select(Alert).where(Alert.sensor_id == sensor_id)).scalars().all()
    assert len(rows) == 1  # refreshed in place, not duplicated
    assert float(rows[0].score) == 9.0


def test_upsert_alerts_never_overwrites_resolved_flag(db_session):
    """A re-run of a detector must not silently un-resolve an alert a
    human already triaged -- `resolved` is intentionally excluded from
    the ON CONFLICT update set."""
    _, sensor_id = _seed_sensor(db_session, "AlertRepoSite3", "alert_repo_b3")
    ts = pd.Timestamp("2017-07-01 00:00", tz="UTC").to_pydatetime()

    alerts_repo.upsert_alerts(db_session, [_alert_row(sensor_id, ts)])
    db_session.flush()
    row = db_session.execute(select(Alert).where(Alert.sensor_id == sensor_id)).scalar_one()
    row.resolved = True
    db_session.flush()

    alerts_repo.upsert_alerts(db_session, [_alert_row(sensor_id, ts, score=7.0)])
    db_session.flush()
    db_session.expire_all()  # the upsert above is a raw Core UPDATE -- the ORM identity map doesn't see it otherwise

    row = db_session.execute(select(Alert).where(Alert.sensor_id == sensor_id)).scalar_one()
    assert row.resolved is True
    assert float(row.score) == 7.0


def test_upsert_alerts_different_methods_at_same_point_are_separate_rows(db_session):
    _, sensor_id = _seed_sensor(db_session, "AlertRepoSite4", "alert_repo_b4")
    ts = pd.Timestamp("2017-07-01 00:00", tz="UTC").to_pydatetime()

    alerts_repo.upsert_alerts(db_session, [
        _alert_row(sensor_id, ts, method="data_quality", anomaly_type="negative_value"),
        _alert_row(sensor_id, ts, method="behavioral", anomaly_type="behavioral_deviation"),
    ])
    db_session.flush()

    rows = db_session.execute(select(Alert).where(Alert.sensor_id == sensor_id)).scalars().all()
    assert len(rows) == 2
    assert {r.method for r in rows} == {"data_quality", "behavioral"}


def test_list_alerts_filters_by_method_and_severity(db_session):
    _, sensor_id = _seed_sensor(db_session, "AlertRepoSite5", "alert_repo_b5")
    ts = pd.Timestamp("2017-07-01 00:00", tz="UTC").to_pydatetime()
    alerts_repo.upsert_alerts(db_session, [
        _alert_row(sensor_id, ts, method="data_quality", severity="high"),
        _alert_row(sensor_id, ts + dt.timedelta(hours=1), method="behavioral", severity="low"),
    ])
    db_session.flush()

    items, total = alerts_repo.list_alerts(db_session, limit=50, offset=0, method="data_quality")
    assert total == 1
    assert items[0].method == "data_quality"

    items, total = alerts_repo.list_alerts(db_session, limit=50, offset=0, severity="low")
    assert total == 1
    assert items[0].severity == "low"


def test_list_alerts_orders_by_ts_descending(db_session):
    _, sensor_id = _seed_sensor(db_session, "AlertRepoSite6", "alert_repo_b6")
    ts0 = pd.Timestamp("2017-07-01 00:00", tz="UTC").to_pydatetime()
    alerts_repo.upsert_alerts(db_session, [
        _alert_row(sensor_id, ts0, anomaly_type="negative_value"),
        _alert_row(sensor_id, ts0 + dt.timedelta(hours=5), anomaly_type="missing"),
    ])
    db_session.flush()

    items, _ = alerts_repo.list_alerts(db_session, limit=50, offset=0, sensor_id=sensor_id)
    assert items[0].ts > items[1].ts


def test_get_alert_returns_none_for_missing(db_session):
    assert alerts_repo.get_alert(db_session, 999999) is None


def test_summary_counts_and_breakdowns(db_session):
    _, sensor_id = _seed_sensor(db_session, "AlertRepoSite7", "alert_repo_b7")
    ts = pd.Timestamp("2017-07-01 00:00", tz="UTC").to_pydatetime()
    alerts_repo.upsert_alerts(db_session, [
        _alert_row(sensor_id, ts, method="data_quality", anomaly_type="negative_value", severity="high"),
        _alert_row(sensor_id, ts + dt.timedelta(hours=1), method="data_quality", anomaly_type="missing", severity="medium"),
        _alert_row(sensor_id, ts + dt.timedelta(hours=2), method="behavioral", anomaly_type="behavioral_deviation", severity="medium"),
    ])
    db_session.flush()

    result = alerts_repo.summary(db_session)
    assert result["total_alerts"] == 3
    assert result["unresolved_alerts"] == 3
    assert result["counts_by_method"] == {"data_quality": 2, "behavioral": 1}
    assert result["counts_by_severity"] == {"high": 1, "medium": 2}
