#!/usr/bin/env python3
"""Promote completed drafts from inbox/<shoot>/_drafts/ into poses/.

A draft is refused (with the reason) while it has:
  - any remaining TODO: marker
  - prompts_approved: false — unless the prompt text was hand-edited,
    which counts as review (compared against the generated set)
  - fewer than 2 prompts, or none with tone nervous_client
  - no posing instructions (every photo ships with setup steps)
  - no categories or no subject_types
  - a light tag set violating the grouped light rules
  - a detected face count EXCEEDING subject_count by more than one (someone
    in frame the record doesn't account for). Fewer detected faces than
    subjects is only warned about: the frontal cascade cannot see profile,
    tilted, or occluded faces. `face_check_waived: true` in the draft
    silences the refusal for a reviewed frame.

On success, per pose: the candidate frame is centre-cropped to exact 4:5
and resized to detail.jpg (1200w) and thumb.jpg (400w), the blurhash is
computed, poses/<ulid>/pose.yaml is written (validated against the pose
schema first), and the cluster's source frames move to archive/<shoot>/.
Nothing is ever deleted; drafts are marked finalized in place.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import blurhash
import yaml
from jsonschema import Draft202012Validator
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import POSES_DIR, REPO_ROOT, load_schema, load_taxonomy  # noqa: E402
from ingest_common import ARCHIVE_DIR, shoot_dir  # noqa: E402
from light_rules import light_condition_errors, light_groups  # noqa: E402

TODO = "TODO:"
DETAIL_WIDTH, THUMB_WIDTH = 1200, 400
ASPECT = 4 / 5


def find_todos(value, path="") -> list[str]:
    if isinstance(value, str) and value.startswith(TODO):
        return [path or "(root)"]
    if isinstance(value, dict):
        return [t for k, v in value.items() for t in find_todos(v, f"{path}.{k}".strip("."))]
    if isinstance(value, list):
        return [t for i, v in enumerate(value) for t in find_todos(v, f"{path}[{i}]")]
    return []


def prompts_hand_edited(draft: dict, generated: dict | None) -> bool:
    if not generated:
        return False
    return draft["prompts"] != generated.get("prompts")


def refusals(draft: dict, generated: dict | None, groups: dict) -> list[str]:
    problems = [f"unfilled field: {t}" for t in find_todos(draft)]

    if not draft.get("prompts_approved") and not prompts_hand_edited(draft, generated):
        problems.append(
            "prompts_approved is false and the prompts are unedited — read them "
            "in _review.md, then set prompts_approved: true or run "
            "make approve-prompts")

    prompts = draft.get("prompts") or []
    if len(prompts) < 2:
        problems.append(f"only {len(prompts)} prompt(s); at least 2 required")
    if not any(p.get("tone") == "nervous_client" for p in prompts):
        problems.append("no nervous_client prompt (product invariant)")

    if not draft.get("instructions"):
        problems.append("no posing instructions (every photo pose ships with "
                        "setup steps for the photographer)")

    if not draft.get("categories"):
        problems.append("no categories")
    if not draft.get("subject_types"):
        problems.append("no subject_types")

    if isinstance(draft.get("light_conditions"), list) and isinstance(
            draft.get("location_types"), list):
        problems += [f"light rules: {e}" for e in light_condition_errors(
            draft["light_conditions"], draft["location_types"], groups)]

    faces = draft["_ingest"].get("face_count")
    count = draft.get("subject_count")
    if isinstance(faces, int) and isinstance(count, int):
        if faces > count + 1 and not draft.get("face_check_waived"):
            problems.append(
                f"detector found {faces} faces but subject_count is {count} — "
                f"someone extra may be in frame; recheck, then set "
                f"face_check_waived: true if the frame is right")
        elif faces < count - 1:
            print(f"  note: only {faces} of {count} faces detected "
                  f"(profiles/occlusion) — not blocking")
    return problems


def to_4x5(im: Image.Image) -> Image.Image:
    w, h = im.size
    if abs(w / h - ASPECT) < 1e-6:
        return im
    if w / h > ASPECT:
        new_w = int(h * ASPECT)
        return im.crop(((w - new_w) // 2, 0, (w - new_w) // 2 + new_w, h))
    new_h = int(w / ASPECT)
    return im.crop((0, (h - new_h) // 2, w, (h - new_h) // 2 + new_h))


def write_images(source: Path, pose_dir: Path) -> str:
    with Image.open(source) as im:
        im = to_4x5(im.convert("RGB"))
        for name, width in (("detail.jpg", DETAIL_WIDTH), ("thumb.jpg", THUMB_WIDTH)):
            out = im.resize((width, int(width / ASPECT)), Image.LANCZOS)
            out.save(pose_dir / name, "JPEG", quality=88)
    with open(pose_dir / "thumb.jpg", "rb") as fh:
        return blurhash.encode(fh, x_components=4, y_components=5)


def finalize_draft(shoot: str, draft_path: Path, draft: dict,
                   cluster: dict, schema_validator) -> None:
    shoot_path = shoot_dir(shoot)
    ing = draft["_ingest"]

    # Draft-only control fields never enter the pose record.
    pose = {k: v for k, v in draft.items()
            if k not in ("_ingest", "prompts_approved", "face_check_waived")}
    pose_dir = POSES_DIR / pose["id"]
    if pose_dir.exists():
        sys.exit(f"error: poses/{pose['id']}/ already exists — refusing to overwrite")
    pose_dir.mkdir(parents=True)

    try:
        pose["image"]["blurhash"] = write_images(shoot_path / ing["use_file"], pose_dir)
        errors = sorted(schema_validator.iter_errors(pose), key=lambda e: list(e.path))
        if errors:
            raise RuntimeError("; ".join(e.message for e in errors))
        (pose_dir / "pose.yaml").write_text(
            yaml.safe_dump(pose, sort_keys=False, allow_unicode=True, width=88))
    except Exception:
        shutil.rmtree(pose_dir)  # never leave a half-written pose
        raise

    # Archive every source frame of the cluster (candidate + alternates).
    archive = ARCHIVE_DIR / shoot
    archive.mkdir(parents=True, exist_ok=True)
    for member in cluster["members"]:
        src = shoot_path / member["file"]
        if src.is_file():
            shutil.move(str(src), archive / member["file"])

    draft["_ingest"]["finalized_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    draft["_ingest"]["pose_dir"] = f"poses/{pose['id']}"
    draft_path.write_text(yaml.safe_dump(draft, sort_keys=False, allow_unicode=True, width=88))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shoot")
    parser.add_argument("--id", help="Finalize only this draft ULID")
    args = parser.parse_args()

    shoot_path = shoot_dir(args.shoot)
    drafts_dir = shoot_path / "_drafts"
    if not drafts_dir.is_dir():
        sys.exit("error: no _drafts/ — run make ingest-draft first")

    clusters = {c["id"]: c for c in
                json.loads((shoot_path / "_clusters.json").read_text())["clusters"]}
    prompts_path = shoot_path / "_prompts.json"
    generated = json.loads(prompts_path.read_text()) if prompts_path.is_file() else {}
    groups = light_groups(load_taxonomy())
    schema_validator = Draft202012Validator(load_schema())

    promoted = refused = skipped = 0
    for draft_path in sorted(drafts_dir.glob("*.yaml")):
        draft = yaml.safe_load(draft_path.read_text())
        if args.id and draft["id"] != args.id:
            continue
        if draft["_ingest"].get("finalized_at"):
            skipped += 1
            continue

        problems = refusals(draft, generated.get(draft["_ingest"]["cluster"]), groups)
        if problems:
            refused += 1
            print(f"REFUSED {draft_path.name} ({draft['_ingest']['cluster']}):")
            for p in problems:
                print(f"  - {p}")
            continue

        finalize_draft(args.shoot, draft_path, draft,
                       clusters[draft["_ingest"]["cluster"]], schema_validator)
        promoted += 1
        print(f"PROMOTED {draft['id']} <- {draft['_ingest']['file']} "
              f"(sources archived to archive/{args.shoot}/)")

    print(f"\n{promoted} promoted, {refused} refused, {skipped} already finalized.")
    if promoted:
        print("Run `make validate` and commit the new pose directories.")
    return 0 if refused == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
