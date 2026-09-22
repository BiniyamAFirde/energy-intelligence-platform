"""Explicit assertions that the Alembic migration produces the schema we
designed. The test_engine fixture already ran `alembic upgrade head`
against a fresh `energy_platform_test` database to get here -- this file
checks the *result* of that in detail rather than just trusting it worked.
"""

import sqlalchemy as sa

EXPECTED_TABLES = {
    "sites", "buildings", "sensors", "energy_measurements",
    "weather_measurements", "predictions", "alerts", "ingestion_runs",
    "model_versions", "synthetic_anomalies",
}


def test_all_ten_tables_exist(test_engine):
    inspector = sa.inspect(test_engine)
    assert EXPECTED_TABLES.issubset(set(inspector.get_table_names()))


def test_alerts_has_phase8_anomaly_columns(test_engine):
    inspector = sa.inspect(test_engine)
    cols = {c["name"] for c in inspector.get_columns("alerts")}
    assert {"anomaly_type", "detector_version", "residual"}.issubset(cols)


def test_alerts_idempotency_unique_constraint(test_engine):
    inspector = sa.inspect(test_engine)
    uniques = inspector.get_unique_constraints("alerts")
    cols_sets = [set(u["column_names"]) for u in uniques]
    assert {"sensor_id", "ts", "method", "detector_version"} in cols_sets


def test_synthetic_anomalies_unique_constraint_and_fk(test_engine):
    inspector = sa.inspect(test_engine)
    uniques = inspector.get_unique_constraints("synthetic_anomalies")
    cols_sets = [set(u["column_names"]) for u in uniques]
    assert {"sensor_id", "ts", "seed"} in cols_sets

    fks = inspector.get_foreign_keys("synthetic_anomalies")
    referred = {fk["referred_table"] for fk in fks}
    assert "sensors" in referred


def test_predictions_fk_to_model_versions(test_engine):
    inspector = sa.inspect(test_engine)
    fks = inspector.get_foreign_keys("predictions")
    matching = [fk for fk in fks if fk["referred_table"] == "model_versions"]
    assert len(matching) == 1
    assert set(matching[0]["constrained_columns"]) == {"model_name", "model_version"}


def test_model_versions_composite_primary_key(test_engine):
    inspector = sa.inspect(test_engine)
    pk = inspector.get_pk_constraint("model_versions")
    assert set(pk["constrained_columns"]) == {"model_name", "model_version"}


def test_energy_measurements_has_composite_primary_key(test_engine):
    inspector = sa.inspect(test_engine)
    pk = inspector.get_pk_constraint("energy_measurements")
    assert set(pk["constrained_columns"]) == {"sensor_id", "ts"}


def test_weather_measurements_has_composite_primary_key(test_engine):
    inspector = sa.inspect(test_engine)
    pk = inspector.get_pk_constraint("weather_measurements")
    assert set(pk["constrained_columns"]) == {"site_id", "ts"}


def test_foreign_keys_present(test_engine):
    inspector = sa.inspect(test_engine)
    expectations = {
        "buildings": ("sites",),
        "sensors": ("buildings",),
        "energy_measurements": ("sensors",),
        "weather_measurements": ("sites",),
        "predictions": ("sensors",),
        "alerts": ("sensors",),
    }
    for table, expected_targets in expectations.items():
        fks = inspector.get_foreign_keys(table)
        referred = {fk["referred_table"] for fk in fks}
        assert set(expected_targets).issubset(referred), f"{table} missing FK to {expected_targets}"


def test_predictions_unique_constraint_on_sensor_target_model(test_engine):
    inspector = sa.inspect(test_engine)
    uniques = inspector.get_unique_constraints("predictions")
    cols_sets = [set(u["column_names"]) for u in uniques]
    assert {"sensor_id", "target_ts", "model_name", "model_version"} in cols_sets


def test_alerts_unresolved_partial_index_exists(test_engine):
    inspector = sa.inspect(test_engine)
    indexes = inspector.get_indexes("alerts")
    names = {ix["name"] for ix in indexes}
    assert "ix_alerts_unresolved" in names
