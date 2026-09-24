"""Offline external-company inference adapter.

Scores an external company's OWN building data against the already-trained
`models/random_forest_v1.joblib` Pipeline artifact, without requiring that
company's building to exist in PostgreSQL/BDG2 first. This module never
trains, retrains, or fits anything -- it only validates/normalizes external
CSV input into the exact DataFrame shape `dataset.load_raw_frame(session)`
would have produced from the database, then hands off to the REAL,
unmodified feature-engineering entry point (`dataset.build_feature_frame`)
and the REAL, unmodified trained Pipeline's `.predict()`.

Why this is possible without touching the database at all:
`dataset.build_feature_frame(raw_df)` (unlike `dataset.load_raw_frame`) is
pure pandas -- it has no SQL, no session, and treats `sensor_id`/
`building_id`/`site_id` purely as grouping keys, not real foreign keys. See
docs/external_inference.md for the full input contract, the validation
rules enforced below, and this project's documented limitations.
"""

from __future__ import annotations

import dataclasses
import functools
import math
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import joblib
import pandas as pd

from energy_platform.forecasting import dataset, train
from energy_platform.forecasting.features import MAX_LOOKBACK_HOURS
from energy_platform.ingestion.transform import TransformStats, normalize_timestamps_to_utc

ENERGY_REQUIRED_COLUMNS = ["timestamp", "energy_kwh"]
BUILDING_REQUIRED_COLUMNS = [
    "building_code", "area_sqm", "number_of_floors", "occupants", "primary_use", "timezone",
]

DEFAULT_ARTIFACT_PATH = train.MODELS_DIR / f"random_forest_{train.MODEL_VERSION}.joblib"

_TZ_OFFSET_SUFFIX = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")


class ExternalDataError(ValueError):
    """Raised when external company input fails validation. Always carries
    a complete, actionable message -- never a bare assertion."""


@dataclasses.dataclass(frozen=True)
class ExternalBuilding:
    building_code: str
    area_sqm: float
    number_of_floors: float
    occupants: float
    primary_use: str
    timezone: str


@dataclasses.dataclass(frozen=True)
class ExternalInferenceResult:
    output: pd.DataFrame
    building: ExternalBuilding
    n_observations: int
    history_start: pd.Timestamp
    history_end: pd.Timestamp
    forecast_origin: pd.Timestamp
    primary_use_warning: str | None
    artifact_path: Path
    model_name: str
    model_version: str


# --- Model loading (once per process; never re-loaded per prediction) ---


def load_model(artifact_path: Path | None = None):
    """Loads the already-fitted sklearn Pipeline (preprocessing + trained
    RandomForestRegressor) from disk. Call this exactly ONCE per process
    and reuse the returned object for every prediction -- the artifact is
    ~552MB and joblib.load dominates runtime if called repeatedly. A
    future API should load this once at application startup, not per
    request, for the same reason.

    Never fits anything: this is a pure deserialization of an artifact
    train.py already produced."""
    path = artifact_path or DEFAULT_ARTIFACT_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"No trained model artifact at {path}. Run "
            "`python -m energy_platform.forecasting.train` first (this module never trains)."
        )
    return joblib.load(path)


@functools.lru_cache(maxsize=1)
def get_cached_pipeline(artifact_path: Path | None = None):
    """Process-level singleton wrapping load_model(): the first call
    actually deserializes the ~552MB artifact; every subsequent call in
    the same process (regardless of caller -- a FastAPI lifespan, a test
    building a fresh TestClient, ...) returns the already-loaded Pipeline
    instantly instead of reading the file again. This is the "equivalent
    application-level cached singleton" the API's lifespan hook uses to
    guarantee joblib.load runs once per process, not once per request or
    once per application boot within a single test session."""
    return load_model(artifact_path)


def known_primary_use_categories(pipeline) -> list[str]:
    """Reads the categories the trained OneHotEncoder actually saw during
    training, straight from the loaded artifact -- not a hardcoded guess."""
    onehot = pipeline.named_steps["preprocess"].named_transformers_["categorical"].named_steps["onehot"]
    return list(onehot.categories_[0])


