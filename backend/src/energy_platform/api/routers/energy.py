"""Energy sub-resources of a building. A separate router from buildings.py
even though every path is nested under /buildings/{building_id}: these
endpoints are about the energy domain (readings, aggregates, summary
statistics), not building CRUD-read, and keeping them apart mirrors the
services they call (energy_service, not buildings_service)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from energy_platform.api.deps import get_db, timeseries_limit
from energy_platform.schemas.energy import (
    BuildingSummaryOut,
    EnergyAggregatePointOut,
    EnergyMeasurementOut,
)
from energy_platform.services import energy_service

router = APIRouter(prefix="/api/v1/buildings/{building_id}", tags=["energy"])

_start_q = Query(None, description="Inclusive start, ISO 8601. Naive values are treated as UTC.")
_end_q = Query(None, description="Inclusive end, ISO 8601. Naive values are treated as UTC.")


@router.get("/energy", response_model=list[EnergyMeasurementOut])
def get_building_energy(
    building_id: int,
    start: dt.datetime | None = _start_q,
    end: dt.datetime | None = _end_q,
    limit: int = Depends(timeseries_limit),
    db: Session = Depends(get_db),
):
    return energy_service.get_building_energy(db, building_id, limit, start, end)


@router.get("/energy/aggregate", response_model=list[EnergyAggregatePointOut])
def get_building_energy_aggregate(
    building_id: int,
    granularity: str = Query("daily", description="hourly | daily | weekly"),
    start: dt.datetime | None = _start_q,
    end: dt.datetime | None = _end_q,
    limit: int = Depends(timeseries_limit),
    db: Session = Depends(get_db),
):
    return energy_service.get_building_energy_aggregate(
        db, building_id, granularity, limit, start, end
    )


@router.get("/summary", response_model=BuildingSummaryOut)
def get_building_summary(building_id: int, db: Session = Depends(get_db)):
    return energy_service.get_building_summary(db, building_id)
