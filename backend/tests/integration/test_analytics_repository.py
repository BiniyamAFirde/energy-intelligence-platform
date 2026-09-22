
import pandas as pd

from energy_platform.ingestion.loader import (
    upsert_buildings,
    upsert_energy_measurements,
    upsert_sensors,
    upsert_sites,
    upsert_weather_measurements,
)
from energy_platform.repositories import analytics as analytics_repo


def _seed_building(db_session, site_code, building_code, tz="US/Eastern"):
    site_ids = upsert_sites(db_session, [site_code], {site_code: tz})
    metadata_df = pd.DataFrame([{
        "building_id": building_code, "site_id": site_code, "primaryspaceusage": "Office",
        "sqm": 200.0, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": None, "occupants": None, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, [building_code], site_ids)
    sensor_ids = upsert_sensors(db_session, building_ids)
    db_session.flush()
    return site_ids[site_code], building_ids[building_code], sensor_ids[building_code]


def _load_energy(db_session, building_code, sensor_id, ts_utc_list, values):
    df = pd.DataFrame({
        "building_code": [building_code] * len(ts_utc_list),
        "ts_utc": ts_utc_list,
        "consumption_kwh": values,
    })
    upsert_energy_measurements(db_session, df, {building_code: sensor_id})
    db_session.flush()


def test_hour_of_day_profile_uses_local_time_not_utc(db_session):
    _, _, sensor_id = _seed_building(db_session, "TzHourSite", "tz_hour_b1", "US/Eastern")
    # 2016-06-15 16:00 UTC is EDT (UTC-4) in June -> local hour 12 (noon)
    ts = pd.Timestamp("2016-06-15 16:00:00", tz="UTC")
    _load_energy(db_session, "tz_hour_b1", sensor_id, [ts], [42.0])

    profile = analytics_repo.hour_of_day_profile(db_session, sensor_id, "US/Eastern")
    assert len(profile) == 1
    assert int(profile[0]["hour_of_day"]) == 12
    assert float(profile[0]["mean_kwh"]) == 42.0


def test_hour_of_day_profile_utc_site_matches_raw_utc_hour(db_session):
    # Sanity control: with a UTC "site timezone", local hour == UTC hour,
    # confirming the AT TIME ZONE conversion isn't silently a no-op bug that
    # would happen to also pass the US/Eastern test above by coincidence.
    _, _, sensor_id = _seed_building(db_session, "TzUtcSite", "tz_utc_b1", "UTC")
    ts = pd.Timestamp("2016-06-15 16:00:00", tz="UTC")
    _load_energy(db_session, "tz_utc_b1", sensor_id, [ts], [1.0])

    profile = analytics_repo.hour_of_day_profile(db_session, sensor_id, "UTC")
    assert int(profile[0]["hour_of_day"]) == 16


def test_day_of_week_profile_distinguishes_known_dates(db_session):
    _, _, sensor_id = _seed_building(db_session, "TzDowSite", "tz_dow_b1", "US/Eastern")
    # 2016-06-11 is a Saturday (dow=6), 2016-06-13 is a Monday (dow=1), local noon
    saturday = pd.Timestamp("2016-06-11 16:00:00", tz="UTC")
    monday = pd.Timestamp("2016-06-13 16:00:00", tz="UTC")
    _load_energy(db_session, "tz_dow_b1", sensor_id, [saturday, monday], [50.0, 5.0])

    profile = analytics_repo.day_of_week_profile(db_session, sensor_id, "US/Eastern")
    by_dow = {int(r["day_of_week"]): r for r in profile}
    assert float(by_dow[6]["mean_kwh"]) == 50.0  # Saturday
    assert float(by_dow[1]["mean_kwh"]) == 5.0   # Monday


def test_weekday_weekend_profile_classifies_known_dates(db_session):
    _, _, sensor_id = _seed_building(db_session, "TzWkndSite", "tz_wknd_b1", "US/Eastern")
    # 2016-06-11 is a Saturday, 2016-06-13 is a Monday (both noon-local UTC-4 -> 16:00 UTC)
    saturday = pd.Timestamp("2016-06-11 16:00:00", tz="UTC")
    monday = pd.Timestamp("2016-06-13 16:00:00", tz="UTC")
    _load_energy(db_session, "tz_wknd_b1", sensor_id, [saturday, monday], [100.0, 10.0])

    profile = analytics_repo.weekday_weekend_profile(db_session, sensor_id, "US/Eastern")
    by_flag = {row["is_weekend"]: row for row in profile}
    assert float(by_flag[True]["mean_kwh"]) == 100.0
    assert float(by_flag[False]["mean_kwh"]) == 10.0


def test_monthly_profile_and_monthly_peaks(db_session):
    _, _, sensor_id = _seed_building(db_session, "TzMonthSite", "tz_month_b1", "US/Eastern")
    jan = pd.Timestamp("2016-01-15 16:00:00", tz="UTC")
    feb = pd.Timestamp("2016-02-15 16:00:00", tz="UTC")
    feb2 = pd.Timestamp("2016-02-16 16:00:00", tz="UTC")
    _load_energy(db_session, "tz_month_b1", sensor_id, [jan, feb, feb2], [5.0, 30.0, 7.0])

    monthly = analytics_repo.monthly_profile(db_session, sensor_id, "US/Eastern")
    by_month = {int(r["month"]): r for r in monthly}
    assert float(by_month[1]["total_kwh"]) == 5.0
    assert float(by_month[2]["total_kwh"]) == 37.0

    peaks = analytics_repo.monthly_peaks(db_session, sensor_id, "US/Eastern")
    by_month_peak = {int(r["month"]): r for r in peaks}
    assert float(by_month_peak[2]["consumption_kwh"]) == 30.0  # not 7.0


def test_building_comparison_returns_expected_columns_and_filters(db_session):
    site_id, building_id, sensor_id = _seed_building(db_session, "CompSite", "comp_b1")
    ts = pd.date_range("2016-01-01", periods=3, freq="h", tz="UTC")
    _load_energy(db_session, "comp_b1", sensor_id, ts, [10.0, 20.0, 30.0])

    rows = analytics_repo.building_comparison(db_session, site_id=site_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["building_id"] == building_id
    assert float(row["mean_kwh"]) == 20.0
    assert float(row["total_kwh"]) == 60.0
    assert float(row["peak_kwh"]) == 30.0
    assert row["area_sqm"] is not None


def test_building_comparison_excludes_buildings_with_no_measurements(db_session):
    site_ids = upsert_sites(db_session, ["NoDataCompSite"], {"NoDataCompSite": "US/Eastern"})
    metadata_df = pd.DataFrame([{
        "building_id": "nodata_comp_b1", "site_id": "NoDataCompSite", "primaryspaceusage": "Office",
        "sqm": 100.0, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": None, "occupants": None, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, ["nodata_comp_b1"], site_ids)
    upsert_sensors(db_session, building_ids)
    db_session.flush()

    rows = analytics_repo.building_comparison(db_session, site_id=site_ids["NoDataCompSite"])
    assert rows == []  # inner join: a building with zero readings contributes no row


def test_site_weather_energy_series_sums_across_buildings_and_joins_weather(db_session):
    site_code = "WxEnergySite"
    site_ids = upsert_sites(db_session, [site_code], {site_code: "US/Eastern"})
    metadata_df = pd.DataFrame([
        {"building_id": f"wx_b{i}", "site_id": site_code, "primaryspaceusage": "Office",
         "sqm": 100.0, "sub_primaryspaceusage": None, "lat": None, "lng": None,
         "yearbuilt": None, "numberoffloors": None, "occupants": None, "industry": None,
         "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
         "source_eui": None, "leed_level": None, "energystarscore": None}
        for i in range(2)
    ])
    building_ids = upsert_buildings(db_session, metadata_df, ["wx_b0", "wx_b1"], site_ids)
    sensor_ids = upsert_sensors(db_session, building_ids)
    db_session.flush()

    ts = pd.date_range("2016-01-01", periods=2, freq="h", tz="UTC")
    for i in range(2):
        df = pd.DataFrame({
            "building_code": [f"wx_b{i}"] * 2, "ts_utc": ts, "consumption_kwh": [10.0, 20.0],
        })
        upsert_energy_measurements(db_session, df, {f"wx_b{i}": sensor_ids[f"wx_b{i}"]})

    weather_df = pd.DataFrame({
        "site_id": [site_code] * 2, "ts_utc": ts,
        "airTemperature": [15.0, 16.0], "cloudCoverage": [None, None],
        "dewTemperature": [None, None], "precipDepth1HR": [None, None],
        "precipDepth6HR": [None, None], "seaLvlPressure": [None, None],
        "windDirection": [None, None], "windSpeed": [None, None],
    })
    upsert_weather_measurements(db_session, weather_df, site_ids)
    db_session.flush()

    rows = analytics_repo.site_weather_energy_series(db_session, site_ids[site_code])
    assert len(rows) == 2
    assert float(rows[0]["total_kwh"]) == 20.0  # 10+10 across both buildings
    assert float(rows[0]["air_temp_c"]) == 15.0
    assert float(rows[1]["total_kwh"]) == 40.0  # 20+20
