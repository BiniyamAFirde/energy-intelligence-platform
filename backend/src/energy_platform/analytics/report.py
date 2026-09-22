"""python -m energy_platform.analytics.report

Generates a reproducible EDA report against the currently-loaded database:
a machine-readable JSON summary (data/processed/eda_report.json) plus eight
plots (reports/eda/*.png). Goes through the analytics/energy/buildings
service and repository layers throughout -- no ad-hoc SQL, no bypassing the
DB to re-read the raw CSVs.
"""

from __future__ import annotations

import json
from pathlib import Path

from energy_platform.db.base import SessionLocal
from energy_platform.ingestion import quality_report
from energy_platform.repositories import analytics as analytics_repo
from energy_platform.repositories import buildings as buildings_repo
from energy_platform.repositories import energy as energy_repo
from energy_platform.repositories import sites as sites_repo
from energy_platform.services import analytics_service
from energy_platform.services.resolvers import resolve_building_and_sensor
from energy_platform.services.stats import average_period_intensity, energy_intensity, pearson_correlation

OUTPUT_JSON = Path("data/processed/eda_report.json")
PLOTS_DIR = Path("reports/eda")

# All buildings/sites currently loaded share this timezone (see
# docs/data_selection.md) -- dataset-wide profiles use it directly rather
# than pretending to be timezone-agnostic.
DATASET_TZ = "US/Eastern"


def _relationship_correlations(comparison_rows: list[dict], metadata_by_id: dict[int, dict]) -> dict:
    """Exploratory only -- correlation, not causation. Every candidate field
    is reported with its actual sample size so a correlation computed from
    (e.g.) 8 buildings isn't presented with the same confidence as one from
    60."""
    mean_kwh = [r["mean_kwh"] for r in comparison_rows]
    area = [r["area_sqm"] for r in comparison_rows]

    results = {
        "area_sqm": dict(zip(("correlation", "n_pairs"), pearson_correlation(mean_kwh, area))),
    }

    for field in ("number_of_floors", "occupants", "year_built"):
        values = [metadata_by_id.get(r["building_id"], {}).get(field) for r in comparison_rows]
        corr, n = pearson_correlation(mean_kwh, values)
        results[field] = {"correlation": corr, "n_pairs": n}

    for eui_field in ("eui", "site_eui", "source_eui", "energy_star_rating"):
        n_populated = sum(
            1 for r in comparison_rows
            if metadata_by_id.get(r["building_id"], {}).get(eui_field) is not None
        )
        results[eui_field] = {
            "correlation": None,
            "n_pairs": n_populated,
            "note": f"not computed: only {n_populated}/{len(comparison_rows)} buildings have this field populated",
        }

    by_primary_use: dict[str, dict] = {}
    for r in comparison_rows:
        use = r["primary_use"] or "(unspecified)"
        by_primary_use.setdefault(use, []).append(r["mean_kwh"])
    results["mean_kwh_by_primary_use"] = {
        use: {"n_buildings": len(vals), "mean_of_means_kwh": sum(vals) / len(vals)}
        for use, vals in by_primary_use.items()
    }

    return results


def _energy_intensity_and_sensor_map(
    session, comparison_rows: list[dict]
) -> tuple[list[dict], dict[int, int]]:
    """kWh/m^2 -- distinct from (and never substituted for) the source EUI
    fields, which are documented separately as unpopulated in this subset.
    Also returns building_id -> sensor_id, resolved explicitly here (not
    assumed equal, even though they happen to coincide in this dataset)
    so the missing-data plot can key correctly off building_id."""
    results = []
    sensor_id_by_building_id: dict[int, int] = {}
    for r in comparison_rows:
        _, sensor_id = resolve_building_and_sensor(session, r["building_id"])
        sensor_id_by_building_id[r["building_id"]] = sensor_id

        daily_rows = energy_repo.aggregate(session, sensor_id, "daily", limit=1000)
        monthly_rows = energy_repo.aggregate(session, sensor_id, "monthly", limit=100)

        results.append({
            "building_id": r["building_id"],
            "building_code": r["building_code"],
            "total_kwh_per_sqm": energy_intensity(r["total_kwh"], r["area_sqm"]),
            "avg_daily_kwh_per_sqm": average_period_intensity(
                [float(d["total_kwh"]) for d in daily_rows if d["total_kwh"] is not None], r["area_sqm"]
            ),
            "avg_monthly_kwh_per_sqm": average_period_intensity(
                [float(m["total_kwh"]) for m in monthly_rows if m["total_kwh"] is not None], r["area_sqm"]
            ),
        })
    return results, sensor_id_by_building_id


