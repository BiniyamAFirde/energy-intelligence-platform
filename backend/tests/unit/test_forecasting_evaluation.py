import math

import numpy as np
import pandas as pd

from energy_platform.forecasting.evaluation import (
    MIN_SAMPLES_FOR_GROUP_METRIC,
    compute_metrics,
    compute_metrics_by_group,
    compute_metrics_by_horizon,
    time_series_cv_alpha_search,
)


def test_compute_metrics_known_values():
    y_true = np.array([10.0, 20.0, 30.0, 40.0])
    y_pred = np.array([12.0, 18.0, 33.0, 36.0])
    result = compute_metrics(y_true, y_pred)
    errors = np.abs(y_true - y_pred)
    expected_mae = errors.mean()
    expected_rmse = np.sqrt((errors ** 2).mean())
    assert result["n"] == 4
    assert math.isclose(result["mae"], expected_mae)
    assert math.isclose(result["rmse"], expected_rmse)
    assert math.isclose(result["cv_rmse_pct"], expected_rmse / y_true.mean() * 100)


def test_compute_metrics_perfect_prediction_gives_r2_one():
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = compute_metrics(y, y)
    assert result["mae"] == 0.0
    assert result["rmse"] == 0.0
    assert result["r2"] == 1.0


def test_compute_metrics_drops_nan_pairs():
    y_true = np.array([10.0, np.nan, 30.0])
    y_pred = np.array([10.0, 20.0, np.nan])
    result = compute_metrics(y_true, y_pred)
    assert result["n"] == 1
    assert result["mae"] == 0.0


def test_compute_metrics_empty_returns_none():
    result = compute_metrics(np.array([]), np.array([]))
    assert result["n"] == 0
    assert result["mae"] is None


def test_compute_metrics_by_group_excludes_small_groups():
    n_small = MIN_SAMPLES_FOR_GROUP_METRIC - 1
    n_large = MIN_SAMPLES_FOR_GROUP_METRIC + 5
    df = pd.concat([
        pd.DataFrame({"grp": ["small"] * n_small, "y": [1.0] * n_small, "yhat": [1.0] * n_small}),
        pd.DataFrame({"grp": ["large"] * n_large, "y": [1.0] * n_large, "yhat": [1.0] * n_large}),
    ])
    result = compute_metrics_by_group(df, "y", "yhat", "grp")
    small_row = result[result.grp == "small"].iloc[0]
    large_row = result[result.grp == "large"].iloc[0]
    assert pd.isna(small_row["mae"])
    assert small_row["excluded_reason"] is not None
    assert not pd.isna(large_row["mae"])
    assert pd.isna(large_row["excluded_reason"])


def test_compute_metrics_by_horizon_sorted():
    df = pd.DataFrame({
        "horizon": [3, 1, 2] * MIN_SAMPLES_FOR_GROUP_METRIC,
        "y": [1.0] * (3 * MIN_SAMPLES_FOR_GROUP_METRIC),
        "yhat": [1.0] * (3 * MIN_SAMPLES_FOR_GROUP_METRIC),
    })
    result = compute_metrics_by_horizon(df, "y", "yhat")
    assert result["horizon"].tolist() == [1, 2, 3]


def test_time_series_cv_alpha_search_never_lets_validation_precede_training():
    """The core leakage guarantee of TimeSeriesSplit: for every fold, every
    validation index must come after every training index."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, 3))
    y = rng.normal(size=200)
    _, fold_results = time_series_cv_alpha_search(X, y, alphas=[1.0], n_splits=4)
    for _, row in fold_results.iterrows():
        train_max = row["train_idx_range"][1]
        val_min = row["val_idx_range"][0]
        assert train_max < val_min


def test_time_series_cv_alpha_search_picks_lower_rmse_alpha():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(100, 2))
    true_coef = np.array([5.0, -3.0])
    y = X @ true_coef + rng.normal(scale=0.01, size=100)
    best_alpha, _ = time_series_cv_alpha_search(X, y, alphas=[0.001, 1000.0], n_splits=3)
    # with near-noiseless linear data, heavy regularization (1000) should
    # underfit badly compared to almost none (0.001)
    assert best_alpha == 0.001
