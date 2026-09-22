from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class WeatherMeasurementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ts: dt.datetime
    air_temp_c: float | None
    cloud_coverage: float | None
    dew_temp_c: float | None
    precip_1hr_mm: float | None
    precip_6hr_mm: float | None
    sea_lvl_pressure: float | None
    wind_direction: float | None
    wind_speed: float | None
