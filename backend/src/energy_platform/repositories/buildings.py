from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from energy_platform.db.models import Building


def list_buildings(
    session: Session,
    limit: int,
    offset: int,
    site_id: int | None = None,
    primary_use: str | None = None,
) -> tuple[list[Building], int]:
    stmt = select(Building)
    count_stmt = select(func.count()).select_from(Building)

    if site_id is not None:
        stmt = stmt.where(Building.site_id == site_id)
        count_stmt = count_stmt.where(Building.site_id == site_id)
    if primary_use is not None:
        stmt = stmt.where(Building.primary_use == primary_use)
        count_stmt = count_stmt.where(Building.primary_use == primary_use)

    total = session.execute(count_stmt).scalar_one()
    items = session.execute(
        stmt.order_by(Building.building_id).limit(limit).offset(offset)
    ).scalars().all()
    return list(items), total


def get_building(session: Session, building_id: int) -> Building | None:
    return session.get(Building, building_id)
