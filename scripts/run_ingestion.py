#!/usr/bin/env python3
"""python scripts/run_ingestion.py (or via docker compose run backend
python -m energy_platform.ingestion.pipeline)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from energy_platform.ingestion.pipeline import run_ingestion  # noqa: E402

if __name__ == "__main__":
    import json

    print(json.dumps(run_ingestion(), indent=2, default=str))
