import numpy as np
import pandas as pd
from sqlalchemy import select

from energy_platform.anomalies.detector_isolation_forest import (
    FEATURE_COLUMNS,
    fit_and_register,
    load_artifact,
)
from energy_platform.db.models import ModelVersion
from energy_platform.forecasting import dataset
from energy_platform.ingestion.loader import (
    upsert_buildings,
    upsert_energy_measurements,
    upsert_sensors,
    upsert_sites,
)


def _seed_building_with_diurnal_pattern(db_session, site_code, building_code, n_hours=60 * 24):
    """n_hours=60 days comfortably covers both the ~14-day behavioral_z
    warmup and the 168h rolling warmup, leaving real rows for the model to
    fit on -- entirely inside dataset.TRAIN_START.. so this never touches
    validation/test."""
    site_ids = upsert_sites(db_session, [site_code], {site_code: "US/Eastern"})
    metadata_df = pd.DataFrame([{
        "building_id": building_code, "site_id": site_code, "primaryspaceusage": "Office",
        "sqm": 150.0, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": None, "occupants": None, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, [building_code], site_ids)
    sensor_ids = upsert_sensors(db_session, building_ids)
    db_session.flush()

    rng = np.random.default_rng(0)
    ts = pd.date_range(dataset.TRAIN_START, periods=n_hours, freq="h", tz="UTC")
    hour = ts.hour.to_numpy()
    base = 5 + 10 * np.sin(np.pi * np.clip(hour - 6, 0, 12) / 12)
    values = base + rng.normal(0, 0.2, n_hours)
    df = pd.DataFrame({"building_code": [building_code] * n_hours, "ts_utc": ts, "consumption_kwh": values})
    upsert_energy_measurements(db_session, df, {building_code: sensor_ids[building_code]})
    db_session.flush()
    return sensor_ids[building_code]


def test_fit_and_register_creates_model_version_row_and_loadable_artifact(db_session, tmp_path, monkeypatch):
    import energy_platform.anomalies.detector_isolation_forest as detector_module
    monkeypatch.setattr(detector_module, "MODELS_DIR", tmp_path)

    _seed_building_with_diurnal_pattern(db_session, "IsoForestSite", "iso_b1")
    result = fit_and_register(db_session, seed=42)

    row = db_session.execute(
        select(ModelVersion).where(
            ModelVersion.model_name == "isolation_forest", ModelVersion.model_version == "v1"
        )
    ).scalar_one()
    assert row.random_seed == 42
    assert row.train_start == dataset.TRAIN_START.to_pydatetime()
    assert row.train_end == dataset.TRAIN_END_EXCL.to_pydatetime()
    assert row.artifact_path == result["artifact_path"]
    assert row.metrics["training_rows"] > 0

    scaler, model = load_artifact(result["artifact_path"])
    assert scaler.mean_.shape == (len(FEATURE_COLUMNS),)
    assert model.random_state == 42


def test_fit_and_register_only_uses_train_period_data(db_session, tmp_path, monkeypatch):
    """Seeds data spanning into what would be the validation period too
    (past TRAIN_END_EXCL) and confirms the reported training_rows count
    matches independently recomputing the TRAIN-only complete-feature
    count -- i.e. the fit never silently pulled in validation-period rows."""
    import energy_platform.anomalies.detector_isolation_forest as detector_module
    monkeypatch.setattr(detector_module, "MODELS_DIR", tmp_path)

    # 60 days of TRAIN-period data plus 10 extra days that spill past
    # TRAIN_END_EXCL would require reseeding right at the boundary, which
    # is awkward with a fixed TRAIN_START offset -- instead, directly seed
    # a big enough span that some hours legitimately fall on/after
    # TRAIN_END_EXCL by seeding from TRAIN_END_EXCL - 30 days for 40 days.
    site_code, building_code = "IsoForestSite2", "iso_b2"
    site_ids = upsert_sites(db_session, [site_code], {site_code: "US/Eastern"})
    metadata_df = pd.DataFrame([{
        "building_id": building_code, "site_id": site_code, "primaryspaceusage": "Office",
        "sqm": 150.0, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": None, "occupants": None, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, [building_code], site_ids)
    sensor_ids = upsert_sensors(db_session, building_ids)
    db_session.flush()

    start = dataset.TRAIN_END_EXCL - pd.Timedelta(days=30)
    n_hours = 40 * 24  # last 10 days land in validation
    rng = np.random.default_rng(1)
    ts = pd.date_range(start, periods=n_hours, freq="h", tz="UTC")
    hour = ts.hour.to_numpy()
    base = 5 + 10 * np.sin(np.pi * np.clip(hour - 6, 0, 12) / 12)
    values = base + rng.normal(0, 0.2, n_hours)
    df = pd.DataFrame({"building_code": [building_code] * n_hours, "ts_utc": ts, "consumption_kwh": values})
    upsert_energy_measurements(db_session, df, {building_code: sensor_ids[building_code]})
    db_session.flush()

    result = fit_and_register(db_session, seed=42)

    from energy_platform.anomalies.detector_isolation_forest import build_features, complete_rows

    data = dataset.build_training_dataset(db_session)
    features_df = build_features(data["hourly_series"])
    expected_train = complete_rows(
        features_df[(features_df["ts"] >= dataset.TRAIN_START) & (features_df["ts"] < dataset.TRAIN_END_EXCL)]
    )
    assert result["metrics"]["training_rows"] == len(expected_train)
