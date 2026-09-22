"""Business logic for the forecast endpoint (Phase 9): resolves a building
to its sensor, determines which model's predictions to serve (whichever
train.py's report currently selects -- read at request time, same file
evaluate.py/detect.py already read directly rather than train.py's own
private _load_model_name_from_report), and joins predicted values against
real energy_measurements for actual/residual -- never the unused
predictions.actual_kwh column, the same principle Phase 8's
forecast-residual detector established."""

from __future__ import annotations

import datetime as dt
import json

from sqlalchemy.orm import Session

from energy_platform.forecasting import train
from energy_platform.repositories import energy as energy_repo
from energy_platform.repositories import predictions as predictions_repo
from energy_platform.services.exceptions import NotFoundError
from energy_platform.services.resolvers import resolve_building_and_sensor
from energy_platform.services.validation import validate_date_range


def _selected_model() -> tuple[str, str]:
    if not train.REPORT_PATH.exists():
        raise NotFoundError("No forecasting model has been trained yet (no training report found)")
    report = json.loads(train.REPORT_PATH.read_text())
    model_name = report.get("selected_model")
    if model_name is None:
        raise NotFoundError(
            f"No ML model beat both baselines in the last training run ({report.get('selection_note')})"
        )
    return model_name, train.MODEL_VERSION


def get_building_forecast(
    session: Session,
    building_id: int,
    limit: int,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> list[dict]:
    _, sensor_id = resolve_building_and_sensor(session, building_id)
    start_utc, end_utc = validate_date_range(start, end)
    model_name, model_version = _selected_model()

    pred_rows = predictions_repo.list_predictions_for_sensor(
        session, sensor_id, model_name, model_version, limit, start_utc, end_utc
    )
    if not pred_rows:
        return []

    target_tss = [r["target_ts"] for r in pred_rows]
    actual_rows = energy_repo.list_measurements(
        session, sensor_id, limit=len(target_tss), start=min(target_tss), end=max(target_tss)
    )
    actual_by_ts = {r.ts: r.consumption_kwh for r in actual_rows}

    results = []
    for r in pred_rows:
        actual = actual_by_ts.get(r["target_ts"])
        actual_f = float(actual) if actual is not None else None
        predicted_f = float(r["predicted_kwh"])
        results.append({
            "sensor_id": sensor_id,
            "target_ts": r["target_ts"],
            "generated_at": r["generated_at"],
            "model_name": model_name,
            "model_version": model_version,
            "horizon": r["horizon"],
            "predicted_kwh": predicted_f,
            "actual_kwh": actual_f,
            "residual": (actual_f - predicted_f) if actual_f is not None else None,
        })
    return results
