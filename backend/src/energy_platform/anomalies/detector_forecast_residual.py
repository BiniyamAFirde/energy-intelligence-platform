"""Detector C: forecast-residual anomalies (Phase 8).

Reuses Phase 7's trained Random Forest model -- never retrains it, never
touches its artifact, split, or Phase 7's own evaluation results. Task
#14 already backfilled `predictions` rows for every origin/target the RF
model can score across TRAIN+VALIDATION+TEST
(forecasting.predict.generate_predictions_for_range); this module only
reads that table plus energy_measurements, and turns the residual
(actual - predicted) into anomaly flags.

CRITICAL: "actual" always comes directly from energy_measurements, never
from predictions.actual_kwh -- that column exists in the schema but is
never populated by this pipeline (Phase 7's forecast job never writes
into it), so it is NULL for every row. Reading it here would silently
produce all-NaN residuals.

Three residual measures, all computed, one used to flag:
  - absolute_residual     = actual - predicted, signed kWh.
  - relative_residual     = absolute_residual / max(|actual|, epsilon) --
                             informative but not what triggers a flag
                             (unstable/explodes for buildings near-zero
                             consumption at some hours).
  - standardized_residual = absolute_residual / a causal rolling std of
                             that SAME sensor's own past residuals
                             (window STD_WINDOW hours, shift(1)-then-
                             rolling, the same leakage-safe pattern as
                             features.add_rolling_features) -- this is
                             what anomaly_type/severity/score are based
                             on. Using the sensor's own historical error
                             scale, rather than a fixed kWh threshold,
                             matters because a large building's RF
                             residual is naturally bigger in raw kWh
                             than a small building's even when the model
                             fits both equally well in relative terms.

anomaly_type is "high_residual" (actual far above predicted) or
"low_residual" (actual far below predicted) -- signed, unlike Detector
B's single behavioral_deviation type, matching the two anomaly_types the
Alert model's docstring documents for this detector.
"""

from __future__ import annotations

import pandas as pd

DETECTOR_VERSION = "v1"
METHOD = "forecast_residual"

STD_WINDOW = 72  # hours of past residual history used for the causal std
RELATIVE_EPSILON = 0.1  # kWh floor for relative_residual's denominator, avoids exploding near-zero actuals

_OUTPUT_COLUMNS = [
    "sensor_id", "ts", "anomaly_type", "severity", "score",
    "expected_value", "actual_value", "residual", "explanation",
]


def join_predictions_to_actuals(predictions_df: pd.DataFrame, actuals_df: pd.DataFrame) -> pd.DataFrame:
    """predictions_df: [sensor_id, target_ts, predicted_kwh] (one model/
    version, already filtered by the caller). actuals_df: [sensor_id, ts,
    consumption_kwh] straight from energy_measurements -- NOT
    predictions.actual_kwh. Inner-joins on (sensor_id, target_ts == ts);
    rows with no matching actual (a genuine gap in energy_measurements) or
    a NULL actual are dropped -- a residual is undefined without a real
    observed value."""
    merged = predictions_df.merge(
        actuals_df.rename(columns={"ts": "target_ts", "consumption_kwh": "actual_kwh"}),
        on=["sensor_id", "target_ts"], how="inner",
    )
    return merged.dropna(subset=["actual_kwh", "predicted_kwh"]).reset_index(drop=True)


def compute_residuals(joined_df: pd.DataFrame, std_window: int = STD_WINDOW) -> pd.DataFrame:
    """joined_df: output of join_predictions_to_actuals (or equivalent),
    at minimum [sensor_id, target_ts, predicted_kwh, actual_kwh]. Adds
    absolute_residual, relative_residual, and a causally-standardized
    residual (NaN until std_window prior residuals exist for that
    sensor -- those rows are simply not scored, not treated as normal)."""
    df = joined_df.sort_values(["sensor_id", "target_ts"]).reset_index(drop=True).copy()
    df["absolute_residual"] = df["actual_kwh"] - df["predicted_kwh"]
    denom = df["actual_kwh"].abs().clip(lower=RELATIVE_EPSILON)
    df["relative_residual"] = df["absolute_residual"] / denom

    shifted = df.groupby("sensor_id")["absolute_residual"].shift(1)
    rolling_std = shifted.groupby(df["sensor_id"]).rolling(std_window, min_periods=std_window).std()
    df["residual_std"] = rolling_std.reset_index(level=0, drop=True)
    df["standardized_residual"] = df["absolute_residual"] / df["residual_std"].where(df["residual_std"] > 1e-6)
    return df


def detect_forecast_residual_anomalies(
    joined_df: pd.DataFrame, z_threshold: float = 4.0, std_window: int = STD_WINDOW,
) -> pd.DataFrame:
    """z_threshold default of 4.0 is a reasonable standalone starting
    point, but Task #20 (evaluation) selects and freezes the actual
    production threshold from VALIDATION-period synthetic injections only
    -- this default exists so the function is usable on its own and in
    tests, not as the tuned operating point."""
    scored = compute_residuals(joined_df, std_window=std_window)
    hits = scored[scored["standardized_residual"].abs() >= z_threshold]

    if len(hits) == 0:
        df = pd.DataFrame(columns=_OUTPUT_COLUMNS)
    else:
        results = []
        for r in hits.itertuples():
            anomaly_type = "high_residual" if r.standardized_residual > 0 else "low_residual"
            direction = "above" if r.standardized_residual > 0 else "below"
            results.append({
                "sensor_id": int(r.sensor_id), "ts": r.target_ts, "anomaly_type": anomaly_type,
                "severity": "high" if abs(r.standardized_residual) >= 2 * z_threshold else "medium",
                "score": float(abs(r.standardized_residual)),
                "expected_value": float(r.predicted_kwh), "actual_value": float(r.actual_kwh),
                "residual": float(r.absolute_residual),
                "explanation": (
                    f"Actual consumption of {r.actual_kwh:.2f} kWh is {direction} the Random Forest "
                    f"forecast of {r.predicted_kwh:.2f} kWh by {abs(r.absolute_residual):.2f} kWh -- "
                    f"{abs(r.standardized_residual):.1f} standard deviations larger than this sensor's "
                    f"typical forecast error over its last {std_window} scored hours."
                ),
            })
        df = pd.DataFrame(results)
        df = df.sort_values("score", ascending=False).drop_duplicates(
            subset=["sensor_id", "ts"], keep="first"
        ).reset_index(drop=True)

    df["method"] = METHOD
    df["detector_version"] = DETECTOR_VERSION
    return df
