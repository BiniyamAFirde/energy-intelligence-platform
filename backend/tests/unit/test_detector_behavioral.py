import numpy as np
import pandas as pd
import pytest

from energy_platform.anomalies.detector_behavioral import (
    BASELINE_WINDOW,
    compute_behavioral_z_scores,
    detect_behavioral_anomalies,
)


def _diurnal_series(n_sensors=2, n_days=40, seed=0, noise=0.2):
    """A realistic daily cycle (low at night, high in the day) with small
    noise, repeated for n_days -- the case that would falsely trigger a
    naive blind-rolling-window z-score (comparing 2am to daytime hours),
    but should NOT trigger the hour-of-day-grouped detector."""
    rng = np.random.default_rng(seed)
    frames = []
    for sid in range(1, n_sensors + 1):
        ts = pd.date_range("2017-07-01", periods=n_days * 24, freq="h", tz="UTC")
        hour = ts.hour.to_numpy()
        base = 5 + 10 * np.sin(np.pi * np.clip(hour - 6, 0, 12) / 12)  # low at night, peak midday
        values = base + rng.normal(0, noise, len(ts))
        frames.append(pd.DataFrame({"sensor_id": sid, "ts": ts, "consumption_kwh": values}))
    return pd.concat(frames, ignore_index=True)


def test_diurnal_cycle_produces_only_rare_false_positives():
    """The key claim this detector makes is that grouping by hour-of-day
    prevents normal diurnal swings (5 kWh at night vs 15 kWh at midday)
    from being flagged just because they differ from each other -- not
    that a threshold of exactly 4 robust-sigma yields literally zero hits.
    With only BASELINE_WINDOW=14 same-hour observations feeding each
    baseline, the MAD estimate itself has real small-sample variance, so a
    handful of false positives from pure noise is expected and is what
    Task #20's validation-tuned threshold accounts for -- this asserts the
    rate stays low (not that diurnal structure itself gets flagged)."""
    series = _diurnal_series()
    result = detect_behavioral_anomalies(series, z_threshold=4.0)
    assert len(result) / len(series) < 0.01


def test_spike_at_one_hour_is_flagged():
    series = _diurnal_series()
    # push one 14:00 reading far above every other 14:00 reading for that sensor
    sensor1 = series[series.sensor_id == 1].sort_values("ts")
    target_ts = sensor1[sensor1.ts.dt.hour == 14]["ts"].iloc[-1]
    series.loc[(series.sensor_id == 1) & (series.ts == target_ts), "consumption_kwh"] = 200.0

    result = detect_behavioral_anomalies(series, z_threshold=4.0)
    hits = result[(result.sensor_id == 1) & (result.ts == target_ts)]
    assert len(hits) == 1
    assert hits.iloc[0]["anomaly_type"] == "behavioral_deviation"
    assert hits.iloc[0]["actual_value"] == pytest.approx(200.0)


def test_baseline_only_uses_strictly_earlier_same_hour_observations():
    series = _diurnal_series(n_sensors=1, n_days=20)
    scored = compute_behavioral_z_scores(series)

    sensor_rows = scored[(scored.sensor_id == 1) & (scored.hour_of_day == 9)].sort_values("ts").reset_index(drop=True)
    # pick a row with enough history and manually recompute the expected baseline
    row_idx = BASELINE_WINDOW + 3
    prior_values = sensor_rows["consumption_kwh"].iloc[row_idx - BASELINE_WINDOW: row_idx].to_numpy()
    expected_median = np.median(prior_values)
    expected_mad = np.median(np.abs(prior_values - expected_median))

    actual_row = sensor_rows.iloc[row_idx]
    assert actual_row["baseline_median"] == pytest.approx(expected_median)
    assert actual_row["baseline_mad"] == pytest.approx(expected_mad)

    # the baseline window (computed via shift(1) within the same-hour group)
    # must be exactly the BASELINE_WINDOW values strictly before row_idx --
    # never including row_idx's own value.
    grouped = series[(series.sensor_id == 1) & (series.ts.dt.hour == 9)].sort_values("ts").reset_index(drop=True)
    shifted = grouped["consumption_kwh"].shift(1)
    window_from_shifted = shifted.iloc[row_idx - BASELINE_WINDOW + 1: row_idx + 1].to_numpy()
    assert np.allclose(np.sort(window_from_shifted), np.sort(prior_values))


def test_insufficient_history_is_not_scored():
    series = _diurnal_series(n_sensors=1, n_days=5)  # fewer than BASELINE_WINDOW occurrences per hour
    scored = compute_behavioral_z_scores(series)
    assert scored["z_score"].isna().all()
    result = detect_behavioral_anomalies(series, z_threshold=4.0)
    assert len(result) == 0


def test_zero_mad_window_does_not_raise_and_is_not_scored():
    # every prior occurrence of hour 3 is bit-identical -> MAD = 0 -> would
    # be a division by zero without the guard.
    ts = pd.date_range("2017-07-01", periods=30 * 24, freq="h", tz="UTC")
    values = np.full(len(ts), 10.0)
    series = pd.DataFrame({"sensor_id": 1, "ts": ts, "consumption_kwh": values})

    scored = compute_behavioral_z_scores(series)
    hour3 = scored[scored.hour_of_day == 3]
    assert hour3["baseline_mad"].dropna().eq(0.0).all()
    assert hour3["z_score"].isna().all()

    result = detect_behavioral_anomalies(series, z_threshold=4.0)
    assert len(result) == 0


def test_output_has_at_most_one_row_per_sensor_and_timestamp():
    series = _diurnal_series()
    result = detect_behavioral_anomalies(series, z_threshold=1.5)  # low threshold -> many hits
    key_counts = result.groupby(["sensor_id", "ts"]).size()
    assert (key_counts == 1).all()


def test_output_has_method_and_detector_version_columns():
    series = _diurnal_series()
    sensor1 = series[series.sensor_id == 1].sort_values("ts")
    target_ts = sensor1[sensor1.ts.dt.hour == 14]["ts"].iloc[-1]
    series.loc[(series.sensor_id == 1) & (series.ts == target_ts), "consumption_kwh"] = 200.0

    result = detect_behavioral_anomalies(series, z_threshold=4.0)
    assert len(result) > 0
    assert (result["method"] == "behavioral").all()
    assert (result["detector_version"] == "v1").all()
