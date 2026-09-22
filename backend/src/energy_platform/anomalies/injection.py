"""Synthetic anomaly injection for detector evaluation.

BDG2 has no reliable anomaly ground truth (Phase 8 spec) -- naturally
"weird-looking" readings in real data are not trustworthy labels; treating
them as such would be fabricating ground truth, not measuring detectors
against it. This module builds a controlled, reproducible substitute
instead.

CRITICAL invariant: this module NEVER writes to `energy_measurements`.
Injection produces two things, both purely additive/in-memory or in a
dedicated table:
  1. An "evaluation series" -- an in-memory COPY of real historical
     readings with specific points overridden -- fed to detectors instead
     of the real data during evaluation.
  2. A ground-truth record (matching the `synthetic_anomalies` table
     schema) of exactly what was changed, for scoring.

Anomalies are injected only into the VALIDATION and TEST periods (never
TRAIN): this is what keeps Isolation Forest's fit uncontaminated (Detector
D fits on clean TRAIN data only) without any special-casing -- there is
simply nothing injected in the period it's fit on.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

ANOMALY_TYPES = (
    "spike", "drop", "sustained_high", "sustained_low", "stuck_meter", "zero_consumption",
)

# Duration ranges (hours) and magnitude ranges, per type. Kept modest and
# documented rather than tuned for detectability -- the point is a
# realistic, reproducible benchmark, not an easy one.
_SPIKE_MULTIPLIER = (3.0, 6.0)
_DROP_FRACTION = (0.05, 0.3)
_SUSTAINED_HIGH_MULTIPLIER = (1.6, 2.5)
_SUSTAINED_LOW_FRACTION = (0.2, 0.5)
_SUSTAINED_DURATION_HOURS = (6, 24)
_ZERO_DURATION_HOURS = (1, 4)
# stuck_meter gets its OWN (longer) duration range, separate from
# zero_consumption's: Phase 8's real evaluation run found that ~21/60
# BDG2 sensors report daily-resolution readings replicated across all 24
# hours, a structural characteristic (not an anomaly) that produces
# natural ~24h flat runs -- so Detector A's stuck_meter check now
# requires MIN_STUCK_RUN_HOURS=30 to tell the two apart (see
# detector_data_quality.py's module docstring). A synthetic stuck_meter
# injection shorter than that threshold could never be detectable by the
# rule-based detector it's meant to benchmark, so injected spans are kept
# comfortably above 30h -- while zero_consumption (unaffected by the
# daily-resolution issue, since MIN_ZERO_RUN_HOURS=3 saw no equivalent
# flood) stays short.
_STUCK_METER_DURATION_HOURS = (36, 72)


@dataclass
class InjectedAnomaly:
    sensor_id: int
    ts: pd.Timestamp
    anomaly_type: str
    original_value: float
    injected_value: float
    split: str
    seed: int


def _duration(rng: np.random.Generator, bounds: tuple[int, int]) -> int:
    return int(rng.integers(bounds[0], bounds[1] + 1))


def _pick_start_points(
    rng: np.random.Generator, candidates: pd.DataFrame, n: int, duration: int, used: set[tuple[int, pd.Timestamp]],
) -> list[tuple[int, pd.Timestamp]]:
    """Rejection-samples n (sensor_id, start_ts) pairs from candidates such
    that the full [start, start+duration) span doesn't overlap any
    already-used point for that sensor (keeps injected spans from
    overlapping each other, so each evaluation point has one unambiguous
    label) and stays within that sensor's contiguous data."""
    by_sensor = {sid: g.sort_values("ts").reset_index(drop=True) for sid, g in candidates.groupby("sensor_id")}
    sensor_ids = list(by_sensor.keys())
    chosen: list[tuple[int, pd.Timestamp]] = []
    attempts = 0
    max_attempts = n * 200
    while len(chosen) < n and attempts < max_attempts:
        attempts += 1
        sid = sensor_ids[rng.integers(0, len(sensor_ids))]
        g = by_sensor[sid]
        if len(g) <= duration:
            continue
        idx = int(rng.integers(0, len(g) - duration))
        start_ts = g["ts"].iloc[idx]
        span = pd.date_range(start_ts, periods=duration, freq="h")
        # must be contiguous real hours for this sensor (no gaps) and unused
        span_rows = g[(g["ts"] >= span[0]) & (g["ts"] <= span[-1])]
        if len(span_rows) != duration:
            continue
        if span_rows["consumption_kwh"].isna().any():
            continue
        if any((sid, s) in used for s in span):
            continue
        chosen.append((sid, start_ts))
        for s in span:
            used.add((sid, s))
    return chosen


