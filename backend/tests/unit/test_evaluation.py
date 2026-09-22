import numpy as np
import pandas as pd
import pytest

from energy_platform.anomalies.evaluation import (
    class_balance_report,
    confusion_matrix_at_points,
    evaluate_predictions,
    precision_recall_f1,
    recall_by_anomaly_type,
    restrict_to_split,
    select_threshold_by_f1,
    stitch_eval_series_for_split,
)


def _full_series(n_sensors=2, n_hours=400, seed=0):
    rng = np.random.default_rng(seed)
    frames = []
    for sid in range(1, n_sensors + 1):
        ts = pd.date_range("2017-06-01", periods=n_hours, freq="h", tz="UTC")
        values = 10 + rng.normal(0, 0.5, n_hours)
        frames.append(pd.DataFrame({"sensor_id": sid, "ts": ts, "consumption_kwh": values}))
    return pd.concat(frames, ignore_index=True)


def test_stitch_only_modifies_rows_inside_the_split():
    series = _full_series()
    split_start = series["ts"].iloc[100]
    split_end = series["ts"].iloc[200]

    eval_full, ground_truth = stitch_eval_series_for_split(
        series, split_start, split_end, split_label="validation", seed=42, n_per_type=3
    )
    assert (ground_truth["ts"] >= split_start).all() and (ground_truth["ts"] < split_end).all()

    outside = eval_full[(eval_full["ts"] < split_start) | (eval_full["ts"] >= split_end)]
    outside_original = series[(series["ts"] < split_start) | (series["ts"] >= split_end)]
    merged = outside.merge(outside_original, on=["sensor_id", "ts"], suffixes=("_eval", "_orig"))
    assert (merged["consumption_kwh_eval"] == merged["consumption_kwh_orig"]).all()
    assert len(merged) == len(outside)


def test_stitch_preserves_full_series_length_and_column_shape():
    series = _full_series()
    split_start = series["ts"].iloc[100]
    split_end = series["ts"].iloc[200]
    eval_full, _ = stitch_eval_series_for_split(series, split_start, split_end, "validation", seed=1, n_per_type=3)
    assert len(eval_full) == len(series)
    assert set(eval_full.columns) == set(series.columns)


def test_restrict_to_split():
    series = _full_series()
    split_start = series["ts"].iloc[50]
    split_end = series["ts"].iloc[150]
    restricted = restrict_to_split(series, "ts", split_start, split_end)
    assert (restricted["ts"] >= split_start).all()
    assert (restricted["ts"] < split_end).all()
    assert len(restricted) == len(series[(series.ts >= split_start) & (series.ts < split_end)])


def test_class_balance_report_matches_hand_counts():
    hourly = pd.DataFrame({"sensor_id": [1] * 100, "ts": pd.date_range("2017-01-01", periods=100, freq="h", tz="UTC")})
    ground_truth = pd.DataFrame({
        "sensor_id": [1, 1, 2],
        "ts": pd.to_datetime(["2017-01-01 01:00", "2017-01-01 02:00", "2017-01-01 03:00"], utc=True),
        "anomaly_type": ["spike", "spike", "drop"],
    })
    report = class_balance_report(hourly, ground_truth)
    assert report["total_observation_slots"] == 100
    assert report["injected_anomalies"] == 3
    assert report["normal_observation_slots"] == 97
    assert report["counts_by_type"] == {"spike": 2, "drop": 1}
    assert report["affected_sensors"] == 2
    assert report["anomaly_rate_pct"] == pytest.approx(3.0)


def test_confusion_matrix_hand_computed():
    ground_truth_keys = {(1, "a"), (1, "b"), (2, "c")}       # 3 positives
    predicted_keys = {(1, "a"), (1, "z")}                     # 1 correct (a), 1 spurious (z)
    cm = confusion_matrix_at_points(ground_truth_keys, predicted_keys, total_population=10)
    assert cm == {"tp": 1, "fp": 1, "fn": 2, "tn": 6}


def test_precision_recall_f1_values():
    cm = {"tp": 8, "fp": 2, "fn": 2, "tn": 88}
    metrics = precision_recall_f1(cm)
    assert metrics["precision"] == pytest.approx(0.8)
    assert metrics["recall"] == pytest.approx(0.8)
    assert metrics["f1"] == pytest.approx(0.8)
    assert metrics["false_positive_rate"] == pytest.approx(2 / 90)
    assert metrics["accuracy"] == pytest.approx(96 / 100)


