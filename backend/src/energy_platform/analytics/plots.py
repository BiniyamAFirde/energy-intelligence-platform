"""Eight reproducible EDA plots, each answering one specific analytical
question from the Phase 6 spec. Pure plotting functions: they take already-
computed data (from analytics_service / analytics_repo) and a save path,
never a DB session -- so they stay simple to reason about and don't need a
database to be importable."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: this runs in a container with no display
import matplotlib.pyplot as plt

DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def plot_overall_consumption_over_time(daily_total: list[dict], out_path: Path) -> None:
    days = [r["day"] for r in daily_total]
    totals = [float(r["total_kwh"]) for r in daily_total]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(days, totals, linewidth=0.8)
    ax.set_title("Total electricity consumption across all 60 buildings, daily")
    ax.set_xlabel("Date")
    ax.set_ylabel("Total kWh/day")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_average_hourly_profile(hour_of_day: list[dict], out_path: Path) -> None:
    hours = [int(r["hour_of_day"]) for r in hour_of_day]
    means = [float(r["mean_kwh"]) for r in hour_of_day]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(hours, means, marker="o")
    ax.set_title("Average hourly consumption profile (local time, all buildings)")
    ax.set_xlabel("Hour of day (local)")
    ax.set_ylabel("Mean kWh")
    ax.set_xticks(range(0, 24, 2))
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_average_day_of_week_profile(day_of_week: list[dict], out_path: Path) -> None:
    rows = sorted(day_of_week, key=lambda r: int(r["day_of_week"]))
    labels = [DOW_LABELS[int(r["day_of_week"])] for r in rows]
    means = [float(r["mean_kwh"]) for r in rows]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(labels, means)
    ax.set_title("Average consumption by day of week (local time, all buildings)")
    ax.set_ylabel("Mean kWh")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_monthly_consumption(daily_total: list[dict], out_path: Path) -> None:
    import pandas as pd

    df = pd.DataFrame(daily_total)
    df["day"] = pd.to_datetime(df["day"])
    monthly = df.groupby(df["day"].dt.to_period("M"))["total_kwh"].sum()
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar([str(p) for p in monthly.index], monthly.values.astype(float))
    ax.set_title("Total consumption by calendar month, all buildings")
    ax.set_ylabel("Total kWh")
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_building_comparison(comparison_rows: list[dict], out_path: Path, top_n: int = 20) -> None:
    rows = sorted(comparison_rows, key=lambda r: float(r["mean_kwh"]), reverse=True)[:top_n]
    labels = [r["building_code"] for r in rows]
    means = [float(r["mean_kwh"]) for r in rows]
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.barh(labels[::-1], means[::-1])
    ax.set_title(f"Mean hourly consumption by building (top {top_n} of {len(comparison_rows)})")
    ax.set_xlabel("Mean kWh")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_peak_demand_distribution(comparison_rows: list[dict], out_path: Path) -> None:
    peaks = [float(r["peak_kwh"]) for r in comparison_rows if r["peak_kwh"] is not None]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(peaks, bins=15)
    ax.set_title(f"Distribution of per-building peak demand (n={len(peaks)} buildings)")
    ax.set_xlabel("Peak hourly kWh")
    ax.set_ylabel("Number of buildings")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_temperature_vs_energy(series_rows: list[dict], out_path: Path, site_id: int) -> None:
    temps = [r["air_temp_c"] for r in series_rows if r["air_temp_c"] is not None]
    kwh = [r["total_kwh"] for r in series_rows if r["air_temp_c"] is not None]
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(temps, kwh, s=4, alpha=0.3)
    ax.set_title(f"Site {site_id}: outdoor air temperature vs. total site consumption")
    ax.set_xlabel("Air temperature (C)")
    ax.set_ylabel("Total kWh across site's buildings, that hour")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_missing_data_overview(comparison_rows: list[dict], null_rates: dict[int, float], out_path: Path) -> None:
    rows = sorted(comparison_rows, key=lambda r: null_rates.get(r["building_id"], 0), reverse=True)
    labels = [r["building_code"] for r in rows]
    rates = [null_rates.get(r["building_id"], 0) * 100 for r in rows]
    fig, ax = plt.subplots(figsize=(8, 10))
    ax.barh(labels[::-1], rates[::-1])
    ax.set_title("Missing/NULL electricity readings per building")
    ax.set_xlabel("% of hourly readings missing")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
