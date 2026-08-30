#!/usr/bin/env python3
"""Generate posing `instructions` for existing library poses via Gemini.

The ingest pipeline generates instructions for photo poses at ingest time
(ingest_prompts.py); this tool backfills the field on poses that already
live in poses/ — primarily the AI-imaged dev subset, so the iOS app has
published fixtures for the instructions UI before real photography lands.

Model gemini-3.7-flash sees the pose's own detail image plus its record
metadata and returns 2–5 photographer-facing setup steps, written into
pose.yaml (version bumped). Resumable: poses that already carry
instructions are skipped. Cost is estimated and confirmed before spending
(--yes skips the question). GEMINI_API_KEY from the environment only.

Usage:
  generate_instructions.py --ids ULID,ULID,...   # specific poses
  generate_instructions.py --missing             # every ai/photo pose without instructions
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

import yaml
from PIL import Image

from common import POSES_DIR, load_pose

MODEL = "gemini-3.7-flash"
# Standard-tier pricing, ai.google.dev/gemini-api/docs/pricing (2026-08-30)
COST_PER_M_INPUT = 0.75
COST_PER_M_OUTPUT = 3.75
EST_INPUT_TOKENS = 1500
EST_OUTPUT_TOKENS = 180

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "instructions": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 5,
        }
    },
    "required": ["instructions"],
}

REQUEST = """The attached photograph is a pose from a posing-reference \
library for portrait photographers. Record metadata:
{meta}

Write two to five ordered setup steps telling the PHOTOGRAPHER how to \
arrange the subjects into this exact pose — bodies, hands, weight, \
spacing, and where each person faces. Working notes read silently, so \
plain technical direction is fine; be specific enough that a photographer \
who has never seen the photo could rebuild the pose. One step per string, \
no numbering prefixes."""


def detail_image(pose: dict, pose_dir) -> bytes:
    with Image.open(pose_dir / pose["image"]["detail"]) as im:
        im.thumbnail((1024, 1024))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=85)
        return buf.getvalue()


def valid(instructions) -> bool:
    return (isinstance(instructions, list) and 2 <= len(instructions) <= 5
            and all(isinstance(s, str) and s.strip() for s in instructions))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ids", help="Comma-separated pose ULIDs")
    group.add_argument("--missing", action="store_true",
                       help="All ai/photo poses without instructions")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the interactive cost confirmation.")
    args = parser.parse_args()

    if args.ids:
        pose_dirs = []
        for pid in args.ids.split(","):
            d = POSES_DIR / pid.strip()
            if not (d / "pose.yaml").is_file():
                sys.exit(f"error: no pose {pid.strip()}")
            pose_dirs.append(d)
    else:
        pose_dirs = [d for d in sorted(POSES_DIR.iterdir())
                     if (d / "pose.yaml").is_file()
                     and load_pose(d).get("image_source") in ("ai", "photo")]

    todo = [(d, load_pose(d)) for d in pose_dirs]
    skipped = [(d, p) for d, p in todo if p.get("instructions")]
    todo = [(d, p) for d, p in todo if not p.get("instructions")]
    if skipped:
        print(f"{len(skipped)} pose(s) already have instructions; skipping.")
    if not todo:
        print("Nothing to generate.")
        return 0

    estimate = len(todo) * (EST_INPUT_TOKENS * COST_PER_M_INPUT
                            + EST_OUTPUT_TOKENS * COST_PER_M_OUTPUT) / 1e6
    print(f"Model {MODEL}: {len(todo)} poses, estimated cost ~${estimate:.3f}")
    if not args.yes:
        try:
            answer = input("Proceed and spend? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "y":
            print("Aborted; nothing spent.")
            return 1

    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("error: GEMINI_API_KEY environment variable is not set.")
    from google import genai
    from google.genai import types
    client = genai.Client()

    spent_in = spent_out = failed = 0
    for pose_dir, pose in todo:
        meta = {k: pose[k] for k in ("categories", "subject_count", "subject_types",
                                     "light_conditions", "location_types",
                                     "orientation", "difficulty")}
        request = REQUEST.format(meta=json.dumps(meta, indent=2))
        instructions = None
        for attempt in range(2):
            response = client.models.generate_content(
                model=MODEL,
                contents=[types.Part.from_bytes(data=detail_image(pose, pose_dir),
                                                mime_type="image/jpeg"),
                          request],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=RESPONSE_SCHEMA,
                    temperature=0.7,
                ),
            )
            usage = response.usage_metadata
            spent_in += usage.prompt_token_count or 0
            spent_out += ((usage.candidates_token_count or 0)
                          + (usage.thoughts_token_count or 0))
            candidate = json.loads(response.text).get("instructions")
            if valid(candidate):
                instructions = candidate
                break
            request += "\nFormat broken: two to five non-empty steps."
        if not instructions:
            failed += 1
            print(f"  {pose['id']} FAILED to produce a valid step list — continuing")
            continue

        pose["instructions"] = instructions
        pose["version"] = int(pose["version"]) + 1
        (pose_dir / "pose.yaml").write_text(
            yaml.safe_dump(pose, sort_keys=False, allow_unicode=True, width=88))
        print(f"  {pose['id']} ({pose['slug']}):")
        for i, step in enumerate(instructions, 1):
            print(f"    {i}. {step}")

    actual = (spent_in * COST_PER_M_INPUT + spent_out * COST_PER_M_OUTPUT) / 1e6
    print(f"\nActual usage: {spent_in} input + {spent_out} output tokens = ${actual:.4f}; "
          f"{failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
