#!/usr/bin/env python3
"""Validate every pose record in poses/.

Checks, per pose:
  1. JSON Schema conformance (schema/pose.schema.json)
  2. Referential integrity against the taxonomy files
  3. At least one prompt with tone `nervous_client` (hard product invariant)
  4. ULID uniqueness across the repo (and directory name matches the id)
  5. Slug uniqueness
  6. Image presence, 4:5 aspect within 1%, 400w thumb / 1200w detail
  7. subject_count consistent with subject_types
  8. Light-condition group rules (light_rules.py): at most one solar band,
     one sky tag, two modifiers, 3 light tags total; the taxonomy's
     excludes/excludes_groups; outdoor daytime poses carry exactly one
     solar band

Exit status is non-zero if any pose fails. Output is a per-pose report with
file paths and specific failures.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from PIL import Image

from common import (
    POSES_DIR,
    REPO_ROOT,
    iter_pose_dirs,
    load_schema,
    load_taxonomy,
    taxonomy_ids,
)
from light_rules import light_condition_errors, light_groups

ASPECT = 4 / 5
ASPECT_TOLERANCE = 0.01
IMAGE_SPECS = {"thumb": 400, "detail": 1200}

# pose field -> taxonomy name
REF_FIELDS = {
    "categories": "categories",
    "light_conditions": "light_conditions",
    "location_types": "location_types",
    "subject_types": "subject_types",
    "accessibility": "accessibility",
}


def rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def check_images(pose_dir: Path, pose: dict, errors: list[str]) -> None:
    image = pose.get("image")
    if not isinstance(image, dict):
        return  # schema check already reported this
    for kind, want_width in IMAGE_SPECS.items():
        name = image.get(kind)
        if not isinstance(name, str):
            continue
        path = pose_dir / name
        if not path.is_file():
            errors.append(f"{rel(pose_dir)}/{name}: image file missing")
            continue
        try:
            with Image.open(path) as im:
                width, height = im.size
        except OSError as exc:
            errors.append(f"{rel(path)}: unreadable image ({exc})")
            continue
        if width != want_width:
            errors.append(
                f"{rel(path)}: width is {width}px, {kind} images must be {want_width}px"
            )
        ratio = width / height
        if abs(ratio - ASPECT) / ASPECT > ASPECT_TOLERANCE:
            errors.append(
                f"{rel(path)}: aspect ratio {width}x{height} = {ratio:.4f}, "
                f"must be 4:5 ({ASPECT:.2f}) within 1%"
            )


def validate_pose(
    pose_dir: Path,
    pose: dict,
    schema_validator: Draft202012Validator,
    valid_ids: dict[str, set[str]],
    groups: dict[str, dict],
    check_image_files: bool = True,
) -> list[str]:
    errors: list[str] = []
    yaml_path = rel(pose_dir / "pose.yaml")

    # 1. Schema conformance
    for err in sorted(schema_validator.iter_errors(pose), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{yaml_path}: schema: {where}: {err.message}")

    # 2. Referential integrity
    for field, taxonomy_name in REF_FIELDS.items():
        values = pose.get(field)
        if not isinstance(values, list):
            continue
        for value in values:
            if value not in valid_ids[taxonomy_name]:
                errors.append(
                    f"{yaml_path}: {field}: '{value}' is not a known id in "
                    f"taxonomy/{taxonomy_name}.yaml"
                )

    # 3. nervous_client prompt invariant
    prompts = pose.get("prompts")
    if isinstance(prompts, list) and prompts:
        tones = {p.get("tone") for p in prompts if isinstance(p, dict)}
        if "nervous_client" not in tones:
            errors.append(
                f"{yaml_path}: prompts: no prompt with tone 'nervous_client' "
                f"(every pose must include one; found tones: {sorted(t for t in tones if t)})"
            )

    # 4 (partial). Directory name must match the pose id
    pose_id = pose.get("id")
    if isinstance(pose_id, str) and pose_dir.name != pose_id:
        errors.append(
            f"{yaml_path}: id '{pose_id}' does not match its directory name "
            f"'{pose_dir.name}'"
        )

    # 6. Images (skipped with --no-images, e.g. before placeholders exist)
    if check_image_files:
        check_images(pose_dir, pose, errors)

    # 8. Light-condition group rules
    lights = pose.get("light_conditions")
    locations = pose.get("location_types")
    if isinstance(lights, list) and isinstance(locations, list):
        for msg in light_condition_errors(lights, locations, groups):
            errors.append(f"{yaml_path}: {msg}")

    # 7. Coherence: distinct subject types cannot exceed subject_count
    count = pose.get("subject_count")
    types = pose.get("subject_types")
    if isinstance(count, int) and isinstance(types, list) and len(types) > count:
        errors.append(
            f"{yaml_path}: subject_count is {count} but subject_types lists "
            f"{len(types)} distinct types ({types}); a pose cannot have more "
            f"subject types than subjects"
        )

    return errors


def main() -> int:
    check_image_files = "--no-images" not in sys.argv[1:]
    schema_validator = Draft202012Validator(load_schema())
    taxonomy = load_taxonomy()
    valid_ids = taxonomy_ids(taxonomy)
    groups = light_groups(taxonomy)

    pose_dirs = iter_pose_dirs()
    if not pose_dirs:
        print(f"No pose directories found under {rel(POSES_DIR)}/ — nothing to validate.")
        return 0

    failures = 0
    seen_ids: dict[str, Path] = {}
    seen_slugs: dict[str, Path] = {}
    source_counts: dict[str, int] = {}

    for pose_dir in pose_dirs:
        yaml_path = pose_dir / "pose.yaml"
        if not yaml_path.is_file():
            print(f"FAIL {pose_dir.name}")
            print(f"  - {rel(pose_dir)}: missing pose.yaml")
            failures += 1
            continue
        try:
            pose = yaml.safe_load(yaml_path.read_text())
        except yaml.YAMLError as exc:
            print(f"FAIL {pose_dir.name}")
            print(f"  - {rel(yaml_path)}: YAML parse error: {exc}")
            failures += 1
            continue
        if not isinstance(pose, dict):
            print(f"FAIL {pose_dir.name}")
            print(f"  - {rel(yaml_path)}: top level must be a mapping")
            failures += 1
            continue

        errors = validate_pose(
            pose_dir, pose, schema_validator, valid_ids, groups, check_image_files
        )

        source = pose.get("image_source", "synthetic")
        source_counts[source] = source_counts.get(source, 0) + 1

        # 4 & 5. Cross-pose uniqueness
        pose_id = pose.get("id")
        if isinstance(pose_id, str):
            if pose_id in seen_ids:
                errors.append(
                    f"{rel(yaml_path)}: duplicate id '{pose_id}' also used by "
                    f"{rel(seen_ids[pose_id] / 'pose.yaml')}"
                )
            else:
                seen_ids[pose_id] = pose_dir
        slug = pose.get("slug")
        if isinstance(slug, str):
            if slug in seen_slugs:
                errors.append(
                    f"{rel(yaml_path)}: duplicate slug '{slug}' also used by "
                    f"{rel(seen_slugs[slug] / 'pose.yaml')}"
                )
            else:
                seen_slugs[slug] = pose_dir

        if errors:
            failures += 1
            print(f"FAIL {pose_dir.name}")
            for e in errors:
                print(f"  - {e}")

    total = len(pose_dirs)
    sources = ", ".join(f"{s}={n}" for s, n in sorted(source_counts.items()))
    if failures:
        print(f"\n{failures} of {total} poses failed validation. Image sources: {sources}")
        return 1
    print(f"All {total} poses valid. Image sources: {sources}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
