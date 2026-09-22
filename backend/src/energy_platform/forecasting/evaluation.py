"""Evaluation metrics and breakdowns. Pure functions on arrays/DataFrames
-- no model fitting here (models.py) and no DB access (dataset.py)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import TimeSeriesSplit

# Below this many samples, a per-group metric is reported as NaN rather
# than a number -- an MAE computed from 3 predictions is not a reliable
# per-building/per-primary_use comparison, and ranking on it would be
# actively misleading (Phase 7 spec, section 10).
MIN_SAMPLES_FOR_GROUP_METRIC = 30


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    y_true, y_pred = y_true[mask], y_pred[mask]

    n = len(y_true)
    if n == 0:
        return {"n": 0, "mae": None, "rmse": None, "r2": None, "cv_rmse_pct": None}

    mae = mean_absolute_error(y_true, y_pred)
    rmse = mean_squared_error(y_true, y_pred) ** 0.5
    r2 = r2_score(y_true, y_pred) if n >= 2 else None
    mean_actual = y_true.mean()
    cv_rmse = (rmse / mean_actual * 100) if mean_actual != 0 else None

    return {"n": n, "mae": mae, "rmse": rmse, "r2": r2, "cv_rmse_pct": cv_rmse}


def compute_metrics_by_group(df: pd.DataFrame, y_true_col: str, y_pred_col: str, group_col: str) -> pd.DataFrame:
    """One row per group value, with an `n` column and NaN metrics for any
    group under MIN_SAMPLES_FOR_GROUP_METRIC -- included in the output (so
    the caller can see it was excluded and why), not silently dropped."""
    rows = []
    for group_value, sub in df.groupby(group_col):
        n = len(sub)
        if n < MIN_SAMPLES_FOR_GROUP_METRIC:
            rows.append({group_col: group_value, "n": n, "mae": None, "rmse": None, "r2": None, "cv_rmse_pct": None,
                         "excluded_reason": f"n={n} < minimum {MIN_SAMPLES_FOR_GROUP_METRIC}"})
            continue
        metrics = compute_metrics(sub[y_true_col].values, sub[y_pred_col].values)
        rows.append({group_col: group_value, "excluded_reason": None, **metrics})
    return pd.DataFrame(rows)


def compute_metrics_by_horizon(df: pd.DataFrame, y_true_col: str, y_pred_col: str) -> pd.DataFrame:
    return compute_metrics_by_group(df, y_true_col, y_pred_col, "horizon").sort_values("horizon").reset_index(drop=True)


def time_series_cv_alpha_search(
    X: np.ndarray, y: np.ndarray, alphas: list[float], n_splits: int = 5
):
    """Expanding-window TimeSeriesSplit cross-validation to pick Ridge's
    regularization strength -- the one hyperparameter search this phase
    performs (a small grid, not "enormous"), and the concrete use of
    TimeSeriesSplit the spec requires. Returns (best_alpha, fold_results)
    where fold_results documents every fold's train/validation row-index
    boundaries, so they can be shown in docs/forecasting.md rather than
    just asserted to be correct."""
    from sklearn.linear_model import Ridge

    tscv = TimeSeriesSplit(n_splits=n_splits)
    fold_results = []
    for alpha in alphas:
        fold_rmses = []
        for fold_i, (train_idx, val_idx) in enumerate(tscv.split(X)):
            model = Ridge(alpha=alpha, random_state=42)
            model.fit(X[train_idx], y[train_idx])
            preds = model.predict(X[val_idx])
            rmse = mean_squared_error(y[val_idx], preds) ** 0.5
            fold_rmses.append(rmse)
            fold_results.append({
                "alpha": alpha, "fold": fold_i,
                "train_rows": len(train_idx), "val_rows": len(val_idx),
                "train_idx_range": (int(train_idx.min()), int(train_idx.max())),
                "val_idx_range": (int(val_idx.min()), int(val_idx.max())),
                "val_rmse": rmse,
            })
    results_df = pd.DataFrame(fold_results)
    mean_rmse_by_alpha = results_df.groupby("alpha")["val_rmse"].mean()
    best_alpha = mean_rmse_by_alpha.idxmin()
    return best_alpha, results_df
