import numpy as np
import pandas as pd
import pytest

from energy_platform.forecasting.features import (
    add_calendar_features,
    add_lag_features,
    add_rolling_features,
    add_target_columns,
    expand_horizons,
    filter_origins_to_local_midnight,
    reindex_to_hourly_grid,
)


def _hourly_series(start, n, group="b1", values=None):
    ts = pd.date_range(start, periods=n, freq="h", tz="UTC")
    vals = values if values is not None else list(range(n))
    return pd.DataFrame({"sensor_id": [group] * n, "ts": ts, "consumption_kwh": vals})


def test_reindex_to_hourly_grid_fills_gaps_with_nan():
    df = _hourly_series("2016-01-01", 5)
    df = df.drop(index=2).reset_index(drop=True)  # remove the 3rd hour
    result = reindex_to_hourly_grid(df, "ts", group_col="sensor_id")
    assert len(result) == 5
    assert result["consumption_kwh"].isna().sum() == 1


def test_reindex_to_hourly_grid_keeps_groups_separate():
    a = _hourly_series("2016-01-01", 3, group="a")
    b = _hourly_series("2016-06-01", 3, group="b")
    df = pd.concat([a, b], ignore_index=True)
    result = reindex_to_hourly_grid(df, "ts", group_col="sensor_id")
    assert set(result["sensor_id"]) == {"a", "b"}
    assert len(result) == 6


def test_add_calendar_features_uses_local_time_not_utc():
    # 2016-06-15 16:00 UTC is EDT (UTC-4) -> local hour 12
    df = pd.DataFrame({"ts": [pd.Timestamp("2016-06-15 16:00:00", tz="UTC")]})
    result = add_calendar_features(df, "ts", "US/Eastern")
    assert result["hour"].iloc[0] == 12


def test_add_calendar_features_day_of_week_matches_postgres_convention():
    # 2016-06-11 is a Saturday; Postgres convention: Saturday=6
    df = pd.DataFrame({"ts": [pd.Timestamp("2016-06-11 16:00:00", tz="UTC")]})
    result = add_calendar_features(df, "ts", "US/Eastern")
    assert result["day_of_week"].iloc[0] == 6
    assert result["is_weekend"].iloc[0] == True  # noqa: E712

    # 2016-06-13 is a Monday; Postgres convention: Monday=1
    df2 = pd.DataFrame({"ts": [pd.Timestamp("2016-06-13 16:00:00", tz="UTC")]})
    result2 = add_calendar_features(df2, "ts", "US/Eastern")
    assert result2["day_of_week"].iloc[0] == 1
    assert result2["is_weekend"].iloc[0] == False  # noqa: E712


def test_add_calendar_features_cyclical_hour_wraps_around_midnight():
    df = pd.DataFrame({
        "ts": [pd.Timestamp("2016-06-15 04:00:00", tz="UTC"),   # local hour 0 (UTC-4)
               pd.Timestamp("2016-06-15 03:00:00", tz="UTC")],  # local hour 23 (prior day)
    })
    result = add_calendar_features(df, "ts", "US/Eastern")
    # hour 23 and hour 0 are adjacent on the clock -> cyclical distance small,
    # unlike the raw hour difference of 23
    dist = np.hypot(
        result["hour_sin"].iloc[0] - result["hour_sin"].iloc[1],
        result["hour_cos"].iloc[0] - result["hour_cos"].iloc[1],
    )
    assert dist < 0.3


def test_add_lag_features_lag1_equals_origin_value_itself():
    df = _hourly_series("2016-01-01", 5, values=[10, 20, 30, 40, 50])
    result = add_lag_features(df, "sensor_id", "consumption_kwh", lags=(1,))
    # lag_1 = consumption(T) itself
    assert result["lag_1"].tolist() == [10, 20, 30, 40, 50]


def test_add_lag_features_lag24_equals_24_hours_before():
    df = _hourly_series("2016-01-01", 30, values=list(range(30)))
    result = add_lag_features(df, "sensor_id", "consumption_kwh", lags=(24,))
    # lag_24 at row T = consumption(T - 23); row 25 (value=25) -> lag_24 = value at row 2 = 2
    assert result["lag_24"].iloc[25] == 2
    # first 23 rows have no valid lag_24 (insufficient history)
    assert result["lag_24"].iloc[:23].isna().all()


def test_add_lag_features_does_not_leak_across_building_boundary():
    a = _hourly_series("2016-01-01", 3, group="a", values=[1, 2, 3])
    b = _hourly_series("2016-01-01", 3, group="b", values=[100, 200, 300])
    df = pd.concat([a, b], ignore_index=True)
    result = add_lag_features(df, "sensor_id", "consumption_kwh", lags=(1, 2))
    b_rows = result[result.sensor_id == "b"].reset_index(drop=True)
    # building b's first row must not pull building a's value as a lag
    assert b_rows["lag_2"].iloc[0] != 3
    assert pd.isna(b_rows["lag_2"].iloc[0])


