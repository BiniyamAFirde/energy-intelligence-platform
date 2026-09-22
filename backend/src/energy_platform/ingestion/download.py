"""Download the three BDG2 files we need directly from the official repo.

Deliberately stdlib-only (urllib, hashlib) so this can run without installing
the backend package or any third-party dependency -- useful both on the host
and inside the backend container.

Source of truth: https://github.com/buds-lab/building-data-genome-project-2
The three files we use are stored via Git LFS in that repo. GitHub serves two
different things for an LFS-tracked path:
  - raw.githubusercontent.com  -> the small LFS *pointer* file (oid + size)
  - media.githubusercontent.com -> the actual binary content

We fetch the pointer first (a few hundred bytes) to learn the expected size
and sha256 *before* deciding whether to re-download, which is what makes this
idempotent and verifiable rather than a guess based on "file exists".
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = "buds-lab/building-data-genome-project-2"
BRANCH = "master"
POINTER_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
MEDIA_BASE = f"https://media.githubusercontent.com/media/{REPO}/{BRANCH}"

FILES = {
    "metadata.csv": "data/metadata/metadata.csv",
    "weather.csv": "data/weather/weather.csv",
    "electricity_cleaned.csv": "data/meters/cleaned/electricity_cleaned.csv",
}

# Resolved relative to the current working directory rather than via __file__
# parent-counting, because that arithmetic silently breaks depending on
# whether this runs on the host (backend/src/.../download.py, 4 levels under
# the repo root) or inside the container (/app/src/..., a different depth
# entirely). Both the host invocation (run from the repo root) and the
# container invocation (WORKDIR /app, with ./data mounted at /app/data) share
# the same convention instead: "data/raw" relative to CWD. DATA_DIR is an
# escape hatch for anything that can't guarantee its CWD.
DATA_DIR = Path(os.environ.get("BDG2_DATA_DIR", "data"))
RAW_DIR = DATA_DIR / "raw"
MANIFEST_PATH = RAW_DIR / "manifest.json"
CHUNK_SIZE = 1024 * 1024  # 1 MB


@dataclass
class LfsPointer:
    oid_sha256: str
    size: int


class DownloadError(RuntimeError):
    pass


def fetch_lfs_pointer(repo_path: str) -> LfsPointer:
    url = f"{POINTER_BASE}/{repo_path}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    oid = None
    size = None
    for line in text.splitlines():
        if line.startswith("oid sha256:"):
            oid = line.split(":", 1)[1].strip()
        elif line.startswith("size "):
            size = int(line.split(" ", 1)[1].strip())
    if oid is None or size is None:
        raise DownloadError(
            f"Could not parse LFS pointer for {repo_path} "
            f"(is the file still LFS-tracked at this path?)"
        )
    return LfsPointer(oid_sha256=oid, size=size)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def local_file_matches(path: Path, pointer: LfsPointer) -> bool:
    if not path.exists():
        return False
    if path.stat().st_size != pointer.size:
        return False
    return sha256_of(path) == pointer.oid_sha256


def download_file(repo_path: str, dest: Path, pointer: LfsPointer) -> None:
    url = f"{MEDIA_BASE}/{repo_path}"
    tmp_path = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)

    start = time.time()
    downloaded = 0
    with urllib.request.urlopen(url, timeout=60) as resp, tmp_path.open("wb") as out:
        while True:
            chunk = resp.read(CHUNK_SIZE)
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)
            pct = downloaded / pointer.size * 100 if pointer.size else 0
            elapsed = time.time() - start
            mbps = (downloaded / 1_000_000) / elapsed if elapsed > 0 else 0
            print(
                f"\r  {dest.name}: {downloaded / 1_000_000:6.1f} MB / "
                f"{pointer.size / 1_000_000:6.1f} MB ({pct:5.1f}%) "
                f"{mbps:5.1f} MB/s",
                end="",
                flush=True,
            )
    print()

    actual_sha256 = sha256_of(tmp_path)
    if actual_sha256 != pointer.oid_sha256:
        tmp_path.unlink(missing_ok=True)
        raise DownloadError(
            f"SHA256 mismatch for {dest.name}: expected {pointer.oid_sha256}, "
            f"got {actual_sha256}. Deleted the partial download."
        )
    tmp_path.rename(dest)


def ensure_file(name: str, repo_path: str) -> dict:
    dest = RAW_DIR / name
    pointer = fetch_lfs_pointer(repo_path)

    if local_file_matches(dest, pointer):
        print(f"[skip] {name}: already present and verified "
              f"({pointer.size / 1_000_000:.1f} MB, sha256 matches remote)")
        status = "already_verified"
    else:
        print(f"[download] {name}: {pointer.size / 1_000_000:.1f} MB expected")
        download_file(repo_path, dest, pointer)
        print(f"[verified] {name}: sha256 matches remote pointer")
        status = "downloaded"

    return {
        "name": name,
        "repo_path": repo_path,
        "size_bytes": pointer.size,
        "sha256": pointer.oid_sha256,
        "status": status,
        "local_path": str(dest),
    }


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for name, repo_path in FILES.items():
        try:
            results.append(ensure_file(name, repo_path))
        except DownloadError as exc:
            print(f"[error] {name}: {exc}", file=sys.stderr)
            sys.exit(1)

    MANIFEST_PATH.write_text(json.dumps({"files": results}, indent=2))
    print(f"\nManifest written to {MANIFEST_PATH}")
    total_mb = sum(r["size_bytes"] for r in results) / 1_000_000
    print(f"Total verified size on disk: {total_mb:.1f} MB")


if __name__ == "__main__":
    main()
