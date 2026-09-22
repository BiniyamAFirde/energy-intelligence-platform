"""Forecast endpoint (Phase 9 addition): read-only access to Phase 7's
backfilled predictions, joined against real energy_measurements for
actual/residual -- mirrors energy.py's nested-under-buildings pattern."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from energy_platform.api.deps import get_db, timeseries_limit
from energy_platform.schemas.forecast import ForecastPointOut
from energy_platform.services import forecast_service

router = APIRouter(prefix="/api/v1/buildings/{building_id}", tags=["forecast"])

_start_q = Query(None, description="Inclusive start, ISO 8601. Naive values are treated as UTC.")
_end_q = Query(None, description="Inclusive end, ISO 8601. Naive values are treated as UTC.")


@router.get("/forecast", response_model=list[ForecastPointOut])
def get_building_forecast(
    building_id: int,
    start: dt.datetime | None = _start_q,
    end: dt.datetime | None = _end_q,
    limit: int = Depends(timeseries_limit),
    db: Session = Depends(get_db),
):
    return forecast_service.get_building_forecast(db, building_id, limit, start, end)
