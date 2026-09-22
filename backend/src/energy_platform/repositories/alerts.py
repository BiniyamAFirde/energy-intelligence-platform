"""Idempotent writes and filtered reads against the alerts table (Phase
8). Upserts follow the same ON CONFLICT DO UPDATE pattern as
predictions_repo.upsert_predictions, keyed on the table's own idempotency
constraint (sensor_id, ts, method, detector_version) -- re-running a
detector for the same point just refreshes that row instead of
duplicating it."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from energy_platform.db.models import Alert

CHUNK_SIZE = 20_000

_UPDATE_COLUMNS = (
    "anomaly_type", "severity", "score", "expected_value",
    "actual_value", "residual", "explanation",
)


def upsert_alerts(session: Session, rows: list[dict]) -> int:
    """Each row: sensor_id, ts, method, anomaly_type, detector_version,
    severity, score, expected_value, actual_value, residual, explanation.
    `resolved` is deliberately never part of the upsert conflict update --
    a re-run of a detector must not silently un-resolve an alert a human
    already triaged, so `resolved` is only ever changed via a dedicated
    resolve action (not implemented here -- Phase 8's scope is detection
    and read access, not a triage workflow)."""
    if not rows:
        return 0

    inserted = 0
    for i in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[i : i + CHUNK_SIZE]
        stmt = insert(Alert).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Alert.sensor_id, Alert.ts, Alert.method, Alert.detector_version],
            set_={col: getattr(stmt.excluded, col) for col in _UPDATE_COLUMNS},
        )
        session.execute(stmt)
        inserted += len(chunk)
    return inserted


def list_alerts(
    session: Session,
    limit: int,
    offset: int,
    sensor_id: int | None = None,
    method: str | None = None,
    anomaly_type: str | None = None,
    severity: str | None = None,
    resolved: bool | None = None,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> tuple[list[Alert], int]:
    stmt = select(Alert)
    count_stmt = select(func.count()).select_from(Alert)

    filters = []
    if sensor_id is not None:
        filters.append(Alert.sensor_id == sensor_id)
    if method is not None:
        filters.append(Alert.method == method)
    if anomaly_type is not None:
        filters.append(Alert.anomaly_type == anomaly_type)
    if severity is not None:
        filters.append(Alert.severity == severity)
    if resolved is not None:
        filters.append(Alert.resolved == resolved)
    if start is not None:
        filters.append(Alert.ts >= start)
    if end is not None:
        filters.append(Alert.ts <= end)

    for f in filters:
        stmt = stmt.where(f)
        count_stmt = count_stmt.where(f)

    total = session.execute(count_stmt).scalar_one()
    items = session.execute(
        stmt.order_by(Alert.ts.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return list(items), total


def get_alert(session: Session, alert_id: int) -> Alert | None:
    return session.get(Alert, alert_id)


def summary(
    session: Session, start: dt.datetime | None = None, end: dt.datetime | None = None
) -> dict:
    """Aggregate counts for the /alerts/summary endpoint: total,
    unresolved, and breakdowns by method/anomaly_type/severity, all under
    the same optional date-range filter."""
    filters = []
    if start is not None:
        filters.append(Alert.ts >= start)
    if end is not None:
        filters.append(Alert.ts <= end)

    total_stmt = select(func.count()).select_from(Alert)
    unresolved_stmt = select(func.count()).select_from(Alert).where(Alert.resolved == False)  # noqa: E712
    for f in filters:
        total_stmt = total_stmt.where(f)
        unresolved_stmt = unresolved_stmt.where(f)

    def _counts_by(col) -> dict:
        stmt = select(col, func.count()).select_from(Alert).group_by(col)
        for f in filters:
            stmt = stmt.where(f)
        return dict(session.execute(stmt).all())

    return {
        "total_alerts": session.execute(total_stmt).scalar_one(),
        "unresolved_alerts": session.execute(unresolved_stmt).scalar_one(),
        "counts_by_method": _counts_by(Alert.method),
        "counts_by_anomaly_type": _counts_by(Alert.anomaly_type),
        "counts_by_severity": _counts_by(Alert.severity),
    }
