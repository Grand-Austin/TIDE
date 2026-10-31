"""Fetch and pin the Natural Earth 10m land archive used by the coast guard.

The runtime reads the tracked archive offline. This script is the only place that
needs network access. Run ``uv run python -m pipeline.navigation_data`` from the
repository root to refresh the archive and its manifest.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import zipfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "navigation"
ARCHIVE_NAME = "ne_10m_land.zip"
MANIFEST_NAME = "manifest.json"
DATASET_VERSION = "5.1.1"
DATASET = "Natural Earth 10m land"
SOURCE_URL = "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_land.zip"
SOURCE_PAGE = "https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-land/"
LICENSE = "Public domain"
PINNED_SHA256 = "e547d749445eaa0964aba76738090ec88f5e63c4585122170f98c67a7ea922dc"
MAX_MEMBER_BYTES = 64 * 1024 * 1024
REQUIRED_MEMBERS = (
    "ne_10m_land.shp",
    "ne_10m_land.shx",
    "ne_10m_land.dbf",
    "ne_10m_land.prj",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_archive(data: bytes) -> dict:
    """Validate the zip layout and return pinned member metadata."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Archive contains duplicate member names")
        for name in names:
            member = Path(name)
            if member.is_absolute() or ".." in member.parts or len(member.parts) != 1:
                raise ValueError(f"Unsafe archive member: {name!r}")
        missing = set(REQUIRED_MEMBERS) - set(names)
        if missing:
            raise ValueError(f"Archive is missing required members: {sorted(missing)}")
        members = []
        for name in names:
            info = archive.getinfo(name)
            if info.file_size > MAX_MEMBER_BYTES:
                raise ValueError(f"Archive member is too large: {name!r}")
            payload = archive.read(name)
            members.append({
                "name": name,
                "bytes": len(payload),
                "sha256": sha256(payload),
            })
    return {"members": members}


def fetch(*, expected_sha256: str = PINNED_SHA256) -> bytes:
    response = httpx.get(SOURCE_URL, follow_redirects=True, timeout=60.0)
    response.raise_for_status()
    data = response.content
    actual = sha256(data)
    if actual != expected_sha256:
        raise ValueError(
            f"Downloaded archive checksum {actual} does not match pinned {expected_sha256}"
        )
    return data


def write_manifest(data: bytes, *, retrieved_utc: str | None = None) -> dict:
    manifest = {
        "schema_version": 1,
        "dataset": DATASET,
        "version": DATASET_VERSION,
        "license": LICENSE,
        "source": {
            "url": SOURCE_URL,
            "documentation": SOURCE_PAGE,
        },
        "archive": {
            "path": ARCHIVE_NAME,
            "bytes": len(data),
            "sha256": sha256(data),
        },
        "retrieved_utc": retrieved_utc
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    manifest.update(read_archive(data))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / ARCHIVE_NAME).write_bytes(data)
    (DATA_DIR / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return manifest


def verify_local() -> dict:
    manifest = json.loads((DATA_DIR / MANIFEST_NAME).read_text())
    data = (DATA_DIR / ARCHIVE_NAME).read_bytes()
    if sha256(data) != manifest["archive"]["sha256"]:
        raise ValueError("Local archive checksum does not match its manifest")
    read_archive(data)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha256", default=PINNED_SHA256)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Check the tracked archive against its manifest without network access",
    )
    args = parser.parse_args()
    if args.verify_only:
        manifest = verify_local()
        print(f"verified {manifest['dataset']} {manifest['version']} ({args.sha256})")
        return
    data = fetch(expected_sha256=args.sha256)
    manifest = write_manifest(data)
    print(
        f"wrote {DATA_DIR / ARCHIVE_NAME} ({manifest['archive']['bytes']} bytes, "
        f"sha256 {manifest['archive']['sha256']})"
    )


if __name__ == "__main__":
    main()
