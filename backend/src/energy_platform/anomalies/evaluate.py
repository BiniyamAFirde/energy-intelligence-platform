"""python -m energy_platform.anomalies.evaluate

Phase 8 Task #20: evaluates all four anomaly detectors against the
synthetic injection framework (injection.py), selecting each
threshold-based detector's operating point on VALIDATION-period
injections only, freezing it, then reporting it against TEST-period
injections it was never tuned on. Detector A has no threshold to select
(five fixed, domain-reasoned rule constants) and is evaluated identically
on both splits.

Never touches Phase 7's model, split, or the real energy_measurements
table -- reads real historical data plus the already-backfilled
`predictions` table (Task #14), injects synthetic anomalies into a
scratch in-memory copy for scoring (evaluation.stitch_eval_series_for_split),
and writes only a report file plus (for Detector D) a genuinely fitted,
registered model_versions row -- fit on TRAIN-period real data only.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from energy_platform.anomalies import (
    detector_behavioral,
    detector_data_quality,
    detector_forecast_residual,
    detector_isolation_forest,
    evaluation,
)
from energy_platform.db.base import SessionLocal
from energy_platform.forecasting import dataset, train
from energy_platform.repositories import predictions as predictions_repo

REPORT_PATH = Path("data/processed/anomaly_evaluation_report.json")
VALIDATION_SEED = 42
TEST_SEED = 43
N_PER_TYPE = 15

Z_THRESHOLD_GRID = [round(x, 1) for x in np.arange(1.5, 8.1, 0.5)]
_IF_PERCENTILES = (80, 85, 90, 93, 95, 97, 98, 99, 99.5, 99.9)


def _percentile_grid(scores: np.ndarray) -> list[float]:
    return sorted({float(np.percentile(scores, p)) for p in _IF_PERCENTILES})


def _select_or_reuse_threshold(
    split_name: str, frozen: dict, key: str, scored: pd.DataFrame, score_col: str,
    ground_truth_keys: set, total_population: int, grid,
) -> dict | None:
    if split_name != "validation":
        return None
    sweep = evaluation.select_threshold_by_f1(
        scored, score_col, ["sensor_id", "ts"], ground_truth_keys, total_population, grid
    )
    frozen[key] = sweep["best"]["threshold"]
    return sweep


def main() -> dict:
    t0 = time.time()
    session = SessionLocal()
    data = dataset.build_training_dataset(session)
    hourly_series = data["hourly_series"]
    split_dates = data["split_dates"]
    print(f"Loaded hourly_series: {len(hourly_series)} rows ({time.time() - t0:.1f}s)")

    forecasting_model_name = json.loads(train.REPORT_PATH.read_text())["selected_model"]
    predictions_df = pd.DataFrame(
        predictions_repo.list_predictions(session, forecasting_model_name, train.MODEL_VERSION)
    )
    predictions_df["target_ts"] = pd.to_datetime(predictions_df["target_ts"], utc=True)
    # Prediction.predicted_kwh is a raw NUMERIC column -- SQLAlchemy Core
    # returns it as decimal.Decimal, same as energy.list_measurements_for_sensors'
    # consumption_kwh (which dataset.load_raw_frame converts the same way).
    predictions_df["predicted_kwh"] = predictions_df["predicted_kwh"].astype(float)
    print(f"Loaded {len(predictions_df)} backfilled predictions for {forecasting_model_name} {train.MODEL_VERSION}")

    t1 = time.time()
    fit_result = detector_isolation_forest.fit_and_register(session, seed=detector_isolation_forest.RANDOM_SEED)
    session.commit()  # fit_and_register only flushes (testable under the rollback fixture); this is the real commit boundary
    scaler, if_model = detector_isolation_forest.load_artifact(fit_result["artifact_path"])
    print(f"Isolation Forest fit on {fit_result['metrics']['training_rows']} TRAIN rows ({time.time() - t1:.1f}s)")

    report = {"splits": {}, "generated_at": pd.Timestamp.now("UTC").isoformat()}
    frozen_thresholds: dict = {}

    splits = {
        "validation": (split_dates["train_end_excl"], split_dates["validation_end_excl"], VALIDATION_SEED),
        "test": (split_dates["validation_end_excl"], split_dates["test_end_excl"], TEST_SEED),
    }

    for split_name, (start, end_excl, seed) in splits.items():
        t_split = time.time()
        eval_full, ground_truth = evaluation.stitch_eval_series_for_split(
            hourly_series, start, end_excl, split_label=split_name, seed=seed, n_per_type=N_PER_TYPE
        )
        split_slice = evaluation.restrict_to_split(eval_full, "ts", start, end_excl)
        total_population = len(split_slice)
        ground_truth_keys = set(zip(ground_truth["sensor_id"], ground_truth["ts"]))
        class_balance = evaluation.class_balance_report(split_slice, ground_truth)
        print(f"[{split_name}] injected {class_balance['injected_anomalies']} anomalies "
              f"into {class_balance['total_observation_slots']} slots "
              f"({class_balance['anomaly_rate_pct']}%)")

        # --- Detector A: fixed rules, no threshold to select ---
        a_out_full = detector_data_quality.detect_data_quality_anomalies(eval_full)
        a_out = evaluation.restrict_to_split(a_out_full, "ts", start, end_excl)

        # --- Detector B ---
        b_scored_full = detector_behavioral.compute_behavioral_z_scores(eval_full)
        b_scored_full["abs_z"] = b_scored_full["z_score"].abs()
        b_scored = evaluation.restrict_to_split(b_scored_full, "ts", start, end_excl)

        # --- Detector C: real backfilled predictions x this split's injected actuals ---
        c_joined_full = detector_forecast_residual.join_predictions_to_actuals(predictions_df, eval_full)
        c_scored_full = detector_forecast_residual.compute_residuals(c_joined_full).rename(columns={"target_ts": "ts"})
        c_scored_full["abs_z"] = c_scored_full["standardized_residual"].abs()
        c_scored = evaluation.restrict_to_split(c_scored_full, "ts", start, end_excl)

        # --- Detector D: score with the TRAIN-only-fitted model, never refit ---
        d_features_full = detector_isolation_forest.complete_rows(detector_isolation_forest.build_features(eval_full))
        d_scored_full = d_features_full.assign(
            score=detector_isolation_forest.score_isolation_forest(scaler, if_model, d_features_full)
        )
        d_scored = evaluation.restrict_to_split(d_scored_full, "ts", start, end_excl)

        sweep_b = _select_or_reuse_threshold(
            split_name, frozen_thresholds, "behavioral", b_scored, "abs_z", ground_truth_keys, total_population, Z_THRESHOLD_GRID
        )
        sweep_c = _select_or_reuse_threshold(
            split_name, frozen_thresholds, "forecast_residual", c_scored, "abs_z", ground_truth_keys, total_population, Z_THRESHOLD_GRID
        )
        sweep_d = _select_or_reuse_threshold(
            split_name, frozen_thresholds, "isolation_forest", d_scored, "score", ground_truth_keys,
            total_population, _percentile_grid(d_scored["score"].to_numpy()),
        )
        if split_name == "validation":
            report["threshold_selection"] = {
                "behavioral": sweep_b, "forecast_residual": sweep_c, "isolation_forest": sweep_d,
            }

        b_out = b_scored[b_scored["abs_z"] >= frozen_thresholds["behavioral"]][["sensor_id", "ts"]]
        c_out = c_scored[c_scored["abs_z"] >= frozen_thresholds["forecast_residual"]][["sensor_id", "ts"]]
        d_out = d_scored[d_scored["score"] >= frozen_thresholds["isolation_forest"]][["sensor_id", "ts"]]

        report["splits"][split_name] = {
            "class_balance": class_balance,
            "frozen_thresholds": dict(frozen_thresholds),
            "detectors": {
                "data_quality": evaluation.evaluate_predictions(ground_truth, a_out, total_population),
                "behavioral": evaluation.evaluate_predictions(ground_truth, b_out, total_population),
                "forecast_residual": evaluation.evaluate_predictions(ground_truth, c_out, total_population),
                "isolation_forest": evaluation.evaluate_predictions(ground_truth, d_out, total_population),
            },
        }
        print(f"[{split_name}] done in {time.time() - t_split:.1f}s")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    session.close()
    print(f"Report written to {REPORT_PATH} (total {time.time() - t0:.1f}s)")
    return report


if __name__ == "__main__":
    main()
