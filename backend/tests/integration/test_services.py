import pandas as pd
import pytest

from energy_platform.ingestion.loader import (
    upsert_buildings,
    upsert_energy_measurements,
    upsert_sensors,
    upsert_sites,
)
from energy_platform.services import (
    buildings_service,
    energy_service,
    sensors_service,
    sites_service,
    weather_service,
)
from energy_platform.services.exceptions import InvalidParameterError, NotFoundError


def _seed_building_with_energy(db_session, site_code, building_code, values):
    site_ids = upsert_sites(db_session, [site_code], {site_code: "US/Eastern"})
    metadata_df = pd.DataFrame([{
        "building_id": building_code, "site_id": site_code, "primaryspaceusage": "Office",
        "sqm": 100.0, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": None, "occupants": None, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, [building_code], site_ids)
    sensor_ids = upsert_sensors(db_session, building_ids)
    db_session.flush()

    if values:
        long_df = pd.DataFrame({
            "building_code": [building_code] * len(values),
            "ts_utc": pd.date_range("2016-01-01", periods=len(values), freq="h", tz="UTC"),
            "consumption_kwh": values,
        })
        upsert_energy_measurements(db_session, long_df, {building_code: sensor_ids[building_code]})
        db_session.flush()

    return site_ids[site_code], building_ids[building_code]


def test_get_site_raises_not_found_for_missing_id(db_session):
    with pytest.raises(NotFoundError):
        sites_service.get_site(db_session, 999_999)


def test_get_building_raises_not_found_for_missing_id(db_session):
    with pytest.raises(NotFoundError):
        buildings_service.get_building(db_session, 999_999)


def test_get_sensor_raises_not_found_for_missing_id(db_session):
    with pytest.raises(NotFoundError):
        sensors_service.get_sensor(db_session, 999_999)


def test_get_building_energy_raises_not_found_for_missing_building(db_session):
    with pytest.raises(NotFoundError):
        energy_service.get_building_energy(db_session, 999_999, limit=10)


def test_get_building_energy_returns_measurements_in_range(db_session):
    _, building_id = _seed_building_with_energy(
        db_session, "SvcEnergySite", "svc_energy_b1", [10.0, 20.0, 30.0]
    )
    rows = energy_service.get_building_energy(db_session, building_id, limit=10)
    assert len(rows) == 3


def test_get_building_summary_computes_missing_observations(db_session):
    _, building_id = _seed_building_with_energy(
        db_session, "SvcSummarySite", "svc_summary_b1", [10.0, 20.0, 30.0]
    )
    summary = energy_service.get_building_summary(db_session, building_id)
    assert summary["total_observations"] == 3
    # expected_hourly_grid_size() is 17544; only 3 rows loaded
    assert summary["missing_observations"] == 17544 - 3


def test_get_building_summary_raises_not_found_when_no_measurements(db_session):
    _, building_id = _seed_building_with_energy(
        db_session, "SvcEmptySite", "svc_empty_b1", values=[]
    )
    with pytest.raises(NotFoundError):
        energy_service.get_building_summary(db_session, building_id)


def test_get_building_energy_aggregate_rejects_bad_granularity(db_session):
    _, building_id = _seed_building_with_energy(
        db_session, "SvcAggSite", "svc_agg_b1", [10.0, 20.0]
    )
    with pytest.raises(InvalidParameterError):
        energy_service.get_building_energy_aggregate(
            db_session, building_id, granularity="yearly", limit=10
        )


def test_get_building_energy_aggregate_returns_grouped_rows(db_session):
    _, building_id = _seed_building_with_energy(
        db_session, "SvcAggSite2", "svc_agg_b2", [10.0] * 25
    )  # spans just over a day at hourly resolution
    rows = energy_service.get_building_energy_aggregate(
        db_session, building_id, granularity="daily", limit=10
    )
    assert len(rows) == 2  # 25 hourly points span 2 calendar days


def test_get_building_energy_rejects_start_after_end(db_session):
    import datetime as dt
    _, building_id = _seed_building_with_energy(
        db_session, "SvcRangeSite", "svc_range_b1", [10.0, 20.0]
    )
    with pytest.raises(InvalidParameterError):
        energy_service.get_building_energy(
            db_session, building_id, limit=10,
            start=dt.datetime(2016, 1, 2), end=dt.datetime(2016, 1, 1),
        )


def test_get_site_weather_raises_not_found_for_missing_site(db_session):
    with pytest.raises(NotFoundError):
        weather_service.get_site_weather(db_session, 999_999, limit=10)
