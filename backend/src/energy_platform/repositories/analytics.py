"""Queries that don't fit a single-entity repository: seasonality profiles
(hour-of-day, day-of-week, month), peak analysis, cross-building comparison,
and the site-level weather/energy join. Still database queries only -- no
statistics beyond what SQL/Postgres computes directly (correlation,
coefficient of variation, etc. are service-layer concerns).

Timezone note: energy_measurements.ts is stored in UTC (Phase 4). "Hour of
day" or "day of week" computed directly on UTC would be meaningless for
occupancy-driven patterns (e.g. a 9am occupancy peak would show up at a
different UTC hour depending on DST). Every profile query here converts to
the building's site's local time first via `ts AT TIME ZONE tz_name`.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from energy_platform.db.models import Building, EnergyMeasurement, Sensor, Site, WeatherMeasurement


def _local_ts(tz_name: str):
    return func.timezone(tz_name, EnergyMeasurement.ts)


def hour_of_day_profile(session: Session, sensor_id: int, tz_name: str) -> list[dict]:
    local_ts = _local_ts(tz_name)
    hour = func.extract("hour", local_ts).label("hour_of_day")
    stmt = (
        select(
            hour,
            func.avg(EnergyMeasurement.consumption_kwh).label("mean_kwh"),
            func.count(EnergyMeasurement.consumption_kwh).label("observation_count"),
        )
        .where(EnergyMeasurement.sensor_id == sensor_id)
        .group_by(hour)
        .order_by(hour)
    )
    return [dict(r._mapping) for r in session.execute(stmt).all()]


def day_of_week_profile(session: Session, sensor_id: int, tz_name: str) -> list[dict]:
    """Postgres DOW: 0=Sunday .. 6=Saturday."""
    local_ts = _local_ts(tz_name)
    dow = func.extract("dow", local_ts).label("day_of_week")
    stmt = (
        select(
            dow,
            func.avg(EnergyMeasurement.consumption_kwh).label("mean_kwh"),
            func.count(EnergyMeasurement.consumption_kwh).label("observation_count"),
        )
        .where(EnergyMeasurement.sensor_id == sensor_id)
        .group_by(dow)
        .order_by(dow)
    )
    return [dict(r._mapping) for r in session.execute(stmt).all()]


def monthly_profile(session: Session, sensor_id: int, tz_name: str) -> list[dict]:
    local_ts = _local_ts(tz_name)
    month = func.extract("month", local_ts).label("month")
    stmt = (
        select(
            month,
            func.avg(EnergyMeasurement.consumption_kwh).label("mean_kwh"),
            func.sum(EnergyMeasurement.consumption_kwh).label("total_kwh"),
            func.count(EnergyMeasurement.consumption_kwh).label("observation_count"),
        )
        .where(EnergyMeasurement.sensor_id == sensor_id)
        .group_by(month)
        .order_by(month)
    )
    return [dict(r._mapping) for r in session.execute(stmt).all()]


def weekday_weekend_profile(session: Session, sensor_id: int, tz_name: str) -> list[dict]:
    local_ts = _local_ts(tz_name)
    dow = func.extract("dow", local_ts)
    is_weekend = dow.in_((0, 6)).label("is_weekend")
    stmt = (
        select(
            is_weekend,
            func.avg(EnergyMeasurement.consumption_kwh).label("mean_kwh"),
            func.count(EnergyMeasurement.consumption_kwh).label("observation_count"),
        )
        .where(EnergyMeasurement.sensor_id == sensor_id)
        .group_by(is_weekend)
    )
    return [dict(r._mapping) for r in session.execute(stmt).all()]


def monthly_peaks(session: Session, sensor_id: int, tz_name: str) -> list[dict]:
    """One row per calendar month: the timestamp and value of that month's
    peak, via Postgres's DISTINCT ON (an ordered "argmax per group")."""
    local_ts = _local_ts(tz_name)
    month = func.extract("month", local_ts).label("month")
    stmt = (
        select(month, EnergyMeasurement.ts, EnergyMeasurement.consumption_kwh)
        .where(
            EnergyMeasurement.sensor_id == sensor_id,
            EnergyMeasurement.consumption_kwh.is_not(None),
        )
        .distinct(month)
        .order_by(month, EnergyMeasurement.consumption_kwh.desc())
    )
    return [dict(r._mapping) for r in session.execute(stmt).all()]


def dataset_overview_counts(session: Session) -> dict:
    row = session.execute(
        select(
            select(func.count()).select_from(Site).scalar_subquery().label("n_sites"),
            select(func.count()).select_from(Building).scalar_subquery().label("n_buildings"),
            select(func.count()).select_from(EnergyMeasurement).scalar_subquery().label("n_energy_rows"),
            select(func.count()).select_from(WeatherMeasurement).scalar_subquery().label("n_weather_rows"),
            select(func.min(EnergyMeasurement.ts)).scalar_subquery().label("first_ts"),
            select(func.max(EnergyMeasurement.ts)).scalar_subquery().label("last_ts"),
        )
    ).one()
    return dict(row._mapping)


