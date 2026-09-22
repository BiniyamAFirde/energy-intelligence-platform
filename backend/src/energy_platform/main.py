import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

from energy_platform.api.routers import analytics, anomalies, buildings, energy, forecast, sensors, sites, weather
from energy_platform.config import settings
from energy_platform.logging_config import configure_logging
from energy_platform.services.exceptions import InvalidParameterError, NotFoundError

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Energy Intelligence Platform API", version="0.1.0")

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
