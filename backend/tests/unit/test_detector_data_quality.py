import numpy as np
import pandas as pd
import pytest

from energy_platform.anomalies.detector_data_quality import detect_data_quality_anomalies


def _clean_series(n_sensors=3, n_hours=200, seed=0, base=10.0, noise=0.3):
    rng = np.random.default_rng(seed)
    frames = []
    for sid in range(1, n_sensors + 1):
        ts = pd.date_range("2017-07-01", periods=n_hours, freq="h", tz="UTC")
        values = base + rng.normal(0, noise, n_hours)
        frames.append(pd.DataFrame({"sensor_id": sid, "ts": ts, "consumption_kwh": values}))
    return pd.concat(frames, ignore_index=True)


def _set_value(df, sensor_id, idx_within_sensor, value):
    df = df.copy()
    sensor_rows = df[df.sensor_id == sensor_id].sort_values("ts")
    target_ts = sensor_rows["ts"].iloc[idx_within_sensor]
    df.loc[(df.sensor_id == sensor_id) & (df.ts == target_ts), "consumption_kwh"] = value
    return df, target_ts


def test_no_false_positives_on_clean_series():
    series = _clean_series()
    result = detect_data_quality_anomalies(series)
    assert len(result) == 0


def test_negative_value_detected():
    series = _clean_series()
    series, target_ts = _set_value(series, 1, 50, -5.0)
    result = detect_data_quality_anomalies(series)
    hits = result[result.anomaly_type == "negative_value"]
    assert len(hits) == 1
    assert hits.iloc[0]["ts"] == target_ts
    assert hits.iloc[0]["sensor_id"] == 1
    assert hits.iloc[0]["actual_value"] == pytest.approx(-5.0)
    assert "negative" in hits.iloc[0]["explanation"].lower()


def test_negative_values_absent_on_clean_data():
    # Per Phase 8 spec: real BDG2 data (once loaded/cleaned) is expected to
    # trigger zero negative_value findings -- this documents that a clean
    # result is the expected pass, not evidence the check is broken.
    series = _clean_series()
    result = detect_data_quality_anomalies(series)
    assert len(result[result.anomaly_type == "negative_value"]) == 0


def test_missing_value_detected():
    series = _clean_series()
    series, target_ts = _set_value(series, 2, 30, np.nan)
    result = detect_data_quality_anomalies(series)
    hits = result[result.anomaly_type == "missing"]
    assert len(hits) == 1
    assert hits.iloc[0]["ts"] == target_ts
    assert hits.iloc[0]["sensor_id"] == 2


def test_zero_run_detected_when_long_enough():
    series = _clean_series()
    sensor_rows = series[series.sensor_id == 1].sort_values("ts")
    zero_ts = sensor_rows["ts"].iloc[60:64]  # 4 consecutive hours >= MIN_ZERO_RUN_HOURS
    series.loc[(series.sensor_id == 1) & (series.ts.isin(zero_ts)), "consumption_kwh"] = 0.0

    result = detect_data_quality_anomalies(series)
    hits = result[result.anomaly_type == "zero_run"]
    assert len(hits) == 4
    assert set(hits["ts"]) == set(zero_ts)


def test_short_zero_run_not_flagged():
    series = _clean_series()
    sensor_rows = series[series.sensor_id == 1].sort_values("ts")
    zero_ts = sensor_rows["ts"].iloc[60:62]  # only 2 hours, below MIN_ZERO_RUN_HOURS=3
    series.loc[(series.sensor_id == 1) & (series.ts.isin(zero_ts)), "consumption_kwh"] = 0.0

    result = detect_data_quality_anomalies(series)
    assert len(result[result.anomaly_type == "zero_run"]) == 0


