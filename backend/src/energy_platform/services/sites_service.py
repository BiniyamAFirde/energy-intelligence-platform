from __future__ import annotations

from sqlalchemy.orm import Session

from energy_platform.db.models import Site
from energy_platform.repositories import sites as sites_repo
from energy_platform.services.exceptions import NotFoundError


def list_sites(session: Session, limit: int, offset: int) -> tuple[list[Site], int]:
    return sites_repo.list_sites(session, limit, offset)


def get_site(session: Session, site_id: int) -> Site:
    site = sites_repo.get_site(session, site_id)
    if site is None:
        raise NotFoundError(f"site {site_id} not found")
    return site
