import datetime as dt

import pytest

from energy_platform.services.exceptions import InvalidParameterError
from energy_platform.services.validation import (
    to_utc,
    validate_date_range,
    validate_granularity,
)


def test_to_utc_assumes_naive_datetime_is_already_utc():
    naive = dt.datetime(2016, 1, 1, 12, 0)
    result = to_utc(naive)
    assert result.tzinfo == dt.timezone.utc
    assert result.hour == 12


def test_to_utc_converts_aware_datetime_to_utc():
    aware = dt.datetime(2016, 1, 1, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
    result = to_utc(aware)
    assert result == dt.datetime(2016, 1, 1, 17, 0, tzinfo=dt.timezone.utc)


def test_validate_date_range_accepts_none_bounds():
    start, end = validate_date_range(None, None)
    assert start is None and end is None


def test_validate_date_range_accepts_start_before_end():
    s = dt.datetime(2016, 1, 1)
    e = dt.datetime(2016, 1, 2)
    start, end = validate_date_range(s, e)
    assert start < end


def test_validate_date_range_rejects_start_after_end():
    s = dt.datetime(2016, 1, 2)
    e = dt.datetime(2016, 1, 1)
    with pytest.raises(InvalidParameterError):
        validate_date_range(s, e)


def test_validate_date_range_allows_equal_start_and_end():
    s = dt.datetime(2016, 1, 1, 12)
    start, end = validate_date_range(s, s)
    assert start == end


def test_validate_granularity_accepts_known_values():
    for g in ("hourly", "daily", "weekly", "monthly"):
        assert validate_granularity(g) == g


def test_validate_granularity_rejects_unknown_value():
    with pytest.raises(InvalidParameterError):
        validate_granularity("yearly")
