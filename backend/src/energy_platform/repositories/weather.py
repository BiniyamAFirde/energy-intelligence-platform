from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from energy_platform.db.models import WeatherMeasurement


def list_measurements(
    session: Session,
    site_id: int,
    limit: int,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> list[WeatherMeasurement]:
    stmt = select(WeatherMeasurement).where(WeatherMeasurement.site_id == site_id)
    if start is not None:
        stmt = stmt.where(WeatherMeasurement.ts >= start)
    if end is not None:
        stmt = stmt.where(WeatherMeasurement.ts <= end)
    stmt = stmt.order_by(WeatherMeasurement.ts).limit(limit)
    return list(session.execute(stmt).scalars().all())
