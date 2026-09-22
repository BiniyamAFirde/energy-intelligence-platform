from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from energy_platform.db.models import WeatherMeasurement
from energy_platform.repositories import sites as sites_repo
from energy_platform.repositories import weather as weather_repo
from energy_platform.services.exceptions import NotFoundError
from energy_platform.services.validation import validate_date_range


def get_site_weather(
    session: Session,
    site_id: int,
    limit: int,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> list[WeatherMeasurement]:
    if sites_repo.get_site(session, site_id) is None:
        raise NotFoundError(f"site {site_id} not found")
    start_utc, end_utc = validate_date_range(start, end)
    return weather_repo.list_measurements(session, site_id, limit, start_utc, end_utc)
