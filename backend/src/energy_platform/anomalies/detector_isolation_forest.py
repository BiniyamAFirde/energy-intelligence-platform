"""Detector D: Isolation Forest -- multivariate anomaly detection (Phase 8).

Unlike Detectors A-C (deterministic rules / a single statistical
baseline / the reused Phase 7 RF residual), this is a genuinely fitted
model: it looks for points that are jointly unusual across several
engineered features at once, even when no single feature crosses an
obvious threshold on its own. That is the entire point of a multivariate
detector -- e.g. a value that isn't extreme in isolation but is unusual
given the sensor's recent trend, its typical value at that hour, AND the
calendar context together.

Features (all causal -- see each source function's own docstring for its
leakage-safety argument; nothing here uses same-or-future information):
  - consumption_kwh                : the point's own raw value.
  - prev_hour_kwh                  : the previous hour's value. Computed
                                      via features.add_lag_features with
                                      lags=(2,) -- NOT lags=(1,): that
                                      module's lag_k = shift(k - 1), so
                                      lag_1 = shift(0) is the origin's own
                                      current value (the correct meaning
                                      for the forecasting module's
                                      "predict from origin T" framing, but
                                      redundant with consumption_kwh
                                      here), while lag_2 = shift(1) is the
                                      true previous hour. Reused verbatim,
                                      just requested with the lag that
                                      means what this module needs.
  - rolling_24h_mean/std           : shift(1)-then-rolling over the prior
                                      24h (features.add_rolling_features).
  - rolling_168h_mean              : shift(1)-then-rolling over the prior
                                      168h (features.add_rolling_features)
                                      -- all three reused verbatim from
                                      the forecasting module, not
                                      reimplemented.
  - behavioral_z                   : Detector B's own robust same-hour-of-
                                      day z-score (detector_behavioral.
                                      compute_behavioral_z_scores) --
                                      composing an earlier detector's
                                      leakage-safe signal in as a feature,
                                      not duplicating its logic.
  - hour_sin/cos, dow_sin/cos       : calendar phase, purely a function of
                                      ts (never leaks future information).

Fit period: TRAIN only (dataset.TRAIN_START .. TRAIN_END_EXCL), never
validation or test. This needs no special-casing to stay uncontaminated
during evaluation: the synthetic injection framework (injection.py) only
ever injects into validation/test periods, and every feature above is
causal (shift-then-rolling), so corrupting a later (validation/test)
timestamp cannot change any earlier (train) row's computed features --
verified directly in
tests/unit/test_detector_isolation_forest.py::
test_fitting_on_train_slice_is_unaffected_by_validation_period_contamination.

Scoring: sklearn's IsolationForest.score_samples returns LOWER values for
MORE abnormal points; this module negates it so higher = more anomalous,
matching every other detector's score convention (and what Task #20's
uniform threshold-selection code expects).
"""

from __future__ import annotations

import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from energy_platform.anomalies.detector_behavioral import compute_behavioral_z_scores
from energy_platform.forecasting.features import add_lag_features, add_rolling_features

DETECTOR_VERSION = "v1"
METHOD = "isolation_forest"
ANOMALY_TYPE = "isolation_forest"
FEATURE_VERSION = "v1"
RANDOM_SEED = 42

MODELS_DIR = Path("models")

FEATURE_COLUMNS = (
    "consumption_kwh", "prev_hour_kwh", "rolling_24h_mean", "rolling_24h_std",
    "rolling_168h_mean", "behavioral_z", "hour_sin", "hour_cos", "dow_sin", "dow_cos",
)

DEFAULT_HYPERPARAMETERS = {"n_estimators": 200, "max_samples": "auto", "contamination": "auto"}

_OUTPUT_COLUMNS = [
    "sensor_id", "ts", "anomaly_type", "severity", "score",
    "expected_value", "actual_value", "explanation",
]