def main() -> dict:
    session = SessionLocal()
    try:
        overview = analytics_service.get_dataset_overview(session)
        comparison_rows = analytics_service.get_building_comparison(session)

        all_buildings, _ = buildings_repo.list_buildings(session, limit=len(comparison_rows) + 10, offset=0)
        metadata_by_id = {
            b.building_id: {
                "number_of_floors": b.number_of_floors, "occupants": b.occupants,
                "year_built": b.year_built, "eui": b.eui, "site_eui": b.site_eui,
                "source_eui": b.source_eui, "energy_star_rating": b.energy_star_rating,
            }
            for b in all_buildings
        }

        relationships = _relationship_correlations(comparison_rows, metadata_by_id)
        intensity, sensor_id_by_building_id = _energy_intensity_and_sensor_map(session, comparison_rows)
        dataset_profile = analytics_service.get_dataset_wide_profile(session, tz_name=DATASET_TZ)
        quality = quality_report.main()

        sites, _ = sites_repo.list_sites(session, limit=50, offset=0)
        weather_energy_by_site = {}
        site_series = {}
        for site in sites:
            weather_energy_by_site[site.site_id] = analytics_service.get_site_weather_energy(session, site.site_id)
            site_series[site.site_id] = analytics_repo.site_weather_energy_series(session, site.site_id)

        report = {
            "dataset_overview": overview,
            "building_comparison": comparison_rows,
            "relationship_correlations": relationships,
            "energy_intensity": intensity,
            "weather_energy_by_site": weather_energy_by_site,
            "data_quality": quality,
        }

        OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_JSON.write_text(json.dumps(report, indent=2, default=str))

        _generate_plots(comparison_rows, dataset_profile, quality, site_series, sites, sensor_id_by_building_id)

        return report
    finally:
        session.close()


def _generate_plots(
    comparison_rows, dataset_profile, quality, site_series, sites, sensor_id_by_building_id
) -> None:
    from energy_platform.analytics import plots

    PLOTS_DIR.mkdir(parents=True, exist_ok=True)

    plots.plot_overall_consumption_over_time(dataset_profile["daily_total"], PLOTS_DIR / "01_overall_consumption.png")
    plots.plot_average_hourly_profile(dataset_profile["hour_of_day"], PLOTS_DIR / "02_hourly_profile.png")
    plots.plot_average_day_of_week_profile(dataset_profile["day_of_week"], PLOTS_DIR / "03_day_of_week_profile.png")
    plots.plot_monthly_consumption(dataset_profile["daily_total"], PLOTS_DIR / "04_monthly_consumption.png")
    plots.plot_building_comparison(comparison_rows, PLOTS_DIR / "05_building_comparison.png")
    plots.plot_peak_demand_distribution(comparison_rows, PLOTS_DIR / "06_peak_demand_distribution.png")

    first_site = sites[0]
    plots.plot_temperature_vs_energy(
        site_series[first_site.site_id], PLOTS_DIR / "07_temperature_vs_energy.png", first_site.site_id
    )

    null_rate_by_sensor = {
        int(sensor_id_str): v["null_rate"] for sensor_id_str, v in quality["sensors_with_gaps"].items()
    }
    null_rates_by_building = {
        building_id: null_rate_by_sensor.get(sensor_id, 0.0)
        for building_id, sensor_id in sensor_id_by_building_id.items()
    }
    plots.plot_missing_data_overview(comparison_rows, null_rates_by_building, PLOTS_DIR / "08_missing_data_overview.png")


if __name__ == "__main__":
    result = main()
    print(f"Report written to {OUTPUT_JSON}")
    print(f"Plots written to {PLOTS_DIR}/")
    print(json.dumps(result["dataset_overview"], indent=2, default=str))
