"""Tests for the offline external-company inference adapter
(energy_platform.forecasting.external). Deliberately DB-free: unlike the
integration test that proved the artifact scores an unseen building already
registered in Postgres (test_forecasting_external_building_inference.py),
this suite proves the same trained artifact scores a company's data that
was NEVER inserted anywhere -- no db_session fixture, no production tables
touched at all, matching the whole point of this adapter.

The 552MB model artifact is loaded once per test session (module-scoped
fixture) and reused across every test, mirroring the "load once per
process" rule the adapter itself enforces.
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from sklearn.ensemble import RandomForestRegressor

from energy_platform.forecasting import external

N_HOURS = 216  # matches examples/external_company/energy.csv: 168h warm-up + slack for one origin
START = pd.Timestamp("2024-06-01 00:00:00")


def _energy_rows(n_hours: int = N_HOURS, start: pd.Timestamp = START) -> pd.DataFrame:
    ts = pd.date_range(start, periods=n_hours, freq="h")
    values = [
        round(80.0 + 20.0 * math.sin(2 * math.pi * (i % 24) / 24) + (15.0 if (i // 24) % 7 < 5 else 0.0), 2)
        for i in range(n_hours)
    ]
    return pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"), "energy_kwh": values})


def _building_row(primary_use: str = "Office", timezone: str = "US/Eastern") -> pd.DataFrame:
    return pd.DataFrame([{
        "building_code": "external_company_001",
        "area_sqm": 4200.0,
        "number_of_floors": 5,
        "occupants": 120,
        "primary_use": primary_use,
        "timezone": timezone,
    }])


def _write_csvs(tmp_path: Path, energy_df: pd.DataFrame, building_df: pd.DataFrame) -> tuple[Path, Path]:
    energy_path = tmp_path / "energy.csv"
    building_path = tmp_path / "building.csv"
    energy_df.to_csv(energy_path, index=False)
    building_df.to_csv(building_path, index=False)
    return energy_path, building_path


@pytest.fixture(scope="module")
def pipeline():
    """Loaded exactly once for the whole test module -- the artifact is
    ~552MB and joblib.load dominates runtime if reloaded per test."""
    assert external.DEFAULT_ARTIFACT_PATH.exists(), (
        f"{external.DEFAULT_ARTIFACT_PATH} not found -- run "
        "`python -m energy_platform.forecasting.train` first (outside this test)."
    )
    return external.load_model()


# --- Test 1: valid external company ---


def test_valid_external_company_produces_24_finite_predictions(tmp_path, pipeline):
    energy_path, building_path = _write_csvs(tmp_path, _energy_rows(), _building_row())

    with patch.object(
        RandomForestRegressor, "fit",
        side_effect=AssertionError("fit() was called during an inference-only test"),
    ) as mock_fit:
        result = external.run_external_inference(
            energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH
        )
    mock_fit.assert_not_called()

    assert result.primary_use_warning is None  # "Office" is a known BDG2 training category
    out = result.output
    assert len(out) == 24
    assert sorted(out["horizon"]) == list(range(1, 25))
    assert {
        "building_code", "target_ts", "horizon", "predicted_kwh", "model_name", "model_version",
    } <= set(out.columns)
    assert (out["building_code"] == "external_company_001").all()
    assert out["predicted_kwh"].apply(lambda v: isinstance(v, float) and math.isfinite(v)).all()
    assert (out["generated_at"] < out["target_ts"]).all()  # leakage invariant, end to end


def test_valid_external_company_loads_the_real_artifact(tmp_path, pipeline):
    energy_path, building_path = _write_csvs(tmp_path, _energy_rows(), _building_row())
    with patch("energy_platform.forecasting.external.joblib.load", wraps=external.joblib.load) as mock_load:
        loaded = external.load_model()
    mock_load.assert_called_once_with(external.DEFAULT_ARTIFACT_PATH)
    assert loaded is not None

    result = external.run_external_inference(energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH)
    assert len(result.output) == 24


# --- Test 2: insufficient history ---


def test_insufficient_history_raises_clear_error(tmp_path, pipeline):
    energy_path, building_path = _write_csvs(tmp_path, _energy_rows(n_hours=100), _building_row())
    with pytest.raises(external.ExternalDataError, match="at least 168 consecutive hourly"):
        external.run_external_inference(energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH)


# --- Test 3: duplicate timestamps ---


def test_duplicate_timestamps_rejected(tmp_path, pipeline):
    energy_df = _energy_rows()
    energy_df.loc[5, "timestamp"] = energy_df.loc[4, "timestamp"]  # duplicate an existing timestamp
    energy_path, building_path = _write_csvs(tmp_path, energy_df, _building_row())
    with pytest.raises(external.ExternalDataError, match="duplicate timestamp"):
        external.run_external_inference(energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH)


# --- Test 4: missing required column ---


def test_missing_required_column_rejected(tmp_path, pipeline):
    energy_df = _energy_rows().drop(columns=["energy_kwh"])
    energy_path, building_path = _write_csvs(tmp_path, energy_df, _building_row())
    with pytest.raises(external.ExternalDataError, match="missing required column"):
        external.run_external_inference(energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH)


def test_missing_required_building_column_rejected(tmp_path, pipeline):
    energy_path, building_path = _write_csvs(tmp_path, _energy_rows(), _building_row().drop(columns=["area_sqm"]))
    with pytest.raises(external.ExternalDataError, match="missing required column"):
        external.run_external_inference(energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH)


# --- Test 5: invalid/unknown primary_use (allowed, but warned -- matches
# the trained OneHotEncoder's real handle_unknown='ignore' behavior) ---


def test_unknown_primary_use_is_allowed_but_warns(tmp_path, pipeline):
    energy_path, building_path = _write_csvs(tmp_path, _energy_rows(), _building_row(primary_use="Retail"))
    result = external.run_external_inference(energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH)

    assert len(result.output) == 24  # still scores -- handle_unknown='ignore', not a hard error
    assert result.primary_use_warning is not None
    assert "Retail" in result.primary_use_warning
    assert result.output["predicted_kwh"].apply(math.isfinite).all()


def test_known_primary_use_categories_come_from_the_loaded_artifact(pipeline):
    categories = external.known_primary_use_categories(pipeline)
    assert "Office" in categories
    assert len(categories) > 0


# --- Test 6: irregular hourly data ---


def test_irregular_timestamps_rejected(tmp_path, pipeline):
    energy_df = _energy_rows()
    # Shift by 30 minutes (not a whole number of hours) so this row lands
    # between two existing hourly slots -- a real gap, not a collision
    # with another row's timestamp (which would instead trip the
    # duplicate-timestamp check).
    energy_df.loc[50, "timestamp"] = (
        pd.Timestamp(energy_df.loc[50, "timestamp"]) + pd.Timedelta(minutes=30)
    ).strftime("%Y-%m-%d %H:%M:%S")
    energy_path, building_path = _write_csvs(tmp_path, energy_df, _building_row())
    with pytest.raises(external.ExternalDataError, match="exactly hourly and gap-free"):
        external.run_external_inference(energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH)


def test_negative_energy_values_rejected(tmp_path, pipeline):
    energy_df = _energy_rows()
    energy_df.loc[10, "energy_kwh"] = -5.0
    energy_path, building_path = _write_csvs(tmp_path, energy_df, _building_row())
    with pytest.raises(external.ExternalDataError, match="negative energy_kwh"):
        external.run_external_inference(energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH)


def test_missing_energy_values_rejected(tmp_path, pipeline):
    energy_df = _energy_rows()
    energy_df.loc[10, "energy_kwh"] = None
    energy_path, building_path = _write_csvs(tmp_path, energy_df, _building_row())
    with pytest.raises(external.ExternalDataError, match="missing energy_kwh"):
        external.run_external_inference(energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH)


def test_non_numeric_energy_values_rejected(tmp_path, pipeline):
    energy_df = _energy_rows()
    energy_df["energy_kwh"] = energy_df["energy_kwh"].astype(object)
    energy_df.loc[10, "energy_kwh"] = "not-a-number"
    energy_path, building_path = _write_csvs(tmp_path, energy_df, _building_row())
    with pytest.raises(external.ExternalDataError, match="non-numeric energy_kwh"):
        external.run_external_inference(energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH)


def test_invalid_metadata_value_rejected(tmp_path, pipeline):
    building_df = _building_row()
    building_df.loc[0, "area_sqm"] = -100.0
    energy_path, building_path = _write_csvs(tmp_path, _energy_rows(), building_df)
    with pytest.raises(external.ExternalDataError, match="positive number"):
        external.run_external_inference(energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH)


# --- Test 7: timezone / DST ---


def test_offset_qualified_timestamps_rejected(tmp_path, pipeline):
    energy_df = _energy_rows()
    energy_df["timestamp"] = energy_df["timestamp"] + "+00:00"
    energy_path, building_path = _write_csvs(tmp_path, energy_df, _building_row())
    with pytest.raises(external.ExternalDataError, match="naive local time"):
        external.run_external_inference(energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH)


def test_unrecognized_timezone_rejected(tmp_path, pipeline):
    energy_path, building_path = _write_csvs(tmp_path, _energy_rows(), _building_row(timezone="Not/A_Timezone"))
    with pytest.raises(external.ExternalDataError, match="not a recognized IANA timezone"):
        external.run_external_inference(energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH)


def test_dst_spring_forward_gap_does_not_produce_invalid_or_duplicate_utc_timestamps(tmp_path, pipeline):
    """US/Eastern springs forward at 2024-03-10 02:00 local (skips straight
    to 03:00) -- exercise history that straddles it and confirm the
    adapter either normalizes cleanly (no duplicate/invalid UTC rows) or
    raises the documented ExternalDataError, never silently producing
    leaked/incorrect timestamps."""
    start = pd.Timestamp("2024-03-05 00:00:00")  # ends after the DST boundary given N_HOURS
    energy_df = _energy_rows(n_hours=N_HOURS, start=start)
    energy_path, building_path = _write_csvs(tmp_path, energy_df, _building_row(timezone="US/Eastern"))

    try:
        result = external.run_external_inference(
            energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH
        )
    except external.ExternalDataError as e:
        assert "daylight" in str(e).lower() or "gap" in str(e).lower()
        return

    assert result.output["target_ts"].is_unique
    assert (result.output["generated_at"] < result.output["target_ts"]).all()


# --- Test 8: no retraining ---


def test_run_external_inference_never_calls_fit(tmp_path, pipeline):
    energy_path, building_path = _write_csvs(tmp_path, _energy_rows(), _building_row())
    with patch.object(
        RandomForestRegressor, "fit",
        side_effect=AssertionError("fit() was called during an inference-only test"),
    ) as mock_fit:
        external.run_external_inference(energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH)
    mock_fit.assert_not_called()


# --- Test 9: artifact unchanged ---


def test_artifact_file_unchanged_after_inference(tmp_path, pipeline):
    import os

    before = os.stat(external.DEFAULT_ARTIFACT_PATH)
    energy_path, building_path = _write_csvs(tmp_path, _energy_rows(), _building_row())
    external.run_external_inference(energy_path, building_path, pipeline, external.DEFAULT_ARTIFACT_PATH)
    after = os.stat(external.DEFAULT_ARTIFACT_PATH)

    assert before.st_mtime == after.st_mtime
    assert before.st_size == after.st_size
