from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class SensorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sensor_id: int
    building_id: int
    meter_type: str
    unit: str
    created_at: dt.datetime