def _inject_point_type(
    eval_series: pd.DataFrame, rng: np.random.Generator, candidates: pd.DataFrame,
    n: int, anomaly_type: str, split: str, seed: int, used: set,
) -> list[InjectedAnomaly]:
    starts = _pick_start_points(rng, candidates, n, 1, used)
    results = []
    for sid, ts in starts:
        original = float(eval_series.loc[(eval_series.sensor_id == sid) & (eval_series.ts == ts), "consumption_kwh"].iloc[0])
        if anomaly_type == "spike":
            injected = original * rng.uniform(*_SPIKE_MULTIPLIER)
        elif anomaly_type == "drop":
            injected = original * rng.uniform(*_DROP_FRACTION)
        else:
            raise ValueError(anomaly_type)
        eval_series.loc[(eval_series.sensor_id == sid) & (eval_series.ts == ts), "consumption_kwh"] = injected
        results.append(InjectedAnomaly(sid, ts, anomaly_type, original, injected, split, seed))
    return results


def _inject_span_type(
    eval_series: pd.DataFrame, rng: np.random.Generator, candidates: pd.DataFrame,
    n: int, anomaly_type: str, split: str, seed: int, used: set,
) -> list[InjectedAnomaly]:
    if anomaly_type == "stuck_meter":
        duration_bounds = _STUCK_METER_DURATION_HOURS
    elif anomaly_type == "zero_consumption":
        duration_bounds = _ZERO_DURATION_HOURS
    else:
        duration_bounds = _SUSTAINED_DURATION_HOURS
    results = []
    for _ in range(n):
        duration = _duration(rng, duration_bounds)
        starts = _pick_start_points(rng, candidates, 1, duration, used)
        if not starts:
            continue
        sid, start_ts = starts[0]
        span = pd.date_range(start_ts, periods=duration, freq="h")
        mask = (eval_series.sensor_id == sid) & (eval_series.ts.isin(span))
        originals = eval_series.loc[mask].sort_values("ts")["consumption_kwh"].tolist()

        if anomaly_type == "sustained_high":
            factor = rng.uniform(*_SUSTAINED_HIGH_MULTIPLIER)
            injected_values = [v * factor for v in originals]
        elif anomaly_type == "sustained_low":
            factor = rng.uniform(*_SUSTAINED_LOW_FRACTION)
            injected_values = [v * factor for v in originals]
        elif anomaly_type == "stuck_meter":
            stuck_value = originals[0]
            injected_values = [stuck_value] * duration
        elif anomaly_type == "zero_consumption":
            injected_values = [0.0] * duration
        else:
            raise ValueError(anomaly_type)

        eval_series.loc[mask, "consumption_kwh"] = pd.Series(
            injected_values, index=eval_series.loc[mask].sort_values("ts").index
        )
        for ts, orig, inj in zip(span, originals, injected_values):
            results.append(InjectedAnomaly(sid, ts, anomaly_type, float(orig), float(inj), split, seed))
    return results


def inject_anomalies(
    hourly_series: pd.DataFrame, split: str, seed: int, n_per_type: int = 15,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """hourly_series: clean, real data with columns [sensor_id, ts,
    consumption_kwh] (e.g. from dataset.reindex_to_hourly_grid), already
    filtered to the desired split's time range. Returns (eval_series,
    ground_truth): eval_series is a full copy of hourly_series with
    injected points overridden; ground_truth has one row per injected
    point, matching the synthetic_anomalies table's columns exactly.
    Deterministic for a fixed seed."""
    rng = np.random.default_rng(seed)
    eval_series = hourly_series.copy().reset_index(drop=True)
    candidates = hourly_series.dropna(subset=["consumption_kwh"])

    used: set[tuple[int, pd.Timestamp]] = set()
    all_results: list[InjectedAnomaly] = []
    for anomaly_type in ANOMALY_TYPES:
        if anomaly_type in ("spike", "drop"):
            all_results += _inject_point_type(eval_series, rng, candidates, n_per_type, anomaly_type, split, seed, used)
        else:
            all_results += _inject_span_type(eval_series, rng, candidates, n_per_type, anomaly_type, split, seed, used)

    ground_truth = pd.DataFrame([
        {
            "sensor_id": r.sensor_id, "ts": r.ts, "anomaly_type": r.anomaly_type,
            "original_value": r.original_value, "injected_value": r.injected_value,
            "split": r.split, "seed": r.seed,
        }
        for r in all_results
    ])
    return eval_series, ground_truth
