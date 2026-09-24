"""Request/response schemas for POST /api/v1/forecast (external-company
inference). Deliberately thin: field-level Pydantic checks here only cover
"is this the right shape" (types, obvious bounds, request-size limits) --
every semantic/domain rule (minimum history, duplicate/irregular
timestamps, negative values, timezone validity, ...) lives in exactly one
place, energy_platform.forecasting.external, and is enforced there so the
CLI and the API can never disagree. See docs/external_inference.md."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

# Generous upper bound protecting the process from an accidental/malicious
# multi-million-row request (the "Security / resource protection"
# requirement) -- 8760 = one full year of hourly readings, far more than
# any single day-ahead forecast request needs (the minimum is 168h/7 days),
# while still small in memory (a few hundred KB before feature generation).
MAX_ENERGY_OBSERVATIONS = 8760


class ExternalBuildingIn(BaseModel):
    # area_sqm/number_of_floors/occupants are deliberately plain floats here
    # (no gt=0 constraint): "must be positive" is a domain rule already
    # enforced, with one specific error message, by
    # forecasting.external.validate_building_metadata -- the same function
    # the CSV adapter uses. Duplicating the rule at the schema layer would
    # risk the API silently disagreeing with the CLI about what's valid, or
    # producing a different error message for the same violation.
    building_code: str = Field(
        ..., min_length=1, max_length=100,
        description="Any identifier you choose. Never validated against BDG2 or the production database.",
    )
    area_sqm: float = Field(..., description="Floor area, square meters. Must be positive.")
    number_of_floors: float = Field(..., description="Must be positive.")
    occupants: float = Field(..., description="Must be positive.")
    primary_use: str = Field(
        ..., min_length=1,
        description=(
            "Building-use category. Unrecognized values are NOT rejected -- the trained "
            "encoder uses handle_unknown='ignore' -- but the response's `warnings` field will "
            "flag it as lower-confidence."
        ),
    )
    timezone: str = Field(..., description="IANA timezone name, e.g. 'US/Eastern', 'America/Chicago'.")


class ExternalEnergyObservationIn(BaseModel):
    timestamp: str = Field(
        ...,
        description=(
            "Naive local time, 'YYYY-MM-DD HH:MM:SS' -- no UTC offset or 'Z' suffix. The "
            "building's own `timezone` field supplies the timezone."
        ),
    )
    energy_kwh: float = Field(..., description="Hourly electricity consumption, kWh.")


class ExternalForecastRequest(BaseModel):
    building: ExternalBuildingIn
    energy: list[ExternalEnergyObservationIn] = Field(
        ...,
        min_length=1,
        max_length=MAX_ENERGY_OBSERVATIONS,
        description=(
            "Recent hourly readings, one per hour, oldest first or last (order doesn't matter -- "
            "they're sorted internally). At least 168 consecutive, gap-free hourly observations "
            "are required before a forecast origin can be produced; see docs/external_inference.md."
        ),
    )


class ExternalForecastPointOut(BaseModel):
    target_ts: dt.datetime = Field(..., description="UTC. The hour this prediction is for.")
    horizon: int = Field(..., description="Hours ahead of the forecast origin (1-24).")
    predicted_kwh: float


class ExternalForecastResponse(BaseModel):
    building_code: str
    model_name: str = Field(..., description="Always 'random_forest' for this endpoint's current model.")
    model_version: str
    forecast_origin: dt.datetime = Field(
        ..., description="UTC. The local-midnight origin these 24 predictions were made from."
    )
    predictions: list[ExternalForecastPointOut] = Field(..., description="Always 24 rows, horizons 1-24.")
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal notices, e.g. an unrecognized primary_use. Empty when there are none.",
    )
