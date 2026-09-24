"""Tests for POST /api/v1/forecast (external-company inference). Uses a
plain starlette TestClient around the real app -- deliberately NOT the
`api_client`/`db_session` fixtures every other API test file uses, since
this endpoint has no `get_db` dependency at all: these tests prove that
directly by never opening a database connection (Postgres is never touched
by this file), not just by asserting it in prose.

The TestClient is module-scoped so the ~552MB model artifact (loaded once
via the app's lifespan -> external.get_cached_pipeline(), a process-level
singleton) is only actually deserialized once across this whole file --
and, if pytest already warmed the cache via another test module in the
same session (e.g. test_external_inference.py), this module pays nothing
at all.
"""

from __future__ import annotations

import math
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sklearn.ensemble import RandomForestRegressor

from energy_platform.forecasting import external
from energy_platform.main import app

N_HOURS = 216
START = pd.Timestamp("2024-06-01 00:00:00")


def _energy_rows(n_hours: int = N_HOURS, start: pd.Timestamp = START) -> list[dict]:
    ts = pd.date_range(start, periods=n_hours, freq="h")
    return [
        {
            "timestamp": t.strftime("%Y-%m-%d %H:%M:%S"),
            "energy_kwh": round(80.0 + 20.0 * math.sin(2 * math.pi * (i % 24) / 24) + (15.0 if (i // 24) % 7 < 5 else 0.0), 2),
        }
        for i, t in enumerate(ts)
    ]


def _building(primary_use: str = "Office", timezone: str = "US/Eastern") -> dict:
    return {
        "building_code": "api_company_001",
        "area_sqm": 2500,
        "number_of_floors": 4,
        "occupants": 180,
        "primary_use": primary_use,
        "timezone": timezone,
    }


def _payload(**kwargs) -> dict:
    return {"building": _building(**{k: v for k, v in kwargs.items() if k in ("primary_use", "timezone")}), "energy": _energy_rows()}


@pytest.fixture(scope="module")
def client():
    assert external.DEFAULT_ARTIFACT_PATH.exists(), (
        f"{external.DEFAULT_ARTIFACT_PATH} not found -- run "
        "`python -m energy_platform.forecasting.train` first (outside this test)."
    )
    with TestClient(app) as c:
        yield c


# --- Test 1: valid request ---


def test_valid_request_returns_24_finite_predictions(client):
    r = client.post("/api/v1/forecast", json=_payload())
    assert r.status_code == 200
    body = r.json()

    assert body["building_code"] == "api_company_001"
    assert body["model_name"] == "random_forest"
    assert "model_version" in body and body["model_version"]
    assert "forecast_origin" in body
    assert len(body["predictions"]) == 24
    assert sorted(p["horizon"] for p in body["predictions"]) == list(range(1, 25))
    for p in body["predictions"]:
        assert isinstance(p["predicted_kwh"], (int, float))
        assert math.isfinite(p["predicted_kwh"])
    assert body["warnings"] == []  # "Office" is a known BDG2 training category


# --- Test 2: insufficient history ---


def test_insufficient_history_returns_4xx(client):
    payload = _payload()
    payload["energy"] = _energy_rows(n_hours=100)
    r = client.post("/api/v1/forecast", json=payload)
    assert 400 <= r.status_code < 500
    assert "168" in r.json()["detail"]


# --- Test 3: duplicate timestamps ---


def test_duplicate_timestamps_returns_4xx(client):
    payload = _payload()
    payload["energy"][5]["timestamp"] = payload["energy"][4]["timestamp"]
    r = client.post("/api/v1/forecast", json=payload)
    assert 400 <= r.status_code < 500
    assert "duplicate timestamp" in r.json()["detail"]


# --- Test 4: irregular timestamps ---


def test_irregular_timestamps_returns_4xx(client):
    payload = _payload()
    shifted = pd.Timestamp(payload["energy"][50]["timestamp"]) + pd.Timedelta(minutes=30)
    payload["energy"][50]["timestamp"] = shifted.strftime("%Y-%m-%d %H:%M:%S")
    r = client.post("/api/v1/forecast", json=payload)
    assert 400 <= r.status_code < 500
    assert "exactly hourly and gap-free" in r.json()["detail"]


# --- Test 5: missing metadata ---


def test_missing_building_metadata_returns_4xx(client):
    payload = _payload()
    del payload["building"]["area_sqm"]
    r = client.post("/api/v1/forecast", json=payload)
    assert r.status_code == 422  # caught by Pydantic's own required-field validation


def test_missing_energy_field_returns_4xx(client):
    payload = _payload()
    del payload["energy"][0]["energy_kwh"]
    r = client.post("/api/v1/forecast", json=payload)
    assert r.status_code == 422


# --- Test 6: invalid energy value ---


def test_negative_energy_value_returns_4xx(client):
    """energy_kwh has no `ge=0` schema constraint on purpose (see
    schemas/external_forecast.py) -- "no negatives" is enforced exactly
    once, in forecasting.external.validate_energy_observations, so this
    exercises that shared rule rather than a schema-level duplicate."""
    payload = _payload()
    payload["energy"][10]["energy_kwh"] = -5.0
    r = client.post("/api/v1/forecast", json=payload)
    assert 400 <= r.status_code < 500
    assert "negative energy_kwh" in r.json()["detail"]


def test_invalid_building_metadata_returns_4xx(client):
    """Same reasoning as above for area_sqm: no `gt=0` in the schema, so a
    non-positive value is caught by the shared validate_building_metadata,
    not duplicated schema logic."""
    payload = _payload()
    payload["building"]["area_sqm"] = -100.0
    r = client.post("/api/v1/forecast", json=payload)
    assert 400 <= r.status_code < 500
    assert "positive number" in r.json()["detail"]


# --- Test 7: unknown primary_use ---


def test_unknown_primary_use_is_allowed_and_surfaces_a_warning(client):
    """Matches the already-tested external-adapter behavior
    (test_external_inference.py::test_unknown_primary_use_is_allowed_but_warns):
    the trained encoder's handle_unknown='ignore' means this is NOT a hard
    rejection -- the API must not invent a stricter rule of its own."""
    payload = _payload(primary_use="Retail")
    r = client.post("/api/v1/forecast", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert len(body["predictions"]) == 24
    assert len(body["warnings"]) == 1
    assert "Retail" in body["warnings"][0]


# --- Test 8: no database dependency ---


def test_forecast_succeeds_with_no_database_interaction(client):
    """This whole file's `client` fixture never touches the `get_db`
    dependency or opens a Postgres connection (unlike every other API test
    file's `api_client` fixture) -- this test, and every other test in
    this module succeeding at all, is itself the proof that
    POST /api/v1/forecast works without registering the building in
    sites/buildings/sensors/energy_measurements."""
    r = client.post("/api/v1/forecast", json=_payload())
    assert r.status_code == 200
    assert len(r.json()["predictions"]) == 24


# --- Test 9: model loaded once, not per request ---


def test_multiple_requests_reuse_the_same_loaded_pipeline(client):
    pipeline_before = client.app.state.forecast_pipeline

    with patch("energy_platform.forecasting.external.joblib.load") as mock_load:
        for _ in range(3):
            r = client.post("/api/v1/forecast", json=_payload())
            assert r.status_code == 200
    mock_load.assert_not_called()  # app.state's already-loaded Pipeline was reused, not re-deserialized

    assert client.app.state.forecast_pipeline is pipeline_before  # same object, never replaced


# --- Test 10: no training ---


def test_forecast_endpoint_never_calls_fit(client):
    with patch.object(
        RandomForestRegressor, "fit",
        side_effect=AssertionError("fit() was called during an inference-only API request"),
    ) as mock_fit:
        r = client.post("/api/v1/forecast", json=_payload())
    mock_fit.assert_not_called()
    assert r.status_code == 200
    assert len(r.json()["predictions"]) == 24
