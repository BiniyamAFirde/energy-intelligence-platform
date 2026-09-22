"""Pure, DB-free transformation functions: timestamp normalization, reshaping,
deduplication, and numeric validation. Kept separate from loader.py so each
step can be unit-tested against small in-memory DataFrames instead of the
full 175 MB file.

BDG2 timestamps are local, timezone-naive "clock time" -- not explicitly
adjusted for daylight saving. Converting to UTC therefore has two edge cases
around DST transitions:
  - a "spring forward" gap (a clock time that never happened)
  - a "fall back" fold (a clock time that happened twice)
We resolve both conservatively rather than guessing: gaps are shifted forward
to the next valid time, folds are marked NaT (dropped as unparseable) and
counted, so the ingestion report shows exactly how many rows were affected
instead of silently mis-localizing them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class TransformStats:
    dst_ambiguous_dropped: int = 0
    duplicate_rows_dropped: int = 0
    negative_values_rejected: int = 0
    rows_in: int = 0
    rows_out: int = 0


def normalize_timestamps_to_utc(
    ts: pd.Series, tz_name: str, stats: TransformStats | None = None
) -> pd.Series:
    """Localize naive local timestamps to `tz_name`, then convert to UTC."""
    localized = ts.dt.tz_localize(
        tz_name, ambiguous="NaT", nonexistent="shift_forward"
    )
    if stats is not None:
        stats.dst_ambiguous_dropped += int(localized.isna().sum())
    return localized.dt.tz_convert("UTC")


def wide_electricity_to_long(
    df: pd.DataFrame, building_codes: list[str], tz_name: str, stats: TransformStats | None = None
) -> pd.DataFrame:
    """Reshape BDG2's wide electricity_cleaned.csv (1 column per building)
    into long (building_code, ts_utc, consumption_kwh) rows, for exactly the
    selected building subset. Missing readings are kept as NULL rows rather
    than dropped, so a gap in the source data is distinguishable from a gap
    in ingestion."""
    cols = ["timestamp"] + [c for c in building_codes if c in df.columns]
    subset = df[cols].copy()

    long_df = subset.melt(
        id_vars="timestamp", var_name="building_code", value_name="consumption_kwh"
    )
    long_df["ts_utc"] = normalize_timestamps_to_utc(long_df["timestamp"], tz_name, stats)
    long_df = long_df.dropna(subset=["ts_utc"])  # DST-ambiguous rows only

    before = len(long_df)
    long_df = long_df.drop_duplicates(subset=["building_code", "ts_utc"])
    if stats is not None:
        stats.duplicate_rows_dropped += before - len(long_df)

    negative_mask = long_df["consumption_kwh"] < 0
    if stats is not None:
        stats.negative_values_rejected += int(negative_mask.sum())
    long_df.loc[negative_mask, "consumption_kwh"] = np.nan

    return long_df[["building_code", "ts_utc", "consumption_kwh"]]


def weather_long_to_utc(
    df: pd.DataFrame, site_timezones: dict[str, str], stats: TransformStats | None = None
) -> pd.DataFrame:
    """weather.csv is already long (site_id, timestamp, ...); this just adds
    a UTC timestamp per row, since each site has its own local timezone."""
    # Built per-site and concatenated rather than assigned in-place: a
    # column pre-filled with pd.NaT defaults to naive datetime64[ns], which
    # cannot hold the tz-aware values normalize_timestamps_to_utc produces.
    per_site_frames = []
    for site_id, tz_name in site_timezones.items():
        site_rows = df[df["site_id"] == site_id].copy()
        if site_rows.empty:
            continue
        site_rows["ts_utc"] = normalize_timestamps_to_utc(
            site_rows["timestamp"], tz_name, stats
        )
        per_site_frames.append(site_rows)
    out = pd.concat(per_site_frames, ignore_index=True) if per_site_frames else df.iloc[0:0].assign(ts_utc=pd.Series(dtype="datetime64[ns, UTC]"))

    # NaT rows here are exactly the DST-ambiguous ones already counted
    # per-site above, so dropping them needs no further stats accounting.
    out = out.dropna(subset=["ts_utc"])

    before = len(out)
    out = out.drop_duplicates(subset=["site_id", "ts_utc"])
    if stats is not None:
        stats.duplicate_rows_dropped += before - len(out)

    return out
