from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class SiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    site_id: int
    site_code: str
    timezone: str
    created_at: dt.datetime
