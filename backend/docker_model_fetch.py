#!/usr/bin/env python3
"""Ensures models/random_forest_v1.joblib exists on disk before uvicorn
(and its lifespan's existing, UNCHANGED model-loading code -- see
energy_platform.forecasting.external.get_cached_pipeline) ever tries to
load it. Run by docker-entrypoint.sh before exec'ing the real command.

Local Docker Compose: the file is already present via the host bind mount
(docker-compose.yml's `./models:/app/models`) -- this is then a fast,
harmless no-op (one hash check, no network access, no behavior change).

Cloud Run (a fresh container on every deploy, no host filesystem, no bind
mount, and the model is deliberately gitignored -- see .gitignore):
downloads it once from a GitHub Release asset URL (MODEL_ARTIFACT_URL env
var) and verifies its SHA256 against MODEL_ARTIFACT_SHA256 (defaults to
the artifact's known, published hash) before returning. Exits non-zero on
a checksum mismatch or download failure -- this process's whole job is to
never let the app start serving with a missing, corrupt, or unverified
model artifact silently. Platform-agnostic despite the name: the same
mechanism works for any container platform that doesn't offer a host
bind mount (Cloud Run, Render, etc.) -- see docs/deployment_cloud_run.md.
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path

# The published hash of models/random_forest_v1.joblib (docs/deployment_cloud_run.md
# and the GitHub Release description carry the same value) -- not a secret,
# just the expected checksum, so a safe default even if the env var is unset.
DEFAULT_SHA256 = "2b859538307206b4516ff23c984878b1051a05cc910480ec9bdcd27ee83421a8"

_CHUNK_SIZE = 4 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_name(destination.name + ".part")
    with urllib.request.urlopen(url, timeout=300) as response, tmp_path.open("wb") as out:
        total = response.getheader("Content-Length")
        print(f"Downloading model artifact from {url} ({total or 'unknown'} bytes)...")
        while chunk := response.read(_CHUNK_SIZE):
            out.write(chunk)
    tmp_path.rename(destination)


def main() -> None:
    model_path = Path(os.environ.get("MODEL_PATH", "models/random_forest_v1.joblib"))
    expected_sha256 = os.environ.get("MODEL_ARTIFACT_SHA256", DEFAULT_SHA256)
    artifact_url = os.environ.get("MODEL_ARTIFACT_URL")

    if model_path.exists() and _sha256(model_path) == expected_sha256:
        print(f"Model artifact already present and verified: {model_path}")
        return

    if not artifact_url:
        print(
            f"WARNING: {model_path} not found (or its hash didn't match) and "
            "MODEL_ARTIFACT_URL is not set -- the app will fail at startup when its "
            "lifespan tries to load the model. Set MODEL_ARTIFACT_URL to a GitHub "
            "Release asset URL, or mount/copy the artifact into the container "
            "yourself. See docs/deployment_cloud_run.md.",
            file=sys.stderr,
        )
        return

    _download(artifact_url, model_path)

    actual_sha256 = _sha256(model_path)
    if actual_sha256 != expected_sha256:
        model_path.unlink(missing_ok=True)
        print(
            f"FATAL: downloaded model SHA256 mismatch.\n  expected: {expected_sha256}\n"
            f"  actual:   {actual_sha256}\nRefusing to start with an unverified artifact.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Model artifact downloaded and verified: {model_path}")


if __name__ == "__main__":
    main()
