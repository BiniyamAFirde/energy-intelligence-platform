"""Pure, DB-free feature engineering. No DB session here -- dataset.py
retrieves data and calls into this module for transformations, so the
actual math is cheap to unit test against small synthetic DataFrames.

Vocabulary (see docs/forecasting.md for the full formulation):
  - "origin" T = the most recent hour with a real observation; a forecast
    is made FROM this point.
  - horizon h in {1..24}: we predict consumption at (T + h hours).
  - Calendar features describe the TARGET timestamp (T + h) -- calendar
    information for a known future hour is legitimately available in
    advance (we know in advance that tomorrow 15:00 is a Tuesday).
  - Lag features describe the ORIGIN: lag_k = consumption(T - k + 1), so
    lag_1 = consumption(T) itself (the most recent known reading).
  - Rolling features are shifted to use only hours strictly BEFORE T:
    rolling_24h_mean(T) = mean(consumption(T-24 .. T-1)).

Day-of-week convention matches Phase 6's API (schemas/analytics.py):
Postgres's 0=Sunday..6=Saturday, NOT pandas' native Monday=0 convention --
translated explicitly below so a "day_of_week" value means the same thing
everywhere in this codebase.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

LAG_HOURS = (1, 2, 3, 24, 48, 168)
ROLLING_SPECS = (("rolling_24h_mean", 24, "mean"), ("rolling_24h_std", 24, "std"), ("rolling_168h_mean", 168, "mean"))
MAX_LOOKBACK_HOURS = max(LAG_HOURS + tuple(spec[1] for spec in ROLLING_SPECS))  # 168: warm-up needed before first usable origin


def reindex_to_hourly_grid(
    df: pd.DataFrame, ts_col: str, group_col: str | None = None
) -> pd.DataFrame:
    """Reindex onto a complete, regular hourly DatetimeIndex per group, so
    positional operations like .shift(n) are guaranteed to look exactly n
    wall-clock hours back -- without this, the small DST-related gaps in
    the source data (Phase 4: 4 missing hours/sensor) would silently make
    a "lag_24" occasionally read a value that's actually 23 or 25 hours
    back instead of 24. Newly-introduced rows from filling a gap get
    consumption_kwh = NaN, same as a pre-existing missing reading -- both
    are "we don't know this value," handled identically downstream.
    """
    def _reindex_one(g: pd.DataFrame) -> pd.DataFrame:
        full_range = pd.date_range(g[ts_col].min(), g[ts_col].max(), freq="h")
        g = g.set_index(ts_col).reindex(full_range)
        g.index.name = ts_col
        return g.reset_index()

    if group_col is None:
        return _reindex_one(df)

    return (
        df.groupby(group_col, group_keys=True)
        .apply(_reindex_one, include_groups=False)
        .reset_index(level=0)
        .reset_index(drop=True)
    )


def filter_origins_to_local_midnight(df: pd.DataFrame, ts_col: str, tz_name: str) -> pd.DataFrame:
    """Restricts forecast origins to local midnight only -- one forecast
    per building per day, predicting the entirety of the next calendar day
    (hours 1-24). This is the standard day-ahead formulation (a forecast
    generated once daily, not continuously every hour): it matches the
    phase spec's own example (generated_at = ...00:00), and avoids treating
    every single hour as an independent forecast origin, which would both
    inflate the dataset ~24x for little statistical benefit (consecutive
    hourly origins share nearly identical lag histories) and blow past
    laptop-sized memory once expanded to (origin, horizon) rows."""
    local_hour = df[ts_col].dt.tz_convert(tz_name).dt.hour
    return df[local_hour == 0].reset_index(drop=True)


def add_calendar_features(df: pd.DataFrame, ts_col: str, tz_name: str) -> pd.DataFrame:
    """Adds calendar features describing df[ts_col] in local time. Caller
    decides whether ts_col holds the origin or the target timestamp --
    for the target (the correct choice for a day-ahead model), see the
    module docstring."""
    df = df.copy()
    local_ts = df[ts_col].dt.tz_convert(tz_name)

    df["hour"] = local_ts.dt.hour
    df["day_of_week"] = (local_ts.dt.dayofweek + 1) % 7  # -> Postgres convention: 0=Sunday
    df["day_of_month"] = local_ts.dt.day
    df["month"] = local_ts.dt.month
    df["is_weekend"] = df["day_of_week"].isin([0, 6])

    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["dow_sin"] = np.sin(2 * np.pi * df["day_of_week"] / 7)
    df["dow_cos"] = np.cos(2 * np.pi * df["day_of_week"] / 7)
    df["month_sin"] = np.sin(2 * np.pi * (df["month"] - 1) / 12)
    df["month_cos"] = np.cos(2 * np.pi * (df["month"] - 1) / 12)
    return df


def add_lag_features(
    df: pd.DataFrame, group_col: str, target_col: str, lags: tuple[int, ...] = LAG_HOURS
) -> pd.DataFrame:
    """df must already be on a regular hourly grid (reindex_to_hourly_grid)
    and sorted by (group_col, ts) -- positional .shift() is only correct
    under that precondition. lag_k = consumption(T - k + 1): shift(k - 1)."""
    df = df.copy()
    grouped = df.groupby(group_col)[target_col]
    for k in lags:
        df[f"lag_{k}"] = grouped.shift(k - 1)
    return df


def add_rolling_features(
    df: pd.DataFrame, group_col: str, target_col: str, specs: tuple = ROLLING_SPECS
) -> pd.DataFrame:
    """Same regular-hourly-grid precondition as add_lag_features. Every
    window is computed on shift(1) of the target -- i.e. only hours
    strictly before the origin -- per the explicit Phase 7 requirement
    that rolling features must never touch the origin's own value."""
    df = df.copy()
    shifted = df.groupby(group_col)[target_col].shift(1)
    for name, window, stat in specs:
        rolled = shifted.groupby(df[group_col]).rolling(window, min_periods=window)
        result = getattr(rolled, stat)()
        df[name] = result.reset_index(level=0, drop=True)
    return df


