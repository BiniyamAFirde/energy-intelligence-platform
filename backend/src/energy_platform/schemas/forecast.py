from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class ForecastPointOut(BaseModel):
    sensor_id: int
    target_ts: dt.datetime
    generated_at: dt.datetime
    model_name: str
    model_version: str
    horizon: int | None
    predicted_kwh: float
    actual_kwh: float | None
    residual: float | None
