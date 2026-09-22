"""Detector A: data-quality anomalies (Phase 8).

Purely rule-based, deterministic checks against the raw hourly series --
no historical baseline and no model involved (that is Detectors B and C).
Operates on the same (sensor_id, ts, consumption_kwh) shape used
throughout Phase 8 (dataset/features.reindex_to_hourly_grid's output, or
an injection.inject_anomalies eval_series), so it runs unchanged against
real data in production or a synthetic-eval series in evaluation.

Five checks, matching the Phase 8 spec:
  - negative_value : consumption_kwh < 0 -- physically impossible for a
                      cumulative electricity meter. BDG2, once loaded and
                      cleaned, is expected to have zero real occurrences
                      of this: a clean result here is a *pass*, not
                      evidence the check is broken. Unit tests inject
                      synthetic negative values to prove detection still
                      works even though production data is expected to
                      trigger it zero times.
  - missing        : consumption_kwh is NaN on the full expected hourly
                      grid (a real gap, not a unit/type issue).
  - zero_run       : a contiguous run of exact-zero readings at least
                      MIN_ZERO_RUN_HOURS long.
  - stuck_meter    : a contiguous run of bit-identical non-zero,
                      non-negative readings at least MIN_STUCK_RUN_HOURS
                      long (excludes zero and negative values, which are
                      already covered -- more specifically -- by
                      zero_run/negative_value; this also means a single
                      detector run cannot emit two different anomaly_type
                      rows for the same point, which matters because the
                      alerts table's idempotency key is
                      (sensor_id, ts, method, detector_version) and does
                      NOT include anomaly_type).

                      MIN_STUCK_RUN_HOURS=30, comfortably above 24, is not
                      an arbitrary choice: Phase 8's evaluation run
                      against real BDG2 data (data/processed/
                      anomaly_evaluation_report.json) first used 4 and
                      found ~35% of ALL validation-period points flagged
                      as stuck_meter -- not a bug, but ~21 of 60 sensors
                      turned out to report genuinely DAILY-resolution
                      readings (one value per calendar day, replicated
                      across all 24 hours once reindexed to an hourly
                      grid), which trivially produces a ~24h "constant"
                      run every single day. That is a structural
                      characteristic of those meters, not an anomaly, and
                      a 4h threshold could not tell the two apart. 30h
                      gives ~6h of margin above one full calendar day (DST
                      and reindexing can shift grid boundaries by an hour)
                      while still catching genuinely multi-day-plus stuck
                      runs. The cost, honestly documented rather than
                      hidden: Detector A can no longer catch very short
                      (a few hours) stuck-meter events -- those are left
                      to the statistical detectors (B/C/D), which do not
                      share this failure mode since they score deviation
                      from a baseline rather than run-length.
  - isolated_spike : a single point far above both its immediate
                      neighbours *and* the local window median, with
                      those neighbours themselves not elevated -- i.e.
                      genuinely a one-point spike, not the leading edge of
                      a sustained shift (Detector B/C's job).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DETECTOR_VERSION = "v1"
METHOD = "data_quality"

MIN_ZERO_RUN_HOURS = 3
MIN_STUCK_RUN_HOURS = 30  # see module docstring: empirically required to avoid daily-resolution sensors' natural ~24h flat runs
SPIKE_WINDOW_HOURS = 3            # neighbours on each side used for the local median
SPIKE_MULTIPLIER = 4.0            # point must exceed window median by this factor
SPIKE_NEIGHBOR_MULTIPLIER = 2.5   # neighbours must stay below this factor of window median to count as "isolated"
SPIKE_MIN_ABS_KWH = 1.0           # guards against flagging tiny-magnitude noise as a "spike"

_OUTPUT_COLUMNS = [
    "sensor_id", "ts", "anomaly_type", "severity", "score",
    "expected_value", "actual_value", "explanation",
]


def _runs(mask: pd.Series) -> list[tuple[int, int]]:
    """(start_idx, end_idx_inclusive) for each maximal run of True in a
    boolean Series with a contiguous 0..n-1 index."""
    runs = []
    start = None
    for i, v in enumerate(mask.tolist()):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs


def _detect_negative(group: pd.DataFrame) -> list[dict]:
    hits = group[group["consumption_kwh"] < 0]
    return [
        {
            "sensor_id": int(r.sensor_id), "ts": r.ts, "anomaly_type": "negative_value",
            "severity": "high", "score": float(abs(r.consumption_kwh)),
            "expected_value": 0.0, "actual_value": float(r.consumption_kwh),
            "explanation": (
                f"Reading of {r.consumption_kwh:.2f} kWh is negative, which is physically "
                "impossible for a cumulative electricity meter."
            ),
        }
        for r in hits.itertuples()
    ]


def _detect_missing(group: pd.DataFrame) -> list[dict]:
    hits = group[group["consumption_kwh"].isna()]
    return [
        {
            "sensor_id": int(r.sensor_id), "ts": r.ts, "anomaly_type": "missing",
            "severity": "medium", "score": 1.0,
            "expected_value": None, "actual_value": None,
            "explanation": f"No reading recorded for {r.ts} -- the expected hourly grid has a gap here.",
        }
        for r in hits.itertuples()
    ]


def _detect_zero_runs(group: pd.DataFrame) -> list[dict]:
    values = group["consumption_kwh"]
    mask = (values == 0.0) & values.notna()
    results = []
    for start, end in _runs(mask.reset_index(drop=True)):
        if end - start + 1 < MIN_ZERO_RUN_HOURS:
            continue
        span = group.iloc[start:end + 1]
        run_len = end - start + 1
        for r in span.itertuples():
            results.append({
                "sensor_id": int(r.sensor_id), "ts": r.ts, "anomaly_type": "zero_run",
                "severity": "medium", "score": float(run_len),
                "expected_value": None, "actual_value": 0.0,
                "explanation": (
                    f"Consumption reads exactly 0 kWh for {run_len} consecutive hours "
                    f"({span['ts'].iloc[0]} to {span['ts'].iloc[-1]}) -- unusual for an occupied building."
                ),
            })
    return results


def _detect_stuck_meter(group: pd.DataFrame) -> list[dict]:
    values = group["consumption_kwh"]
    same_as_prev = (values == values.shift(1)) & values.notna() & (values > 0.0)
    results = []
    for start, end in _runs(same_as_prev.reset_index(drop=True)):
        # same_as_prev[start] True means row `start` matches row `start-1` --
        # the run actually begins one row earlier.
        run_start = start - 1
        run_len = end - run_start + 1
        if run_len < MIN_STUCK_RUN_HOURS:
            continue
        span = group.iloc[run_start:end + 1]
        stuck_value = float(span["consumption_kwh"].iloc[0])
        for r in span.itertuples():
            results.append({
                "sensor_id": int(r.sensor_id), "ts": r.ts, "anomaly_type": "stuck_meter",
                "severity": "medium", "score": float(run_len),
                "expected_value": None, "actual_value": stuck_value,
                "explanation": (
                    f"Reading is bit-identical at {stuck_value:.4f} kWh for {run_len} consecutive "
                    f"hours ({span['ts'].iloc[0]} to {span['ts'].iloc[-1]}) -- consistent with a "
                    "meter that stopped updating rather than genuinely constant load."
                ),
            })
    return results


def _detect_isolated_spikes(group: pd.DataFrame) -> list[dict]:
    values = group["consumption_kwh"].to_numpy(dtype=float)
    n = len(values)
    results = []
    for i in range(1, n - 1):
        v = values[i]
        if np.isnan(v) or v < SPIKE_MIN_ABS_KWH:
            continue
        lo, hi = max(0, i - SPIKE_WINDOW_HOURS), min(n, i + SPIKE_WINDOW_HOURS + 1)
        window = np.delete(values[lo:hi], i - lo)
        window = window[~np.isnan(window)]
        if len(window) == 0:
            continue
        window_median = np.median(window)
        if window_median <= 0:
            continue
        left, right = values[i - 1], values[i + 1]
        if np.isnan(left) or np.isnan(right):
            continue
        is_far_above = v >= SPIKE_MULTIPLIER * window_median
        neighbors_not_elevated = (
            left < SPIKE_NEIGHBOR_MULTIPLIER * window_median
            and right < SPIKE_NEIGHBOR_MULTIPLIER * window_median
        )
        if is_far_above and neighbors_not_elevated:
            row = group.iloc[i]
            ratio = v / window_median
            results.append({
                "sensor_id": int(row.sensor_id), "ts": row.ts, "anomaly_type": "isolated_spike",
                "severity": "high" if ratio >= 2 * SPIKE_MULTIPLIER else "medium",
                "score": float(ratio),
                "expected_value": float(window_median), "actual_value": float(v),
                "explanation": (
                    f"Single-hour reading of {v:.2f} kWh is {ratio:.1f}x the local median of "
                    f"{window_median:.2f} kWh, while the immediately preceding ({left:.2f} kWh) and "
                    f"following ({right:.2f} kWh) hours are not elevated -- consistent with a "
                    "one-point sensor glitch rather than a genuine sustained change."
                ),
            })
    return results


def detect_data_quality_anomalies(hourly_series: pd.DataFrame) -> pd.DataFrame:
    """hourly_series: [sensor_id, ts, consumption_kwh], one row per
    sensor/hour on the full expected grid. Returns one row per flagged
    point with `method`/`detector_version` filled in, ready to feed the
    alerts upsert. If more than one check fires on the exact same
    (sensor_id, ts) -- not expected given the checks above are built to be
    mutually exclusive, but not structurally impossible -- only the
    highest-score row is kept, since the alerts table's idempotency key
    (sensor_id, ts, method, detector_version) has no room for two rows
    from the same detector at the same point."""
    results: list[dict] = []
    for _, group in hourly_series.sort_values(["sensor_id", "ts"]).groupby("sensor_id"):
        group = group.reset_index(drop=True)
        results += _detect_negative(group)
        results += _detect_missing(group)
        results += _detect_zero_runs(group)
        results += _detect_stuck_meter(group)
        results += _detect_isolated_spikes(group)

    if not results:
        df = pd.DataFrame(columns=_OUTPUT_COLUMNS)
    else:
        df = pd.DataFrame(results)
        df = df.sort_values("score", ascending=False).drop_duplicates(
            subset=["sensor_id", "ts"], keep="first"
        ).reset_index(drop=True)

    df["method"] = METHOD
    df["detector_version"] = DETECTOR_VERSION
    return df
