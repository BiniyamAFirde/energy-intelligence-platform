"""python -m energy_platform.anomalies.detect

Phase 8: runs all four anomaly detectors against real (non-injected)
current data and persists the results to the `alerts` table via an
idempotent upsert. Threshold-based detectors (B, C, D) use the exact
thresholds Task #20's evaluation selected on VALIDATION-period synthetic
injections and froze (data/processed/anomaly_evaluation_report.json) --
never re-tuned here, and never touching TEST-period injections at all.
Detector D scores with the existing TRAIN-only-fitted Isolation Forest
artifact (models/isolation_forest_v1.joblib, written by evaluate.py's
fit_and_register call) -- this script never fits or refits anything.
"""

from __future__ import annotations

import json
import time

import pandas as pd

from energy_platform.anomalies import (
    detector_behavioral,
    detector_data_quality,
    detector_forecast_residual,
    detector_isolation_forest,
)
from energy_platform.anomalies.evaluate import REPORT_PATH as EVAL_REPORT_PATH
from energy_platform.db.base import SessionLocal
from energy_platform.forecasting import dataset, train
from energy_platform.repositories import alerts as alerts_repo
from energy_platform.repositories import predictions as predictions_repo


def _load_frozen_thresholds() -> dict:
    if not EVAL_REPORT_PATH.exists():
        raise SystemExit(
            f"No evaluation report at {EVAL_REPORT_PATH}. Run "
            "`python -m energy_platform.anomalies.evaluate` first -- detect.py "
            "reuses its frozen validation-selected thresholds rather than picking its own."
        )
    report = json.loads(EVAL_REPORT_PATH.read_text())
    return report["splits"]["validation"]["frozen_thresholds"]


def _rows_from_detector_output(df: pd.DataFrame) -> list[dict]:
    if len(df) == 0:
        return []
    df = df.copy()
    if "residual" not in df.columns:
        df["residual"] = None
    rows = []
    for r in df.itertuples():
        rows.append({
            "sensor_id": int(r.sensor_id),
            "ts": r.ts.to_pydatetime() if hasattr(r.ts, "to_pydatetime") else r.ts,
            "method": r.method,
            "anomaly_type": r.anomaly_type,
            "detector_version": r.detector_version,
            "severity": r.severity,
            "score": float(r.score),
            "expected_value": float(r.expected_value) if pd.notna(r.expected_value) else None,
            "actual_value": float(r.actual_value) if pd.notna(r.actual_value) else None,
            "residual": float(r.residual) if pd.notna(r.residual) else None,
            "explanation": r.explanation,
        })
    return rows


def main() -> dict:
    t0 = time.time()
    thresholds = _load_frozen_thresholds()
    session = SessionLocal()

    data = dataset.build_training_dataset(session)
    hourly_series = data["hourly_series"]
    print(f"Loaded hourly_series: {len(hourly_series)} rows ({time.time() - t0:.1f}s)")

    forecasting_model_name = json.loads(train.REPORT_PATH.read_text())["selected_model"]
    predictions_df = pd.DataFrame(
        predictions_repo.list_predictions(session, forecasting_model_name, train.MODEL_VERSION)
    )
    predictions_df["target_ts"] = pd.to_datetime(predictions_df["target_ts"], utc=True)
    predictions_df["predicted_kwh"] = predictions_df["predicted_kwh"].astype(float)

    artifact_path = (
        detector_isolation_forest.MODELS_DIR
        / f"isolation_forest_{detector_isolation_forest.DETECTOR_VERSION}.joblib"
    )
    if not artifact_path.exists():
        raise SystemExit(
            f"No Isolation Forest artifact at {artifact_path}. Run "
            "`python -m energy_platform.anomalies.evaluate` first -- it fits and "
            "registers this artifact; detect.py only scores with it, never fits."
        )
    scaler, if_model = detector_isolation_forest.load_artifact(artifact_path)

    all_rows: list[dict] = []
    counts: dict[str, int] = {}

    a_out = detector_data_quality.detect_data_quality_anomalies(hourly_series)
    all_rows += _rows_from_detector_output(a_out)
    counts["data_quality"] = len(a_out)
    print(f"Detector A (data_quality): {len(a_out)} alerts")

    b_out = detector_behavioral.detect_behavioral_anomalies(hourly_series, z_threshold=thresholds["behavioral"])
    all_rows += _rows_from_detector_output(b_out)
    counts["behavioral"] = len(b_out)
    print(f"Detector B (behavioral): {len(b_out)} alerts (threshold={thresholds['behavioral']})")

    c_joined = detector_forecast_residual.join_predictions_to_actuals(predictions_df, hourly_series)
    c_out = detector_forecast_residual.detect_forecast_residual_anomalies(
        c_joined, z_threshold=thresholds["forecast_residual"]
    )
    all_rows += _rows_from_detector_output(c_out)
    counts["forecast_residual"] = len(c_out)
    print(f"Detector C (forecast_residual): {len(c_out)} alerts (threshold={thresholds['forecast_residual']})")

    d_out = detector_isolation_forest.detect_isolation_forest_anomalies(
        scaler, if_model, hourly_series, score_threshold=thresholds["isolation_forest"]
    )
    all_rows += _rows_from_detector_output(d_out)
    counts["isolation_forest"] = len(d_out)
    print(f"Detector D (isolation_forest): {len(d_out)} alerts (threshold={thresholds['isolation_forest']:.4f})")

    n_written = alerts_repo.upsert_alerts(session, all_rows)
    session.commit()
    session.close()

    summary = {
        "thresholds": thresholds,
        "alerts_by_method": counts,
        "total_alerts_written": n_written,
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    main()
