"""Business logic for the alert (anomaly detection) endpoints (Phase 8):
resolves a building to its sensor, validates the date range, and shapes
repository output -- mirrors energy_service.py's role for the energy
endpoints."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from energy_platform.db.models import Alert
from energy_platform.repositories import alerts as alerts_repo
from energy_platform.services.exceptions import NotFoundError
from energy_platform.services.resolvers import resolve_building_and_sensor
from energy_platform.services.validation import validate_date_range


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
    start_utc, end_utc = validate_date_range(start, end)
    return alerts_repo.list_alerts(
        session, limit, offset, sensor_id=sensor_id, method=method,
        anomaly_type=anomaly_type, severity=severity, resolved=resolved,
        start=start_utc, end=end_utc,
    )


def get_alert(session: Session, alert_id: int) -> Alert:
    alert = alerts_repo.get_alert(session, alert_id)
    if alert is None:
        raise NotFoundError(f"alert {alert_id} not found")
    return alert


def list_building_alerts(
    session: Session,
    building_id: int,
    limit: int,
    offset: int,
    method: str | None = None,
    anomaly_type: str | None = None,
    severity: str | None = None,
    resolved: bool | None = None,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> tuple[list[Alert], int]:
    _, sensor_id = resolve_building_and_sensor(session, building_id)  # 404s if building/sensor missing
    return list_alerts(
        session, limit, offset, sensor_id=sensor_id, method=method,
        anomaly_type=anomaly_type, severity=severity, resolved=resolved,
        start=start, end=end,
    )


def get_alerts_summary(
    session: Session, start: dt.datetime | None = None, end: dt.datetime | None = None
) -> dict:
    start_utc, end_utc = validate_date_range(start, end)
    return alerts_repo.summary(session, start_utc, end_utc)
