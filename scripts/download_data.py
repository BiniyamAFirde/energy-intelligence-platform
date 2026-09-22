#!/usr/bin/env python3
"""Thin wrapper: `python scripts/download_data.py`.

download.py is stdlib-only, so this works with any Python 3.9+, no venv or
package install required -- we just need backend/src on sys.path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from energy_platform.ingestion.download import main  # noqa: E402

if __name__ == "__main__":
    main()
