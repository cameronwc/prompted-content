#!/usr/bin/env python3
"""Build dist/catalog.json from the taxonomy and pose records.

Refuses to build unless validation passes. Recomputes the blurhash from each
pose's thumbnail so the catalog always reflects the images on disk, rewrites
image paths as catalog-relative object keys (poses/<ulid>/thumb.jpg), and
prints a build summary.

SCHEMA_VERSION changes only on a breaking field change. catalog_version
increments on every build, continuing from the committed dist/catalog.json.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone

import blurhash

import validate
from common import DIST_DIR, iter_pose_dirs, load_pose, load_taxonomy

SCHEMA_VERSION = 1
CATALOG_PATH = DIST_DIR / "catalog.json"


def previous_catalog_version() -> int:
    try:
        return int(json.loads(CATALOG_PATH.read_text())["catalog_version"])
    except (FileNotFoundError, ValueError, KeyError, json.JSONDecodeError):
        return 0


def main() -> int:
    print("Running validation before build...")
    if validate.main() != 0:
        print("\nRefusing to build: validation failed.", file=sys.stderr)
        return 1

    taxonomy = load_taxonomy()
    poses = []
    for pose_dir in iter_pose_dirs():
        pose = load_pose(pose_dir)
        with open(pose_dir / pose["image"]["thumb"], "rb") as fh:
            pose["image"]["blurhash"] = blurhash.encode(fh, x_components=4, y_components=5)
        pose["image"]["thumb"] = f"poses/{pose['id']}/{pose['image']['thumb']}"
        pose["image"]["detail"] = f"poses/{pose['id']}/{pose['image']['detail']}"
        poses.append(pose)

    catalog = {
        "schema_version": SCHEMA_VERSION,
        "catalog_version": previous_catalog_version() + 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "taxonomy": taxonomy,
        "poses": poses,
    }

    DIST_DIR.mkdir(exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")

    by_category = Counter(c for p in poses for c in p["categories"])
    tones = Counter(pr["tone"] for p in poses for pr in p["prompts"])
    placeholders = sum(1 for p in poses if p["placeholder"])

    print(f"\nWrote {CATALOG_PATH} "
          f"(schema_version={SCHEMA_VERSION}, catalog_version={catalog['catalog_version']})")
    print(f"Poses: {len(poses)} total, {placeholders} placeholder")
    print("Per category: " + ", ".join(f"{c}={n}" for c, n in sorted(by_category.items())))
    print("Prompt tones: " + ", ".join(f"{t}={n}" for t, n in sorted(tones.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
