"""Idempotent bulk loading into PostgreSQL.

Idempotency strategy: every insert is an `INSERT ... ON CONFLICT DO UPDATE`
against the table's natural unique constraint (site_code, building_code,
(building_id, meter_type), or the composite time-series primary keys). This
means re-running the pipeline against the same source data converges to the
same rows instead of erroring on the second run or silently duplicating data
on the first. DO UPDATE (not DO NOTHING) is used everywhere so `RETURNING`
always yields the row's id, whether it was just inserted or already existed
-- avoiding a second round-trip query to resolve building_code -> sensor_id.
"""

from __future__ import annotations

import math

import pandas as pd
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from energy_platform.db.models import (
    Building,
    EnergyMeasurement,
    Sensor,
    Site,
    WeatherMeasurement,
)

CHUNK_SIZE = 20_000


def upsert_sites(session: Session, site_codes: list[str], timezones: dict[str, str]) -> dict[str, int]:
    if not site_codes:
        return {}
    rows = [{"site_code": s, "timezone": timezones[s]} for s in site_codes]
    stmt = insert(Site).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Site.site_code],
        set_={"timezone": stmt.excluded.timezone},
    ).returning(Site.site_id, Site.site_code)
    result = session.execute(stmt)
    return {row.site_code: row.site_id for row in result}


def upsert_buildings(
    session: Session, metadata_df: pd.DataFrame, building_codes: list[str], site_id_by_code: dict[str, int]
) -> dict[str, int]:
    if not building_codes:
        return {}
    subset = metadata_df[metadata_df["building_id"].isin(building_codes)]
    rows = []
    for _, r in subset.iterrows():
        rows.append({
            "building_code": r["building_id"],
            "site_id": site_id_by_code[r["site_id"]],
            "primary_use": _clean(r.get("primaryspaceusage")),
            "area_sqm": _clean(r.get("sqm")),
            "sub_primary_use": _clean(r.get("sub_primaryspaceusage")),
            "latitude": _clean(r.get("lat")),
            "longitude": _clean(r.get("lng")),
            "year_built": _clean_int(r.get("yearbuilt")),
            "number_of_floors": _clean_int(r.get("numberoffloors")),
            "occupants": _clean_int(r.get("occupants")),
            "industry": _clean(r.get("industry")),
            "subindustry": _clean(r.get("subindustry")),
            "heating_type": _clean(r.get("heatingtype")),
            "eui": _clean(r.get("eui")),
            "site_eui": _clean(r.get("site_eui")),
            "source_eui": _clean(r.get("source_eui")),
            "leed_level": _clean(r.get("leed_level")),
            "energy_star_rating": _clean_int(r.get("energystarscore")),
        })
    stmt = insert(Building).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Building.building_code],
        set_={"primary_use": stmt.excluded.primary_use, "area_sqm": stmt.excluded.area_sqm},
    ).returning(Building.building_id, Building.building_code)
    result = session.execute(stmt)
    return {row.building_code: row.building_id for row in result}


def upsert_sensors(session: Session, building_id_by_code: dict[str, int]) -> dict[str, int]:
    if not building_id_by_code:
        return {}
    rows = [
        {"building_id": bid, "meter_type": "electricity", "unit": "kWh"}
        for bid in building_id_by_code.values()
    ]
    stmt = insert(Sensor).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Sensor.building_id, Sensor.meter_type],
        set_={"unit": stmt.excluded.unit},
    ).returning(Sensor.sensor_id, Sensor.building_id)
    result = session.execute(stmt)
    sensor_id_by_building_id = {row.building_id: row.sensor_id for row in result}
    return {
        code: sensor_id_by_building_id[bid]
        for code, bid in building_id_by_code.items()
        if bid in sensor_id_by_building_id
    }


def upsert_energy_measurements(
    session: Session, long_df: pd.DataFrame, sensor_id_by_building_code: dict[str, int]
) -> tuple[int, int]:
    """long_df has columns building_code, ts_utc, consumption_kwh. Returns
    (rows_read, rows_inserted)."""
    df = long_df.copy()
    df["sensor_id"] = df["building_code"].map(sensor_id_by_building_code)
    df = df.dropna(subset=["sensor_id"])
    rows_read = len(df)
    rows_inserted = 0

    n_chunks = math.ceil(len(df) / CHUNK_SIZE)
    for i in range(n_chunks):
        chunk = df.iloc[i * CHUNK_SIZE : (i + 1) * CHUNK_SIZE]
        values = [
            {
                "sensor_id": int(r.sensor_id),
                "ts": r.ts_utc.to_pydatetime(),
                "consumption_kwh": None if pd.isna(r.consumption_kwh) else float(r.consumption_kwh),
            }
            for r in chunk.itertuples()
        ]
        stmt = insert(EnergyMeasurement).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[EnergyMeasurement.sensor_id, EnergyMeasurement.ts],
            set_={"consumption_kwh": stmt.excluded.consumption_kwh},
        )
        session.execute(stmt)
        rows_inserted += len(values)

    return rows_read, rows_inserted


def upsert_weather_measurements(
    session: Session, long_df: pd.DataFrame, site_id_by_code: dict[str, int]
) -> tuple[int, int]:
    df = long_df.copy()
    df["site_pk"] = df["site_id"].map(site_id_by_code)
    df = df.dropna(subset=["site_pk"])
    rows_read = len(df)
    rows_inserted = 0

    weather_cols = {
        "airTemperature": "air_temp_c",
        "cloudCoverage": "cloud_coverage",
        "dewTemperature": "dew_temp_c",
        "precipDepth1HR": "precip_1hr_mm",
        "precipDepth6HR": "precip_6hr_mm",
        "seaLvlPressure": "sea_lvl_pressure",
        "windDirection": "wind_direction",
        "windSpeed": "wind_speed",
    }

    n_chunks = math.ceil(len(df) / CHUNK_SIZE)
    for i in range(n_chunks):
        chunk = df.iloc[i * CHUNK_SIZE : (i + 1) * CHUNK_SIZE]
        values = []
        for r in chunk.itertuples():
            row = {"site_id": int(r.site_pk), "ts": r.ts_utc.to_pydatetime()}
            for src_col, dest_col in weather_cols.items():
                val = getattr(r, src_col, None)
                row[dest_col] = None if pd.isna(val) else float(val)
            values.append(row)
        stmt = insert(WeatherMeasurement).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[WeatherMeasurement.site_id, WeatherMeasurement.ts],
            set_={dest: getattr(stmt.excluded, dest) for dest in weather_cols.values()},
        )
        session.execute(stmt)
        rows_inserted += len(values)

    return rows_read, rows_inserted


def _clean(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def _clean_int(value):
    value = _clean(value)
    return int(value) if value is not None else None
