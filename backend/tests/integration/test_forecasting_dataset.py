import numpy as np
import pandas as pd

from energy_platform.forecasting import dataset
from energy_platform.ingestion.loader import (
    upsert_buildings,
    upsert_energy_measurements,
    upsert_sensors,
    upsert_sites,
)


def _seed_building_with_hours(db_session, site_code, building_code, n_hours, area_sqm=150.0):
    site_ids = upsert_sites(db_session, [site_code], {site_code: "US/Eastern"})
    metadata_df = pd.DataFrame([{
        "building_id": building_code, "site_id": site_code, "primaryspaceusage": "Office",
        "sqm": area_sqm, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": 3, "occupants": None, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, [building_code], site_ids)
    sensor_ids = upsert_sensors(db_session, building_ids)
    db_session.flush()

    ts = pd.date_range(dataset.TRAIN_START, periods=n_hours, freq="h", tz="UTC")
    rng = np.random.default_rng(42)
    values = 10 + rng.normal(0, 1, n_hours)
    df = pd.DataFrame({"building_code": [building_code] * n_hours, "ts_utc": ts, "consumption_kwh": values})
    upsert_energy_measurements(db_session, df, {building_code: sensor_ids[building_code]})
    db_session.flush()
    return building_ids[building_code], sensor_ids[building_code]


def test_build_training_dataset_end_to_end_produces_usable_rows(db_session):
    # 168h (lag_168 warm-up) + 24h (last origin needs a full 24h horizon) +
    # a bit of margin = enough for at least a handful of complete rows.
    _seed_building_with_hours(db_session, "DsSite", "ds_b1", n_hours=200)

    result = dataset.build_training_dataset(db_session)
    assert result["row_counts"]["train_after_dropna"] > 0
    train = result["train"]
    assert set(dataset.FEATURE_COLUMNS).issubset(train.columns)
    # Regression test: build_feature_frame's static_cols once omitted
    # site_id, so per-site evaluation broke downstream in train.py with a
    # KeyError only surfaced by a real training run against the full DB --
    # small synthetic test fixtures happened not to exercise that path.
    assert "site_id" in train.columns
    assert train["target_kwh"].notna().all()
    # REQUIRED_COMPLETE_COLUMNS must be fully clean after dropna...
    for col in dataset.REQUIRED_COMPLETE_COLUMNS:
        if col != "primary_use":
            assert train[col].notna().all(), f"{col} should have no NaN after dropna"
    # ...but sparse static metadata (occupants: 43% populated dataset-wide)
    # is deliberately allowed to stay NaN here -- imputation happens later,
    # at the model level, fit on train only (this seeded test building has
    # no occupants value at all, by design of the fixture).
    assert train["occupants"].isna().all()


def test_build_training_dataset_target_values_match_source_readings(db_session):
    building_id, sensor_id = _seed_building_with_hours(db_session, "DsSite2", "ds_b2", n_hours=200)

    result = dataset.build_training_dataset(db_session)
    train = result["train"]
    row = train[(train["sensor_id"] == sensor_id) & (train["horizon"] == 1)].iloc[0]

    # target_kwh for horizon=1 must equal the actual reading at origin_ts+1h
    from energy_platform.repositories import energy as energy_repo
    actual = [
        m for m in energy_repo.list_measurements(db_session, sensor_id, limit=1000)
        if m.ts == row["origin_ts"] + pd.Timedelta(hours=1)
    ][0]
    assert float(row["target_kwh"]) == float(actual.consumption_kwh)


def test_build_training_dataset_static_features_attached_correctly(db_session):
    _seed_building_with_hours(db_session, "DsSite3", "ds_b3", n_hours=200, area_sqm=999.0)
    result = dataset.build_training_dataset(db_session)
    train = result["train"]
    assert (train["area_sqm"] == 999.0).all()
    assert (train["number_of_floors"] == 3.0).all()
    assert (train["primary_use"] == "Office").all()
