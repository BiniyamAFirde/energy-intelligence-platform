from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sensor_id: int
    ts: dt.datetime
    method: str
    anomaly_type: str
    detector_version: str
    severity: str
    score: float
    expected_value: float | None
    actual_value: float | None
    residual: float | None
    explanation: str
    resolved: bool
    created_at: dt.datetime


class AlertSummaryOut(BaseModel):
    total_alerts: int
    unresolved_alerts: int
    counts_by_method: dict[str, int]
    counts_by_anomaly_type: dict[str, int]
    counts_by_severity: dict[str, int]
