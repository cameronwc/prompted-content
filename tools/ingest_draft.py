#!/usr/bin/env python3
"""Emit one draft.yaml per candidate into inbox/<shoot>/_drafts/, plus a
human review checklist (_review.md).

Auto-filled: id (new ULID), image_source: photo, placeholder: false, the
derived light band, gear, orientation, location_types from the manifest,
generated prompts (prompts_approved: false), and an _ingest block with the
capture metadata finalize needs. Human-supplied fields are emitted as
'TODO:' markers: slug, categories, subject_count, subject_types,
difficulty, accessibility, and the Group C light modifiers.

Approval: set prompts_approved: true in a draft after reading its prompts,
or bulk-approve with --approve-prompts (make approve-prompts SHOOT=...).
Editing prompt text by hand also counts as approval — finalize treats a
prompt set that differs from the generated one as reviewed.

Resumable: existing drafts are never overwritten (operator edits are
sacred); only new candidates get drafts. _review.md is rewritten each run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from ulid import ULID

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import REPO_ROOT  # noqa: E402
from ingest_common import load_manifest, read_step, shoot_dir  # noqa: E402

TODO = "TODO:"


def build_draft(candidate: dict, cluster: dict, prompts_record: dict | None,
                scan_frame: dict, quality_frame: dict, shoot: str) -> dict:
    width, height = scan_frame["width"], scan_frame["height"]
    prompts = (prompts_record or {}).get("prompts") or [
        {"text": TODO, "tone": "nervous_client"},
        {"text": TODO, "tone": "calm"},
    ]
    instructions = (prompts_record or {}).get("instructions") or [TODO]
    return {
        "id": str(ULID()),
        "slug": TODO,
        "image": {"thumb": "thumb.jpg", "detail": "detail.jpg", "blurhash": "PENDING"},
        "placeholder": False,
        "image_source": "photo",
        "categories": TODO,
        "subject_count": TODO,
        "subject_types": TODO,
        # Solar band is derived; add up to two Group C modifiers
        # (backlit / open_shade) by eye — they are never inferred.
        "light_conditions": candidate["light_conditions"] or [TODO],
        "location_types": [candidate["location_type"]],
        "orientation": "vertical" if height >= width else "horizontal",
        "difficulty": TODO,
        # Photographer-facing setup steps; reviewed together with prompts.
        "instructions": instructions,
        "prompts": prompts,
        "prompts_approved": False,
        "gear": candidate["gear"],
        "accessibility": TODO,
        "version": 1,
        "status": "active",
        "_ingest": {
            "shoot": shoot,
            "cluster": candidate["cluster"],
            "file": candidate["file"],
            "use_file": quality_frame.get("use_file", candidate["file"]),
            "cluster_size": len(cluster["members"]),
            "utc": candidate["utc"],
            "solar_elevation": candidate["solar_elevation"],
            "band": candidate["band"],
            "needs_manual_band": candidate["needs_manual_band"],
            "face_count": quality_frame["face_count"],
            "prompt_model": (prompts_record or {}).get("model"),
        },
    }


def review_entry(draft: dict, shoot: str) -> str:
    ing = draft["_ingest"]
    todo_fields = [k for k, v in draft.items()
                   if v == TODO or v == [TODO]]
    if any(p["text"] == TODO for p in draft["prompts"]):
        todo_fields.append("prompts (generation pending)")
    lines = [
        f"## {ing['cluster']} — `{ing['file']}` -> draft `{draft['id']}`",
        "",
        f"- thumbnail: `inbox/{shoot}/{ing['use_file']}`",
        f"- light: band **{ing['band'] or 'MANUAL — no timestamp'}**"
        + (f" (elevation {ing['solar_elevation']} deg)" if ing["solar_elevation"] is not None else "")
        + f", tags {draft['light_conditions']}",
        f"- gear: {draft['gear']['focal_mm']} mm, {draft['gear']['aperture']}",
        f"- cluster size: {ing['cluster_size']} frame(s)",
        f"- faces detected: {ing['face_count']} (checked against subject_count at finalize)",
        f"- still needed: {', '.join(todo_fields) if todo_fields else 'nothing — fill approval'}",
        "",
        "Posing instructions:",
        "",
    ]
    for i, step in enumerate(draft["instructions"], 1):
        lines.append(f"> {i}. {step}")
    lines += ["",
              f"Prompts ({'approved' if draft['prompts_approved'] else 'NOT approved'}):",
              ""]
    for p in draft["prompts"]:
        lines.append(f"> **{p['tone']}** — {p['text']}")
    lines.append("")
    return "\n".join(lines)


def approve_prompts(shoot: str) -> int:
    drafts_dir = shoot_dir(shoot) / "_drafts"
    approved = 0
    for path in sorted(drafts_dir.glob("*.yaml")):
        draft = yaml.safe_load(path.read_text())
        if draft.get("prompts_approved"):
            continue
        if any(p["text"] == TODO for p in draft["prompts"]):
            print(f"  skipping {path.name}: prompts not generated yet")
            continue
        draft["prompts_approved"] = True
        path.write_text(yaml.safe_dump(draft, sort_keys=False, allow_unicode=True, width=88))
        approved += 1
    print(f"Approved prompts on {approved} draft(s). Only do this after "
          f"actually reading _review.md.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shoot")
    parser.add_argument("--approve-prompts", action="store_true",
                        help="Bulk-approve generated prompts after review")
    args = parser.parse_args()

    if args.approve_prompts:
        return approve_prompts(args.shoot)

    shoot_path = shoot_dir(args.shoot)
    manifest = load_manifest(args.shoot)  # validates the manifest exists
    derived = read_step(args.shoot, "_derived.json")["candidates"]
    clusters = {c["id"]: c for c in read_step(args.shoot, "_clusters.json")["clusters"]}
    scan = {f["file"]: f for f in read_step(args.shoot, "_scan.json")["frames"]}
    quality = {f["file"]: f for f in read_step(args.shoot, "_quality.json")["frames"]}
    prompts_path = shoot_path / "_prompts.json"
    prompts = json.loads(prompts_path.read_text()) if prompts_path.is_file() else {}

    drafts_dir = shoot_path / "_drafts"
    drafts_dir.mkdir(exist_ok=True)
    existing_clusters = {}
    for path in drafts_dir.glob("*.yaml"):
        existing_clusters[yaml.safe_load(path.read_text())["_ingest"]["cluster"]] = path

    drafts = []
    created = 0
    for candidate in derived:
        cluster_id = candidate["cluster"]
        if cluster_id in existing_clusters:
            drafts.append(yaml.safe_load(existing_clusters[cluster_id].read_text()))
            continue
        draft = build_draft(candidate, clusters[cluster_id], prompts.get(cluster_id),
                            scan[candidate["file"]], quality[candidate["file"]],
                            args.shoot)
        (drafts_dir / f"{draft['id']}.yaml").write_text(
            yaml.safe_dump(draft, sort_keys=False, allow_unicode=True, width=88))
        drafts.append(draft)
        created += 1

    review = [f"# Review — shoot {args.shoot} ({manifest['location']['name']})", "",
              f"{len(drafts)} candidate(s). Fill every TODO in _drafts/, read the "
              f"prompts below, then approve (edit prompts_approved, or "
              f"`make approve-prompts SHOOT={args.shoot}`).", ""]
    review += [review_entry(d, args.shoot) for d in drafts]
    (shoot_path / "_review.md").write_text("\n".join(review))

    print(f"{created} draft(s) created, {len(drafts) - created} kept -> "
          f"{drafts_dir.relative_to(REPO_ROOT)}/")
    print(f"Review checklist: {(shoot_path / '_review.md').relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
