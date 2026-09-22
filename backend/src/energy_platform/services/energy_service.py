"""Business logic for the energy endpoints: resolves a building to its
electricity sensor, validates parameters, and shapes repository output.
Aggregation *decisions* (which granularity is valid, how it's validated)
live here; the actual GROUP BY SQL lives in the repository."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from energy_platform.db.models import EnergyMeasurement
from energy_platform.ingestion.quality_report import expected_hourly_grid_size
from energy_platform.repositories import energy as energy_repo
from energy_platform.services.exceptions import NotFoundError
from energy_platform.services.resolvers import resolve_building_and_sensor
from energy_platform.services.validation import validate_date_range, validate_granularity


def get_building_energy(
    session: Session,
    building_id: int,
    limit: int,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> list[EnergyMeasurement]:
    _, sensor_id = resolve_building_and_sensor(session, building_id)
    start_utc, end_utc = validate_date_range(start, end)
    return energy_repo.list_measurements(session, sensor_id, limit, start_utc, end_utc)


def get_building_summary(session: Session, building_id: int) -> dict:
    building, sensor_id = resolve_building_and_sensor(session, building_id)
    stats = energy_repo.summary(session, sensor_id)
    if stats is None:
        raise NotFoundError(f"building {building_id} has no energy measurements")

    expected = expected_hourly_grid_size()
    return {
        "building_id": building.building_id,
        "building_code": building.building_code,
        "total_observations": stats["total_observations"],
        "mean_consumption_kwh": stats["mean_kwh"],
        "min_consumption_kwh": stats["min_kwh"],
        "max_consumption_kwh": stats["max_kwh"],
        "stddev_consumption_kwh": stats["stddev_kwh"],
        "first_timestamp": stats["first_ts"],
        "last_timestamp": stats["last_ts"],
        "missing_observations": max(expected - stats["total_observations"], 0),
    }


def get_building_energy_aggregate(
    session: Session,
    building_id: int,
    granularity: str,
    limit: int,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> list[dict]:
    _, sensor_id = resolve_building_and_sensor(session, building_id)
    granularity = validate_granularity(granularity)
    start_utc, end_utc = validate_date_range(start, end)
    return energy_repo.aggregate(session, sensor_id, granularity, limit, start_utc, end_utc)
