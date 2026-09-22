"""Phase 8 evaluation: precision/recall/F1/FPR for all four anomaly
detectors against the synthetic injection framework's ground truth
(injection.py) -- BDG2 has no real, trustworthy anomaly labels, so this
controlled substitute is the only benchmark available (see injection.py's
own module docstring for why).

Methodology (all four detectors evaluated identically):
  1. VALIDATION-period thresholds are selected by sweeping candidate
     values and picking the one that maximizes F1 against
     VALIDATION-period synthetic injections only.
  2. That exact threshold is then FROZEN and applied, unchanged, to
     TEST-period data -- TEST metrics are never used to pick or adjust
     anything. Standard train/validate/test discipline, applied to
     detector thresholds instead of model weights.
  3. Detector A has no single sweepable threshold (five independent
     rule-based checks, each with its own fixed, domain-reasoned
     constant, e.g. MIN_ZERO_RUN_HOURS) -- it is evaluated identically on
     both splits using its built-in constants, never tuned against
     either split's labels.

Binary evaluation: a point counts as a "positive" if it appears anywhere
in that split's synthetic ground truth, REGARDLESS of which of the six
injected anomaly_types produced it, and a detector's output counts as
"predicted positive" if it flagged that (sensor_id, ts) at all,
REGARDLESS of which anomaly_type the detector itself assigned. This tests
each detector as a general-purpose anomaly flag (the practical use case
-- "does this point need a human look") rather than requiring it to name
the exact injected mechanism. A secondary per-injected-type recall
breakdown (recall_by_anomaly_type) is reported alongside for diagnostic
insight, e.g. "Detector A catches 100% of stuck_meter but 0% of spike, by
design -- that is not a failure, it is a different detector's job."
"""

from __future__ import annotations

import pandas as pd


