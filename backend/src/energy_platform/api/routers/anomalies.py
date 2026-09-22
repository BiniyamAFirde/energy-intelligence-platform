"""Alert (anomaly detection) endpoints (Phase 8). Two routers in one file,
mirroring buildings.py + energy.py's split: a top-level /api/v1/alerts
resource (list/detail/summary) plus a building-nested
/api/v1/buildings/{building_id}/alerts, following the exact nested
pattern energy.py already established for a building's energy endpoints."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from energy_platform.api.deps import get_db, list_pagination
from energy_platform.schemas.anomalies import AlertOut, AlertSummaryOut
from energy_platform.schemas.common import PaginatedResponse
from energy_platform.services import anomaly_service

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])
building_router = APIRouter(prefix="/api/v1/buildings/{building_id}", tags=["alerts"])

_start_q = Query(None, description="Inclusive start, ISO 8601. Naive values are treated as UTC.")
_end_q = Query(None, description="Inclusive end, ISO 8601. Naive values are treated as UTC.")
_method_q = Query(None, description="data_quality | behavioral | forecast_residual | isolation_forest")
_anomaly_type_q = Query(None, description="e.g. negative_value | missing | zero_run | stuck_meter | isolated_spike | behavioral_deviation | high_residual | low_residual | isolation_forest")
_severity_q = Query(None, description="low | medium | high")
_resolved_q = Query(None, description="Filter to resolved (true) or unresolved (false) alerts only")


# /summary is registered before /{alert_id} in the SAME router -- FastAPI
# matches routes in registration order, so without this ordering
# "summary" would be parsed (and 422-rejected) as an alert_id, the exact
# collision main.py's own comment documents for analytics vs buildings.
@router.get("/summary", response_model=AlertSummaryOut)
def get_alerts_summary(
    start: dt.datetime | None = _start_q,
    end: dt.datetime | None = _end_q,
    db: Session = Depends(get_db),
):
    return anomaly_service.get_alerts_summary(db, start, end)


@router.get("", response_model=PaginatedResponse[AlertOut])
def list_alerts(
    sensor_id: int | None = Query(None, description="Filter by sensor"),
    method: str | None = _method_q,
    anomaly_type: str | None = _anomaly_type_q,
    severity: str | None = _severity_q,
    resolved: bool | None = _resolved_q,
    start: dt.datetime | None = _start_q,
    end: dt.datetime | None = _end_q,
    pagination: dict = Depends(list_pagination),
    db: Session = Depends(get_db),
):
    items, total = anomaly_service.list_alerts(
        db, sensor_id=sensor_id, method=method, anomaly_type=anomaly_type,
        severity=severity, resolved=resolved, start=start, end=end, **pagination,
    )
    return PaginatedResponse(items=items, total=total, **pagination)


@router.get("/{alert_id}", response_model=AlertOut)
def get_alert(alert_id: int, db: Session = Depends(get_db)):
    return anomaly_service.get_alert(db, alert_id)


@building_router.get("/alerts", response_model=PaginatedResponse[AlertOut])
def get_building_alerts(
    building_id: int,
    method: str | None = _method_q,
    anomaly_type: str | None = _anomaly_type_q,
    severity: str | None = _severity_q,
    resolved: bool | None = _resolved_q,
    start: dt.datetime | None = _start_q,
    end: dt.datetime | None = _end_q,
    pagination: dict = Depends(list_pagination),
    db: Session = Depends(get_db),
):
    items, total = anomaly_service.list_building_alerts(
        db, building_id, method=method, anomaly_type=anomaly_type,
        severity=severity, resolved=resolved, start=start, end=end, **pagination,
    )
    return PaginatedResponse(items=items, total=total, **pagination)
