"""Idempotent writes to the predictions table -- same ON CONFLICT DO UPDATE
pattern as ingestion/loader.py's upserts (Phase 4), applied here so
re-running a prediction job for the same model/version/target never
duplicates rows, just refreshes them."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from energy_platform.db.models import Prediction

CHUNK_SIZE = 20_000


def list_predictions_for_sensor(
    session: Session,
    sensor_id: int,
    model_name: str,
    model_version: str,
    limit: int,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> list[dict]:
    """One sensor's predictions for one model/version, optionally windowed
    by target_ts -- feeds the /buildings/{id}/forecast API endpoint (Phase
    9). Unlike list_predictions (Detector C's bulk cross-sensor fetch),
    this is scoped to a single sensor/date-range, matching energy.
    list_measurements' convention for the same reason: a single-building
    API request should never pull every sensor's rows."""
    stmt = (
        select(
            Prediction.target_ts, Prediction.generated_at,
            Prediction.horizon, Prediction.predicted_kwh,
        )
        .where(
            Prediction.sensor_id == sensor_id,
            Prediction.model_name == model_name,
            Prediction.model_version == model_version,
        )
    )
    if start is not None:
        stmt = stmt.where(Prediction.target_ts >= start)
    if end is not None:
        stmt = stmt.where(Prediction.target_ts <= end)
    stmt = stmt.order_by(Prediction.target_ts).limit(limit)
    return [dict(r) for r in session.execute(stmt).mappings().all()]


def list_predictions(session: Session, model_name: str, model_version: str) -> list[dict]:
    """Bulk fetch of every (sensor_id, target_ts, predicted_kwh) row for
    one model/version -- feeds Detector C (forecast-residual anomalies),
    which joins this against energy_measurements itself rather than
    reading the unused predictions.actual_kwh column. Plain dicts from a
    column-level select, not ORM objects, matching
    energy.list_measurements_for_sensors' bulk-read convention."""
    stmt = (
        select(Prediction.sensor_id, Prediction.target_ts, Prediction.predicted_kwh)
        .where(Prediction.model_name == model_name, Prediction.model_version == model_version)
        .order_by(Prediction.sensor_id, Prediction.target_ts)
    )
    return [dict(r) for r in session.execute(stmt).mappings().all()]


def upsert_predictions(session: Session, rows: list[dict]) -> int:
    """Each row: sensor_id, target_ts, generated_at, model_name,
    model_version, horizon, predicted_kwh. Upserts on the table's existing
    unique constraint (sensor_id, target_ts, model_name, model_version) --
    a later prediction run for the same model/version/target *refreshes*
    generated_at and predicted_kwh rather than erroring or duplicating."""
    if not rows:
        return 0

    inserted = 0
    for i in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[i : i + CHUNK_SIZE]
        stmt = insert(Prediction).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                Prediction.sensor_id, Prediction.target_ts,
                Prediction.model_name, Prediction.model_version,
            ],
            set_={
                "generated_at": stmt.excluded.generated_at,
                "predicted_kwh": stmt.excluded.predicted_kwh,
                "horizon": stmt.excluded.horizon,
            },
        )
        session.execute(stmt)
        inserted += len(chunk)
    return inserted
