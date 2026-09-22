"""python -m energy_platform.forecasting.predict [model_name]

Loads an already-trained model artifact (never retrains), generates the
next 24 hours for every building from each building's latest available
data, and writes the predictions to PostgreSQL via an idempotent upsert.
model_name defaults to whichever model train.py's report selected; pass one
explicitly to override (e.g. `python -m energy_platform.forecasting.predict
seasonal_naive_24h`, which needs no artifact file at all).

generated_at is stamped per-row as that row's own forecast origin timestamp
-- not datetime.now(). This system operates on a fixed 2016-2018 historical
dataset rather than a live feed, so real wall-clock time is always *after*
every target_ts it could ever produce; using it would make generated_at <
target_ts false for every row, the opposite of what that invariant is
supposed to prove. generated_at = origin_ts is correct in both this
backtest context and a live one (where the origin naturally *is*
approximately "now"), and is always strictly before target_ts = origin_ts +
horizon hours. This is the exact invariant the leakage test suite asserts
(test_forecasting_leakage.py::test_prediction_generated_at_precedes_target_ts).
"""

from __future__ import annotations

import json
import sys

import joblib
import pandas as pd

from energy_platform.db.base import SessionLocal
from energy_platform.forecasting import baselines, dataset, train
from energy_platform.repositories import predictions as predictions_repo

BASELINE_MODELS = {"seasonal_naive_24h": 24, "seasonal_naive_168h": 168}


def _load_model_name_from_report() -> str:
    if not train.REPORT_PATH.exists():
        raise SystemExit(
            f"No training report at {train.REPORT_PATH} and no model name given. "
            "Run `python -m energy_platform.forecasting.train` first, or pass a model name."
        )
    report = json.loads(train.REPORT_PATH.read_text())
    selected = report.get("selected_model")
    if selected is None:
        raise SystemExit(
            "No ML model beat both baselines in the last training run "
            f"({report.get('selection_note')}); refusing to auto-select. "
            "Pass a model name explicitly, e.g. 'seasonal_naive_24h'."
        )
    return selected


def _predict_with_model(session, model_name: str, request_df: pd.DataFrame):
    """Shared by generate_predictions (latest origin) and
    generate_predictions_for_range (Phase 8's historical backfill): scores
    an already-built request frame with either a seasonal-naive baseline
    (no artifact needed) or a saved model artifact. Never trains anything."""
    if model_name in BASELINE_MODELS:
        raw_df = dataset.load_raw_frame(session)
        hourly_series = dataset.reindex_to_hourly_grid(
            raw_df[["sensor_id", "ts", "consumption_kwh"]].sort_values(["sensor_id", "ts"]),
            "ts", group_col="sensor_id",
        )
        return baselines.seasonal_naive_forecast(hourly_series, request_df, BASELINE_MODELS[model_name])

    artifact_path = train.MODELS_DIR / f"{model_name}_{train.MODEL_VERSION}.joblib"
    if not artifact_path.exists():
        raise SystemExit(f"No saved model artifact at {artifact_path}. Run train.py first.")
    pipeline = joblib.load(artifact_path)
    return pipeline.predict(request_df[dataset.FEATURE_COLUMNS])


def _rows_from_predictions(request_df: pd.DataFrame, preds, model_name: str) -> list[dict]:
    # generated_at = each row's own forecast origin, not wall-clock
    # datetime.now(). This system operates on a fixed 2016-2018 historical
    # dataset (not a live stream): wall-clock "now" is 2026, which is
    # *after* every target_ts in that dataset -- using it would make
    # generated_at < target_ts false for every row, backwards from what
    # it's supposed to prove. generated_at = origin_ts is what the phase's
    # own example implies (an origin-aligned hour, not a real timestamp)
    # and is correct in both this backtest context and a live one (where
    # the origin naturally *is* approximately "now").
    rows = []
    for (_, row), pred in zip(request_df.iterrows(), preds):
        if pd.isna(pred):
            continue
        rows.append({
            "sensor_id": int(row["sensor_id"]),
            "target_ts": row["target_ts"].to_pydatetime(),
            "generated_at": row["origin_ts"].to_pydatetime(),
            "model_name": model_name,
            "model_version": train.MODEL_VERSION,
            "horizon": int(row["horizon"]),
            "predicted_kwh": float(pred),
        })
    return rows


def generate_predictions(session, model_name: str) -> list[dict]:
    request_df = dataset.build_prediction_request(session)
    preds = _predict_with_model(session, model_name, request_df)
    return _rows_from_predictions(request_df, preds, model_name)


def generate_predictions_for_range(
    session, model_name: str, origin_start: pd.Timestamp, origin_end_excl: pd.Timestamp
) -> list[dict]:
    """Phase 8's historical backfill: scores every origin in
    [origin_start, origin_end_excl) with an already-trained model artifact
    -- no retraining, no change to Phase 7's model or split. Used by the
    forecast-residual anomaly detector, which needs prediction coverage
    over a full historical period rather than just the single most-recent
    origin `generate_predictions` covers."""
    request_df = dataset.build_prediction_request_for_range(session, origin_start, origin_end_excl)
    preds = _predict_with_model(session, model_name, request_df)
    return _rows_from_predictions(request_df, preds, model_name)


def main(model_name: str | None = None) -> dict:
    model_name = model_name or _load_model_name_from_report()
    session = SessionLocal()
    try:
        rows = generate_predictions(session, model_name)
        n_written = predictions_repo.upsert_predictions(session, rows)
        session.commit()
    finally:
        session.close()

    summary = {"model_name": model_name, "model_version": train.MODEL_VERSION, "predictions_written": n_written}
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
