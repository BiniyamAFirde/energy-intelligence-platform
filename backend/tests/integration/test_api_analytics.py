import pandas as pd

from energy_platform.ingestion.loader import (
    upsert_buildings,
    upsert_energy_measurements,
    upsert_sensors,
    upsert_sites,
    upsert_weather_measurements,
)


def _seed(db_session, site_code, building_code, n_hours=48, area_sqm=200.0, tz="US/Eastern"):
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

    ts = pd.date_range("2016-01-01", periods=n_hours, freq="h", tz="UTC")
    energy_df = pd.DataFrame({
        "building_code": [building_code] * n_hours, "ts_utc": ts,
        "consumption_kwh": [float(i % 24) for i in range(n_hours)],
    })
    upsert_energy_measurements(db_session, energy_df, {building_code: sensor_ids[building_code]})

    weather_df = pd.DataFrame({
        "site_id": [site_code] * n_hours, "ts_utc": ts,
        "airTemperature": [float(i) for i in range(n_hours)],
        "cloudCoverage": [None] * n_hours, "dewTemperature": [None] * n_hours,
        "precipDepth1HR": [None] * n_hours, "precipDepth6HR": [None] * n_hours,
        "seaLvlPressure": [None] * n_hours, "windDirection": [None] * n_hours,
        "windSpeed": [None] * n_hours,
    })
    upsert_weather_measurements(db_session, weather_df, site_ids)
    db_session.flush()

    return site_ids[site_code], building_ids[building_code]


def test_get_buildings_compare_route_not_shadowed_by_building_id_route(api_client, db_session):
    """Regression test for the static-vs-dynamic path ordering issue
    documented in api/routers/analytics.py: this must return the comparison
    list (200, a JSON array), not a 422 from FastAPI trying to parse
    "compare" as an int building_id."""
    _seed(db_session, "RouteOrderSite", "routeorder_b1")
    r = api_client.get("/api/v1/buildings/compare")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_compare_buildings_endpoint_filters_by_site(api_client, db_session):
    site_id, building_id = _seed(db_session, "CompareApiSite", "compareapi_b1")
    r = api_client.get("/api/v1/buildings/compare", params={"site_id": site_id})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["building_id"] == building_id
    assert "coefficient_of_variation" in rows[0]


def test_building_analytics_summary_endpoint(api_client, db_session):
    _, building_id = _seed(db_session, "AnaSumApiSite", "anasumapi_b1")
    r = api_client.get(f"/api/v1/buildings/{building_id}/analytics/summary")
    assert r.status_code == 200
    body = r.json()
    assert "median_kwh" in body
    assert "coefficient_of_variation" in body


def test_building_analytics_summary_404(api_client):
    r = api_client.get("/api/v1/buildings/999999/analytics/summary")
    assert r.status_code == 404


def test_building_analytics_profile_endpoint(api_client, db_session):
    _, building_id = _seed(db_session, "AnaProfApiSite", "anaprofapi_b1")
    r = api_client.get(f"/api/v1/buildings/{building_id}/analytics/profile")
    assert r.status_code == 200
    body = r.json()
    assert len(body["hour_of_day"]) > 0
    assert len(body["weekday_weekend"]) > 0
    assert body["timezone"] == "US/Eastern"


def test_building_analytics_peaks_endpoint_respects_top_n(api_client, db_session):
    _, building_id = _seed(db_session, "AnaPeakApiSite", "anapeakapi_b1", n_hours=48)
    r = api_client.get(f"/api/v1/buildings/{building_id}/analytics/peaks", params={"top_n": 5})
    assert r.status_code == 200
    body = r.json()
    assert len(body["top_peaks"]) == 5
    assert body["top_peaks"][0]["consumption_kwh"] >= body["top_peaks"][1]["consumption_kwh"]


def test_building_analytics_peaks_rejects_top_n_above_max(api_client, db_session):
    _, building_id = _seed(db_session, "AnaPeakBadApiSite", "anapeakbadapi_b1")
    r = api_client.get(f"/api/v1/buildings/{building_id}/analytics/peaks", params={"top_n": 10000})
    assert r.status_code == 422


def test_site_weather_energy_endpoint(api_client, db_session):
    site_id, _ = _seed(db_session, "AnaWxApiSite", "anawxapi_b1", n_hours=24)
    r = api_client.get(f"/api/v1/sites/{site_id}/analytics/weather-energy")
    assert r.status_code == 200
    body = r.json()
    assert body["n_timestamps"] == 24
    assert "air_temp_c" in body["correlations"]


def test_site_weather_energy_404_for_missing_site(api_client):
    r = api_client.get("/api/v1/sites/999999/analytics/weather-energy")
    assert r.status_code == 404


def test_site_weather_energy_with_date_range(api_client, db_session):
    site_id, _ = _seed(db_session, "AnaWxRangeApiSite", "anawxrangeapi_b1", n_hours=48)
    r = api_client.get(
        f"/api/v1/sites/{site_id}/analytics/weather-energy",
        params={"start": "2016-01-01T00:00:00", "end": "2016-01-01T05:00:00"},
    )
    assert r.status_code == 200
    assert r.json()["n_timestamps"] == 6
