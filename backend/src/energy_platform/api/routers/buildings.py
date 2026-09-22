from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from energy_platform.api.deps import get_db, list_pagination
from energy_platform.schemas.buildings import BuildingOut
from energy_platform.schemas.common import PaginatedResponse
from energy_platform.services import buildings_service

router = APIRouter(prefix="/api/v1/buildings", tags=["buildings"])


@router.get("", response_model=PaginatedResponse[BuildingOut])
def list_buildings(
    site_id: int | None = Query(None, description="Filter by site"),
    primary_use: str | None = Query(None, description="Filter by primary space usage"),
    pagination: dict = Depends(list_pagination),
    db: Session = Depends(get_db),
):
    items, total = buildings_service.list_buildings(
        db, site_id=site_id, primary_use=primary_use, **pagination
    )
    return PaginatedResponse(items=items, total=total, **pagination)


@router.get("/{building_id}", response_model=BuildingOut)
def get_building(building_id: int, db: Session = Depends(get_db)):
    return buildings_service.get_building(db, building_id)
