from __future__ import annotations

from sqlalchemy.orm import Session

from energy_platform.db.models import Sensor
from energy_platform.repositories import sensors as sensors_repo
from energy_platform.services.exceptions import NotFoundError


def list_sensors(
    session: Session, limit: int, offset: int, building_id: int | None = None
) -> tuple[list[Sensor], int]:
    return sensors_repo.list_sensors(session, limit, offset, building_id=building_id)


def get_sensor(session: Session, sensor_id: int) -> Sensor:
    sensor = sensors_repo.get_sensor(session, sensor_id)
    if sensor is None:
        raise NotFoundError(f"sensor {sensor_id} not found")
    return sensor