def add_target_columns(
    df: pd.DataFrame, group_col: str, target_col: str, horizons: range = range(1, 25)
) -> pd.DataFrame:
    """One column per horizon (target_h1..target_h24), each a vectorized
    negative shift on the *same regular hourly grid* used for lags: at row
    T, target_hN = consumption(T + N). Deliberately not a per-row Python
    loop with a dict lookup -- with 60 buildings x ~17.5k hours, that would
    be slow and is unnecessary when the grid is already regular."""
    df = df.copy()
    grouped = df.groupby(group_col)[target_col]
    for h in horizons:
        df[f"target_h{h}"] = grouped.shift(-h)
    return df


def expand_horizons(df_with_targets: pd.DataFrame, ts_col: str, horizons: range = range(1, 25)) -> pd.DataFrame:
    """Reshape from one row per origin (with target_h1..target_hN columns)
    to one row per (origin, horizon) -- the "direct multi-horizon,
    horizon-as-feature" strategy (docs/forecasting.md): a single model
    learns f(origin_features, horizon) -> consumption(origin + horizon
    hours), rather than 24 independent models or a recursive one-step
    model. Implemented as a vectorized pandas melt, not a Python loop."""
    target_cols = [f"target_h{h}" for h in horizons]
    id_cols = [c for c in df_with_targets.columns if c not in target_cols]

    long_df = df_with_targets.melt(
        id_vars=id_cols, value_vars=target_cols, var_name="_h", value_name="target_kwh"
    )
    long_df["horizon"] = long_df["_h"].str.removeprefix("target_h").astype(int)
    long_df["target_ts"] = long_df[ts_col] + pd.to_timedelta(long_df["horizon"], unit="h")
    return long_df.drop(columns=["_h"])
