import numpy as np
import pandas as pd
import pytest

from energy_platform.anomalies.injection import ANOMALY_TYPES, inject_anomalies


def _clean_series(n_sensors=5, n_hours=500, seed=0):
    rng = np.random.default_rng(seed)
    frames = []
    for sid in range(1, n_sensors + 1):
        ts = pd.date_range("2017-07-01", periods=n_hours, freq="h", tz="UTC")
        values = 10 + rng.normal(0, 1, n_hours)
        frames.append(pd.DataFrame({"sensor_id": sid, "ts": ts, "consumption_kwh": values}))
    return pd.concat(frames, ignore_index=True)


def test_injection_never_mutates_the_input_series():
    series = _clean_series()
    original = series.copy(deep=True)
    inject_anomalies(series, split="validation", seed=42, n_per_type=3)
    pd.testing.assert_frame_equal(series, original)


def test_injection_is_reproducible_for_a_fixed_seed():
    series = _clean_series()
    eval1, gt1 = inject_anomalies(series, split="validation", seed=123, n_per_type=3)
    eval2, gt2 = inject_anomalies(series, split="validation", seed=123, n_per_type=3)
    pd.testing.assert_frame_equal(eval1, eval2)
    pd.testing.assert_frame_equal(gt1.reset_index(drop=True), gt2.reset_index(drop=True))


def test_injection_produces_different_results_for_different_seeds():
    series = _clean_series()
    _, gt1 = inject_anomalies(series, split="validation", seed=1, n_per_type=3)
    _, gt2 = inject_anomalies(series, split="validation", seed=2, n_per_type=3)
    assert not gt1[["sensor_id", "ts"]].equals(gt2[["sensor_id", "ts"]])


def test_ground_truth_has_all_six_anomaly_types():
    series = _clean_series()
    _, gt = inject_anomalies(series, split="validation", seed=42, n_per_type=5)
    assert set(gt["anomaly_type"].unique()) == set(ANOMALY_TYPES)


def test_ground_truth_schema_matches_synthetic_anomalies_table():
    series = _clean_series()
    _, gt = inject_anomalies(series, split="test", seed=42, n_per_type=3)
    expected_cols = {"sensor_id", "ts", "anomaly_type", "original_value", "injected_value", "split", "seed"}
    assert expected_cols.issubset(set(gt.columns))
    assert (gt["split"] == "test").all()
    assert (gt["seed"] == 42).all()


def test_spike_increases_value_and_drop_decreases_value():
    series = _clean_series()
    _, gt = inject_anomalies(series, split="validation", seed=42, n_per_type=10)
    spikes = gt[gt.anomaly_type == "spike"]
    drops = gt[gt.anomaly_type == "drop"]
    assert len(spikes) > 0 and len(drops) > 0
    assert (spikes["injected_value"] > spikes["original_value"]).all()
    assert (drops["injected_value"] < drops["original_value"]).all()


def test_zero_consumption_injects_exactly_zero():
    series = _clean_series()
    _, gt = inject_anomalies(series, split="validation", seed=42, n_per_type=10)
    zeros = gt[gt.anomaly_type == "zero_consumption"]
    assert len(zeros) > 0
    assert (zeros["injected_value"] == 0.0).all()


def test_stuck_meter_span_is_constant():
    """A sensor can receive more than one separate stuck-meter run (each
    internally constant, but at a *different* value from other runs) --
    so this groups by contiguous run (a gap in timestamps starts a new
    run), not just by sensor, before asserting constancy within a run."""
    series = _clean_series()
    eval_series, gt = inject_anomalies(series, split="validation", seed=42, n_per_type=10)
    stuck = gt[gt.anomaly_type == "stuck_meter"].sort_values(["sensor_id", "ts"])
    assert len(stuck) > 0

    n_runs_checked = 0
    for sid, group in stuck.groupby("sensor_id"):
        ts_sorted = group["ts"].tolist()
        gaps = [ts_sorted[i] - ts_sorted[i - 1] for i in range(1, len(ts_sorted))]
        run_id = [0]
        for gap in gaps:
            run_id.append(run_id[-1] + (1 if gap > pd.Timedelta(hours=1) else 0))
        group = group.assign(run_id=run_id)
        for _, run_rows in group.groupby("run_id"):
            values = eval_series[
                (eval_series.sensor_id == sid) & (eval_series.ts.isin(run_rows["ts"]))
            ]["consumption_kwh"]
            assert values.nunique() == 1
            n_runs_checked += 1
    assert n_runs_checked > 0


def test_injected_spans_do_not_overlap_within_a_sensor():
    series = _clean_series(n_sensors=2, n_hours=800)
    _, gt = inject_anomalies(series, split="validation", seed=42, n_per_type=15)
    for sid, group in gt.groupby("sensor_id"):
        ts_list = group["ts"].tolist()
        assert len(ts_list) == len(set(ts_list)), f"sensor {sid} has overlapping injected timestamps"


def test_eval_series_matches_original_everywhere_except_injected_points():
    series = _clean_series()
    eval_series, gt = inject_anomalies(series, split="validation", seed=42, n_per_type=5)
    injected_keys = set(zip(gt["sensor_id"], gt["ts"]))

    merged = series.merge(eval_series, on=["sensor_id", "ts"], suffixes=("_orig", "_eval"))
    for _, row in merged.iterrows():
        key = (row["sensor_id"], row["ts"])
        if key in injected_keys:
            continue
        assert row["consumption_kwh_orig"] == pytest.approx(row["consumption_kwh_eval"])
