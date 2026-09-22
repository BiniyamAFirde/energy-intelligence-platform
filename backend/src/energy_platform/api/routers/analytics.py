"""Analytics endpoints. `/buildings/compare` is registered in this router
and must be included in main.py *before* buildings.router: both
"/api/v1/buildings/compare" and "/api/v1/buildings/{building_id}" are a
single path segment after "/buildings/", and Starlette matches routes in
registration order, coercing to the path param's type only after a
structural match -- so if {building_id} were registered first, a request to
"/compare" would match it and fail int coercion with a 422 instead of ever
reaching this route. See test_get_buildings_compare_route_not_shadowed in
test_api_analytics.py for the regression test."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from energy_platform.api.deps import get_db
from energy_platform.schemas.analytics import (
    BuildingAnalyticsSummaryOut,
    BuildingComparisonRowOut,
    BuildingPeaksOut,
    BuildingProfileOut,
    SiteWeatherEnergyOut,
)
from energy_platform.services import analytics_service

router = APIRouter(prefix="/api/v1", tags=["analytics"])


@router.get("/buildings/compare", response_model=list[BuildingComparisonRowOut])
def compare_buildings(
    site_id: int | None = Query(None, description="Filter by site"),
    primary_use: str | None = Query(None, description="Filter by primary space usage"),
    db: Session = Depends(get_db),
):
    return analytics_service.get_building_comparison(db, site_id=site_id, primary_use=primary_use)


@router.get("/buildings/{building_id}/analytics/summary", response_model=BuildingAnalyticsSummaryOut)
def get_building_analytics_summary(building_id: int, db: Session = Depends(get_db)):
    return analytics_service.get_building_analytics_summary(db, building_id)


@router.get("/buildings/{building_id}/analytics/profile", response_model=BuildingProfileOut)
def get_building_profile(building_id: int, db: Session = Depends(get_db)):
    return analytics_service.get_building_profile(db, building_id)


@router.get("/buildings/{building_id}/analytics/peaks", response_model=BuildingPeaksOut)
def get_building_peaks(
    building_id: int,
    top_n: int = Query(analytics_service.TOP_PEAKS_DEFAULT, ge=1, le=analytics_service.TOP_PEAKS_MAX),
    db: Session = Depends(get_db),
):
    return analytics_service.get_building_peaks(db, building_id, top_n)


@router.get("/sites/{site_id}/analytics/weather-energy", response_model=SiteWeatherEnergyOut)
def get_site_weather_energy(
    site_id: int,
    start: dt.datetime | None = Query(None, description="Inclusive start, ISO 8601 UTC"),
    end: dt.datetime | None = Query(None, description="Inclusive end, ISO 8601 UTC"),
    db: Session = Depends(get_db),
):
    return analytics_service.get_site_weather_energy(db, site_id, start, end)
