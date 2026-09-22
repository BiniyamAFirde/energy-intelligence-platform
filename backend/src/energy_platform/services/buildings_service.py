from __future__ import annotations

from sqlalchemy.orm import Session

from energy_platform.db.models import Building
from energy_platform.repositories import buildings as buildings_repo
from energy_platform.services.exceptions import NotFoundError


def list_buildings(
    session: Session,
    limit: int,
    offset: int,
    site_id: int | None = None,
    primary_use: str | None = None,
) -> tuple[list[Building], int]:
    return buildings_repo.list_buildings(
        session, limit, offset, site_id=site_id, primary_use=primary_use
    )


def get_building(session: Session, building_id: int) -> Building:
    building = buildings_repo.get_building(session, building_id)
    if building is None:
        raise NotFoundError(f"building {building_id} not found")
    return building
