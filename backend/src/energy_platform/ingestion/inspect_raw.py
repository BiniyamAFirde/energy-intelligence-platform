"""Inspect the raw BDG2 files and produce a factual report before any
ingestion logic is written against assumptions about their shape.

Read-only: never writes into data/raw/. Output goes to
data/processed/raw_inspection_report.json plus a human-readable stdout
summary.
"""

from __future__ import annotations

import json

import pandas as pd

from energy_platform.ingestion.download import DATA_DIR, RAW_DIR

PROCESSED_DIR = DATA_DIR / "processed"
REPORT_PATH = PROCESSED_DIR / "raw_inspection_report.json"

# Metadata columns we decided to retain (Phase 2 discussion): core (active)
# + extended (retained for future analysis). Anything else in the raw CSV
# (building_id_kaggle, site_id_kaggle, date_opened, rating, gas, solar, ...)
# is deliberately dropped at ingestion time, not because it's unavailable,
# but because it's either a competition artifact or out of MVP scope.
RETAINED_METADATA_COLUMNS = [
    "building_id", "site_id", "primaryspaceusage", "sub_primaryspaceusage",
    "sqm", "lat", "lng", "yearbuilt", "numberoffloors", "occupants",
    "industry", "subindustry", "heatingtype", "eui", "site_eui",
    "source_eui", "leed_level", "energystarscore", "electricity",
]


def inspect_metadata() -> dict:
    path = RAW_DIR / "metadata.csv"
    df = pd.read_csv(path)

    missing_retained = [c for c in RETAINED_METADATA_COLUMNS if c not in df.columns]

    return {
        "file": "metadata.csv",
        "rows": len(df),
        "columns": len(df.columns),
        "column_names": list(df.columns),
        "retained_columns_present": [c for c in RETAINED_METADATA_COLUMNS if c in df.columns],
        "retained_columns_missing_from_csv": missing_retained,
        "duplicate_building_id_count": int(df["building_id"].duplicated().sum()),
        "site_id_value_counts": df["site_id"].value_counts().to_dict(),
        "electricity_flag_value_counts": df["electricity"].fillna("<blank>").value_counts().to_dict(),
        "missing_rate_by_retained_column": {
            c: round(float(df[c].isna().mean()), 4)
            for c in RETAINED_METADATA_COLUMNS if c in df.columns
        },
        "sample_rows": df.head(3).to_dict(orient="records"),
    }


def inspect_weather() -> dict:
    path = RAW_DIR / "weather.csv"
    df = pd.read_csv(path, parse_dates=["timestamp"])

    dup_key = df.duplicated(subset=["site_id", "timestamp"]).sum()
    value_cols = [c for c in df.columns if c not in ("timestamp", "site_id")]

    return {
        "file": "weather.csv",
        "rows": len(df),
        "columns": len(df.columns),
        "column_names": list(df.columns),
        "site_ids": sorted(df["site_id"].unique().tolist()),
        "timestamp_min": str(df["timestamp"].min()),
        "timestamp_max": str(df["timestamp"].max()),
        "duplicate_site_timestamp_rows": int(dup_key),
        "missing_rate_by_column": {
            c: round(float(df[c].isna().mean()), 4) for c in value_cols
        },
        "sample_rows": df.head(3).astype(str).to_dict(orient="records"),
    }


def inspect_electricity() -> dict:
    path = RAW_DIR / "electricity_cleaned.csv"
    df = pd.read_csv(path, parse_dates=["timestamp"])

    building_cols = [c for c in df.columns if c != "timestamp"]
    dup_ts = df["timestamp"].duplicated().sum()
    missing_rate = df[building_cols].isna().mean()

    return {
        "file": "electricity_cleaned.csv",
        "rows": len(df),
        "columns": len(df.columns),
        "building_column_count": len(building_cols),
        "timestamp_min": str(df["timestamp"].min()),
        "timestamp_max": str(df["timestamp"].max()),
        "duplicate_timestamp_rows": int(dup_ts),
        "missing_rate_summary": {
            "min": round(float(missing_rate.min()), 4),
            "median": round(float(missing_rate.median()), 4),
            "mean": round(float(missing_rate.mean()), 4),
            "max": round(float(missing_rate.max()), 4),
        },
        "buildings_100pct_missing": int((missing_rate == 1.0).sum()),
        "buildings_under_5pct_missing": int((missing_rate < 0.05).sum()),
        "sample_columns": building_cols[:5],
    }


def cross_check(metadata_report: dict, electricity_report: dict) -> dict:
    path = RAW_DIR / "metadata.csv"
    meta = pd.read_csv(path)
    elec_path = RAW_DIR / "electricity_cleaned.csv"
    elec_cols = set(pd.read_csv(elec_path, nrows=0).columns) - {"timestamp"}

    meta_ids = set(meta["building_id"])
    flagged_electric = set(meta.loc[meta["electricity"] == "Yes", "building_id"])

    return {
        "metadata_building_ids": len(meta_ids),
        "electricity_csv_building_columns": len(elec_cols),
        "in_both": len(meta_ids & elec_cols),
        "in_metadata_only": len(meta_ids - elec_cols),
        "in_electricity_csv_only": len(elec_cols - meta_ids),
        "flagged_electricity_yes_in_metadata": len(flagged_electric),
        "flagged_yes_but_no_column_in_csv": len(flagged_electric - elec_cols),
        "has_column_but_not_flagged_yes": len(elec_cols - flagged_electric),
    }


def main() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    print("Inspecting metadata.csv ...")
    metadata_report = inspect_metadata()
    print("Inspecting weather.csv ...")
    weather_report = inspect_weather()
    print("Inspecting electricity_cleaned.csv ...")
    electricity_report = inspect_electricity()
    print("Cross-checking metadata vs electricity columns ...")
    cross = cross_check(metadata_report, electricity_report)

    report = {
        "metadata": metadata_report,
        "weather": weather_report,
        "electricity": electricity_report,
        "cross_check": cross,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))

    print("\n=== metadata.csv ===")
    print(f"rows={metadata_report['rows']} cols={metadata_report['columns']}")
    print(f"site_id counts: {metadata_report['site_id_value_counts']}")
    print(f"electricity flag counts: {metadata_report['electricity_flag_value_counts']}")
    print(f"duplicate building_id: {metadata_report['duplicate_building_id_count']}")

    print("\n=== weather.csv ===")
    print(f"rows={weather_report['rows']} cols={weather_report['columns']}")
    print(f"sites: {weather_report['site_ids']}")
    print(f"range: {weather_report['timestamp_min']} .. {weather_report['timestamp_max']}")
    print(f"duplicate (site_id, timestamp): {weather_report['duplicate_site_timestamp_rows']}")

    print("\n=== electricity_cleaned.csv ===")
    print(f"rows={electricity_report['rows']} cols={electricity_report['columns']} "
          f"(buildings={electricity_report['building_column_count']})")
    print(f"range: {electricity_report['timestamp_min']} .. {electricity_report['timestamp_max']}")
    print(f"duplicate timestamps: {electricity_report['duplicate_timestamp_rows']}")
    print(f"per-building missing rate: {electricity_report['missing_rate_summary']}")
    print(f"buildings 100% missing: {electricity_report['buildings_100pct_missing']}")
    print(f"buildings <5% missing: {electricity_report['buildings_under_5pct_missing']}")

    print("\n=== cross-check ===")
    for k, v in cross.items():
        print(f"{k}: {v}")

    print(f"\nFull report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