def stitch_eval_series_for_split(
    full_hourly_series: pd.DataFrame, split_start: pd.Timestamp, split_end_excl: pd.Timestamp,
    split_label: str, seed: int, n_per_type: int = 15,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Injects synthetic anomalies into ONLY the rows within
    [split_start, split_end_excl), then stitches the result back into a
    copy of the FULL series -- every other row, crucially including real
    TRAIN-period history immediately before the split, stays untouched.
    This lets causal, warm-up-dependent detectors (B: 14-day same-hour
    baseline; D: 168h rolling + B's own baseline as one of its features;
    C: 72h causal residual std) score the split's early rows using real
    preceding history, exactly as they would in production, instead of
    an artificially cold start at the split boundary. Returns
    (eval_series_full, ground_truth) -- ground_truth contains only the
    split's own injected points; the caller restricts final
    scoring/evaluation to the split's own rows via restrict_to_split."""
    from energy_platform.anomalies.injection import inject_anomalies

    split_slice = full_hourly_series[
        (full_hourly_series["ts"] >= split_start) & (full_hourly_series["ts"] < split_end_excl)
    ]
    injected_slice, ground_truth = inject_anomalies(split_slice, split=split_label, seed=seed, n_per_type=n_per_type)

    eval_full = full_hourly_series.merge(
        injected_slice[["sensor_id", "ts", "consumption_kwh"]].rename(columns={"consumption_kwh": "_injected"}),
        on=["sensor_id", "ts"], how="left",
    )
    eval_full["consumption_kwh"] = eval_full["_injected"].combine_first(eval_full["consumption_kwh"])
    eval_full = eval_full.drop(columns=["_injected"])
    return eval_full, ground_truth


def restrict_to_split(df: pd.DataFrame, ts_col: str, split_start: pd.Timestamp, split_end_excl: pd.Timestamp) -> pd.DataFrame:
    return df[(df[ts_col] >= split_start) & (df[ts_col] < split_end_excl)].reset_index(drop=True)


def class_balance_report(hourly_series_split: pd.DataFrame, ground_truth: pd.DataFrame) -> dict:
    """The class-balance/prevalence numbers required before any
    performance claim: total observation slots in the split (including
    missing/NaN ones -- those are valid detectable positions too, e.g.
    Detector A's "missing" check), how many were replaced by a synthetic
    injection, the resulting anomaly rate, counts per injected type, and
    how many distinct sensors were touched."""
    total_points = len(hourly_series_split)
    n_anomalies = len(ground_truth)
    return {
        "total_observation_slots": int(total_points),
        "injected_anomalies": int(n_anomalies),
        "normal_observation_slots": int(total_points - n_anomalies),
        "anomaly_rate_pct": round(100 * n_anomalies / total_points, 4) if total_points else None,
        "counts_by_type": {k: int(v) for k, v in ground_truth["anomaly_type"].value_counts().items()},
        "affected_sensors": int(ground_truth["sensor_id"].nunique()) if n_anomalies else 0,
    }


def confusion_matrix_at_points(ground_truth_keys: set, predicted_keys: set, total_population: int) -> dict:
    tp = len(ground_truth_keys & predicted_keys)
    fp = len(predicted_keys - ground_truth_keys)
    fn = len(ground_truth_keys - predicted_keys)
    tn = total_population - tp - fp - fn
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def precision_recall_f1(cm: dict) -> dict:
    """Accuracy is included but, per the Phase 8 spec's own caution, is
    barely meaningful here: with anomalies at well under 1% prevalence, a
    detector that never fires at all still scores >99% accuracy. Reported
    for completeness, not as the headline number."""
    tp, fp, fn, tn = cm["tp"], cm["fp"], cm["fn"], cm["tn"]
    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    if precision is None or recall is None:
        f1 = None
    elif (precision + recall) == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    fpr = fp / (fp + tn) if (fp + tn) > 0 else None
    total = tp + tn + fp + fn
    accuracy = (tp + tn) / total if total > 0 else None
    return {
        "precision": precision, "recall": recall, "f1": f1,
        "false_positive_rate": fpr, "accuracy": accuracy, **cm,
    }


def _keys_from_df(df: pd.DataFrame, key_cols: list[str]) -> set:
    return set(map(tuple, df[key_cols].itertuples(index=False, name=None)))


def select_threshold_by_f1(
    scored_df: pd.DataFrame, score_col: str, key_cols: list[str],
    ground_truth_keys: set, total_population: int, candidate_thresholds,
) -> dict:
    """Sweeps candidate_thresholds, computing precision/recall/F1 at each
    (predicted-positive = score >= threshold), and returns the full sweep
    plus the threshold with the highest F1 (ties broken toward the LOWER
    threshold -- i.e. preferring the higher-recall option when F1 ties,
    since a missed anomaly is arguably costlier than one extra review in
    this application). This must only ever be called with VALIDATION-
    period `scored_df`/`ground_truth_keys` -- the caller is responsible
    for that; this function has no way to enforce it itself."""
    scored = scored_df.dropna(subset=[score_col])
    sweep = []
    best = None
    for t in candidate_thresholds:
        predicted_keys = _keys_from_df(scored.loc[scored[score_col] >= t], key_cols)
        cm = confusion_matrix_at_points(ground_truth_keys, predicted_keys, total_population)
        metrics = precision_recall_f1(cm)
        entry = {"threshold": t, **metrics}
        sweep.append(entry)
        entry_f1 = entry["f1"] or 0.0
        best_f1 = (best["f1"] or 0.0) if best else -1.0
        if best is None or entry_f1 > best_f1 or (entry_f1 == best_f1 and t < best["threshold"]):
            best = entry
    return {"best": best, "sweep": sweep}


def recall_by_anomaly_type(ground_truth: pd.DataFrame, predicted_keys: set) -> dict:
    result = {}
    for anomaly_type, group in ground_truth.groupby("anomaly_type"):
        keys = set(zip(group["sensor_id"], group["ts"]))
        tp = len(keys & predicted_keys)
        result[anomaly_type] = {"n": len(keys), "tp": tp, "recall": (tp / len(keys)) if keys else None}
    return result


def evaluate_predictions(
    ground_truth: pd.DataFrame, predicted_df: pd.DataFrame, total_population: int,
) -> dict:
    """One-shot evaluation of an already-thresholded detector output
    (predicted_df: alerts-shaped, with sensor_id/ts columns) against a
    split's ground truth -- used for the frozen TEST-period evaluation,
    where there is no sweep, just the single frozen operating point."""
    ground_truth_keys = _keys_from_df(ground_truth, ["sensor_id", "ts"])
    predicted_keys = _keys_from_df(predicted_df, ["sensor_id", "ts"]) if len(predicted_df) else set()
    cm = confusion_matrix_at_points(ground_truth_keys, predicted_keys, total_population)
    metrics = precision_recall_f1(cm)
    metrics["recall_by_anomaly_type"] = recall_by_anomaly_type(ground_truth, predicted_keys)
    return metrics
