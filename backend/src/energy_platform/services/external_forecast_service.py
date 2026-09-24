"""Thin service layer for POST /api/v1/forecast: converts the validated
request schema into the plain dicts energy_platform.forecasting.external
expects, calls the shared inference adapter (no DB session, no feature
engineering, no model loading here), and shapes the result into the
response schema. Mirrors forecast_service.py's role for the existing
GET /buildings/{id}/forecast endpoint -- routers stay thin, services hold
the (here: minimal) orchestration."""

from __future__ import annotations

from energy_platform.forecasting import external
from energy_platform.schemas.external_forecast import (
    ExternalForecastPointOut,
    ExternalForecastRequest,
    ExternalForecastResponse,
)


def run_external_forecast(request: ExternalForecastRequest, pipeline) -> ExternalForecastResponse:
    """`pipeline` must already be loaded (see api/deps.py::get_forecast_pipeline,
    sourced from app.state at application startup) -- this function never
    calls joblib.load or .fit(), only external.run_external_inference_from_payload,
    which in turn only calls pipeline.predict()."""
    building_data = request.building.model_dump()
    energy_rows = [obs.model_dump() for obs in request.energy]

    result = external.run_external_inference_from_payload(
        building_data, energy_rows, pipeline, external.DEFAULT_ARTIFACT_PATH
    )

    predictions = [
        ExternalForecastPointOut(
            target_ts=row.target_ts, horizon=int(row.horizon), predicted_kwh=float(row.predicted_kwh)
        )
        for row in result.output.itertuples()
    ]
    warnings = [result.primary_use_warning] if result.primary_use_warning else []

    return ExternalForecastResponse(
        building_code=result.building.building_code,
        model_name=result.model_name,
        model_version=result.model_version,
        forecast_origin=result.forecast_origin,
        predictions=predictions,
        warnings=warnings,
    )
