"""Queries against energy_measurements (1M+ rows). Every query here filters
on sensor_id first, matching the (sensor_id, ts) composite primary key, so
Postgres can use an index seek instead of a sequential scan -- see
docs/architecture.md for EXPLAIN ANALYZE evidence.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from energy_platform.db.models import EnergyMeasurement

VALID_GRANULARITIES = {"hourly": "hour", "daily": "day", "weekly": "week", "monthly": "month"}


def list_measurements(
    session: Session,
    sensor_id: int,
    limit: int,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    order_by: str = "ts",
) -> list[EnergyMeasurement]:
    """order_by="ts" (chronological, the default used everywhere) or
    "consumption_desc" (how analytics.top_peaks reuses this query instead
    of duplicating it)."""
    stmt = select(EnergyMeasurement).where(EnergyMeasurement.sensor_id == sensor_id)
    if start is not None:
        stmt = stmt.where(EnergyMeasurement.ts >= start)
    if end is not None:
        stmt = stmt.where(EnergyMeasurement.ts <= end)
    if order_by == "ts":
        order_col = EnergyMeasurement.ts
    else:
        # Postgres sorts NULLs FIRST in DESC order by default, so without
        # excluding them a missing reading would rank as the "top" peak
        # ahead of every real value. A NULL is a missing observation, never
        # a legitimate peak, so it's filtered out rather than just
        # reordered past.
        stmt = stmt.where(EnergyMeasurement.consumption_kwh.is_not(None))
        order_col = EnergyMeasurement.consumption_kwh.desc()
    stmt = stmt.order_by(order_col).limit(limit)
    return list(session.execute(stmt).scalars().all())


def list_measurements_for_sensors(session: Session, sensor_ids: list[int]) -> list[dict]:
    """Bulk fetch across many sensors in one round trip -- used by
    forecasting/dataset.py to build the training set (~1M rows). Returns
    plain dicts from a column-level Core select, not ORM-mapped
    EnergyMeasurement instances: at this row count, ORM identity-map
    hydration is measurably expensive (this was originally written with
    `select(EnergyMeasurement)` + `.scalars()`, which took ~170s just to
    fetch+materialize 1,052,400 rows in practice -- the query itself is
    fast, per Phase 5's EXPLAIN ANALYZE; the cost was Python-side object
    construction). Every other repository function here still returns ORM
    objects, which is the right choice for typical single/few-sensor API
    calls; this one function trades that convention for bulk-read speed on
    a genuinely different access pattern."""
    stmt = (
        select(EnergyMeasurement.sensor_id, EnergyMeasurement.ts, EnergyMeasurement.consumption_kwh)
        .where(EnergyMeasurement.sensor_id.in_(sensor_ids))
        .order_by(EnergyMeasurement.sensor_id, EnergyMeasurement.ts)
    )
    return [dict(r) for r in session.execute(stmt).mappings().all()]


def summary(session: Session, sensor_id: int) -> dict | None:
    """Extended in Phase 6 with median/total (Phase 5's /summary endpoint
    only reads the subset of keys its schema declares, so this is a safe,
    additive change -- verified by re-running the Phase 5 test suite)."""
    row = session.execute(
        select(
            func.count(EnergyMeasurement.consumption_kwh).label("total_observations"),
            func.avg(EnergyMeasurement.consumption_kwh).label("mean_kwh"),
            func.percentile_cont(0.5).within_group(EnergyMeasurement.consumption_kwh).label("median_kwh"),
            func.min(EnergyMeasurement.consumption_kwh).label("min_kwh"),
            func.max(EnergyMeasurement.consumption_kwh).label("max_kwh"),
            func.stddev_samp(EnergyMeasurement.consumption_kwh).label("stddev_kwh"),
            func.sum(EnergyMeasurement.consumption_kwh).label("total_kwh"),
            func.min(EnergyMeasurement.ts).label("first_ts"),
            func.max(EnergyMeasurement.ts).label("last_ts"),
            func.count().label("total_rows"),
        ).where(EnergyMeasurement.sensor_id == sensor_id)
    ).one_or_none()

    if row is None or row.total_rows == 0:
        return None
    return dict(row._mapping)


def aggregate(
    session: Session,
    sensor_id: int,
    granularity: str,
    limit: int,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> list[dict]:
    unit = VALID_GRANULARITIES[granularity]  # caller (service) validates membership
    period = func.date_trunc(unit, EnergyMeasurement.ts).label("period_start")

    stmt = (
        select(
            period,
            func.avg(EnergyMeasurement.consumption_kwh).label("mean_kwh"),
            func.sum(EnergyMeasurement.consumption_kwh).label("total_kwh"),
            func.min(EnergyMeasurement.consumption_kwh).label("min_kwh"),
            func.max(EnergyMeasurement.consumption_kwh).label("max_kwh"),
            func.stddev_samp(EnergyMeasurement.consumption_kwh).label("stddev_kwh"),
            func.count(EnergyMeasurement.consumption_kwh).label("observation_count"),
        )
        .where(EnergyMeasurement.sensor_id == sensor_id)
    )
    if start is not None:
        stmt = stmt.where(EnergyMeasurement.ts >= start)
    if end is not None:
        stmt = stmt.where(EnergyMeasurement.ts <= end)
    stmt = stmt.group_by(period).order_by(period).limit(limit)

    return [dict(r._mapping) for r in session.execute(stmt).all()]
