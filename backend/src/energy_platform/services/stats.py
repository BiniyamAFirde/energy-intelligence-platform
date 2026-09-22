"""Pure statistical helpers -- no DB session, no FastAPI. Kept separate from
analytics_service.py's orchestration so the actual math is cheap to unit
test in isolation."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def coefficient_of_variation(mean: float | None, stddev: float | None) -> float | None:
    """CV = stddev / mean. Undefined (returned as None) when the mean is
    zero/None -- reporting "infinite variability" is not useful."""
    if mean is None or stddev is None or mean == 0:
        return None
    return float(stddev) / float(mean)


def energy_intensity(total_kwh: float | None, area_sqm: float | None) -> float | None:
    """kWh per square meter. None when area is missing/zero -- BDG2 leaves
    some buildings without an area, and dividing by zero/None is not a
    meaningful intensity."""
    if total_kwh is None or area_sqm is None or area_sqm == 0:
        return None
    return float(total_kwh) / float(area_sqm)


def average_period_intensity(period_totals_kwh: Sequence[float], area_sqm: float | None) -> float | None:
    """Mean of per-period (e.g. daily) totals, divided by area -- distinct
    from `total_kwh / area_sqm / n_periods` only in how it handles periods
    with zero observations (excluded here rather than averaged as zero)."""
    if area_sqm is None or area_sqm == 0 or not period_totals_kwh:
        return None
    totals = [float(v) for v in period_totals_kwh]
    return (sum(totals) / len(totals)) / float(area_sqm)


def pearson_correlation(
    xs: Sequence[float | None], ys: Sequence[float | None]
) -> tuple[float | None, int]:
    """Pearson correlation with pairwise NaN/None deletion: a pair is used
    only if both values are present. Returns (correlation, n_pairs_used) so
    callers can report how much of the requested data actually contributed
    -- essential here given how sparse some BDG2 weather columns are
    (cloud_coverage ~58% missing, precip_6hr_mm ~92% missing per Phase 4).
    Returns (None, n) when fewer than 2 usable pairs exist or either series
    has zero variance (correlation undefined)."""
    pairs = [
        (x, y) for x, y in zip(xs, ys)
        if x is not None and y is not None and not (isinstance(x, float) and math.isnan(x))
        and not (isinstance(y, float) and math.isnan(y))
    ]
    n = len(pairs)
    if n < 2:
        return None, n

    x_arr = np.array([p[0] for p in pairs], dtype=float)
    y_arr = np.array([p[1] for p in pairs], dtype=float)
    if np.std(x_arr) == 0 or np.std(y_arr) == 0:
        return None, n

    corr = float(np.corrcoef(x_arr, y_arr)[0, 1])
    return corr, n
