"""External-company inference endpoint: scores a company's OWN building
metadata + recent hourly energy observations against the already-trained
Random Forest, without requiring that company to be registered anywhere in
PostgreSQL. This is the API counterpart to scripts/predict_external.py --
both call the same energy_platform.forecasting.external adapter, so their
validation and prediction behavior is identical by construction. See
docs/external_inference.md.

Deliberately thin: no feature engineering, no pandas manipulation, no
joblib.load, no database session -- all of that lives in
forecasting/external.py and services/external_forecast_service.py."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from energy_platform.api.deps import get_forecast_pipeline
from energy_platform.schemas.external_forecast import ExternalForecastRequest, ExternalForecastResponse
from energy_platform.services import external_forecast_service

router = APIRouter(prefix="/api/v1", tags=["external-forecast"])


@router.post(
    "/forecast",
    response_model=ExternalForecastResponse,
    summary="Forecast an external company's building (no database registration required)",
    description=(
        "Scores a company's own building metadata + recent hourly energy observations against "
        "the already-trained Random Forest model -- pure inference, never retrains, never "
        "writes to the production database. Requires at least 168 consecutive, gap-free hourly "
        "observations (naive local time) and produces a 24-hour forecast from the most recent "
        "local-midnight origin. See docs/external_inference.md for the full contract, "
        "limitations, and the equivalent CLI (scripts/predict_external.py)."
    ),
)
def forecast_external_building(
    payload: ExternalForecastRequest,
    pipeline=Depends(get_forecast_pipeline),
) -> ExternalForecastResponse:
    return external_forecast_service.run_external_forecast(payload, pipeline)
