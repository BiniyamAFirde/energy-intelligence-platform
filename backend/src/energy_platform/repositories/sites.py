"""Database queries only -- no HTTP concerns, no business rules."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from energy_platform.db.models import Site


def list_sites(session: Session, limit: int, offset: int) -> tuple[list[Site], int]:
    total = session.execute(select(func.count()).select_from(Site)).scalar_one()
    items = session.execute(
        select(Site).order_by(Site.site_id).limit(limit).offset(offset)
    ).scalars().all()
    return list(items), total


def get_site(session: Session, site_id: int) -> Site | None:
    return session.get(Site, site_id)
