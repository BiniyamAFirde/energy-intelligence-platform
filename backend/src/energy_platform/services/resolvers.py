"""Shared "resolve an id to the object(s) a service needs, or 404" helpers.
Extracted from energy_service.py in Phase 6 so analytics_service.py can
reuse the same building/sensor/site-timezone resolution instead of
duplicating it (both services need it for the same reason: every energy or
analytics query is scoped to one building's electricity sensor)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from energy_platform.db.models import Building
from energy_platform.repositories import buildings as buildings_repo
from energy_platform.repositories import sensors as sensors_repo
from energy_platform.repositories import sites as sites_repo
from energy_platform.services.exceptions import NotFoundError


def resolve_building_and_sensor(session: Session, building_id: int) -> tuple[Building, int]:
    building = buildings_repo.get_building(session, building_id)
    if building is None:
        raise NotFoundError(f"building {building_id} not found")
    sensor = sensors_repo.get_sensor_for_building(session, building_id)
    if sensor is None:
        raise NotFoundError(f"building {building_id} has no electricity sensor")
    return building, sensor.sensor_id


def resolve_site_timezone(session: Session, site_id: int) -> str:
    site = sites_repo.get_site(session, site_id)
    if site is None:
        raise NotFoundError(f"site {site_id} not found")
    return site.timezone
