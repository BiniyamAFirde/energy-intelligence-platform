"""Orchestrates the full ingestion run: read raw CSVs -> select subset ->
transform -> load -> record ingestion_runs. Every stage is wrapped so a
failure in one file's ingestion doesn't corrupt the audit trail for the
others -- each source file gets its own ingestion_runs row.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import pandas as pd
from sqlalchemy.orm import Session

from energy_platform.db.base import SessionLocal
from energy_platform.db.models import IngestionRun
from energy_platform.ingestion.download import RAW_DIR
from energy_platform.ingestion.loader import (
    upsert_buildings,
    upsert_energy_measurements,
    upsert_sensors,
    upsert_sites,
    upsert_weather_measurements,
)
from energy_platform.ingestion.selection import select_subset
from energy_platform.ingestion.transform import (
    TransformStats,
    weather_long_to_utc,
    wide_electricity_to_long,
)

logger = logging.getLogger(__name__)


def _record_run(session: Session, source_file: str, started_at: dt.datetime, **kwargs) -> None:
    run = IngestionRun(source_file=source_file, started_at=started_at, **kwargs)
    session.add(run)
    session.commit()


def run_ingestion(session: Session | None = None) -> dict:
    own_session = session is None
    session = session or SessionLocal()

    metadata_df = pd.read_csv(RAW_DIR / "metadata.csv")
    electricity_df = pd.read_csv(RAW_DIR / "electricity_cleaned.csv", parse_dates=["timestamp"])
    weather_df = pd.read_csv(RAW_DIR / "weather.csv", parse_dates=["timestamp"])

    selection = select_subset(metadata_df, electricity_df)
    site_timezones = (
        metadata_df.drop_duplicates("site_id").set_index("site_id")["timezone"].to_dict()
    )

    summary = {"selection": {
        "sites": selection.site_codes,
        "per_site_counts": selection.per_site_counts,
        "building_count": len(selection.building_codes),
    }}

    # --- Dimensions: sites, buildings, sensors ---
    started = dt.datetime.now(dt.timezone.utc)
    try:
        site_id_by_code = upsert_sites(
            session, selection.site_codes, {s: site_timezones[s] for s in selection.site_codes}
        )
        building_id_by_code = upsert_buildings(
            session, metadata_df, selection.building_codes, site_id_by_code
        )
        sensor_id_by_building_code = upsert_sensors(session, building_id_by_code)
        session.commit()
        _record_run(
            session, "metadata.csv (dimensions)", started,
            finished_at=dt.datetime.now(dt.timezone.utc),
            rows_read=len(selection.building_codes),
            rows_inserted=len(sensor_id_by_building_code),
            rows_rejected=len(selection.building_codes) - len(sensor_id_by_building_code),
            status="success",
        )
    except Exception as exc:
        session.rollback()
        _record_run(
            session, "metadata.csv (dimensions)", started,
            finished_at=dt.datetime.now(dt.timezone.utc),
            status="failed", error_summary=str(exc),
        )
        raise

    # --- Fact table: energy_measurements ---
    started = dt.datetime.now(dt.timezone.utc)
    try:
        stats = TransformStats()

        # electricity_cleaned.csv is wide across *all* selected buildings,
        # but each building's timezone follows its site. Group by site so
        # normalize_timestamps_to_utc gets one consistent tz per call.
        long_frames = []
        for site_code in selection.site_codes:
            site_buildings = [
                b for b, s in zip(
                    selection.building_codes,
                    metadata_df.set_index("building_id").loc[selection.building_codes, "site_id"],
                )
                if s == site_code
            ]
            frame = wide_electricity_to_long(
                electricity_df, site_buildings, site_timezones[site_code], stats
            )
            long_frames.append(frame)
        long_elec = pd.concat(long_frames, ignore_index=True)

        rows_read, rows_inserted = upsert_energy_measurements(
            session, long_elec, sensor_id_by_building_code
        )
        session.commit()
        _record_run(
            session, "electricity_cleaned.csv", started,
            finished_at=dt.datetime.now(dt.timezone.utc),
            rows_read=rows_read, rows_inserted=rows_inserted,
            rows_rejected=rows_read - rows_inserted,
            status="success",
            error_summary=json.dumps(vars(stats)),
        )
        summary["energy_measurements"] = {"rows_read": rows_read, "rows_inserted": rows_inserted,
                                           "transform_stats": vars(stats)}
    except Exception as exc:
        session.rollback()
        _record_run(
            session, "electricity_cleaned.csv", started,
            finished_at=dt.datetime.now(dt.timezone.utc),
            status="failed", error_summary=str(exc),
        )
        raise

    # --- Fact table: weather_measurements ---
    started = dt.datetime.now(dt.timezone.utc)
    try:
        weather_subset = weather_df[weather_df["site_id"].isin(selection.site_codes)]
        stats = TransformStats()
        long_weather = weather_long_to_utc(
            weather_subset, {s: site_timezones[s] for s in selection.site_codes}, stats
        )
        rows_read, rows_inserted = upsert_weather_measurements(
            session, long_weather, site_id_by_code
        )
        session.commit()
        _record_run(
            session, "weather.csv", started,
            finished_at=dt.datetime.now(dt.timezone.utc),
            rows_read=rows_read, rows_inserted=rows_inserted,
            rows_rejected=rows_read - rows_inserted,
            status="success",
            error_summary=json.dumps(vars(stats)),
        )
        summary["weather_measurements"] = {"rows_read": rows_read, "rows_inserted": rows_inserted,
                                            "transform_stats": vars(stats)}
    except Exception as exc:
        session.rollback()
        _record_run(
            session, "weather.csv", started,
            finished_at=dt.datetime.now(dt.timezone.utc),
            status="failed", error_summary=str(exc),
        )
        raise

    if own_session:
        session.close()

    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_ingestion()
    print(json.dumps(result, indent=2, default=str))
