from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class BuildingAnalyticsSummaryOut(BaseModel):
    building_id: int
    building_code: str
    total_observations: int
    mean_kwh: float | None
    median_kwh: float | None
    min_kwh: float | None
    max_kwh: float | None
    stddev_kwh: float | None
    total_kwh: float | None
    coefficient_of_variation: float | None
    missing_observations: int
    first_timestamp: dt.datetime
    last_timestamp: dt.datetime


class HourOfDayPointOut(BaseModel):
    hour_of_day: int
    mean_kwh: float | None
    observation_count: int


class DayOfWeekPointOut(BaseModel):
    day_of_week: int  # Postgres convention: 0=Sunday .. 6=Saturday
    mean_kwh: float | None
    observation_count: int


class MonthPointOut(BaseModel):
    month: int  # 1=January .. 12=December
    mean_kwh: float | None
    total_kwh: float | None
    observation_count: int


class WeekdayWeekendPointOut(BaseModel):
    is_weekend: bool
    mean_kwh: float | None
    observation_count: int


class BuildingProfileOut(BaseModel):
    building_id: int
    timezone: str
    hour_of_day: list[HourOfDayPointOut]
    day_of_week: list[DayOfWeekPointOut]
    month: list[MonthPointOut]
    weekday_weekend: list[WeekdayWeekendPointOut]


class PeakPointOut(BaseModel):
    ts: dt.datetime
    consumption_kwh: float


class MonthlyPeakOut(BaseModel):
    month: int
    ts: dt.datetime
    consumption_kwh: float


class BuildingPeaksOut(BaseModel):
    building_id: int
    peak_kwh: float
    peak_timestamp: dt.datetime
    top_peaks: list[PeakPointOut]
    monthly_peaks: list[MonthlyPeakOut]


class BuildingComparisonRowOut(BaseModel):
    building_id: int
    building_code: str
    site_id: int
    primary_use: str | None
    area_sqm: float | None
    mean_kwh: float | None
    median_kwh: float | None
    total_kwh: float | None
    peak_kwh: float | None
    std_kwh: float | None
    coefficient_of_variation: float | None


class WeatherCorrelationOut(BaseModel):
    correlation: float | None
    n_pairs: int
    n_total: int


class DateRangeOut(BaseModel):
    first_timestamp: dt.datetime | None
    last_timestamp: dt.datetime | None


class SiteWeatherEnergyOut(BaseModel):
    site_id: int
    n_timestamps: int
    date_range: DateRangeOut
    correlations: dict[str, WeatherCorrelationOut]
