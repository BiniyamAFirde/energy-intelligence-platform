from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from energy_platform.api.deps import get_db, timeseries_limit
from energy_platform.schemas.weather import WeatherMeasurementOut
from energy_platform.services import weather_service

router = APIRouter(prefix="/api/v1/sites/{site_id}", tags=["weather"])


@router.get("/weather", response_model=list[WeatherMeasurementOut])
def get_site_weather(
    site_id: int,
    start: dt.datetime | None = Query(None, description="Inclusive start, ISO 8601 UTC"),
    end: dt.datetime | None = Query(None, description="Inclusive end, ISO 8601 UTC"),
    limit: int = Depends(timeseries_limit),
    db: Session = Depends(get_db),
):
    return weather_service.get_site_weather(db, site_id, limit, start, end)
