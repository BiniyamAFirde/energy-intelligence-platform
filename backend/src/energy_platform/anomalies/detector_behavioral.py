"""Detector B: historical behavioral anomalies (Phase 8).

Flags points whose consumption is unusual relative to how that SAME
sensor has behaved at that SAME hour-of-day historically -- not how it
behaves right now. This is why the baseline is grouped by
(sensor_id, hour_of_day), not a blind rolling window over all hours:
comparing a 2am reading to whatever the last 168 raw hours happened to
contain would mix daytime peaks and overnight troughs together and flag
completely normal diurnal variation as "anomalous."

Uses a ROBUST (median / MAD) z-score rather than mean/std, because the
baseline itself is estimated from a rolling window of real history that
can contain a handful of genuine anomalies (its own past, or -- during
evaluation -- earlier injected points). Mean/std are pulled hard by
exactly the outliers this detector exists to catch; median/MAD stay close
to the typical value even with a few contaminating points in the window.

Leakage: for each point, the baseline uses only STRICTLY EARLIER
observations of the same (sensor_id, hour_of_day) -- reuses the exact
shift(1)-then-rolling pattern features.add_rolling_features established
for the forecasting model, so "the origin's own value is never used in
its own baseline" holds here exactly as it does there. What this does NOT
eliminate: if an earlier point in the same rolling window is itself
anomalous (a real anomaly, or -- during evaluation -- an earlier injected
one), it can pull the baseline slightly. This is an honest, realistic
property of any historical-baseline detector -- a live production
detector has the identical issue, since it cannot retroactively know
which past points were anomalous either -- and is part of why the
baseline is robust (median/MAD) rather than mean/std, not something
evaluation hides.

Timezone note: hour-of-day is computed from `ts` directly (UTC), not a
per-building local hour. Every site currently loaded is US/Eastern
(dataset.py), a fixed offset from local hour, so grouping by it still
correctly groups "the same wall-clock hour of day" together. This would
need to become a genuine per-building local hour (mirroring
features.add_calendar_features) if a future dataset spans multiple
timezones.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DETECTOR_VERSION = "v1"
METHOD = "behavioral"
ANOMALY_TYPE = "behavioral_deviation"

BASELINE_WINDOW = 14  # occurrences of the same (sensor, hour-of-day) -- ~14 days of history
MAD_SCALE = 1.4826     # scales MAD to be a consistent estimator of std under normality
MIN_MAD = 1e-6          # guards against division by ~0 when the window is nearly constant

_OUTPUT_COLUMNS = [
    "sensor_id", "ts", "anomaly_type", "severity", "score",
    "expected_value", "actual_value", "explanation",
]


def _mad(x: np.ndarray) -> float:
    return float(np.median(np.abs(x - np.median(x))))


def compute_behavioral_z_scores(hourly_series: pd.DataFrame, window: int = BASELINE_WINDOW) -> pd.DataFrame:
    """hourly_series: [sensor_id, ts, consumption_kwh]. Returns hourly_series
    with added hour_of_day, baseline_median, baseline_mad, z_score columns.
    z_score is NaN wherever fewer than `window` prior same-hour
    observations exist, or where the baseline MAD is ~0 -- those points are
    simply not scored, not treated as normal."""
    df = hourly_series.sort_values(["sensor_id", "ts"]).reset_index(drop=True).copy()
    df["hour_of_day"] = df["ts"].dt.hour

    # shift(1) within (sensor, hour_of_day) so the baseline for a point
    # never includes that point's own value -- same leakage-safe pattern as
    # features.add_rolling_features (shift-then-rolling), applied to a
    # same-hour-of-day group instead of every consecutive hour.
    shifted = df.groupby(["sensor_id", "hour_of_day"])["consumption_kwh"].shift(1)
    rolling = shifted.groupby([df["sensor_id"], df["hour_of_day"]]).rolling(window, min_periods=window)
    df["baseline_median"] = rolling.median().reset_index(level=[0, 1], drop=True)
    df["baseline_mad"] = rolling.apply(_mad, raw=True).reset_index(level=[0, 1], drop=True)

    scale = df["baseline_mad"] * MAD_SCALE
    scale_safe = scale.where(scale > MIN_MAD)
    df["z_score"] = (df["consumption_kwh"] - df["baseline_median"]) / scale_safe
    return df


def detect_behavioral_anomalies(
    hourly_series: pd.DataFrame, z_threshold: float = 4.0, window: int = BASELINE_WINDOW,
) -> pd.DataFrame:
    """z_threshold default of 4.0 is a reasonable standalone starting
    point, but Task #20 (evaluation) selects and freezes the actual
    production threshold from VALIDATION-period synthetic injections only
    -- this default exists so the function is usable on its own and in
    tests, not as the tuned operating point."""
    scored = compute_behavioral_z_scores(hourly_series, window=window)
    hits = scored[scored["z_score"].abs() >= z_threshold]

    if len(hits) == 0:
        df = pd.DataFrame(columns=_OUTPUT_COLUMNS)
    else:
        results = []
        for r in hits.itertuples():
            direction = "above" if r.z_score > 0 else "below"
            results.append({
                "sensor_id": int(r.sensor_id), "ts": r.ts, "anomaly_type": ANOMALY_TYPE,
                "severity": "high" if abs(r.z_score) >= 2 * z_threshold else "medium",
                "score": float(abs(r.z_score)),
                "expected_value": float(r.baseline_median), "actual_value": float(r.consumption_kwh),
                "explanation": (
                    f"Reading of {r.consumption_kwh:.2f} kWh at hour {r.hour_of_day:02d}:00 is "
                    f"{abs(r.z_score):.1f} robust standard deviations {direction} this sensor's "
                    f"typical value for that hour ({r.baseline_median:.2f} kWh, based on its last "
                    f"{window} occurrences of hour {r.hour_of_day:02d}:00)."
                ),
            })
        df = pd.DataFrame(results)
        df = df.sort_values("score", ascending=False).drop_duplicates(
            subset=["sensor_id", "ts"], keep="first"
        ).reset_index(drop=True)

    df["method"] = METHOD
    df["detector_version"] = DETECTOR_VERSION
    return df