def build_features(hourly_series: pd.DataFrame) -> pd.DataFrame:
    """hourly_series: [sensor_id, ts, consumption_kwh], already on the
    full regular hourly grid. Returns the same rows with FEATURE_COLUMNS
    added (NaN wherever a feature's own warm-up hasn't been satisfied yet
    -- not dropped here, see complete_rows)."""
    df = hourly_series.sort_values(["sensor_id", "ts"]).reset_index(drop=True).copy()
    df = add_lag_features(df, "sensor_id", "consumption_kwh", lags=(2,))
    df = df.rename(columns={"lag_2": "prev_hour_kwh"})
    df = add_rolling_features(df, "sensor_id", "consumption_kwh")

    behavioral = compute_behavioral_z_scores(df[["sensor_id", "ts", "consumption_kwh"]])
    df["behavioral_z"] = behavioral["z_score"]

    hour = df["ts"].dt.hour
    dow = df["ts"].dt.dayofweek
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    df["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    df["dow_cos"] = np.cos(2 * np.pi * dow / 7)
    return df


def complete_rows(features_df: pd.DataFrame) -> pd.DataFrame:
    """Complete-case policy, matching dataset.drop_incomplete_rows'
    convention: IsolationForest can't handle NaN, and using per-row
    imputation here would mean silently fabricating input values for a
    detector whose entire job is flagging unusual values."""
    return features_df.dropna(subset=list(FEATURE_COLUMNS)).reset_index(drop=True)


def fit_isolation_forest(
    train_features: pd.DataFrame, seed: int, **hyperparameters,
) -> tuple[StandardScaler, IsolationForest]:
    """train_features: complete_rows(build_features(...)) restricted to
    the TRAIN period by the caller. Scales features (IsolationForest
    itself is scale-invariant per-tree, but the shared StandardScaler is
    what makes the per-feature 'most unusual dimension' explanation in
    detect_isolation_forest_anomalies meaningful) then fits."""
    X = train_features[list(FEATURE_COLUMNS)].to_numpy()
    scaler = StandardScaler().fit(X)
    X_scaled = scaler.transform(X)
    model = IsolationForest(random_state=seed, **hyperparameters).fit(X_scaled)
    return scaler, model


def score_isolation_forest(scaler: StandardScaler, model: IsolationForest, features_df: pd.DataFrame) -> np.ndarray:
    """Higher = more anomalous (negated from sklearn's score_samples,
    where lower = more abnormal)."""
    X_scaled = scaler.transform(features_df[list(FEATURE_COLUMNS)].to_numpy())
    return -model.score_samples(X_scaled)


def detect_isolation_forest_anomalies(
    scaler: StandardScaler, model: IsolationForest, hourly_series: pd.DataFrame, score_threshold: float,
) -> pd.DataFrame:
    """score_threshold is intentionally required (no default): unlike
    Detectors A-C's threshold defaults (reasonable starting points for
    standalone use), IsolationForest's score_samples scale has no
    universal, interpretable default cutoff -- Task #20 selects and
    freezes it from VALIDATION-period synthetic injections, and using it
    unthresholded elsewhere would be misleading."""
    features_df = complete_rows(build_features(hourly_series))
    scores = score_isolation_forest(scaler, model, features_df)
    features_df = features_df.assign(score=scores)
    scaled = scaler.transform(features_df[list(FEATURE_COLUMNS)].to_numpy())

    hits = features_df[features_df["score"] >= score_threshold]
    if len(hits) == 0:
        df = pd.DataFrame(columns=_OUTPUT_COLUMNS)
    else:
        hit_positions = features_df.index.get_indexer(hits.index)
        results = []
        for pos, r in zip(hit_positions, hits.itertuples()):
            row_scaled = scaled[pos]
            worst_idx = int(np.argmax(np.abs(row_scaled)))
            worst_feature = FEATURE_COLUMNS[worst_idx]
            worst_z = float(row_scaled[worst_idx])
            results.append({
                "sensor_id": int(r.sensor_id), "ts": r.ts, "anomaly_type": ANOMALY_TYPE,
                "severity": "high" if r.score >= 1.5 * score_threshold else "medium",
                "score": float(r.score),
                "expected_value": None, "actual_value": float(r.consumption_kwh),
                "explanation": (
                    f"Isolation Forest flagged this point as jointly unusual across its engineered "
                    f"features (anomaly score {r.score:.3f} vs. threshold {score_threshold:.3f}); the "
                    f"most unusual single input was '{worst_feature}', {abs(worst_z):.1f} standard "
                    "deviations from its typical scaled range."
                ),
            })
        df = pd.DataFrame(results)
        df = df.sort_values("score", ascending=False).drop_duplicates(
            subset=["sensor_id", "ts"], keep="first"
        ).reset_index(drop=True)

    df["method"] = METHOD
    df["detector_version"] = DETECTOR_VERSION
    return df


def fit_and_register(session, seed: int = RANDOM_SEED, hyperparameters: dict | None = None) -> dict:
    """DB-touching orchestration (mirrors forecasting/train.py's fit +
    save-artifact + record-model_versions pattern): builds TRAIN-only
    features from the real (never-injected) hourly series, fits, saves the
    artifact, and records a model_versions row -- the only Phase 8
    detector that gets one (see db/models.py's Alert docstring for why the
    other three don't). Flushes but does NOT commit -- matching
    predictions_repo.upsert_predictions' convention (the caller owns the
    commit boundary), not train.py's _record_model_version (which commits
    internally, the one place in this codebase that does -- and is why
    that function has no integration test under the rollback-per-test
    fixture: an internal commit would break the fixture's isolation)."""
    from energy_platform.db.models import ModelVersion
    from energy_platform.forecasting import dataset

    hyperparameters = dict(hyperparameters or DEFAULT_HYPERPARAMETERS)
    data = dataset.build_training_dataset(session)
    hourly_series = data["hourly_series"]
    split_dates = data["split_dates"]

    features_df = build_features(hourly_series)
    train_mask = (features_df["ts"] >= split_dates["train_start"]) & (features_df["ts"] < split_dates["train_end_excl"])
    train_features = complete_rows(features_df[train_mask])

    t0 = time.time()
    scaler, model = fit_isolation_forest(train_features, seed=seed, **hyperparameters)
    fit_seconds = time.time() - t0

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = MODELS_DIR / f"isolation_forest_{DETECTOR_VERSION}.joblib"
    joblib.dump({"scaler": scaler, "model": model, "feature_columns": list(FEATURE_COLUMNS)}, artifact_path)

    train_scores = score_isolation_forest(scaler, model, train_features)
    metrics = {
        "training_rows": int(len(train_features)),
        "training_score_mean": float(np.mean(train_scores)),
        "training_score_std": float(np.std(train_scores)),
        "note": (
            "Descriptive fit-time statistics only. Precision/recall/F1 against "
            "synthetic injections are computed later, uniformly across all four "
            "detectors, in Task #20's evaluation stage -- not stored on this row."
        ),
    }

    row = ModelVersion(
        model_name="isolation_forest", model_version=DETECTOR_VERSION, feature_version=FEATURE_VERSION,
        train_start=split_dates["train_start"], train_end=split_dates["train_end_excl"],
        validation_start=split_dates["train_end_excl"], validation_end=split_dates["validation_end_excl"],
        test_start=split_dates["validation_end_excl"], test_end=split_dates["test_end_excl"],
        hyperparameters=hyperparameters, random_seed=seed, metrics=metrics,
        artifact_path=str(artifact_path),
        notes=(
            f"fit_seconds={fit_seconds:.1f}. Fit on TRAIN-period real data only "
            "(never validation/test, where synthetic anomalies are injected during "
            "evaluation) -- see injection.py's module docstring."
        ),
    )
    session.merge(row)
    session.flush()
    return {"artifact_path": str(artifact_path), "metrics": metrics, "hyperparameters": hyperparameters, "seed": seed}


def load_artifact(artifact_path: str | Path) -> tuple[StandardScaler, IsolationForest]:
    obj = joblib.load(artifact_path)
    return obj["scaler"], obj["model"]
