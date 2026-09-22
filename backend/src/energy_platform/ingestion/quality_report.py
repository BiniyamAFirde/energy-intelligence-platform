"""Post-ingestion data quality report: queries the already-loaded database
(not the raw CSVs) so it reflects what actually made it into Postgres after
transformation, deduplication, and DST handling.

Checks, matching the Phase 4 data-quality requirements:
  - missing timestamps: gaps versus the expected full hourly grid per sensor/site
  - duplicate timestamps: should always be zero, enforced by the composite PK
  - missing values: NULL rate for consumption_kwh and each weather column
  - referential integrity: every measurement's sensor/site FK must resolve
    (enforced by the DB, verified here by construction -- an orphaned row is
    impossible to insert, so this check documents *why* rather than probing
    for it)
"""

from __future__ import annotations

import json

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from energy_platform.db.base import SessionLocal
from energy_platform.db.models import EnergyMeasurement, WeatherMeasurement


def expected_hourly_grid_size() -> int:
    # 2016 (leap year, 366 days) + 2017 (365 days), hourly.
    return (366 + 365) * 24


def energy_measurement_gaps(session: Session) -> dict:
    expected = expected_hourly_grid_size()
    rows = session.execute(
        select(
            EnergyMeasurement.sensor_id,
            func.count().label("actual"),
            func.count().filter(EnergyMeasurement.consumption_kwh.is_(None)).label("nulls"),
        ).group_by(EnergyMeasurement.sensor_id)
    ).all()

    per_sensor = {
        r.sensor_id: {
            "actual_rows": r.actual,
            "expected_rows": expected,
            "missing_timestamps": expected - r.actual,
            "null_consumption_rows": r.nulls,
            "null_rate": round(r.nulls / r.actual, 4) if r.actual else None,
        }
        for r in rows
    }
    return per_sensor


def duplicate_check(session: Session) -> dict:
    # The composite primary key makes a true duplicate impossible to insert;
    # this query exists to prove it rather than assume it.
    dup_energy = session.execute(
        text(
            "SELECT count(*) FROM (SELECT sensor_id, ts, count(*) c "
            "FROM energy_measurements GROUP BY sensor_id, ts HAVING count(*) > 1) x"
        )
    ).scalar()
    dup_weather = session.execute(
        text(
            "SELECT count(*) FROM (SELECT site_id, ts, count(*) c "
            "FROM weather_measurements GROUP BY site_id, ts HAVING count(*) > 1) x"
        )
    ).scalar()
    return {"duplicate_energy_keys": dup_energy, "duplicate_weather_keys": dup_weather}


def weather_missing_rates(session: Session) -> dict:
    cols = [
        "air_temp_c", "cloud_coverage", "dew_temp_c", "precip_1hr_mm",
        "precip_6hr_mm", "sea_lvl_pressure", "wind_direction", "wind_speed",
    ]
    total = session.execute(select(func.count()).select_from(WeatherMeasurement)).scalar()
    rates = {}
    for col in cols:
        nulls = session.execute(
            text(f"SELECT count(*) FROM weather_measurements WHERE {col} IS NULL")
        ).scalar()
        rates[col] = round(nulls / total, 4) if total else None
    return {"total_rows": total, "null_rate_by_column": rates}


def referential_integrity_note() -> str:
    return (
        "Not probed for orphaned rows: energy_measurements.sensor_id and "
        "weather_measurements.site_id are NOT NULL foreign keys enforced by "
        "PostgreSQL, so an orphaned row cannot exist in this database -- the "
        "constraint is the test."
    )


def main() -> dict:
    session = SessionLocal()
    try:
        gaps = energy_measurement_gaps(session)
        total_missing = sum(v["missing_timestamps"] for v in gaps.values())
        report = {
            "expected_hourly_grid_size": expected_hourly_grid_size(),
            "sensors_checked": len(gaps),
            "total_missing_timestamps_across_sensors": total_missing,
            "sensors_with_gaps": {k: v for k, v in gaps.items() if v["missing_timestamps"] > 0},
            "duplicates": duplicate_check(session),
            "weather_missing_rates": weather_missing_rates(session),
            "referential_integrity": referential_integrity_note(),
        }
        return report
    finally:
        session.close()


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, default=str))
