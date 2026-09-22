from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class BuildingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    building_id: int
    building_code: str
    site_id: int

    # Core: used by the analytics/ML pipeline
    primary_use: str | None
    area_sqm: float | None

    # Extended BDG2 metadata: retained for completeness / future analysis
    sub_primary_use: str | None
    latitude: float | None
    longitude: float | None
    year_built: int | None
    number_of_floors: int | None
    occupants: int | None
    industry: str | None
    subindustry: str | None
    heating_type: str | None
    eui: float | None
    site_eui: float | None
    source_eui: float | None
    leed_level: str | None
    energy_star_rating: int | None

    created_at: dt.datetime
