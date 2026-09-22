"""Unit tests for the download/integrity logic. No network calls: LfsPointer
parsing and file verification are tested against synthetic local data."""

import hashlib

from energy_platform.ingestion.download import (
    LfsPointer,
    local_file_matches,
    sha256_of,
)

POINTER_TEXT = (
    "version https://git-lfs.github.com/spec/v1\n"
    "oid sha256:abc123\n"
    "size 272024\n"
)


def _parse(text: str) -> LfsPointer:
    oid = size = None
    for line in text.splitlines():
        if line.startswith("oid sha256:"):
            oid = line.split(":", 1)[1].strip()
        elif line.startswith("size "):
            size = int(line.split(" ", 1)[1].strip())
    return LfsPointer(oid_sha256=oid, size=size)


def test_parses_lfs_pointer_fields():
    pointer = _parse(POINTER_TEXT)
    assert pointer.oid_sha256 == "abc123"
    assert pointer.size == 272024


def test_sha256_of_matches_hashlib(tmp_path):
    f = tmp_path / "sample.csv"
    f.write_bytes(b"building_id,site_id\nA,1\n")
    expected = hashlib.sha256(f.read_bytes()).hexdigest()
    assert sha256_of(f) == expected


def test_local_file_matches_true_when_size_and_hash_agree(tmp_path):
    f = tmp_path / "sample.csv"
    content = b"some,bytes,here\n"
    f.write_bytes(content)
    pointer = LfsPointer(oid_sha256=hashlib.sha256(content).hexdigest(), size=len(content))
    assert local_file_matches(f, pointer) is True


def test_local_file_matches_false_on_size_mismatch(tmp_path):
    f = tmp_path / "sample.csv"
    f.write_bytes(b"short")
    pointer = LfsPointer(oid_sha256="irrelevant", size=999999)
    assert local_file_matches(f, pointer) is False


def test_local_file_matches_false_when_missing(tmp_path):
    pointer = LfsPointer(oid_sha256="x", size=1)
    assert local_file_matches(tmp_path / "does_not_exist.csv", pointer) is False
