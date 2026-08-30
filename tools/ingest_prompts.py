#!/usr/bin/env python3
"""Generate prompt copy for each ingest candidate via Gemini.

Model: gemini-3.7-flash (the current flash-tier multimodal model, checked
against ai.google.dev/gemini-api/docs/models 2026-08-30). The API key
comes from the GEMINI_API_KEY environment variable only — never a file,
never committed, never logged.

Each request sends the candidate PHOTOGRAPH itself alongside its derived
metadata and the full voice guide (prompts/voice_guide.md): the model can
see the pose, the setting, and the light, which produces far better copy
than metadata alone. Output is three prompts in distinct tones, always
including nervous_client. Prompts land in _prompts.json with
prompts_approved: false — nothing is auto-accepted; the draft step carries
them to review.

Resumable: clusters that already have prompts are skipped unless
--regenerate <cluster-id> is given (optionally with --note "steering
note"). A cost estimate is printed and confirmed before spending (--yes
skips the question, e.g. via make ingest-prompts CONFIRM=1).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import REPO_ROOT  # noqa: E402
from ingest_common import read_step, shoot_dir  # noqa: E402

MODEL = "gemini-3.7-flash"
VOICE_GUIDE_PATH = REPO_ROOT / "prompts" / "voice_guide.md"

# Standard-tier pricing, ai.google.dev/gemini-api/docs/pricing (checked
# 2026-08-30; rates double 2027-01-01).
COST_PER_M_INPUT = 0.75
COST_PER_M_OUTPUT = 3.75
# Rough per-request token shape for the estimate: image (~1120 for a
# downscaled frame) + voice guide + instructions in; three spoken lines
# plus a handful of setup steps out.
EST_INPUT_TOKENS = 3300
EST_OUTPUT_TOKENS = 360

TONES = ("playful", "calm", "romantic", "nervous_client")

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "instructions": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 5,
        },
        "prompts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "tone": {"type": "string", "enum": list(TONES)},
                },
                "required": ["text", "tone"],
            },
        },
    },
    "required": ["instructions", "prompts"],
}


def build_request_text(candidate: dict, voice_guide: str, note: str | None) -> str:
    meta = {
        "light_band": candidate["band"],
        "light_conditions": candidate["light_conditions"],
        "location": candidate["location"],
        "location_type": candidate["location_type"],
        "focal_mm": candidate["gear"]["focal_mm"],
        "aperture": candidate["gear"]["aperture"],
    }
    steering = f"\nSteering note from the photographer: {note}\n" if note else ""
    return (
        f"{voice_guide}\n\n---\n\n"
        "The attached photograph is a pose reference being added to the "
        "library. Shot metadata:\n"
        f"{json.dumps(meta, indent=2)}\n"
        f"{steering}\n"
        "Look at the photograph — the number of people, their pose, the "
        "setting, the light — and produce two things.\n\n"
        "1. `instructions`: two to five numbered-in-order setup steps "
        "telling the PHOTOGRAPHER how to arrange the subjects into this "
        "exact pose — bodies, hands, weight, spacing, and where each "
        "person faces. These are working notes read silently, so plain "
        "technical direction is fine here; be specific enough that a "
        "photographer who has never seen the photo could rebuild the "
        "pose. One step per string, no numbering prefixes.\n\n"
        "2. `prompts`: exactly three prompts the photographer would say "
        "ALOUD to the clients to get them into and through the pose. "
        "Three distinct tones: one MUST be nervous_client; pick the other "
        "two from playful, calm, romantic to fit what you see. Follow the "
        "voice guide exactly — the guide governs prompts only, not the "
        "instructions."
    )


def downscaled_jpeg(path: Path, max_edge: int = 1024) -> bytes:
    """Send a bounded image: plenty for the model, fewer input tokens."""
    import io

    from PIL import Image

    with Image.open(path) as im:
        im.thumbnail((max_edge, max_edge))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=85)
        return buf.getvalue()


def valid_prompts(prompts: list[dict]) -> bool:
    tones = [p.get("tone") for p in prompts]
    return (len(prompts) == 3 and len(set(tones)) == 3
            and "nervous_client" in tones
            and all(p.get("text", "").strip() for p in prompts))


def valid_instructions(instructions: list) -> bool:
    return (isinstance(instructions, list) and 2 <= len(instructions) <= 5
            and all(isinstance(s, str) and s.strip() for s in instructions))


def generate_for(client, image_bytes: bytes, request_text: str) -> tuple[dict, dict]:
    from google.genai import types

    for attempt in range(2):
        response = client.models.generate_content(
            model=MODEL,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                request_text,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=RESPONSE_SCHEMA,
                temperature=0.9,
            ),
        )
        usage = response.usage_metadata
        used = {
            "input_tokens": usage.prompt_token_count or 0,
            "output_tokens": (usage.candidates_token_count or 0)
            + (usage.thoughts_token_count or 0),
        }
        payload = json.loads(response.text)
        if (valid_prompts(payload.get("prompts", []))
                and valid_instructions(payload.get("instructions"))):
            return payload, used
        if attempt == 0:
            request_text += ("\nYour previous answer broke the format rules. "
                             "Two to five instruction steps, plus exactly three "
                             "prompts in three distinct tones, one of them "
                             "nervous_client.")
    raise RuntimeError(f"model returned invalid prompt/instruction set: {payload}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shoot")
    parser.add_argument("--regenerate", metavar="CLUSTER",
                        help="Regenerate one cluster's prompts even if present")
    parser.add_argument("--note", help="Steering note passed with --regenerate")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the interactive cost confirmation.")
    args = parser.parse_args()

    shoot_path = shoot_dir(args.shoot)
    derived = read_step(args.shoot, "_derived.json")["candidates"]
    voice_guide = VOICE_GUIDE_PATH.read_text()

    prompts_path = shoot_path / "_prompts.json"
    existing = json.loads(prompts_path.read_text()) if prompts_path.is_file() else {}

    if args.regenerate:
        todo = [c for c in derived if c["cluster"] == args.regenerate]
        if not todo:
            sys.exit(f"error: no candidate for cluster '{args.regenerate}'")
    else:
        todo = [c for c in derived if c["cluster"] not in existing]
        skipped = len(derived) - len(todo)
        if skipped:
            print(f"{skipped} candidates already have prompts; skipping "
                  f"(--regenerate <cluster> to redo one)")
    if not todo:
        print("Nothing to generate.")
        return 0

    estimate = len(todo) * (EST_INPUT_TOKENS * COST_PER_M_INPUT
                            + EST_OUTPUT_TOKENS * COST_PER_M_OUTPUT) / 1e6
    print(f"Model {MODEL}: {len(todo)} candidates, estimated cost "
          f"~${estimate:.3f} (${COST_PER_M_INPUT}/M in, ${COST_PER_M_OUTPUT}/M out)")
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
    client = genai.Client()

    spent_in = spent_out = 0
    for candidate in todo:
        image = downscaled_jpeg(shoot_path / candidate["file"])
        text = build_request_text(candidate, voice_guide, args.note)
        try:
            payload, used = generate_for(client, image, text)
        except Exception as exc:
            print(f"  {candidate['cluster']} FAILED: {exc} — continuing")
            continue
        spent_in += used["input_tokens"]
        spent_out += used["output_tokens"]
        existing[candidate["cluster"]] = {
            "file": candidate["file"],
            "model": MODEL,
            "note": args.note,
            "instructions": payload["instructions"],
            "prompts": payload["prompts"],
            "prompts_approved": False,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        prompts_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n")
        print(f"  {candidate['cluster']} {candidate['file']}:")
        for i, step in enumerate(payload["instructions"], 1):
            print(f"    {i}. {step}")
        for p in payload["prompts"]:
            print(f"    [{p['tone']}] {p['text']}")

    actual = (spent_in * COST_PER_M_INPUT + spent_out * COST_PER_M_OUTPUT) / 1e6
    print(f"\nActual usage: {spent_in} input + {spent_out} output tokens "
          f"= ${actual:.4f}")
    print(f"Prompts saved to {prompts_path.name} with prompts_approved: false — "
          f"review before finalize.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
