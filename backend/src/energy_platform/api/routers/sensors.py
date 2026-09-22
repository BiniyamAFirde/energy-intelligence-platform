from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from energy_platform.api.deps import get_db, list_pagination
from energy_platform.schemas.common import PaginatedResponse
from energy_platform.schemas.sensors import SensorOut
from energy_platform.services import sensors_service

router = APIRouter(prefix="/api/v1/sensors", tags=["sensors"])


@router.get("", response_model=PaginatedResponse[SensorOut])
def list_sensors(
    building_id: int | None = Query(None, description="Filter by building"),
    pagination: dict = Depends(list_pagination),
    db: Session = Depends(get_db),
):
    items, total = sensors_service.list_sensors(db, building_id=building_id, **pagination)
    return PaginatedResponse(items=items, total=total, **pagination)


@router.get("/{sensor_id}", response_model=SensorOut)
def get_sensor(sensor_id: int, db: Session = Depends(get_db)):
    return sensors_service.get_sensor(db, sensor_id)
