"""Orchestrates analytics repository queries into dashboard-ready shapes,
and owns the statistics that don't belong in SQL (coefficient of variation,
correlation). Building-scoped functions resolve the building's electricity
sensor and its site's timezone via services/resolvers.py rather than
duplicating that lookup."""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from energy_platform.ingestion.quality_report import expected_hourly_grid_size
from energy_platform.repositories import analytics as analytics_repo
from energy_platform.repositories import energy as energy_repo
from energy_platform.services.exceptions import NotFoundError
from energy_platform.services.resolvers import resolve_building_and_sensor, resolve_site_timezone
from energy_platform.services.stats import coefficient_of_variation, pearson_correlation
from energy_platform.services.validation import validate_date_range

TOP_PEAKS_DEFAULT = 10
TOP_PEAKS_MAX = 100

WEATHER_VARIABLES = ["air_temp_c", "dew_temp_c", "wind_speed", "cloud_coverage", "precip_1hr_mm"]


def get_building_analytics_summary(session: Session, building_id: int) -> dict:
    building, sensor_id = resolve_building_and_sensor(session, building_id)
    stats = energy_repo.summary(session, sensor_id)
    if stats is None:
        raise NotFoundError(f"building {building_id} has no energy measurements")

    expected = expected_hourly_grid_size()
    return {
        "building_id": building.building_id,
        "building_code": building.building_code,
        "total_observations": stats["total_observations"],
        "mean_kwh": stats["mean_kwh"],
        "median_kwh": stats["median_kwh"],
        "min_kwh": stats["min_kwh"],
        "max_kwh": stats["max_kwh"],
        "stddev_kwh": stats["stddev_kwh"],
        "total_kwh": stats["total_kwh"],
        "coefficient_of_variation": coefficient_of_variation(stats["mean_kwh"], stats["stddev_kwh"]),
        "missing_observations": max(expected - stats["total_observations"], 0),
        "first_timestamp": stats["first_ts"],
        "last_timestamp": stats["last_ts"],
    }


def get_building_profile(session: Session, building_id: int) -> dict:
    building, sensor_id = resolve_building_and_sensor(session, building_id)
    tz_name = resolve_site_timezone(session, building.site_id)

    return {
        "building_id": building.building_id,
        "timezone": tz_name,
        "hour_of_day": analytics_repo.hour_of_day_profile(session, sensor_id, tz_name),
        "day_of_week": analytics_repo.day_of_week_profile(session, sensor_id, tz_name),
        "month": analytics_repo.monthly_profile(session, sensor_id, tz_name),
        "weekday_weekend": analytics_repo.weekday_weekend_profile(session, sensor_id, tz_name),
    }


def get_building_peaks(session: Session, building_id: int, top_n: int = TOP_PEAKS_DEFAULT) -> dict:
    top_n = min(max(top_n, 1), TOP_PEAKS_MAX)
    building, sensor_id = resolve_building_and_sensor(session, building_id)
    tz_name = resolve_site_timezone(session, building.site_id)

    top_rows = energy_repo.list_measurements(session, sensor_id, top_n, order_by="consumption_desc")
    if not top_rows:
        raise NotFoundError(f"building {building_id} has no energy measurements")

    return {
        "building_id": building.building_id,
        "peak_kwh": float(top_rows[0].consumption_kwh),
        "peak_timestamp": top_rows[0].ts,
        "top_peaks": [
            {"ts": r.ts, "consumption_kwh": float(r.consumption_kwh)} for r in top_rows
        ],
        "monthly_peaks": [
            {
                "month": int(r["month"]),
                "ts": r["ts"],
                "consumption_kwh": float(r["consumption_kwh"]),
            }
            for r in analytics_repo.monthly_peaks(session, sensor_id, tz_name)
        ],
    }


def get_building_comparison(
    session: Session, site_id: int | None = None, primary_use: str | None = None
) -> list[dict]:
    rows = analytics_repo.building_comparison(session, site_id=site_id, primary_use=primary_use)
    for row in rows:
        row["coefficient_of_variation"] = coefficient_of_variation(row["mean_kwh"], row["std_kwh"])
    return rows


def get_dataset_overview(session: Session) -> dict:
    """Used by the CLI EDA report; not an API endpoint (nothing in the
    dashboard spec needs raw dataset-wide row counts over the wire)."""
    return analytics_repo.dataset_overview_counts(session)


def get_dataset_wide_profile(session: Session, tz_name: str = "US/Eastern") -> dict:
    """Aggregate views across every building in the loaded dataset -- used
    by the CLI EDA report, not exposed as an API endpoint (nothing in the
    spec calls for a cross-building time series over the wire). tz_name
    defaults to US/Eastern because every currently-selected site uses it
    (see docs/data_selection.md); this would need to become genuinely
    per-site if a future subset spans multiple timezones."""
    return {
        "daily_total": analytics_repo.dataset_daily_total(session, tz_name),
        "hour_of_day": analytics_repo.dataset_hour_of_day_profile(session, tz_name),
        "day_of_week": analytics_repo.dataset_day_of_week_profile(session, tz_name),
    }


def get_site_weather_energy(
    session: Session,
    site_id: int,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> dict:
    # resolve_site_timezone doubles as the existence check (404 if missing).
    resolve_site_timezone(session, site_id)
    start_utc, end_utc = validate_date_range(start, end)

    rows = analytics_repo.site_weather_energy_series(session, site_id, start_utc, end_utc)
    kwh_series = [r["total_kwh"] for r in rows]

    correlations = {}
    for var in WEATHER_VARIABLES:
        weather_series = [r[var] for r in rows]
        corr, n = pearson_correlation(kwh_series, weather_series)
        correlations[var] = {"correlation": corr, "n_pairs": n, "n_total": len(rows)}

    return {
        "site_id": site_id,
        "n_timestamps": len(rows),
        "date_range": {
            "first_timestamp": rows[0]["ts"] if rows else None,
            "last_timestamp": rows[-1]["ts"] if rows else None,
        },
        "correlations": correlations,
    }