def test_stuck_meter_detected_when_long_enough():
    series = _clean_series()
    sensor_rows = series[series.sensor_id == 1].sort_values("ts")
    stuck_ts = sensor_rows["ts"].iloc[80:115]  # 35 consecutive hours >= MIN_STUCK_RUN_HOURS=30
    series.loc[(series.sensor_id == 1) & (series.ts.isin(stuck_ts)), "consumption_kwh"] = 12.345

    result = detect_data_quality_anomalies(series)
    hits = result[result.anomaly_type == "stuck_meter"]
    assert len(hits) == 35
    assert set(hits["ts"]) == set(stuck_ts)
    assert hits["actual_value"].tolist() == pytest.approx([12.345] * 35)


def test_short_stuck_run_not_flagged():
    series = _clean_series()
    sensor_rows = series[series.sensor_id == 1].sort_values("ts")
    stuck_ts = sensor_rows["ts"].iloc[80:85]  # 5 hours, well below MIN_STUCK_RUN_HOURS=30
    series.loc[(series.sensor_id == 1) & (series.ts.isin(stuck_ts)), "consumption_kwh"] = 12.345

    result = detect_data_quality_anomalies(series)
    assert len(result[result.anomaly_type == "stuck_meter"]) == 0


def test_one_calendar_day_flat_run_is_not_flagged_as_stuck_meter():
    """Regression test for the exact false-positive flood Phase 8's real
    evaluation run found: ~21/60 BDG2 sensors report daily-resolution
    readings (one value per calendar day, replicated across all 24
    hours), which is a structural data characteristic, not an anomaly.
    MIN_STUCK_RUN_HOURS=30 exists specifically so a single day's worth
    (24h) of a repeated value is never flagged -- if this regresses to 24
    or below, it reintroduces a ~35% false-positive rate against real
    data (see detector_data_quality.py's module docstring)."""
    series = _clean_series()
    sensor_rows = series[series.sensor_id == 1].sort_values("ts")
    one_day_ts = sensor_rows["ts"].iloc[80:104]  # exactly 24 consecutive hours
    series.loc[(series.sensor_id == 1) & (series.ts.isin(one_day_ts)), "consumption_kwh"] = 42.0

    result = detect_data_quality_anomalies(series)
    assert len(result[result.anomaly_type == "stuck_meter"]) == 0


def test_isolated_spike_detected():
    series = _clean_series()
    series, target_ts = _set_value(series, 1, 100, 80.0)  # ~10 baseline -> 80 is far above
    result = detect_data_quality_anomalies(series)
    hits = result[result.anomaly_type == "isolated_spike"]
    assert len(hits) == 1
    assert hits.iloc[0]["ts"] == target_ts
    assert hits.iloc[0]["actual_value"] == pytest.approx(80.0)


def test_sustained_elevated_span_not_flagged_as_isolated_spike():
    # Elevated neighbours mean this is a sustained shift, not a one-point
    # glitch -- that is Detector B/C's job, not Detector A's isolated_spike.
    series = _clean_series()
    sensor_rows = series[series.sensor_id == 1].sort_values("ts")
    span_ts = sensor_rows["ts"].iloc[98:103]
    series.loc[(series.sensor_id == 1) & (series.ts.isin(span_ts)), "consumption_kwh"] = 40.0

    result = detect_data_quality_anomalies(series)
    assert len(result[result.anomaly_type == "isolated_spike"]) == 0


def test_output_has_at_most_one_row_per_sensor_and_timestamp():
    series = _clean_series()
    series, _ = _set_value(series, 1, 50, -5.0)
    series, _ = _set_value(series, 2, 30, np.nan)
    sensor_rows = series[series.sensor_id == 3].sort_values("ts")
    zero_ts = sensor_rows["ts"].iloc[60:64]
    series.loc[(series.sensor_id == 3) & (series.ts.isin(zero_ts)), "consumption_kwh"] = 0.0

    result = detect_data_quality_anomalies(series)
    key_counts = result.groupby(["sensor_id", "ts"]).size()
    assert (key_counts == 1).all()


def test_output_has_method_and_detector_version_columns():
    series = _clean_series()
    series, _ = _set_value(series, 1, 50, -5.0)
    result = detect_data_quality_anomalies(series)
    assert (result["method"] == "data_quality").all()
    assert (result["detector_version"] == "v1").all()
