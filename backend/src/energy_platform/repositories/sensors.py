from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from energy_platform.db.models import Sensor


def list_sensors(
    session: Session, limit: int, offset: int, building_id: int | None = None
) -> tuple[list[Sensor], int]:
    stmt = select(Sensor)
    count_stmt = select(func.count()).select_from(Sensor)

    if building_id is not None:
        stmt = stmt.where(Sensor.building_id == building_id)
        count_stmt = count_stmt.where(Sensor.building_id == building_id)

    total = session.execute(count_stmt).scalar_one()
    items = session.execute(
        stmt.order_by(Sensor.sensor_id).limit(limit).offset(offset)
    ).scalars().all()
    return list(items), total


def get_sensor(session: Session, sensor_id: int) -> Sensor | None:
    return session.get(Sensor, sensor_id)


def get_sensor_for_building(
    session: Session, building_id: int, meter_type: str = "electricity"
) -> Sensor | None:
    return session.execute(
        select(Sensor).where(
            Sensor.building_id == building_id, Sensor.meter_type == meter_type
        )
    ).scalar_one_or_none()
