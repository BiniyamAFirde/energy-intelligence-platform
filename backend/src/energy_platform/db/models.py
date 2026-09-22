"""ORM models implementing the Phase 2 schema.

Key decisions carried over from the design phase:
  - energy_measurements / weather_measurements use composite primary keys
    (sensor_id, ts) / (site_id, ts) instead of a surrogate id: this is the
    natural key for a time-series fact table, and it makes a duplicate
    ingestion run fail at the database level instead of silently duplicating
    rows.
  - All timestamps are TIMESTAMPTZ, normalized to UTC by the ingestion
    pipeline (BDG2 ships local, timezone-naive timestamps).
  - predictions keeps target_ts (the hour being forecast) separate from
    generated_at (when the forecast was made), so a day-ahead-forecasting
    leakage bug is something a test can assert against, not just something we
    claim.
  - alerts is the single source of truth for anomaly status -- there is no
    denormalized is_anomaly flag on energy_measurements.
  - buildings keeps a small "core" column group actively used by the MVP
    analytics/ML pipeline (primary_use, area_sqm) plus a larger "extended"
    group of retained BDG2 metadata that the MVP does not read yet.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from energy_platform.db.base import Base


class Site(Base):
    __tablename__ = "sites"

    site_id: Mapped[int] = mapped_column(primary_key=True)
    site_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    buildings: Mapped[list["Building"]] = relationship(back_populates="site")
    weather_measurements: Mapped[list["WeatherMeasurement"]] = relationship(
        back_populates="site"
    )


class Building(Base):
    __tablename__ = "buildings"

    building_id: Mapped[int] = mapped_column(primary_key=True)
    building_code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.site_id"), nullable=False)

    # --- Core: actively used by ingestion validation, EDA grouping (Phase 6)
    # and as ML features (Phase 7) ---
    primary_use: Mapped[str | None] = mapped_column(String(100))
    area_sqm: Mapped[float | None] = mapped_column(Numeric(10, 2))

    # --- Extended BDG2 metadata: retained for completeness / future
    # analysis. Not read by the MVP analytics or ML pipeline. ---
    sub_primary_use: Mapped[str | None] = mapped_column(String(100))
    latitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    year_built: Mapped[int | None] = mapped_column(SmallInteger)
    number_of_floors: Mapped[int | None] = mapped_column(SmallInteger)
    occupants: Mapped[int | None] = mapped_column()
    industry: Mapped[str | None] = mapped_column(String(100))
    subindustry: Mapped[str | None] = mapped_column(String(100))
    heating_type: Mapped[str | None] = mapped_column(String(100))
    eui: Mapped[float | None] = mapped_column(Numeric(10, 2))
    site_eui: Mapped[float | None] = mapped_column(Numeric(10, 2))
    source_eui: Mapped[float | None] = mapped_column(Numeric(10, 2))
    leed_level: Mapped[str | None] = mapped_column(String(30))
    energy_star_rating: Mapped[int | None] = mapped_column(SmallInteger)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    site: Mapped[Site] = relationship(back_populates="buildings")
    sensors: Mapped[list["Sensor"]] = relationship(back_populates="building")

    __table_args__ = (Index("ix_buildings_site_id", "site_id"),)


class Sensor(Base):
    __tablename__ = "sensors"

    sensor_id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.building_id"), nullable=False)
    meter_type: Mapped[str] = mapped_column(String(30), nullable=False, default="electricity")
    unit: Mapped[str] = mapped_column(String(10), nullable=False, default="kWh")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    building: Mapped[Building] = relationship(back_populates="sensors")
    energy_measurements: Mapped[list["EnergyMeasurement"]] = relationship(
        back_populates="sensor"
    )
    predictions: Mapped[list["Prediction"]] = relationship(back_populates="sensor")
    alerts: Mapped[list["Alert"]] = relationship(back_populates="sensor")

    __table_args__ = (UniqueConstraint("building_id", "meter_type"),)


class EnergyMeasurement(Base):
    __tablename__ = "energy_measurements"

    sensor_id: Mapped[int] = mapped_column(
        ForeignKey("sensors.sensor_id"), primary_key=True
    )
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    consumption_kwh: Mapped[float | None] = mapped_column(Numeric(12, 4))

    sensor: Mapped[Sensor] = relationship(back_populates="energy_measurements")

    __table_args__ = (Index("ix_energy_measurements_ts", "ts"),)


class WeatherMeasurement(Base):
    __tablename__ = "weather_measurements"

    site_id: Mapped[int] = mapped_column(ForeignKey("sites.site_id"), primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    air_temp_c: Mapped[float | None] = mapped_column(Numeric(5, 2))
    cloud_coverage: Mapped[float | None] = mapped_column(Numeric(4, 2))
    dew_temp_c: Mapped[float | None] = mapped_column(Numeric(5, 2))
    precip_1hr_mm: Mapped[float | None] = mapped_column(Numeric(6, 2))
    precip_6hr_mm: Mapped[float | None] = mapped_column(Numeric(6, 2))
    sea_lvl_pressure: Mapped[float | None] = mapped_column(Numeric(7, 2))
    wind_direction: Mapped[float | None] = mapped_column(Numeric(5, 1))
    wind_speed: Mapped[float | None] = mapped_column(Numeric(5, 2))

    site: Mapped[Site] = relationship(back_populates="weather_measurements")


class ModelVersion(Base):
    """One row per trained (or baseline) model version -- the reproducibility
    record Phase 7 requires: exactly what data it was trained/validated/
    tested on, what hyperparameters and seed produced it, and what its
    validation metrics were. `predictions.model_name`/`model_version`
    references this table via a composite FK, so a prediction can never
    point at a model version that was never actually recorded."""

    __tablename__ = "model_versions"

    model_name: Mapped[str] = mapped_column(String(50), primary_key=True)
    model_version: Mapped[str] = mapped_column(String(20), primary_key=True)
    feature_version: Mapped[str] = mapped_column(String(20), nullable=False)

    train_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    train_end: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    validation_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    validation_end: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    test_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    test_end: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    hyperparameters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    random_seed: Mapped[int | None] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    artifact_path: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    predictions: Mapped[list["Prediction"]] = relationship(back_populates="model_version_ref")


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensors.sensor_id"), nullable=False)
    target_ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_name: Mapped[str] = mapped_column(String(50), nullable=False)
    model_version: Mapped[str] = mapped_column(String(20), nullable=False)
    horizon: Mapped[int | None] = mapped_column(SmallInteger)
    predicted_kwh: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    actual_kwh: Mapped[float | None] = mapped_column(Numeric(12, 4))

    sensor: Mapped[Sensor] = relationship(back_populates="predictions")
    model_version_ref: Mapped[ModelVersion] = relationship(back_populates="predictions")

    __table_args__ = (
        UniqueConstraint("sensor_id", "target_ts", "model_name", "model_version"),
        Index("ix_predictions_sensor_target", "sensor_id", "target_ts"),
        ForeignKeyConstraint(
            ["model_name", "model_version"],
            ["model_versions.model_name", "model_versions.model_version"],
        ),
    )


class Alert(Base):
    """The anomaly-detection record (Phase 8). `method` names the detector
    that produced this row (data_quality | behavioral | forecast_residual |
    isolation_forest); `anomaly_type` classifies the specific finding
    (missing, zero_run, stuck_meter, spike, drop, high_residual,
    low_residual, behavioral_deviation, isolation_forest). These are
    deliberately two separate columns, not one overloaded field -- `method`
    answers "which detector," `anomaly_type` answers "what kind of anomaly."

    Traceability is asymmetric by design: Isolation Forest is a genuinely
    fitted model, so it gets a real `model_versions` row (seed,
    hyperparameters, fit period, artifact); the three deterministic/
    statistical detectors (data_quality, behavioral, forecast_residual)
    have no training phase to record and are traced instead via
    `method` + `detector_version` alone -- forcing them through
    `model_versions`' NOT NULL train/validation/test date columns would
    mean fabricating meaningless date ranges for something that was never
    trained."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensors.sensor_id"), nullable=False)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    anomaly_type: Mapped[str] = mapped_column(String(30), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    score: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    expected_value: Mapped[float | None] = mapped_column(Numeric(12, 4))
    actual_value: Mapped[float | None] = mapped_column(Numeric(12, 4))
    residual: Mapped[float | None] = mapped_column(Numeric(12, 4))
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    sensor: Mapped[Sensor] = relationship(back_populates="alerts")

    __table_args__ = (
        Index("ix_alerts_sensor_ts", "sensor_id", "ts"),
        Index("ix_alerts_unresolved", "resolved", postgresql_where=(resolved == False)),  # noqa: E712
        UniqueConstraint("sensor_id", "ts", "method", "detector_version", name="uq_alerts_idempotency"),
    )


class SyntheticAnomaly(Base):
    """Ground-truth labels for the synthetic anomaly injection framework
    (Phase 8). Completely separate from `energy_measurements` -- injection
    NEVER writes into the production source-of-truth table; it records
    "if this reading had been X instead of Y, here's the label" here, and
    detectors are evaluated by comparing their output against this table,
    not by modifying real data."""

    __tablename__ = "synthetic_anomalies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensors.sensor_id"), nullable=False)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    anomaly_type: Mapped[str] = mapped_column(String(30), nullable=False)
    original_value: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    injected_value: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    split: Mapped[str] = mapped_column(String(20), nullable=False)  # 'validation' | 'test'
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("sensor_id", "ts", "seed", name="uq_synthetic_anomalies_point"),
        Index("ix_synthetic_anomalies_split", "split"),
    )


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_file: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    rows_read: Mapped[int | None] = mapped_column()
    rows_inserted: Mapped[int | None] = mapped_column()
    rows_rejected: Mapped[int | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text)
