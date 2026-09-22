import numpy as np
import pandas as pd
import pytest

from energy_platform.anomalies.detector_isolation_forest import (
    FEATURE_COLUMNS,
    build_features,
    complete_rows,
    detect_isolation_forest_anomalies,
    fit_isolation_forest,
    score_isolation_forest,
)


def _diurnal_series(n_sensors=1, n_days=60, seed=0, noise=0.2):
    rng = np.random.default_rng(seed)
    frames = []
    for sid in range(1, n_sensors + 1):
        ts = pd.date_range("2016-01-01", periods=n_days * 24, freq="h", tz="UTC")
        hour = ts.hour.to_numpy()
        base = 5 + 10 * np.sin(np.pi * np.clip(hour - 6, 0, 12) / 12)
        values = base + rng.normal(0, noise, len(ts))
        frames.append(pd.DataFrame({"sensor_id": sid, "ts": ts, "consumption_kwh": values}))
    return pd.concat(frames, ignore_index=True)


def test_build_features_has_expected_columns():
    series = _diurnal_series()
    features = build_features(series)
    assert set(FEATURE_COLUMNS) <= set(features.columns)
    assert {"sensor_id", "ts", "consumption_kwh"} <= set(features.columns)


def test_prev_hour_kwh_is_strictly_the_previous_hours_value():
    series = _diurnal_series(n_days=20)
    features = build_features(series).sort_values("ts").reset_index(drop=True)
    non_null = features.dropna(subset=["prev_hour_kwh"])
    for idx in non_null.index[:5]:
        if idx == 0:
            continue
        assert features.loc[idx, "prev_hour_kwh"] == pytest.approx(features.loc[idx - 1, "consumption_kwh"])


def test_complete_rows_drops_warmup_nans():
    series = _diurnal_series(n_days=3)  # far short of the ~14-day behavioral_z / 168h rolling warmup
    features = build_features(series)
    assert features[list(FEATURE_COLUMNS)].isna().any(axis=None)
    cleaned = complete_rows(features)
    assert not cleaned[list(FEATURE_COLUMNS)].isna().any(axis=None)
    assert len(cleaned) < len(features)


def test_fitting_on_train_slice_is_unaffected_by_validation_period_contamination():
    """Strongest possible no-leakage check: every feature is causal
    (shift-then-rolling / same-hour-of-day baseline), so corrupting a
    LATER (validation-period) timestamp must not change any EARLIER
    (train-period) row's computed features -- and therefore must not
    change what the model fits on. This proves it by actually corrupting
    validation data and confirming the fitted scaler/model are identical,
    not just by asserting the code "should" behave this way."""
    series = _diurnal_series(n_days=60)
    split_ts = series["ts"].iloc[40 * 24]  # last 20 days are the "validation" period

    features_clean = build_features(series)
    train_clean = complete_rows(features_clean[features_clean["ts"] < split_ts])
    scaler_clean, model_clean = fit_isolation_forest(train_clean, seed=42)

    corrupted = series.copy()
    corrupted.loc[corrupted["ts"] >= split_ts, "consumption_kwh"] = 9999.0
    features_corrupted = build_features(corrupted)
    train_corrupted = complete_rows(features_corrupted[features_corrupted["ts"] < split_ts])
    scaler_corrupted, model_corrupted = fit_isolation_forest(train_corrupted, seed=42)

    # train-period features themselves must be byte-identical
    pd.testing.assert_frame_equal(
        train_clean[list(FEATURE_COLUMNS)].reset_index(drop=True),
        train_corrupted[list(FEATURE_COLUMNS)].reset_index(drop=True),
    )
    assert np.allclose(scaler_clean.mean_, scaler_corrupted.mean_)
    assert np.allclose(scaler_clean.scale_, scaler_corrupted.scale_)

    # and the fitted models must score a fixed probe row identically
    probe = train_clean.iloc[[10]]
    score_clean = score_isolation_forest(scaler_clean, model_clean, probe)
    score_corrupted = score_isolation_forest(scaler_corrupted, model_corrupted, probe)
    assert score_clean == pytest.approx(score_corrupted)


def test_multivariate_outlier_scores_higher_than_typical_points():
    series = _diurnal_series(n_days=60)
    features = complete_rows(build_features(series))
    scaler, model = fit_isolation_forest(features, seed=42)

    typical_scores = score_isolation_forest(scaler, model, features.iloc[:50])

    outlier_row = features.iloc[[-1]].copy()
    outlier_row["consumption_kwh"] = 500.0  # wildly inconsistent with its own lag/rolling context
    outlier_score = score_isolation_forest(scaler, model, outlier_row)

    assert outlier_score[0] > typical_scores.max()


def test_detect_isolation_forest_anomalies_output_shape_and_dedup():
    series = _diurnal_series(n_days=60)
    features = complete_rows(build_features(series))
    scaler, model = fit_isolation_forest(features, seed=42)

    contaminated = series.copy()
    contaminated.loc[contaminated.index[-1], "consumption_kwh"] = 500.0

    scores = score_isolation_forest(scaler, model, complete_rows(build_features(contaminated)))
    low_threshold = float(np.percentile(scores, 50))  # deliberately low to force at least one hit

    result = detect_isolation_forest_anomalies(scaler, model, contaminated, score_threshold=low_threshold)
    assert len(result) > 0
    assert (result["method"] == "isolation_forest").all()
    assert (result["anomaly_type"] == "isolation_forest").all()
    assert (result["detector_version"] == "v1").all()
    key_counts = result.groupby(["sensor_id", "ts"]).size()
    assert (key_counts == 1).all()
    assert result.iloc[0]["explanation"]  # non-empty, concrete explanation string
