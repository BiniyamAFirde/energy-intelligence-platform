"""Seasonal-naive baselines: forecast(target_ts) = consumption(target_ts -
season_hours), looked up directly from the regularized hourly series (not
via the sparse lag_k columns in features.py, which only cover a handful of
specific k values -- a baseline needs an arbitrary lookback per horizon,
so it reads straight from the per-building hourly series instead).

Every model in Phase 7 -- Ridge, Random Forest, HistGradientBoosting -- must
be compared against these. A baseline that isn't beaten is not a reason to
ship the fancier model.
"""

from __future__ import annotations

import pandas as pd

SEASONAL_NAIVE_24H = 24
SEASONAL_NAIVE_168H = 168


def seasonal_naive_forecast(
    hourly_series: pd.DataFrame,
    targets: pd.DataFrame,
    season_hours: int,
    group_col: str = "sensor_id",
) -> pd.Series:
    """hourly_series: regularized per-building series with columns
    [group_col, "ts", "consumption_kwh"] (output of
    features.reindex_to_hourly_grid). targets: any frame with columns
    [group_col, "target_ts"]. Returns a Series aligned to targets.index:
    consumption_kwh observed exactly `season_hours` before each target_ts,
    for that same group -- NaN if that hour doesn't exist in the series
    (e.g. before the group's own history starts) or was itself missing."""
    lookup = hourly_series[[group_col, "ts", "consumption_kwh"]].rename(
        columns={"ts": "_lookup_ts", "consumption_kwh": "_forecast"}
    )
    query = targets[[group_col, "target_ts"]].copy()
    query["_lookup_ts"] = query["target_ts"] - pd.Timedelta(hours=season_hours)
    query["_orig_order"] = range(len(query))

    merged = query.merge(lookup, on=[group_col, "_lookup_ts"], how="left").sort_values("_orig_order")
    return merged["_forecast"].reset_index(drop=True)


def seasonal_naive_24h(hourly_series: pd.DataFrame, targets: pd.DataFrame, group_col: str = "sensor_id") -> pd.Series:
    return seasonal_naive_forecast(hourly_series, targets, SEASONAL_NAIVE_24H, group_col)


def seasonal_naive_168h(hourly_series: pd.DataFrame, targets: pd.DataFrame, group_col: str = "sensor_id") -> pd.Series:
    return seasonal_naive_forecast(hourly_series, targets, SEASONAL_NAIVE_168H, group_col)
