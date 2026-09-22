import numpy as np
import pandas as pd
import pytest

from energy_platform.anomalies.detector_forecast_residual import (
    STD_WINDOW,
    compute_residuals,
    detect_forecast_residual_anomalies,
    join_predictions_to_actuals,
)


def _clean_joined(n_hours=200, seed=0, noise=0.3, sensor_id=1):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2017-07-01", periods=n_hours, freq="h", tz="UTC")
    predicted = 10 + 2 * np.sin(np.linspace(0, 20, n_hours))
    actual = predicted + rng.normal(0, noise, n_hours)
    return pd.DataFrame({
        "sensor_id": sensor_id, "target_ts": ts, "predicted_kwh": predicted, "actual_kwh": actual,
    })


def test_join_uses_energy_measurements_actuals_not_predictions_actual_kwh():
    predictions_df = pd.DataFrame({
        "sensor_id": [1, 1], "target_ts": pd.to_datetime(["2017-07-01 00:00", "2017-07-01 01:00"], utc=True),
        "predicted_kwh": [10.0, 11.0],
    })
    actuals_df = pd.DataFrame({
        "sensor_id": [1, 1], "ts": pd.to_datetime(["2017-07-01 00:00", "2017-07-01 01:00"], utc=True),
        "consumption_kwh": [12.0, 9.0],
    })
    joined = join_predictions_to_actuals(predictions_df, actuals_df)
    assert list(joined["actual_kwh"]) == [12.0, 9.0]
    assert "predictions_actual_kwh" not in joined.columns


def test_join_drops_rows_with_no_matching_actual_or_null_actual():
    predictions_df = pd.DataFrame({
        "sensor_id": [1, 1, 1],
        "target_ts": pd.to_datetime(["2017-07-01 00:00", "2017-07-01 01:00", "2017-07-01 02:00"], utc=True),
        "predicted_kwh": [10.0, 11.0, 12.0],
    })
    actuals_df = pd.DataFrame({
        "sensor_id": [1, 1],
        "ts": pd.to_datetime(["2017-07-01 00:00", "2017-07-01 01:00"], utc=True),
        "consumption_kwh": [12.0, np.nan],  # 01:00 present but NULL; 02:00 missing entirely
    })
    joined = join_predictions_to_actuals(predictions_df, actuals_df)
    assert len(joined) == 1
    assert joined.iloc[0]["actual_kwh"] == 12.0


def test_absolute_and_relative_residual_signs_and_values():
    joined = pd.DataFrame({
        "sensor_id": [1, 1], "target_ts": pd.to_datetime(["2017-07-01 00:00", "2017-07-01 01:00"], utc=True),
        "predicted_kwh": [10.0, 10.0], "actual_kwh": [15.0, 5.0],
    })
    result = compute_residuals(joined)
    assert result["absolute_residual"].tolist() == pytest.approx([5.0, -5.0])
    # relative_residual = absolute_residual / actual: row0 = 5/15, row1 = -5/5
    assert result["relative_residual"].tolist() == pytest.approx([5.0 / 15.0, -1.0])


def test_standardized_residual_is_nan_before_warmup():
    joined = _clean_joined(n_hours=STD_WINDOW)  # exactly warmup-1 usable history at most
    result = compute_residuals(joined)
    assert result["standardized_residual"].isna().sum() >= STD_WINDOW - 1


def test_high_residual_flagged_when_actual_far_above_predicted():
    joined = _clean_joined(n_hours=300)
    joined.loc[joined.index[-1], "actual_kwh"] = joined.loc[joined.index[-1], "predicted_kwh"] + 100.0

    result = detect_forecast_residual_anomalies(joined, z_threshold=4.0)
    assert len(result) >= 1
    hit = result.iloc[0]
    assert hit["anomaly_type"] == "high_residual"
    assert hit["residual"] == pytest.approx(100.0)


def test_low_residual_flagged_when_actual_far_below_predicted():
    joined = _clean_joined(n_hours=300)
    joined.loc[joined.index[-1], "actual_kwh"] = joined.loc[joined.index[-1], "predicted_kwh"] - 100.0

    result = detect_forecast_residual_anomalies(joined, z_threshold=4.0)
    assert len(result) >= 1
    hit = result.iloc[0]
    assert hit["anomaly_type"] == "low_residual"


def test_no_false_positives_on_well_fit_clean_series():
    joined = _clean_joined(n_hours=300, noise=0.3)
    result = detect_forecast_residual_anomalies(joined, z_threshold=4.0)
    assert len(result) == 0


def test_output_has_method_and_detector_version_columns():
    joined = _clean_joined(n_hours=300)
    joined.loc[joined.index[-1], "actual_kwh"] = joined.loc[joined.index[-1], "predicted_kwh"] + 100.0
    result = detect_forecast_residual_anomalies(joined, z_threshold=4.0)
    assert len(result) > 0
    assert (result["method"] == "forecast_residual").all()
    assert (result["detector_version"] == "v1").all()


def test_output_has_at_most_one_row_per_sensor_and_timestamp():
    joined = _clean_joined(n_hours=300, noise=1.5)
    result = detect_forecast_residual_anomalies(joined, z_threshold=1.5)
    key_counts = result.groupby(["sensor_id", "ts"]).size()
    assert (key_counts == 1).all()
