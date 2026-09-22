"""python -m energy_platform.forecasting.train

Builds the training dataset, evaluates the seasonal-naive baselines, tunes
Ridge's alpha via TimeSeriesSplit (train-only), trains Random Forest and
HistGradientBoosting with fixed reasonable configurations, evaluates every
model on validation AND test, records one model_versions row per model
(so the choice is reproducible), saves ML model artifacts to models/
(gitignored), and writes a full comparison report to
data/processed/forecasting_training_report.json.

Model-selection criterion (decided here, before results are seen, per the
Phase 7 spec's explicit requirement): among candidates that beat BOTH
seasonal-naive baselines on validation RMSE, select the one with the lowest
validation RMSE. If no candidate beats both baselines, the honest
conclusion is that neither ML model earns its complexity over the naive
baseline -- reported as such, not hidden.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import joblib
import pandas as pd

from energy_platform.db.base import SessionLocal
from energy_platform.db.models import ModelVersion
from energy_platform.forecasting import baselines, dataset, evaluation, models

FEATURE_VERSION = "v1"
MODEL_VERSION = "v1"
RANDOM_SEED = 42
MODELS_DIR = Path("models")
REPORT_PATH = Path("data/processed/forecasting_training_report.json")

RIDGE_ALPHA_GRID = [0.1, 1.0, 10.0, 100.0]


def _evaluate_on(df: pd.DataFrame, y_pred, label: str) -> dict:
    df = df.copy()
    df["_pred"] = y_pred
    overall = evaluation.compute_metrics(df["target_kwh"], df["_pred"])
    by_horizon = evaluation.compute_metrics_by_horizon(df, "target_kwh", "_pred")
    by_building = evaluation.compute_metrics_by_group(df, "target_kwh", "_pred", "building_id")
    by_site = evaluation.compute_metrics_by_group(df, "target_kwh", "_pred", "site_id")
    by_primary_use = evaluation.compute_metrics_by_group(df, "target_kwh", "_pred", "primary_use")
    return {
        "split": label,
        "overall": overall,
        "by_horizon": by_horizon.to_dict(orient="records"),
        "by_building": by_building.to_dict(orient="records"),
        "by_site": by_site.to_dict(orient="records"),
        "by_primary_use": by_primary_use.to_dict(orient="records"),
    }


def _record_model_version(
    session, model_name: str, hyperparameters: dict, seed: int | None,
    metrics: dict, split_dates: dict, artifact_path: str | None, notes: str,
) -> None:
    row = ModelVersion(
        model_name=model_name,
        model_version=MODEL_VERSION,
        feature_version=FEATURE_VERSION,
        train_start=split_dates["train_start"],
        train_end=split_dates["train_end_excl"],
        validation_start=split_dates["train_end_excl"],
        validation_end=split_dates["validation_end_excl"],
        test_start=split_dates["validation_end_excl"],
        test_end=split_dates["test_end_excl"],
        hyperparameters=hyperparameters,
        random_seed=seed,
        metrics=metrics,
        artifact_path=artifact_path,
        notes=notes,
    )
    session.merge(row)  # merge, not add: reproducible re-runs of training overwrite the same (name, version) row rather than erroring
    session.commit()


def main() -> dict:
    session = SessionLocal()
    data = dataset.build_training_dataset(session)
    train, val, test = data["train"], data["validation"], data["test"]
    hourly_series = data["hourly_series"]
    split_dates = data["split_dates"]

    print(f"train={len(train)} val={len(val)} test={len(test)} rows "
          f"(extraction took {data['timing_seconds']['total']:.1f}s)")

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    all_results = {}

    # --- Baselines ---
    for name, season_hours in [("seasonal_naive_24h", 24), ("seasonal_naive_168h", 168)]:
        val_pred = baselines.seasonal_naive_forecast(hourly_series, val, season_hours)
        test_pred = baselines.seasonal_naive_forecast(hourly_series, test, season_hours)
        val_eval = _evaluate_on(val, val_pred, "validation")
        test_eval = _evaluate_on(test, test_pred, "test")
        all_results[name] = {"validation": val_eval, "test": test_eval}
        _record_model_version(
            session, name, hyperparameters={"season_hours": season_hours}, seed=None,
            metrics={"validation": val_eval["overall"], "test": test_eval["overall"]},
            split_dates=split_dates, artifact_path=None,
            notes="baseline: no training, no artifact",
        )
        print(f"{name}: val RMSE={val_eval['overall']['rmse']:.3f} test RMSE={test_eval['overall']['rmse']:.3f}")

    X_train, y_train = train[dataset.FEATURE_COLUMNS], train["target_kwh"]
    X_val = val[dataset.FEATURE_COLUMNS]
    X_test = test[dataset.FEATURE_COLUMNS]

    # --- Ridge: TimeSeriesSplit CV (train-only) to pick alpha ---
    preprocessor = models.build_preprocessor(scale_numeric=True)
    X_train_transformed = preprocessor.fit_transform(X_train)
    t0 = time.time()
    best_alpha, cv_fold_results = evaluation.time_series_cv_alpha_search(
        X_train_transformed, y_train.values, alphas=RIDGE_ALPHA_GRID, n_splits=5
    )
    print(f"Ridge alpha selected via TimeSeriesSplit: {best_alpha} ({time.time()-t0:.1f}s)")

    model_builders = {
        "ridge": lambda: models.build_ridge_pipeline(alpha=best_alpha, seed=RANDOM_SEED),
        "random_forest": lambda: models.build_random_forest_pipeline(seed=RANDOM_SEED),
        "hist_gradient_boosting": lambda: models.build_hist_gb_pipeline(seed=RANDOM_SEED),
    }
    hyperparams = {
        "ridge": {"alpha": best_alpha, "cv_alpha_grid": RIDGE_ALPHA_GRID},
        "random_forest": {"n_estimators": 100, "max_depth": 20, "min_samples_leaf": 5},
        "hist_gradient_boosting": {"defaults": "sklearn HistGradientBoostingRegressor defaults"},
    }

    for name, builder in model_builders.items():
        pipeline = builder()
        t0 = time.time()
        pipeline.fit(X_train, y_train)
        fit_seconds = time.time() - t0

        val_pred = pipeline.predict(X_val)
        test_pred = pipeline.predict(X_test)
        val_eval = _evaluate_on(val, val_pred, "validation")
        test_eval = _evaluate_on(test, test_pred, "test")
        all_results[name] = {"validation": val_eval, "test": test_eval, "fit_seconds": fit_seconds}

        artifact_path = MODELS_DIR / f"{name}_{MODEL_VERSION}.joblib"
        joblib.dump(pipeline, artifact_path)

        _record_model_version(
            session, name, hyperparameters=hyperparams[name], seed=RANDOM_SEED,
            metrics={"validation": val_eval["overall"], "test": test_eval["overall"]},
            split_dates=split_dates, artifact_path=str(artifact_path),
            notes=f"fit_seconds={fit_seconds:.1f}",
        )
        print(f"{name}: fit {fit_seconds:.1f}s, val RMSE={val_eval['overall']['rmse']:.3f} "
              f"test RMSE={test_eval['overall']['rmse']:.3f}")

    # --- Model selection (criterion documented above, applied mechanically) ---
    baseline_val_rmse = min(
        all_results["seasonal_naive_24h"]["validation"]["overall"]["rmse"],
        all_results["seasonal_naive_168h"]["validation"]["overall"]["rmse"],
    )
    ml_candidates = {
        k: v for k, v in all_results.items()
        if k not in ("seasonal_naive_24h", "seasonal_naive_168h")
        and v["validation"]["overall"]["rmse"] < baseline_val_rmse
    }
    if ml_candidates:
        selected = min(ml_candidates, key=lambda k: ml_candidates[k]["validation"]["overall"]["rmse"])
        selection_note = f"selected '{selected}': lowest validation RMSE among models beating both baselines"
    else:
        selected = None
        selection_note = "no ML model beat both seasonal-naive baselines on validation RMSE"
    print(selection_note)

    comparison_table = [
        {
            "model": name,
            "validation_mae": r["validation"]["overall"]["mae"],
            "validation_rmse": r["validation"]["overall"]["rmse"],
            "validation_r2": r["validation"]["overall"]["r2"],
            "validation_cv_rmse_pct": r["validation"]["overall"]["cv_rmse_pct"],
            "test_mae": r["test"]["overall"]["mae"],
            "test_rmse": r["test"]["overall"]["rmse"],
            "test_r2": r["test"]["overall"]["r2"],
            "test_cv_rmse_pct": r["test"]["overall"]["cv_rmse_pct"],
        }
        for name, r in all_results.items()
    ]

    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "feature_version": FEATURE_VERSION,
        "model_version": MODEL_VERSION,
        "random_seed": RANDOM_SEED,
        "split_dates": {k: str(v) for k, v in split_dates.items()},
        "row_counts": data["row_counts"],
        "extraction_timing_seconds": data["timing_seconds"],
        "ridge_cv_alpha_search": {
            "grid": RIDGE_ALPHA_GRID, "selected_alpha": best_alpha,
            "fold_boundaries": cv_fold_results.to_dict(orient="records"),
        },
        "comparison_table": comparison_table,
        "selection_criterion": (
            "Among models that beat BOTH seasonal-naive baselines (24h and 168h) "
            "on validation RMSE, select the lowest validation RMSE. Decided before "
            "results were computed."
        ),
        "selected_model": selected,
        "selection_note": selection_note,
        "full_results": all_results,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    print(f"Report written to {REPORT_PATH}")

    session.close()
    return report


if __name__ == "__main__":
    main()
