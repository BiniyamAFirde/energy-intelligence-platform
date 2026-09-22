"""Shared FastAPI dependencies: DB session injection and pagination/limit
query-param defaults, centralized so every router applies the same bounds
instead of each endpoint picking its own numbers."""

from __future__ import annotations

from typing import Generator

from fastapi import Query
from sqlalchemy.orm import Session

from energy_platform.db.base import SessionLocal

# List endpoints (sites/buildings/sensors): small tables, modest page sizes.
LIST_DEFAULT_LIMIT = 50
LIST_MAX_LIMIT = 200

# Time-series endpoints (energy/weather): a building has at most ~17.5k
# hourly rows total, so the max is set below that -- callers retrieving a
# full history must page with start/end rather than one unbounded request.
TIMESERIES_DEFAULT_LIMIT = 1000
TIMESERIES_MAX_LIMIT = 5000


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def list_pagination(
    limit: int = Query(LIST_DEFAULT_LIMIT, ge=1, le=LIST_MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> dict:
    return {"limit": limit, "offset": offset}


def timeseries_limit(
    limit: int = Query(TIMESERIES_DEFAULT_LIMIT, ge=1, le=TIMESERIES_MAX_LIMIT),
) -> int:
    return limit
