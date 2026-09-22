import datetime as dt

import pandas as pd
import pytest

from energy_platform.ingestion.loader import (
    upsert_buildings,
    upsert_energy_measurements,
    upsert_sensors,
    upsert_sites,
    upsert_weather_measurements,
)
from energy_platform.services import analytics_service
from energy_platform.services.exceptions import NotFoundError


def _seed_building(db_session, site_code, building_code, area_sqm=150.0, tz="US/Eastern"):
    site_ids = upsert_sites(db_session, [site_code], {site_code: tz})
    metadata_df = pd.DataFrame([{
        "building_id": building_code, "site_id": site_code, "primaryspaceusage": "Office",
        "sqm": area_sqm, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": None, "occupants": None, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, [building_code], site_ids)
    sensor_ids = upsert_sensors(db_session, building_ids)
    db_session.flush()
    return site_ids[site_code], building_ids[building_code], sensor_ids[building_code]


def _load_energy(db_session, building_code, sensor_id, ts_list, values):
    df = pd.DataFrame({"building_code": [building_code] * len(ts_list), "ts_utc": ts_list, "consumption_kwh": values})
    upsert_energy_measurements(db_session, df, {building_code: sensor_id})
    db_session.flush()


def test_analytics_summary_includes_coefficient_of_variation(db_session):
    _, building_id, sensor_id = _seed_building(db_session, "AnaSumSite", "ana_sum_b1")
    _load_energy(db_session, "ana_sum_b1", sensor_id,
                 pd.date_range("2016-01-01", periods=4, freq="h", tz="UTC"), [10.0, 20.0, 30.0, 40.0])

    summary = analytics_service.get_building_analytics_summary(db_session, building_id)
    assert summary["total_observations"] == 4
    assert summary["median_kwh"] == 25.0
    assert summary["coefficient_of_variation"] == pytest.approx(
        float(summary["stddev_kwh"]) / float(summary["mean_kwh"])
    )


def test_analytics_summary_404_for_building_with_no_measurements(db_session):
    _, building_id, _ = _seed_building(db_session, "AnaEmptySite", "ana_empty_b1")
    with pytest.raises(NotFoundError):
        analytics_service.get_building_analytics_summary(db_session, building_id)


def test_building_profile_returns_all_four_views_and_resolves_timezone(db_session):
    _, building_id, sensor_id = _seed_building(db_session, "AnaProfSite", "ana_prof_b1", tz="US/Eastern")
    _load_energy(db_session, "ana_prof_b1", sensor_id,
                 pd.date_range("2016-06-01", periods=48, freq="h", tz="UTC"), [10.0] * 48)

    profile = analytics_service.get_building_profile(db_session, building_id)
    assert profile["timezone"] == "US/Eastern"
    assert len(profile["hour_of_day"]) > 0
    assert len(profile["day_of_week"]) > 0
    assert len(profile["month"]) > 0
    assert len(profile["weekday_weekend"]) > 0


def test_building_profile_404_for_missing_building(db_session):
    with pytest.raises(NotFoundError):
        analytics_service.get_building_profile(db_session, 999_999)


def test_get_building_peaks_orders_and_includes_monthly_peaks(db_session):
    _, building_id, sensor_id = _seed_building(db_session, "AnaPeakSite", "ana_peak_b1")
    ts = pd.date_range("2016-01-01", periods=5, freq="h", tz="UTC")
    _load_energy(db_session, "ana_peak_b1", sensor_id, ts, [10.0, 99.0, 20.0, 5.0, 50.0])

    peaks = analytics_service.get_building_peaks(db_session, building_id, top_n=3)
    assert peaks["peak_kwh"] == 99.0
    assert [p["consumption_kwh"] for p in peaks["top_peaks"]] == [99.0, 50.0, 20.0]
    assert len(peaks["monthly_peaks"]) == 1
    assert peaks["monthly_peaks"][0]["consumption_kwh"] == 99.0


def test_get_building_peaks_404_when_no_measurements(db_session):
    _, building_id, _ = _seed_building(db_session, "AnaPeakEmptySite", "ana_peakempty_b1")
    with pytest.raises(NotFoundError):
        analytics_service.get_building_peaks(db_session, building_id)


def test_get_building_peaks_single_day_data_still_works(db_session):
    _, building_id, sensor_id = _seed_building(db_session, "AnaSingleDaySite", "ana_singleday_b1")
    ts = pd.date_range("2016-01-01", periods=3, freq="h", tz="UTC")  # single calendar day
    _load_energy(db_session, "ana_singleday_b1", sensor_id, ts, [1.0, 2.0, 3.0])

    peaks = analytics_service.get_building_peaks(db_session, building_id, top_n=10)
    assert peaks["peak_kwh"] == 3.0
    assert len(peaks["top_peaks"]) == 3  # fewer rows than requested top_n, not an error


def test_building_comparison_computes_cv_per_building(db_session):
    site_id, building_id, sensor_id = _seed_building(db_session, "AnaCompSite", "ana_comp_b1")
    _load_energy(db_session, "ana_comp_b1", sensor_id,
                 pd.date_range("2016-01-01", periods=3, freq="h", tz="UTC"), [10.0, 20.0, 30.0])

    rows = analytics_service.get_building_comparison(db_session, site_id=site_id)
    assert len(rows) == 1
    assert "coefficient_of_variation" in rows[0]
    assert rows[0]["coefficient_of_variation"] is not None


def test_building_comparison_empty_when_no_buildings_match_filter(db_session):
    rows = analytics_service.get_building_comparison(db_session, primary_use="NoSuchUseAtAll")
    assert rows == []


def test_site_weather_energy_correlates_temperature_with_consumption(db_session):
    site_code = "AnaWxSite"
    site_ids = upsert_sites(db_session, [site_code], {site_code: "US/Eastern"})
    metadata_df = pd.DataFrame([{
        "building_id": "ana_wx_b1", "site_id": site_code, "primaryspaceusage": "Office",
        "sqm": 100.0, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": None, "occupants": None, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, ["ana_wx_b1"], site_ids)
    sensor_ids = upsert_sensors(db_session, building_ids)
    db_session.flush()

    ts = pd.date_range("2016-01-01", periods=5, freq="h", tz="UTC")
    energy_vals = [10.0, 20.0, 30.0, 40.0, 50.0]
    temp_vals = [1.0, 2.0, 3.0, 4.0, 5.0]  # perfectly correlated with energy by construction
    _load_energy(db_session, "ana_wx_b1", sensor_ids["ana_wx_b1"], ts, energy_vals)

    weather_df = pd.DataFrame({
        "site_id": [site_code] * 5, "ts_utc": ts,
        "airTemperature": temp_vals, "cloudCoverage": [None] * 5, "dewTemperature": [None] * 5,
        "precipDepth1HR": [None] * 5, "precipDepth6HR": [None] * 5, "seaLvlPressure": [None] * 5,
        "windDirection": [None] * 5, "windSpeed": [None] * 5,
    })
    upsert_weather_measurements(db_session, weather_df, site_ids)
    db_session.flush()

    result = analytics_service.get_site_weather_energy(db_session, site_ids[site_code])
    assert result["n_timestamps"] == 5
    assert result["correlations"]["air_temp_c"]["correlation"] == pytest.approx(1.0)
    assert result["correlations"]["air_temp_c"]["n_pairs"] == 5
    # cloud_coverage is entirely NULL in this fixture -> correlation undefined
    assert result["correlations"]["cloud_coverage"]["correlation"] is None
    assert result["correlations"]["cloud_coverage"]["n_pairs"] == 0


def test_site_weather_energy_404_for_missing_site(db_session):
    with pytest.raises(NotFoundError):
        analytics_service.get_site_weather_energy(db_session, 999_999)


def test_site_weather_energy_empty_date_range_returns_zero_timestamps(db_session):
    site_code = "AnaWxEmptyRangeSite"
    site_ids = upsert_sites(db_session, [site_code], {site_code: "US/Eastern"})
    db_session.flush()

    result = analytics_service.get_site_weather_energy(
        db_session, site_ids[site_code],
        start=dt.datetime(2099, 1, 1), end=dt.datetime(2099, 1, 2),
    )
    assert result["n_timestamps"] == 0
    assert result["correlations"]["air_temp_c"]["correlation"] is None


def test_get_dataset_wide_profile_aggregates_across_buildings(db_session):
    _, _, s1 = _seed_building(db_session, "DatasetWideSiteA", "dw_b1")
    _, _, s2 = _seed_building(db_session, "DatasetWideSiteB", "dw_b2")
    ts = pd.date_range("2016-01-01", periods=3, freq="h", tz="UTC")
    _load_energy(db_session, "dw_b1", s1, ts, [10.0, 10.0, 10.0])
    _load_energy(db_session, "dw_b2", s2, ts, [5.0, 5.0, 5.0])

    profile = analytics_service.get_dataset_wide_profile(db_session, tz_name="UTC")
    # each test runs in its own rolled-back transaction, so these two
    # buildings are the only rows visible here
    assert len(profile["daily_total"]) == 1
    assert float(profile["daily_total"][0]["total_kwh"]) == 45.0  # 3*10 + 3*5
