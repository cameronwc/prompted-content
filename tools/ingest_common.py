"""Shared helpers for the photo ingest pipeline (tools/ingest_*.py).

A shoot lives in inbox/<shoot-name>/ (gitignored, never modified beyond
the underscore-prefixed pipeline files):

  _shoot.yaml     manifest: location, timezone, notes, optional overrides
  _scan.json      per-frame EXIF extraction (ingest_scan)
  _rejects.json   quality flags and scores (ingest_quality)
  _clusters.json  near-duplicate clusters and candidate picks (ingest_cluster)
  _derived.json   solar band + gear per candidate (ingest_derive)
  _drafts/        one <ulid>.yaml draft per candidate (ingest_draft)
  _review.md      human review checklist (ingest_draft)

Source frames are read-only; finalize archives them to archive/<shoot>/.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

from common import REPO_ROOT

INBOX_DIR = REPO_ROOT / "inbox"
ARCHIVE_DIR = REPO_ROOT / "archive"
LOCATIONS_PATH = REPO_ROOT / "locations.yaml"
INGEST_CONFIG_PATH = REPO_ROOT / "ingest_config.yaml"

IMAGE_SUFFIXES = {".jpg", ".jpeg"}


def shoot_dir(shoot: str) -> Path:
    d = INBOX_DIR / shoot
    if not d.is_dir():
        sys.exit(f"error: no shoot directory {d.relative_to(REPO_ROOT)}/ — "
                 f"run tools/ingest_init.py first.")
    return d


def load_manifest(shoot: str) -> dict:
    path = shoot_dir(shoot) / "_shoot.yaml"
    if not path.is_file():
        sys.exit(f"error: {path.relative_to(REPO_ROOT)} missing — "
                 f"run tools/ingest_init.py first.")
    manifest = yaml.safe_load(path.read_text())
    for key in ("shoot_name", "location", "timezone"):
        if key not in manifest:
            sys.exit(f"error: _shoot.yaml is missing required key '{key}'")
    return manifest


def shoot_frames(shoot: str) -> list[Path]:
    """Source image files in the shoot folder, sorted by name."""
    return sorted(
        p for p in shoot_dir(shoot).iterdir()
        if p.suffix.lower() in IMAGE_SUFFIXES and not p.name.startswith("_")
    )


def read_step(shoot: str, filename: str) -> dict:
    path = shoot_dir(shoot) / filename
    if not path.is_file():
        sys.exit(f"error: {path.relative_to(REPO_ROOT)} missing — "
                 f"run the earlier pipeline step first.")
    return json.loads(path.read_text())


def write_step(shoot: str, filename: str, payload: dict) -> Path:
    payload = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               **payload}
    path = shoot_dir(shoot) / filename
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def load_locations() -> list[dict]:
    if not LOCATIONS_PATH.is_file():
        return []
    data = yaml.safe_load(LOCATIONS_PATH.read_text()) or {}
    return data.get("locations", [])


def save_locations(locations: list[dict]) -> None:
    LOCATIONS_PATH.write_text(
        "# Saved shoot locations, offered by name in ingest_init.\n"
        + yaml.safe_dump({"locations": locations}, sort_keys=False, allow_unicode=True)
    )


def load_ingest_config(overrides: dict | None = None) -> dict:
    config = yaml.safe_load(INGEST_CONFIG_PATH.read_text())
    if overrides:
        config.update({k: v for k, v in overrides.items() if v is not None})
    return config