def test_precision_recall_f1_handles_no_predictions():
    cm = {"tp": 0, "fp": 0, "fn": 5, "tn": 95}
    metrics = precision_recall_f1(cm)
    assert metrics["precision"] is None  # 0/0 undefined, not silently 0 or 1
    assert metrics["recall"] == 0.0
    assert metrics["f1"] is None


def test_precision_recall_f1_handles_no_positives_no_predictions():
    cm = {"tp": 0, "fp": 0, "fn": 0, "tn": 100}
    metrics = precision_recall_f1(cm)
    assert metrics["precision"] is None
    assert metrics["recall"] is None
    assert metrics["f1"] is None
    assert metrics["accuracy"] == pytest.approx(1.0)


def test_select_threshold_by_f1_picks_the_correct_threshold():
    # 3 true positives at score 5; 1 false positive that only appears at score>=2
    scored_df = pd.DataFrame({
        "sensor_id": [1, 1, 1, 2],
        "ts": ["a", "b", "c", "d"],
        "score": [5.0, 5.0, 5.0, 2.0],
    })
    ground_truth_keys = {(1, "a"), (1, "b"), (1, "c")}
    result = select_threshold_by_f1(
        scored_df, "score", ["sensor_id", "ts"], ground_truth_keys,
        total_population=100, candidate_thresholds=[1.0, 2.0, 3.0, 6.0],
    )
    # threshold=3.0 catches exactly the 3 true positives, no false positives -> perfect F1
    assert result["best"]["threshold"] == pytest.approx(3.0)
    assert result["best"]["f1"] == pytest.approx(1.0)
    assert len(result["sweep"]) == 4


def test_select_threshold_by_f1_breaks_ties_toward_lower_threshold():
    scored_df = pd.DataFrame({"sensor_id": [1, 1], "ts": ["a", "b"], "score": [5.0, 5.0]})
    ground_truth_keys = {(1, "a"), (1, "b")}
    result = select_threshold_by_f1(
        scored_df, "score", ["sensor_id", "ts"], ground_truth_keys,
        total_population=50, candidate_thresholds=[1.0, 3.0],  # both catch the same 2 points -> tied F1=1.0
    )
    assert result["best"]["threshold"] == pytest.approx(1.0)


def test_recall_by_anomaly_type():
    ground_truth = pd.DataFrame({
        "sensor_id": [1, 1, 2, 2],
        "ts": ["a", "b", "c", "d"],
        "anomaly_type": ["spike", "spike", "drop", "drop"],
    })
    predicted_keys = {(1, "a"), (2, "c"), (2, "d")}  # catches 1/2 spikes, 2/2 drops
    result = recall_by_anomaly_type(ground_truth, predicted_keys)
    assert result["spike"]["recall"] == pytest.approx(0.5)
    assert result["drop"]["recall"] == pytest.approx(1.0)


def test_evaluate_predictions_end_to_end_small_example():
    ground_truth = pd.DataFrame({
        "sensor_id": [1, 1], "ts": [pd.Timestamp("2017-01-01"), pd.Timestamp("2017-01-02")],
        "anomaly_type": ["spike", "drop"],
    })
    predicted_df = pd.DataFrame({"sensor_id": [1], "ts": [pd.Timestamp("2017-01-01")]})
    result = evaluate_predictions(ground_truth, predicted_df, total_population=1000)
    assert result["tp"] == 1
    assert result["fn"] == 1
    assert result["fp"] == 0
    assert "recall_by_anomaly_type" in result
    assert result["recall_by_anomaly_type"]["spike"]["recall"] == pytest.approx(1.0)
    assert result["recall_by_anomaly_type"]["drop"]["recall"] == pytest.approx(0.0)


def test_evaluate_predictions_handles_empty_predictions():
    ground_truth = pd.DataFrame({
        "sensor_id": [1], "ts": [pd.Timestamp("2017-01-01")], "anomaly_type": ["spike"],
    })
    predicted_df = pd.DataFrame(columns=["sensor_id", "ts"])
    result = evaluate_predictions(ground_truth, predicted_df, total_population=100)
    assert result["tp"] == 0
    assert result["fn"] == 1
