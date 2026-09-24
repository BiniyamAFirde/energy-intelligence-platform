import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

from energy_platform.api.routers import (
    analytics,
    anomalies,
    buildings,
    energy,
    external_forecast,
    forecast,
    sensors,
    sites,
    weather,
)
from energy_platform.config import settings
from energy_platform.forecasting import external
from energy_platform.logging_config import configure_logging
from energy_platform.services.exceptions import InvalidParameterError, NotFoundError

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Loaded once here (via the process-level cached singleton, so a fresh
    # ASGI lifespan -- e.g. a new TestClient in tests -- reuses an
    # already-loaded Pipeline instead of re-reading the ~552MB artifact
    # every time) and kept in app.state for the life of the process. A
    # missing/corrupt artifact must fail startup loudly, not be swallowed --
    # so this call is deliberately not wrapped in try/except.
    logger.info("Loading forecast model artifact: %s", external.DEFAULT_ARTIFACT_PATH)
    app.state.forecast_pipeline = external.get_cached_pipeline()
    logger.info("Forecast model artifact loaded.")
    yield


app = FastAPI(title="Energy Intelligence Platform API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Domain exceptions raised anywhere in the service layer are translated here
# into HTTP responses with one consistent error shape ({"detail": ...}, the
# same shape FastAPI already uses for its own HTTPException and validation
# errors), so routers never need their own try/except blocks.
@app.exception_handler(NotFoundError)
def handle_not_found(request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(InvalidParameterError)
def handle_invalid_parameter(request: Request, exc: InvalidParameterError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# external.ExternalDataError: external-company input that's syntactically
# valid JSON/CSV but semantically invalid (bad timestamps, insufficient
# history, ...) -- 422, matching FastAPI's own convention for "well-formed
# request, invalid content" (its built-in Pydantic validation errors also
# return 422), distinct from InvalidParameterError's 400 (bad query params).
@app.exception_handler(external.ExternalDataError)
def handle_external_data_error(request: Request, exc: external.ExternalDataError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


app.include_router(sites.router)
# analytics.router defines /api/v1/buildings/compare -- must come before
# buildings.router's /api/v1/buildings/{building_id}, or "compare" would be
# matched (and 422-rejected) as a building_id. See analytics.py docstring.
app.include_router(analytics.router)
app.include_router(buildings.router)
app.include_router(sensors.router)
app.include_router(energy.router)
app.include_router(weather.router)
app.include_router(anomalies.router)
app.include_router(anomalies.building_router)
app.include_router(forecast.router)
app.include_router(external_forecast.router)

# Module-level engine: SQLAlchemy engines are meant to be created once and reused
# (they manage a connection pool internally), not recreated per request.
_engine = create_engine(settings.database_url, pool_pre_ping=True)


@app.get("/health")
def health() -> dict:
    """Liveness check: is the process up and serving requests."""
    return {"status": "ok"}


@app.get("/health/db")
def health_db() -> dict:
    """Readiness check: can we actually reach PostgreSQL."""
    with _engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable"}