def test_add_rolling_features_excludes_the_origin_row_itself():
    """The critical leakage-prevention test: a rolling window computed at
    row T must never include T's own value."""
    # 25 hourly rows, value = row index; rolling_24h_mean at the last row
    # would be mean(1..24)=12.5 if T (=24) were wrongly included, or
    # mean(0..23)=11.5 if correctly excluded.
    df = _hourly_series("2016-01-01", 25, values=list(range(25)))
    result = add_rolling_features(df, "sensor_id", "consumption_kwh", specs=(("rolling_24h_mean", 24, "mean"),))
    assert result["rolling_24h_mean"].iloc[24] == pytest.approx(11.5)


def test_add_rolling_features_std_also_shifted():
    df = _hourly_series("2016-01-01", 26, values=[0] * 24 + [1000, 1000])
    result = add_rolling_features(df, "sensor_id", "consumption_kwh", specs=(("rolling_24h_std", 24, "std"),))
    # at row 24 (value=1000), the window (rows 0..23, all zeros) has std=0;
    # if 1000 leaked into its own window, std would be far from 0
    assert result["rolling_24h_std"].iloc[24] == 0.0


def test_add_target_columns_target_h1_is_next_hour_value():
    df = _hourly_series("2016-01-01", 5, values=[10, 20, 30, 40, 50])
    result = add_target_columns(df, "sensor_id", "consumption_kwh", horizons=range(1, 3))
    assert result["target_h1"].tolist()[:4] == [20.0, 30.0, 40.0, 50.0]
    assert np.isnan(result["target_h1"].iloc[4])
    assert result["target_h2"].tolist()[:3] == [30.0, 40.0, 50.0]
    assert result["target_h2"].iloc[3:].isna().all()


def test_add_target_columns_does_not_leak_across_building_boundary():
    a = _hourly_series("2016-01-01", 2, group="a", values=[1, 2])
    b = _hourly_series("2016-01-01", 2, group="b", values=[100, 200])
    df = pd.concat([a, b], ignore_index=True)
    result = add_target_columns(df, "sensor_id", "consumption_kwh", horizons=range(1, 2))
    a_rows = result[result.sensor_id == "a"].reset_index(drop=True)
    # building a's last row's target_h1 must not be building b's first value
    assert pd.isna(a_rows["target_h1"].iloc[-1])


def test_expand_horizons_produces_one_row_per_origin_per_horizon():
    df = _hourly_series("2016-01-01", 3, values=[10, 20, 30])
    with_targets = add_target_columns(df, "sensor_id", "consumption_kwh", horizons=range(1, 3))
    long_df = expand_horizons(with_targets, "ts", horizons=range(1, 3))
    assert len(long_df) == 3 * 2  # 3 origins x 2 horizons


def test_expand_horizons_target_ts_arithmetic():
    df = _hourly_series("2016-01-01", 2, values=[10, 20])
    with_targets = add_target_columns(df, "sensor_id", "consumption_kwh", horizons=range(1, 2))
    long_df = expand_horizons(with_targets, "ts", horizons=range(1, 2))
    row = long_df.iloc[0]
    assert row["target_ts"] == row["ts"] + pd.Timedelta(hours=1)
    assert row["horizon"] == 1


def test_filter_origins_to_local_midnight_keeps_only_daily_origins():
    # 48 hourly rows starting at UTC midnight = 2 local-midnight rows for a
    # UTC "site" (hour 0 and hour 24), since UTC local time == UTC here.
    df = _hourly_series("2016-01-01 00:00", 48)
    result = filter_origins_to_local_midnight(df, "ts", "UTC")
    assert len(result) == 2
    assert (result["ts"].dt.hour == 0).all()


def test_filter_origins_to_local_midnight_respects_site_timezone():
    # 2016-06-15 04:00 UTC is local midnight in US/Eastern (EDT, UTC-4)
    df = pd.DataFrame({
        "sensor_id": ["b1"] * 3,
        "ts": [
            pd.Timestamp("2016-06-15 03:00", tz="UTC"),  # local 23:00 (prev day)
            pd.Timestamp("2016-06-15 04:00", tz="UTC"),  # local 00:00 -- the only match
            pd.Timestamp("2016-06-15 05:00", tz="UTC"),  # local 01:00
        ],
    })
    result = filter_origins_to_local_midnight(df, "ts", "US/Eastern")
    assert len(result) == 1
    assert result["ts"].iloc[0] == pd.Timestamp("2016-06-15 04:00", tz="UTC")
