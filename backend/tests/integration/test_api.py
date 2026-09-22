import pandas as pd

from energy_platform.ingestion.loader import (
    upsert_buildings,
    upsert_energy_measurements,
    upsert_sensors,
    upsert_sites,
    upsert_weather_measurements,
)


def _seed_full_stack(db_session, site_code, building_code, n_hours=48):
    site_ids = upsert_sites(db_session, [site_code], {site_code: "US/Eastern"})
    metadata_df = pd.DataFrame([{
        "building_id": building_code, "site_id": site_code, "primaryspaceusage": "Office",
        "sqm": 300.0, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": None, "occupants": None, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, [building_code], site_ids)
    sensor_ids = upsert_sensors(db_session, building_ids)
    db_session.flush()

    ts = pd.date_range("2016-01-01", periods=n_hours, freq="h", tz="UTC")
    energy_df = pd.DataFrame({
        "building_code": [building_code] * n_hours,
        "ts_utc": ts,
        "consumption_kwh": [float(i) for i in range(n_hours)],
    })
    upsert_energy_measurements(db_session, energy_df, {building_code: sensor_ids[building_code]})

    weather_df = pd.DataFrame({
        "site_id": [site_code] * n_hours,
        "ts_utc": ts,
        "airTemperature": [float(i) for i in range(n_hours)],
        "cloudCoverage": [None] * n_hours,
        "dewTemperature": [None] * n_hours,
        "precipDepth1HR": [None] * n_hours,
        "precipDepth6HR": [None] * n_hours,
        "seaLvlPressure": [None] * n_hours,
        "windDirection": [None] * n_hours,
        "windSpeed": [None] * n_hours,
    })
    upsert_weather_measurements(db_session, weather_df, site_ids)
    db_session.flush()

    return site_ids[site_code], building_ids[building_code]


# --- sites ---

def test_list_sites_endpoint(api_client, db_session):
    _seed_full_stack(db_session, "ApiSiteList", "api_sitelist_b1")
    r = api_client.get("/api/v1/sites")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body and "limit" in body and "offset" in body
    assert body["total"] >= 1


def test_get_site_endpoint(api_client, db_session):
    site_id, _ = _seed_full_stack(db_session, "ApiSiteGet", "api_siteget_b1")
    r = api_client.get(f"/api/v1/sites/{site_id}")
    assert r.status_code == 200
    assert r.json()["site_code"] == "ApiSiteGet"


def test_get_site_404_for_missing(api_client):
    r = api_client.get("/api/v1/sites/999999")
    assert r.status_code == 404
    assert "detail" in r.json()


# --- buildings ---

def test_list_buildings_with_site_filter(api_client, db_session):
    site_id, building_id = _seed_full_stack(db_session, "ApiBldgSite", "api_bldg_b1")
    r = api_client.get("/api/v1/buildings", params={"site_id": site_id})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["building_id"] == building_id


def test_get_building_404_for_missing(api_client):
    r = api_client.get("/api/v1/buildings/999999")
    assert r.status_code == 404


def test_list_buildings_pagination(api_client, db_session):
    for i in range(3):
        _seed_full_stack(db_session, f"ApiPageSite{i}", f"api_page_b{i}", n_hours=1)

    r = api_client.get("/api/v1/buildings", params={"limit": 2, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 0


def test_list_buildings_rejects_limit_above_max(api_client):
    r = api_client.get("/api/v1/buildings", params={"limit": 100000})
    assert r.status_code == 422


# --- sensors ---

def test_list_sensors_filtered_by_building(api_client, db_session):
    _, building_id = _seed_full_stack(db_session, "ApiSensorSite", "api_sensor_b1")
    r = api_client.get("/api/v1/sensors", params={"building_id": building_id})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["meter_type"] == "electricity"


def test_get_sensor_404_for_missing(api_client):
    r = api_client.get("/api/v1/sensors/999999")
    assert r.status_code == 404


# --- energy ---

def test_get_building_energy_returns_timestamp_and_consumption(api_client, db_session):
    _, building_id = _seed_full_stack(db_session, "ApiEnergySite", "api_energy_b1", n_hours=10)
    r = api_client.get(f"/api/v1/buildings/{building_id}/energy")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 10
    assert set(rows[0].keys()) == {"ts", "consumption_kwh"}


def test_get_building_energy_date_range_filtering(api_client, db_session):
    _, building_id = _seed_full_stack(db_session, "ApiEnergyRange", "api_energyrange_b1", n_hours=48)
    r = api_client.get(
        f"/api/v1/buildings/{building_id}/energy",
        params={"start": "2016-01-01T00:00:00", "end": "2016-01-01T02:00:00"},
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 3  # hours 0, 1, 2 inclusive


def test_get_building_energy_rejects_start_after_end(api_client, db_session):
    _, building_id = _seed_full_stack(db_session, "ApiEnergyBad", "api_energybad_b1", n_hours=5)
    r = api_client.get(
        f"/api/v1/buildings/{building_id}/energy",
        params={"start": "2016-01-02T00:00:00", "end": "2016-01-01T00:00:00"},
    )
    assert r.status_code == 400


def test_get_building_energy_404_for_missing_building(api_client):
    r = api_client.get("/api/v1/buildings/999999/energy")
    assert r.status_code == 404


def test_get_building_energy_does_not_exceed_max_limit(api_client, db_session):
    r = api_client.get("/api/v1/buildings/1/energy", params={"limit": 999999})
    assert r.status_code == 422  # above TIMESERIES_MAX_LIMIT=5000, rejected before hitting DB


def test_get_building_energy_respects_default_limit_ordering(api_client, db_session):
    _, building_id = _seed_full_stack(db_session, "ApiEnergyLimit", "api_energylimit_b1", n_hours=5)
    r = api_client.get(f"/api/v1/buildings/{building_id}/energy", params={"limit": 2})
    rows = r.json()
    assert len(rows) == 2
    assert rows[0]["ts"] < rows[1]["ts"]


# --- weather ---

def test_get_site_weather_returns_rows(api_client, db_session):
    site_id, _ = _seed_full_stack(db_session, "ApiWeatherSite", "api_weather_b1", n_hours=6)
    r = api_client.get(f"/api/v1/sites/{site_id}/weather")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 6
    assert "air_temp_c" in rows[0]


def test_get_site_weather_404_for_missing_site(api_client):
    r = api_client.get("/api/v1/sites/999999/weather")
    assert r.status_code == 404


# --- summary ---

def test_get_building_summary_endpoint(api_client, db_session):
    _, building_id = _seed_full_stack(db_session, "ApiSummarySite", "api_summary_b1", n_hours=24)
    r = api_client.get(f"/api/v1/buildings/{building_id}/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_observations"] == 24
    assert body["min_consumption_kwh"] == 0.0
    assert body["max_consumption_kwh"] == 23.0
    assert "missing_observations" in body


def test_get_building_summary_404_when_no_measurements(api_client, db_session):
    site_ids = upsert_sites(db_session, ["ApiNoDataSite"], {"ApiNoDataSite": "US/Eastern"})
    metadata_df = pd.DataFrame([{
        "building_id": "api_nodata_b1", "site_id": "ApiNoDataSite", "primaryspaceusage": "Office",
        "sqm": 100.0, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": None, "occupants": None, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, ["api_nodata_b1"], site_ids)
    upsert_sensors(db_session, building_ids)
    db_session.flush()

    r = api_client.get(f"/api/v1/buildings/{building_ids['api_nodata_b1']}/summary")
    assert r.status_code == 404


# --- aggregate ---

def test_get_building_energy_aggregate_daily(api_client, db_session):
    _, building_id = _seed_full_stack(db_session, "ApiAggSite", "api_agg_b1", n_hours=48)
    r = api_client.get(
        f"/api/v1/buildings/{building_id}/energy/aggregate", params={"granularity": "daily"}
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert rows[0]["observation_count"] == 24


def test_get_building_energy_aggregate_monthly(api_client, db_session):
    _, building_id = _seed_full_stack(db_session, "ApiAggMonthly", "api_aggmonthly_b1", n_hours=48)
    r = api_client.get(
        f"/api/v1/buildings/{building_id}/energy/aggregate", params={"granularity": "monthly"}
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1  # 48 hours from 2016-01-01 stays within January
    assert rows[0]["observation_count"] == 48


def test_get_building_energy_aggregate_rejects_bad_granularity(api_client, db_session):
    _, building_id = _seed_full_stack(db_session, "ApiAggBad", "api_aggbad_b1", n_hours=5)
    r = api_client.get(
        f"/api/v1/buildings/{building_id}/energy/aggregate", params={"granularity": "yearly"}
    )
    assert r.status_code == 400