def check_primary_use_supported(pipeline, primary_use: str) -> str | None:
    """The trained encoder uses handle_unknown='ignore': an unseen
    primary_use is NOT a hard error at prediction time -- sklearn silently
    encodes it as all-zeros and the forest still produces a number. This
    function preserves that existing, already-shipped model behavior
    rather than overriding it with a stricter external-only rule; it only
    surfaces a clear warning so an operator isn't silently unaware the
    prediction may be degraded. Returns None if primary_use is a known
    training-time category."""
    known = known_primary_use_categories(pipeline)
    if primary_use not in known:
        return (
            f"primary_use '{primary_use}' is not one of the categories the model was trained on "
            f"({', '.join(sorted(known))}). The trained encoder (handle_unknown='ignore') will "
            "score it as an unrecognized category rather than raising an error; treat this "
            "prediction as lower-confidence."
        )
    return None


# --- building metadata: shared core (CSV rows and JSON request bodies both
# funnel through this one function, so a value that's valid/invalid for one
# interface is valid/invalid for the other -- see docs/external_inference.md,
# "CLI and API share one inference service") ---


def _is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and value.strip() == ""


def validate_building_metadata(data: dict) -> ExternalBuilding:
    """data must have the six BUILDING_REQUIRED_COLUMNS keys (a CSV row
    turned into a dict, or a JSON request body's `building` object turned
    into a dict -- both call this exact function, so both are held to
    exactly the same rules)."""
    missing_fields = [c for c in BUILDING_REQUIRED_COLUMNS if _is_missing(data.get(c))]
    if missing_fields:
        raise ExternalDataError(
            f"building metadata is missing required field(s): {', '.join(missing_fields)}. "
            f"Required fields: {', '.join(BUILDING_REQUIRED_COLUMNS)}."
        )

    tz_name = str(data["timezone"]).strip()
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ExternalDataError(
            f"building metadata: timezone '{tz_name}' is not a recognized IANA timezone "
            "(e.g. 'US/Eastern', 'America/Chicago', 'Europe/Berlin')."
        ) from None

    numeric_fields = {}
    for col in ("area_sqm", "number_of_floors", "occupants"):
        try:
            value = float(data[col])
        except (TypeError, ValueError):
            raise ExternalDataError(f"building metadata: '{col}' must be numeric, got {data[col]!r}.") from None
        if value <= 0:
            raise ExternalDataError(f"building metadata: '{col}' must be a positive number, got {value}.")
        numeric_fields[col] = value

    return ExternalBuilding(
        building_code=str(data["building_code"]).strip(),
        area_sqm=numeric_fields["area_sqm"],
        number_of_floors=numeric_fields["number_of_floors"],
        occupants=numeric_fields["occupants"],
        primary_use=str(data["primary_use"]).strip(),
        timezone=tz_name,
    )


