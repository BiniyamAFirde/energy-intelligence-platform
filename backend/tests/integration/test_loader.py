import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from energy_platform.db.models import EnergyMeasurement, Site
from energy_platform.ingestion.loader import (
    upsert_buildings,
    upsert_energy_measurements,
    upsert_sensors,
    upsert_sites,
)


def test_upsert_sites_is_idempotent(db_session):
    ids_first = upsert_sites(db_session, ["TestSiteA"], {"TestSiteA": "US/Eastern"})
    ids_second = upsert_sites(db_session, ["TestSiteA"], {"TestSiteA": "US/Eastern"})

    assert ids_first == ids_second
    count = db_session.execute(
        select(Site).where(Site.site_code == "TestSiteA")
    ).scalars().all()
    assert len(count) == 1


def test_upsert_buildings_referential_integrity_rejects_unknown_site(db_session):
    metadata_df = pd.DataFrame([{
        "building_id": "orphan_bldg", "site_id": "NoSuchSite",
        "primaryspaceusage": "Office", "sqm": 100.0, "sub_primaryspaceusage": None,
        "lat": None, "lng": None, "yearbuilt": None, "numberoffloors": None,
        "occupants": None, "industry": None, "subindustry": None, "heatingtype": None,
        "eui": None, "site_eui": None, "source_eui": None, "leed_level": None,
        "energystarscore": None,
    }])
    with pytest.raises(KeyError):
        # site_id_by_code doesn't contain "NoSuchSite" -> KeyError before it
        # ever reaches the database, which is the correct failure point:
        # we want this caught in Python, not as a raw FK violation.
        upsert_buildings(db_session, metadata_df, ["orphan_bldg"], site_id_by_code={})


def test_energy_measurement_duplicate_composite_key_rejected_without_upsert(db_session):
    site_ids = upsert_sites(db_session, ["TestSiteB"], {"TestSiteB": "US/Eastern"})
    metadata_df = pd.DataFrame([{
        "building_id": "b1", "site_id": "TestSiteB", "primaryspaceusage": "Office",
        "sqm": 100.0, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": None, "occupants": None, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, ["b1"], site_ids)
    sensor_ids = upsert_sensors(db_session, building_ids)
    db_session.flush()

    ts = dt.datetime(2016, 1, 1, tzinfo=dt.timezone.utc)
    db_session.add(EnergyMeasurement(sensor_id=sensor_ids["b1"], ts=ts, consumption_kwh=10.0))
    db_session.flush()

    # A SAVEPOINT confines the expected IntegrityError to itself: without
    # it, the raised error deassociates the db_session fixture's outer
    # transaction, and its teardown then warns trying to roll back a
    # transaction that's already gone.
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(EnergyMeasurement(sensor_id=sensor_ids["b1"], ts=ts, consumption_kwh=99.0))
            db_session.flush()


def test_upsert_energy_measurements_is_idempotent_and_updates_value(db_session):
    site_ids = upsert_sites(db_session, ["TestSiteC"], {"TestSiteC": "US/Eastern"})
    metadata_df = pd.DataFrame([{
        "building_id": "c1", "site_id": "TestSiteC", "primaryspaceusage": "Office",
        "sqm": 100.0, "sub_primaryspaceusage": None, "lat": None, "lng": None,
        "yearbuilt": None, "numberoffloors": None, "occupants": None, "industry": None,
        "subindustry": None, "heatingtype": None, "eui": None, "site_eui": None,
        "source_eui": None, "leed_level": None, "energystarscore": None,
    }])
    building_ids = upsert_buildings(db_session, metadata_df, ["c1"], site_ids)
    sensor_ids = upsert_sensors(db_session, building_ids)
    db_session.flush()

    long_df = pd.DataFrame({
        "building_code": ["c1"],
        "ts_utc": [pd.Timestamp("2016-01-01", tz="UTC")],
        "consumption_kwh": [10.0],
    })
    upsert_energy_measurements(db_session, long_df, sensor_ids)
    db_session.flush()

    long_df_updated = long_df.assign(consumption_kwh=[25.0])
    upsert_energy_measurements(db_session, long_df_updated, sensor_ids)
    db_session.flush()

    rows = db_session.execute(
        select(EnergyMeasurement).where(EnergyMeasurement.sensor_id == sensor_ids["c1"])
    ).scalars().all()
    assert len(rows) == 1
    assert float(rows[0].consumption_kwh) == 25.0
