"""Pure validation/normalization helpers used by services. Kept dependency-
free (no DB session) so they're cheap to unit test directly."""

from __future__ import annotations

import datetime as dt

from energy_platform.services.exceptions import InvalidParameterError

GRANULARITIES = ("hourly", "daily", "weekly", "monthly")


def to_utc(value: dt.datetime) -> dt.datetime:
    """Normalize a datetime to UTC. Naive datetimes are assumed to already
    be UTC (FastAPI/Pydantic parses bare ISO timestamps as naive, and our
    API's documented contract is that unqualified timestamps are UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def validate_date_range(
    start: dt.datetime | None, end: dt.datetime | None
) -> tuple[dt.datetime | None, dt.datetime | None]:
    start_utc = to_utc(start) if start is not None else None
    end_utc = to_utc(end) if end is not None else None
    if start_utc is not None and end_utc is not None and start_utc > end_utc:
        raise InvalidParameterError(
            f"start ({start_utc.isoformat()}) must not be after end ({end_utc.isoformat()})"
        )
    return start_utc, end_utc


def validate_granularity(granularity: str) -> str:
    if granularity not in GRANULARITIES:
        raise InvalidParameterError(
            f"granularity must be one of {GRANULARITIES}, got {granularity!r}"
        )
    return granularity
