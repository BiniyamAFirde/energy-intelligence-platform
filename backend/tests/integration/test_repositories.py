import datetime as dt

import pandas as pd

from energy_platform.ingestion.loader import (
    upsert_buildings,
    upsert_energy_measurements,
    upsert_sensors,
    upsert_sites,
    upsert_weather_measurements,
)
from energy_platform.repositories import buildings as buildings_repo
from energy_platform.repositories import energy as energy_repo
from energy_platform.repositories import sensors as sensors_repo
from energy_platform.repositories import sites as sites_repo
from energy_platform.repositories import weather as weather_repo


def _seed_one_building(db_session, site_code="RepoSiteA", building_code="repo_b1"):
    site_ids = upsert_sites(db_session, [site_code], {site_code: "US/Eastern"})
    metadata_df = pd.DataFrame([{
        "building_id": building_code, "site_id": site_code, "primaryspaceusage": "Office",
        "sqm": 250.0, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": None, "occupants": None, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, [building_code], site_ids)
    sensor_ids = upsert_sensors(db_session, building_ids)
    db_session.flush()
    return site_ids[site_code], building_ids[building_code], sensor_ids[building_code]


def test_list_sites_paginates_and_counts(db_session):
    for i in range(3):
        upsert_sites(db_session, [f"PageSite{i}"], {f"PageSite{i}": "US/Eastern"})
    db_session.flush()

    items, total = sites_repo.list_sites(db_session, limit=2, offset=0)
    assert total >= 3
    assert len(items) == 2


def test_get_site_returns_none_for_missing(db_session):
    assert sites_repo.get_site(db_session, 999_999) is None


def test_list_buildings_filters_by_site_and_primary_use(db_session):
    site_id, building_id, _ = _seed_one_building(db_session, "FilterSite", "filter_b1")

    items, total = buildings_repo.list_buildings(db_session, limit=50, offset=0, site_id=site_id)
    assert total == 1
    assert items[0].building_id == building_id

    items_by_use, total_use = buildings_repo.list_buildings(
        db_session, limit=50, offset=0, primary_use="Office"
    )
    assert total_use >= 1

    items_none, total_none = buildings_repo.list_buildings(
        db_session, limit=50, offset=0, primary_use="NoSuchUseType"
    )
    assert total_none == 0
    assert items_none == []


def test_get_sensor_for_building_returns_electricity_sensor(db_session):
    _, building_id, sensor_id = _seed_one_building(db_session, "SensorSite", "sensor_b1")

    sensor = sensors_repo.get_sensor_for_building(db_session, building_id)
    assert sensor is not None
    assert sensor.sensor_id == sensor_id
    assert sensor.meter_type == "electricity"


def test_energy_list_measurements_respects_date_range_and_limit(db_session):
    _, _, sensor_id = _seed_one_building(db_session, "EnergySite", "energy_b1")
    long_df = pd.DataFrame({
        "building_code": ["energy_b1"] * 5,
        "ts_utc": pd.date_range("2016-01-01", periods=5, freq="h", tz="UTC"),
        "consumption_kwh": [10.0, 20.0, 30.0, 40.0, 50.0],
    })
    upsert_energy_measurements(db_session, long_df, {"energy_b1": sensor_id})
    db_session.flush()

    all_rows = energy_repo.list_measurements(db_session, sensor_id, limit=100)
    assert len(all_rows) == 5

    ranged = energy_repo.list_measurements(
        db_session, sensor_id, limit=100,
        start=dt.datetime(2016, 1, 1, 1, tzinfo=dt.timezone.utc),
        end=dt.datetime(2016, 1, 1, 3, tzinfo=dt.timezone.utc),
    )
    assert [float(r.consumption_kwh) for r in ranged] == [20.0, 30.0, 40.0]


def test_energy_list_measurements_consumption_desc_excludes_nulls(db_session):
    """Regression test: Postgres sorts NULL first in DESC order by default,
    so without an explicit filter, a missing reading would rank above every
    real value when order_by="consumption_desc" -- exactly the bug this
    caught live against real data (a building's "peak" was reported as a
    NULL row)."""
    _, _, sensor_id = _seed_one_building(db_session, "NullPeakSite", "nullpeak_b1")
    long_df = pd.DataFrame({
        "building_code": ["nullpeak_b1"] * 4,
        "ts_utc": pd.date_range("2016-01-01", periods=4, freq="h", tz="UTC"),
        "consumption_kwh": [10.0, None, 30.0, 20.0],
    })
    upsert_energy_measurements(db_session, long_df, {"nullpeak_b1": sensor_id})
    db_session.flush()

    top = energy_repo.list_measurements(db_session, sensor_id, limit=10, order_by="consumption_desc")
    assert [float(r.consumption_kwh) for r in top] == [30.0, 20.0, 10.0]  # no None in the results

    limited = energy_repo.list_measurements(db_session, sensor_id, limit=2)
    assert len(limited) == 2


def test_energy_summary_computes_expected_statistics(db_session):
    _, _, sensor_id = _seed_one_building(db_session, "SummarySite", "summary_b1")
    long_df = pd.DataFrame({
        "building_code": ["summary_b1"] * 4,
        "ts_utc": pd.date_range("2016-01-01", periods=4, freq="h", tz="UTC"),
        "consumption_kwh": [10.0, 20.0, 30.0, None],
    })
    upsert_energy_measurements(db_session, long_df, {"summary_b1": sensor_id})
    db_session.flush()

    result = energy_repo.summary(db_session, sensor_id)
    assert result["total_observations"] == 3  # NULL excluded by count(column)
    assert result["total_rows"] == 4  # includes the NULL row
    assert float(result["mean_kwh"]) == 20.0
    assert float(result["min_kwh"]) == 10.0
    assert float(result["max_kwh"]) == 30.0


def test_energy_summary_returns_none_when_sensor_has_no_rows(db_session):
    _, _, sensor_id = _seed_one_building(db_session, "EmptySite", "empty_b1")
    assert energy_repo.summary(db_session, sensor_id) is None


def test_energy_aggregate_groups_by_day(db_session):
    _, _, sensor_id = _seed_one_building(db_session, "AggSite", "agg_b1")
    long_df = pd.DataFrame({
        "building_code": ["agg_b1"] * 4,
        "ts_utc": [
            pd.Timestamp("2016-01-01 00:00", tz="UTC"),
            pd.Timestamp("2016-01-01 12:00", tz="UTC"),
            pd.Timestamp("2016-01-02 00:00", tz="UTC"),
            pd.Timestamp("2016-01-02 12:00", tz="UTC"),
        ],
        "consumption_kwh": [10.0, 10.0, 5.0, 5.0],
    })
    upsert_energy_measurements(db_session, long_df, {"agg_b1": sensor_id})
    db_session.flush()

    daily = energy_repo.aggregate(db_session, sensor_id, "daily", limit=100)
    assert len(daily) == 2
    assert float(daily[0]["total_kwh"]) == 20.0
    assert float(daily[1]["total_kwh"]) == 10.0
    assert daily[0]["observation_count"] == 2


def test_weather_list_measurements_respects_date_range(db_session):
    site_ids = upsert_sites(db_session, ["WeatherSite"], {"WeatherSite": "US/Eastern"})
    db_session.flush()
    long_df = pd.DataFrame({
        "site_id": ["WeatherSite"] * 3,
        "ts_utc": pd.date_range("2016-01-01", periods=3, freq="h", tz="UTC"),
        "airTemperature": [1.0, 2.0, 3.0],
        "cloudCoverage": [None, None, None],
        "dewTemperature": [None, None, None],
        "precipDepth1HR": [None, None, None],
        "precipDepth6HR": [None, None, None],
        "seaLvlPressure": [None, None, None],
        "windDirection": [None, None, None],
        "windSpeed": [None, None, None],
    })
    upsert_weather_measurements(db_session, long_df, site_ids)
    db_session.flush()

    rows = weather_repo.list_measurements(db_session, site_ids["WeatherSite"], limit=100)
    assert len(rows) == 3
    assert float(rows[0].air_temp_c) == 1.0