def load_building_csv(path: Path) -> ExternalBuilding:
    df = pd.read_csv(path)

    missing_cols = [c for c in BUILDING_REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ExternalDataError(
            f"building.csv is missing required column(s): {', '.join(missing_cols)}. "
            f"Required columns: {', '.join(BUILDING_REQUIRED_COLUMNS)}."
        )
    if len(df) != 1:
        raise ExternalDataError(
            f"building.csv must contain exactly one building row (this interface scores one "
            f"building per run); found {len(df)} row(s)."
        )
    return validate_building_metadata(df.iloc[0].to_dict())


# --- energy observations: shared core (CSV rows and JSON request bodies
# both funnel through this one function -- see the building-metadata note
# above; the same reasoning applies here) ---


def validate_energy_observations(df: pd.DataFrame, tz_name: str) -> pd.DataFrame:
    """df must have `timestamp` (naive local time string) and `energy_kwh`
    columns -- whether built from a CSV file or a JSON request body's
    `energy` list. Returns a DataFrame with columns `ts` (UTC, tz-aware)
    and `consumption_kwh` (float), validated and normalized per
    docs/external_inference.md. Raises ExternalDataError with an
    actionable message on any contract violation. Never fills, fabricates,
    or silently drops a row to make prediction possible -- every rejection
    is surfaced to the caller instead."""
    missing_cols = [c for c in ENERGY_REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ExternalDataError(
            f"energy data is missing required column(s): {', '.join(missing_cols)}. "
            f"Required columns: {', '.join(ENERGY_REQUIRED_COLUMNS)}."
        )

    raw_ts = df["timestamp"].astype(str)
    offset_rows = raw_ts[raw_ts.str.contains(_TZ_OFFSET_SUFFIX)]
    if len(offset_rows) > 0:
        raise ExternalDataError(
            "energy data timestamps must be naive local time (no UTC offset or 'Z' suffix), e.g. "
            f"'2024-06-01 00:00:00' -- found a timezone-qualified value: {offset_rows.iloc[0]!r}. "
            "The building's 'timezone' field in building.csv supplies the timezone, matching this "
            "project's existing BDG2 ingestion convention (ingestion/transform.py)."
        )

    parsed = pd.to_datetime(df["timestamp"], errors="coerce")
    bad_mask = parsed.isna() & df["timestamp"].notna()
    if bad_mask.any():
        raise ExternalDataError(
            f"energy data contains {int(bad_mask.sum())} unparseable timestamp(s), e.g. "
            f"{df['timestamp'][bad_mask].iloc[0]!r}. Expected format: 'YYYY-MM-DD HH:MM:SS'."
        )

    if parsed.duplicated().any():
        dupes = parsed[parsed.duplicated()]
        raise ExternalDataError(
            f"energy data contains {len(dupes)} duplicate timestamp(s), e.g. {dupes.iloc[0]}. "
            "Each timestamp must appear exactly once."
        )

    order = parsed.sort_values().index
    parsed = parsed.loc[order].reset_index(drop=True)
    energy_kwh_raw = df["energy_kwh"].loc[order].reset_index(drop=True)

    diffs = parsed.diff().dropna()
    irregular = diffs[diffs != pd.Timedelta(hours=1)]
    if len(irregular) > 0:
        bad_idx = irregular.index[0]
        raise ExternalDataError(
            "energy data timestamps must be exactly hourly and gap-free -- found a "
            f"{irregular.iloc[0]} gap between {parsed[bad_idx - 1]} and {parsed[bad_idx]}. "
            "External inference does not fabricate missing hours; provide complete, contiguous "
            "hourly readings."
        )

    missing_mask = energy_kwh_raw.isna() | (energy_kwh_raw.astype(str).str.strip() == "")
    if missing_mask.any():
        raise ExternalDataError(
            f"energy data is missing energy_kwh for {int(missing_mask.sum())} row(s), e.g. at "
            f"{parsed[missing_mask].iloc[0]}. Gaps are not automatically filled -- provide "
            "complete history or exclude this building from this run."
        )

    numeric = pd.to_numeric(energy_kwh_raw, errors="coerce")
    nonnumeric_mask = numeric.isna() & ~missing_mask
    if nonnumeric_mask.any():
        raise ExternalDataError(
            f"energy data has {int(nonnumeric_mask.sum())} non-numeric energy_kwh value(s), e.g. "
            f"{energy_kwh_raw[nonnumeric_mask].iloc[0]!r} at {parsed[nonnumeric_mask].iloc[0]}."
        )

    negative_mask = numeric < 0
    if negative_mask.any():
        raise ExternalDataError(
            f"energy data has {int(negative_mask.sum())} negative energy_kwh value(s), e.g. "
            f"{numeric[negative_mask].iloc[0]} at {parsed[negative_mask].iloc[0]}. Negative "
            "consumption is not physically valid (same domain rule BDG2 ingestion applies)."
        )

    if len(parsed) < MAX_LOOKBACK_HOURS:
        raise ExternalDataError(
            f"External inference requires at least {MAX_LOOKBACK_HOURS} consecutive hourly "
            f"observations before the first forecast origin; got {len(parsed)}."
        )

    stats = TransformStats()
    ts_utc = normalize_timestamps_to_utc(parsed, tz_name, stats)
    if stats.dst_ambiguous_dropped > 0:
        raise ExternalDataError(
            f"{stats.dst_ambiguous_dropped} timestamp(s) fall in a daylight-saving 'fall back' "
            f"ambiguous window for timezone '{tz_name}' and cannot be unambiguously localized. "
            "External inference refuses to guess -- correct or remove these rows."
        )
    if ts_utc.duplicated().any():
        raise ExternalDataError(
            "After converting to UTC, some timestamps collide -- this happens when local "
            "readings span a daylight-saving 'spring forward' gap. External inference refuses "
            "to guess which reading is authoritative; correct or remove the affected rows."
        )

    return pd.DataFrame({"ts": ts_utc.reset_index(drop=True), "consumption_kwh": numeric.reset_index(drop=True)})


def load_and_validate_energy_csv(path: Path, tz_name: str) -> pd.DataFrame:
    return validate_energy_observations(pd.read_csv(path), tz_name)


# --- adapting external data into the REAL feature-generation pipeline ---


def _build_raw_frame(energy_df_utc: pd.DataFrame, building: ExternalBuilding) -> pd.DataFrame:
    """Assembles a DataFrame with exactly the columns
    `dataset.load_raw_frame(session)` would have produced from PostgreSQL
    (sensor_id, ts, consumption_kwh, building_id, site_id, tz_name,
    area_sqm, primary_use, number_of_floors, occupants), so
    `dataset.build_feature_frame` -- the real, unmodified feature-
    engineering entry point -- can be reused unchanged. sensor_id/
    building_id/site_id here are synthetic grouping keys only (this
    adapter never queries or writes the database); they are not real
    primary keys and are not persisted anywhere."""
    n = len(energy_df_utc)
    return pd.DataFrame({
        "sensor_id": [1] * n,
        "ts": energy_df_utc["ts"].reset_index(drop=True),
        "consumption_kwh": energy_df_utc["consumption_kwh"].astype(float).reset_index(drop=True),
        "building_id": [1] * n,
        "site_id": [1] * n,
        "tz_name": [building.timezone] * n,
        "area_sqm": [building.area_sqm] * n,
        "primary_use": [building.primary_use] * n,
        "number_of_floors": [building.number_of_floors] * n,
        "occupants": [building.occupants] * n,
    })


def build_prediction_ready_features(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Same post-processing `dataset._prediction_ready_features` applies
    after its DB fetch (dataset.build_feature_frame, then drop rows
    incomplete on every column except the target) -- reused verbatim here,
    just starting from an already-built raw_df instead of a live session."""
    feature_df = dataset.build_feature_frame(raw_df)
    required = [c for c in dataset.REQUIRED_COMPLETE_COLUMNS if c != "target_kwh"]
    return feature_df.dropna(subset=required)


def latest_origin_request(feature_df: pd.DataFrame) -> pd.DataFrame:
    """Same origin-selection dataset.build_prediction_request applies: the
    single most recent local-midnight origin's 24 (horizon) rows -- i.e.
    the actual day-ahead forecast request, using the same "latest
    available data" semantics as production, not a different horizon
    policy."""
    if feature_df.empty:
        return feature_df
    latest_origin = feature_df["origin_ts"].max()
    return feature_df[feature_df["origin_ts"] == latest_origin].reset_index(drop=True)


def predict_external(pipeline, request_df: pd.DataFrame):
    """The ONLY model call in this module: pipeline.predict(). Mirrors
    predict.py's `_predict_with_model` exactly for the non-baseline path.
    Never calls .fit() or .fit_transform()."""
    return pipeline.predict(request_df[dataset.FEATURE_COLUMNS])


def build_output_frame(
    request_df: pd.DataFrame, preds, building_code: str, model_name: str, model_version: str,
) -> pd.DataFrame:
    out = pd.DataFrame({
        "building_code": building_code,
        "target_ts": request_df["target_ts"].dt.tz_convert("UTC").reset_index(drop=True),
        "horizon": request_df["horizon"].astype(int).reset_index(drop=True),
        "predicted_kwh": pd.Series([float(p) for p in preds]),
        "model_name": model_name,
        "model_version": model_version,
        "generated_at": request_df["origin_ts"].dt.tz_convert("UTC").reset_index(drop=True),
    })
    return out.sort_values("horizon").reset_index(drop=True)


# --- orchestration ---


def _score(
    building: ExternalBuilding,
    energy_df: pd.DataFrame,
    pipeline,
    artifact_path: Path,
    model_name: str,
    model_version: str,
) -> ExternalInferenceResult:
    """The shared tail both the CLI (run_external_inference) and the API
    (run_external_inference_from_payload) call once their respective input
    has already been reduced to one validated ExternalBuilding + one
    validated/UTC-normalized energy_df -- feature generation, prediction,
    and output-shaping are identical regardless of where the input came
    from, so there is exactly one implementation of this tail, not two."""
    warning = check_primary_use_supported(pipeline, building.primary_use)

    raw_df = _build_raw_frame(energy_df, building)
    feature_df = build_prediction_ready_features(raw_df)
    request_df = latest_origin_request(feature_df)
    if request_df.empty:
        raise ExternalDataError(
            "No valid forecast origin could be produced from the supplied history -- check that "
            "observations are contiguous, hourly, and span at least "
            f"{MAX_LOOKBACK_HOURS} hours before a local midnight in the building's timezone."
        )

    preds = predict_external(pipeline, request_df)
    output = build_output_frame(request_df, preds, building.building_code, model_name, model_version)

    return ExternalInferenceResult(
        output=output,
        building=building,
        n_observations=len(energy_df),
        history_start=energy_df["ts"].min(),
        history_end=energy_df["ts"].max(),
        forecast_origin=request_df["origin_ts"].iloc[0],
        primary_use_warning=warning,
        artifact_path=artifact_path,
        model_name=model_name,
        model_version=model_version,
    )


def run_external_inference(
    energy_csv: Path,
    building_csv: Path,
    pipeline,
    artifact_path: Path,
    model_name: str = "random_forest",
    model_version: str | None = None,
) -> ExternalInferenceResult:
    """CLI entry point: validate -> normalize -> real feature generation ->
    real trained pipeline's .predict() -> CSV-ready output frame.
    `pipeline` must already be loaded (see load_model) -- this function
    never loads or fits a model itself, so callers control exactly when
    the ~552MB artifact is deserialized."""
    model_version = model_version or train.MODEL_VERSION
    building = load_building_csv(building_csv)
    energy_df = load_and_validate_energy_csv(energy_csv, building.timezone)
    return _score(building, energy_df, pipeline, artifact_path, model_name, model_version)


def run_external_inference_from_payload(
    building_data: dict,
    energy_rows: list[dict],
    pipeline,
    artifact_path: Path,
    model_name: str = "random_forest",
    model_version: str | None = None,
) -> ExternalInferenceResult:
    """API entry point (POST /api/v1/forecast): identical to
    run_external_inference except the input is already-parsed JSON (a
    building dict and a list of {timestamp, energy_kwh} dicts) rather than
    CSV file paths. Goes through the exact same validate_building_metadata
    / validate_energy_observations / _score functions the CLI uses -- there
    is no second, API-specific validation or prediction implementation."""
    model_version = model_version or train.MODEL_VERSION
    building = validate_building_metadata(building_data)
    energy_df = pd.DataFrame(energy_rows, columns=ENERGY_REQUIRED_COLUMNS)
    energy_df_utc = validate_energy_observations(energy_df, building.timezone)
    return _score(building, energy_df_utc, pipeline, artifact_path, model_name, model_version)
