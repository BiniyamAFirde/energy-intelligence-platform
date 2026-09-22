import numpy as np
import pandas as pd

from energy_platform.ingestion.transform import (
    TransformStats,
    normalize_timestamps_to_utc,
    weather_long_to_utc,
    wide_electricity_to_long,
)


def test_normalize_timestamps_to_utc_basic():
    ts = pd.Series(pd.to_datetime(["2016-06-01 12:00:00"]))
    result = normalize_timestamps_to_utc(ts, "US/Eastern")
    # US/Eastern is UTC-4 in June (daylight saving in effect)
    assert result.iloc[0] == pd.Timestamp("2016-06-01 16:00:00", tz="UTC")


def test_normalize_timestamps_handles_dst_fallback_ambiguous_time():
    # 2016-11-06 01:30 US/Eastern occurred twice (clocks fell back at 2am)
    ts = pd.Series(pd.to_datetime(["2016-11-06 01:30:00"]))
    stats = TransformStats()
    result = normalize_timestamps_to_utc(ts, "US/Eastern", stats)
    assert result.isna().all()
    assert stats.dst_ambiguous_dropped == 1


def test_normalize_timestamps_handles_dst_spring_gap():
    # 2016-03-13 02:30 US/Eastern never existed (clocks sprang forward at 2am)
    ts = pd.Series(pd.to_datetime(["2016-03-13 02:30:00"]))
    result = normalize_timestamps_to_utc(ts, "US/Eastern")
    # shifted forward to a valid instant, not dropped
    assert result.notna().all()


def test_wide_electricity_to_long_reshapes_and_filters_to_selected_buildings():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2016-06-01 00:00:00", "2016-06-01 01:00:00"]),
        "site_a_bldg1": [10.0, 12.0],
        "site_a_bldg2": [5.0, np.nan],
        "unselected_bldg": [1.0, 1.0],
    })
    long_df = wide_electricity_to_long(df, ["site_a_bldg1", "site_a_bldg2"], "US/Eastern")

    assert set(long_df["building_code"]) == {"site_a_bldg1", "site_a_bldg2"}
    assert len(long_df) == 4
    # missing reading preserved as NULL, not dropped
    missing_row = long_df[(long_df.building_code == "site_a_bldg2") & (long_df.consumption_kwh.isna())]
    assert len(missing_row) == 1


def test_wide_electricity_to_long_nulls_out_negative_values():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2016-06-01 00:00:00"]),
        "b1": [-5.0],
    })
    stats = TransformStats()
    long_df = wide_electricity_to_long(df, ["b1"], "US/Eastern", stats)
    assert long_df["consumption_kwh"].isna().all()
    assert stats.negative_values_rejected == 1


def test_wide_electricity_to_long_drops_duplicate_building_timestamp():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2016-06-01 00:00:00", "2016-06-01 00:00:00"]),
        "b1": [10.0, 99.0],
    })
    stats = TransformStats()
    long_df = wide_electricity_to_long(df, ["b1"], "US/Eastern", stats)
    assert len(long_df) == 1
    assert stats.duplicate_rows_dropped == 1


def test_weather_long_to_utc_applies_per_site_timezone():
    df = pd.DataFrame({
        "site_id": ["A", "B"],
        "timestamp": pd.to_datetime(["2016-06-01 12:00:00", "2016-06-01 12:00:00"]),
        "air_temperature": [20.0, 25.0],
    })
    result = weather_long_to_utc(df, {"A": "US/Eastern", "B": "Europe/London"})
    a_row = result[result.site_id == "A"].iloc[0]
    b_row = result[result.site_id == "B"].iloc[0]
    assert a_row["ts_utc"] == pd.Timestamp("2016-06-01 16:00:00", tz="UTC")
    assert b_row["ts_utc"] == pd.Timestamp("2016-06-01 11:00:00", tz="UTC")
