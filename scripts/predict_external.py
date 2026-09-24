#!/usr/bin/env python3
"""python scripts/predict_external.py \\
    --energy examples/external_company/energy.csv \\
    --building examples/external_company/building.csv \\
    --output forecast.csv

Offline external-company inference: scores a company's OWN building data
against the already-trained models/random_forest_v1.joblib artifact --
never retrains, never touches PostgreSQL, never requires the building to
be registered in the production database. See docs/external_inference.md
for the full input contract and limitations.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from energy_platform.forecasting import external  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score external company building data with the trained forecasting "
        "model. Inference only -- never retrains, never writes to the production database."
    )
    parser.add_argument("--energy", required=True, type=Path, help="CSV with hourly timestamp,energy_kwh columns")
    parser.add_argument("--building", required=True, type=Path, help="CSV with one building metadata row")
    parser.add_argument("--output", required=True, type=Path, help="Path to write the forecast CSV")
    parser.add_argument(
        "--model", type=Path, default=None,
        help=f"Model artifact path (default: {external.DEFAULT_ARTIFACT_PATH})",
    )
    args = parser.parse_args()

    artifact_path = args.model or external.DEFAULT_ARTIFACT_PATH
    print(f"Loading model artifact: {artifact_path} (once per run; this may take a while)...")
    try:
        pipeline = external.load_model(artifact_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1) from None

    try:
        result = external.run_external_inference(args.energy, args.building, pipeline, artifact_path)
    except external.ExternalDataError as e:
        print(f"Validation error: {e}", file=sys.stderr)
        raise SystemExit(1) from None

    b = result.building
    print(f"Input building: {b.building_code} (primary_use={b.primary_use!r}, timezone={b.timezone})")
    print(f"Observations: {result.n_observations} hourly readings ({result.history_start} .. {result.history_end})")
    print(f"History duration: {result.history_end - result.history_start}")
    print(f"Forecast origin (local midnight, most recent): {result.forecast_origin}")
    if result.primary_use_warning:
        print(f"WARNING: {result.primary_use_warning}")
    print(f"Model artifact: {result.artifact_path} ({result.model_name} {result.model_version})")
    print(f"Predictions generated: {len(result.output)}")

    result.output.to_csv(args.output, index=False)
    print(f"Output written to: {args.output}")


if __name__ == "__main__":
    main()
