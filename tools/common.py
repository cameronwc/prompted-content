"""Shared helpers for the Prompted content pipeline tools."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "pose.schema.json"
TAXONOMY_DIR = REPO_ROOT / "taxonomy"
LIGHT_BANDS_PATH = TAXONOMY_DIR / "light_bands.json"
POSES_DIR = REPO_ROOT / "poses"
DIST_DIR = REPO_ROOT / "dist"

TAXONOMY_FILES = {
    "categories": "categories.yaml",
    "light_conditions": "light_conditions.yaml",
    "location_types": "location_types.yaml",
    "subject_types": "subject_types.yaml",
    "accessibility": "accessibility.yaml",
}


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def load_taxonomy() -> dict[str, list[dict]]:
    """Return {taxonomy_name: [entry, ...]} with each entry's id/display/parent."""
    taxonomy = {}
    for name, filename in TAXONOMY_FILES.items():
        data = yaml.safe_load((TAXONOMY_DIR / filename).read_text())
        taxonomy[name] = data["entries"]
    return taxonomy


def load_light_bands() -> dict:
    """Solar-elevation band thresholds (taxonomy/light_bands.json)."""
    return json.loads(LIGHT_BANDS_PATH.read_text())


def taxonomy_ids(taxonomy: dict[str, list[dict]]) -> dict[str, set[str]]:
    return {name: {e["id"] for e in entries} for name, entries in taxonomy.items()}


def iter_pose_dirs() -> list[Path]:
    """Pose directories, sorted, skipping non-directories."""
    if not POSES_DIR.is_dir():
        return []
    return sorted(p for p in POSES_DIR.iterdir() if p.is_dir())


def load_pose(pose_dir: Path) -> dict:
    return yaml.safe_load((pose_dir / "pose.yaml").read_text())