def dataset_daily_total(session: Session, tz_name: str) -> list[dict]:
    """Total consumption per calendar day, summed across every sensor in
    the loaded dataset -- used only by the CLI EDA report (no API endpoint
    requested for a dataset-wide series)."""
    local_ts = func.timezone(tz_name, EnergyMeasurement.ts)
    day = func.date_trunc("day", local_ts).label("day")
    stmt = (
        select(day, func.sum(EnergyMeasurement.consumption_kwh).label("total_kwh"))
        .group_by(day)
        .order_by(day)
    )
    return [dict(r._mapping) for r in session.execute(stmt).all()]


def dataset_hour_of_day_profile(session: Session, tz_name: str) -> list[dict]:
    local_ts = func.timezone(tz_name, EnergyMeasurement.ts)
    hour = func.extract("hour", local_ts).label("hour_of_day")
    stmt = (
        select(hour, func.avg(EnergyMeasurement.consumption_kwh).label("mean_kwh"))
        .group_by(hour)
        .order_by(hour)
    )
    return [dict(r._mapping) for r in session.execute(stmt).all()]


def dataset_day_of_week_profile(session: Session, tz_name: str) -> list[dict]:
    local_ts = func.timezone(tz_name, EnergyMeasurement.ts)
    dow = func.extract("dow", local_ts).label("day_of_week")
    stmt = (
        select(dow, func.avg(EnergyMeasurement.consumption_kwh).label("mean_kwh"))
        .group_by(dow)
        .order_by(dow)
    )
    return [dict(r._mapping) for r in session.execute(stmt).all()]


def building_comparison(
    session: Session, site_id: int | None = None, primary_use: str | None = None
) -> list[dict]:
    stmt = (
        select(
            Building.building_id,
            Building.building_code,
            Building.site_id,
            Building.primary_use,
            Building.area_sqm,
            func.avg(EnergyMeasurement.consumption_kwh).label("mean_kwh"),
            func.percentile_cont(0.5).within_group(EnergyMeasurement.consumption_kwh).label("median_kwh"),
            func.sum(EnergyMeasurement.consumption_kwh).label("total_kwh"),
            func.max(EnergyMeasurement.consumption_kwh).label("peak_kwh"),
            func.stddev_samp(EnergyMeasurement.consumption_kwh).label("std_kwh"),
        )
        .join(Sensor, Sensor.building_id == Building.building_id)
        .join(EnergyMeasurement, EnergyMeasurement.sensor_id == Sensor.sensor_id)
        .group_by(
            Building.building_id, Building.building_code, Building.site_id,
            Building.primary_use, Building.area_sqm,
        )
        .order_by(Building.building_id)
    )
    if site_id is not None:
        stmt = stmt.where(Building.site_id == site_id)
    if primary_use is not None:
        stmt = stmt.where(Building.primary_use == primary_use)

    return [dict(r._mapping) for r in session.execute(stmt).all()]


def site_weather_energy_series(
    session: Session,
    site_id: int,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> list[dict]:
    """Per-timestamp series for a site: total electricity consumption summed
    across every selected building at that site, joined to that site's
    weather reading at the same instant. No `limit` -- correlation over an
    arbitrarily truncated window would be misleading, and a site's full
    2-year hourly series (~17.5k rows) is cheap enough to fetch whole."""
    energy_by_ts = (
        select(
            EnergyMeasurement.ts,
            func.sum(EnergyMeasurement.consumption_kwh).label("total_kwh"),
        )
        .join(Sensor, Sensor.sensor_id == EnergyMeasurement.sensor_id)
        .join(Building, Building.building_id == Sensor.building_id)
        .where(Building.site_id == site_id)
        .group_by(EnergyMeasurement.ts)
        .subquery()
    )

    stmt = (
        select(
            energy_by_ts.c.ts,
            energy_by_ts.c.total_kwh,
            WeatherMeasurement.air_temp_c,
            WeatherMeasurement.dew_temp_c,
            WeatherMeasurement.wind_speed,
            WeatherMeasurement.cloud_coverage,
            WeatherMeasurement.precip_1hr_mm,
        )
        .join(
            WeatherMeasurement,
            (WeatherMeasurement.site_id == site_id) & (WeatherMeasurement.ts == energy_by_ts.c.ts),
        )
        .order_by(energy_by_ts.c.ts)
    )
    if start is not None:
        stmt = stmt.where(energy_by_ts.c.ts >= start)
    if end is not None:
        stmt = stmt.where(energy_by_ts.c.ts <= end)

    return [dict(r._mapping) for r in session.execute(stmt).all()]
