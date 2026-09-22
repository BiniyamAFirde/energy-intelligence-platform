from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class EnergyMeasurementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ts: dt.datetime
    consumption_kwh: float | None


class BuildingSummaryOut(BaseModel):
    building_id: int
    building_code: str
    total_observations: int
    mean_consumption_kwh: float | None
    min_consumption_kwh: float | None
    max_consumption_kwh: float | None
    stddev_consumption_kwh: float | None
    first_timestamp: dt.datetime
    last_timestamp: dt.datetime
    missing_observations: int


class EnergyAggregatePointOut(BaseModel):
    period_start: dt.datetime
    total_kwh: float | None
    mean_kwh: float | None
    observation_count: int
