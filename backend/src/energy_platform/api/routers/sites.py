from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from energy_platform.api.deps import get_db, list_pagination
from energy_platform.schemas.common import PaginatedResponse
from energy_platform.schemas.sites import SiteOut
from energy_platform.services import sites_service

router = APIRouter(prefix="/api/v1/sites", tags=["sites"])


@router.get("", response_model=PaginatedResponse[SiteOut])
def list_sites(pagination: dict = Depends(list_pagination), db: Session = Depends(get_db)):
    items, total = sites_service.list_sites(db, **pagination)
    return PaginatedResponse(items=items, total=total, **pagination)


@router.get("/{site_id}", response_model=SiteOut)
def get_site(site_id: int, db: Session = Depends(get_db)):
    return sites_service.get_site(db, site_id)
