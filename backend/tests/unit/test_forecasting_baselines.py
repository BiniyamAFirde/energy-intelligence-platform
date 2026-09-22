import numpy as np
import pandas as pd

from energy_platform.forecasting.baselines import seasonal_naive_24h, seasonal_naive_168h


def _series(n_hours, values, group="b1", start="2016-01-01"):
    ts = pd.date_range(start, periods=n_hours, freq="h", tz="UTC")
    return pd.DataFrame({"sensor_id": [group] * n_hours, "ts": ts, "consumption_kwh": values})


def test_seasonal_naive_24h_looks_up_value_from_previous_day_same_hour():
    series = _series(48, list(range(48)))
    targets = pd.DataFrame({
        "sensor_id": ["b1"],
        "target_ts": [pd.Timestamp("2016-01-02 05:00", tz="UTC")],  # hour index 29
    })
    result = seasonal_naive_24h(series, targets)
    # 24h before hour 29 is hour 5 -> value 5
    assert result.iloc[0] == 5


def test_seasonal_naive_168h_looks_up_value_from_previous_week_same_hour():
    series = _series(200, list(range(200)))
    targets = pd.DataFrame({
        "sensor_id": ["b1"],
        "target_ts": [series["ts"].iloc[180]],
    })
    result = seasonal_naive_168h(series, targets)
    assert result.iloc[0] == 180 - 168


def test_seasonal_naive_returns_nan_when_lookup_before_history_start():
    series = _series(10, list(range(10)))
    targets = pd.DataFrame({"sensor_id": ["b1"], "target_ts": [series["ts"].iloc[5]]})
    result = seasonal_naive_24h(series, targets)  # 24h before hour 5 doesn't exist
    assert np.isnan(result.iloc[0])


def test_seasonal_naive_does_not_mix_up_buildings():
    a = _series(48, [1.0] * 48, group="a")
    b = _series(48, [999.0] * 48, group="b")
    series = pd.concat([a, b], ignore_index=True)
    targets = pd.DataFrame({"sensor_id": ["a"], "target_ts": [a["ts"].iloc[30]]})
    result = seasonal_naive_24h(series, targets)
    assert result.iloc[0] == 1.0  # not 999.0 from building b


def test_seasonal_naive_preserves_target_row_order():
    series = _series(60, list(range(60)))
    targets = pd.DataFrame({
        "sensor_id": ["b1", "b1", "b1"],
        "target_ts": [series["ts"].iloc[50], series["ts"].iloc[30], series["ts"].iloc[40]],
    })
    result = seasonal_naive_24h(series, targets)
    assert result.tolist() == [26, 6, 16]
