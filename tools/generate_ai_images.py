#!/usr/bin/env python3
"""Generate AI placeholder images for the poses selected in dist/ai_subset.json.

Model: gemini-3.1-flash-image (Nano Banana 2) via the Gemini Interactions
API. The API key comes from the GEMINI_API_KEY environment variable only —
it is never written to a file, never committed, and never logged.

Each image prompt is built from the pose record's own metadata (category,
subject count/types, light, location, orientation, difficulty, slug). The
image is derived from the record; the record is NEVER derived from the
image — this tool only touches image.*, image_source, and nothing else in
pose.yaml.

Style consistency: the first selected pose in each category is generated
first as that category's hero (1K, like everything else, by operator
decision); every remaining
generation in the category passes the hero as a reference image, and all
prompts share a fixed style suffix.

Output per pose: detail_ai.jpg (1200x1500) and thumb_ai.jpg (400x500),
4:5, EXIF Software tag marking them as AI placeholder content, real
blurhash written back to pose.yaml, image_source: ai, placeholder: true
retained. No visible watermark — provenance is the _ai suffix, the
image_source field, the placeholder flag, the EXIF tag, and SynthID.

Resumable: poses whose _ai images already exist on disk are never
regenerated. Repeated per-pose failure is logged and skipped; the run
continues.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

import blurhash
import yaml
from PIL import Image

from common import DIST_DIR, POSES_DIR, load_pose

API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
MODEL = "gemini-3.1-flash-image"
SUBSET_PATH = DIST_DIR / "ai_subset.json"

DETAIL_SIZE = (1200, 1500)
THUMB_SIZE = (400, 500)

# Standard-tier pricing, ai.google.dev/gemini-api/docs/pricing (checked 2026-08-29)
COST_PER_1K = 0.067

MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 2
PACE_SECONDS = 2  # minimum spacing between requests

EXIF_SOFTWARE = (
    "AI-generated placeholder (gemini-3.1-flash-image) -- Prompted UI test "
    "fixture, not for release"
)

STYLE_SUFFIX = (
    "Simple uncluttered background, consistent warm colour grade, 4:5 "
    "vertical framing, shallow depth of field, candid documentary portrait "
    "photography rather than anything stylised or cinematic. No text, no "
    "watermark, no logos."
)

LIGHT_PHRASES = {
    "golden": "warm golden-hour sunlight",
    "blue": "dim blue-hour twilight",
    "overcast": "soft overcast daylight",
    "soft_low": "soft low-angle sun, long gentle shadows",
    "mid": "clear midday sun",
    "harsh_overhead": "harsh midday overhead sun with strong hard shadows",
    "open_shade": "even open shade",
    "backlit": "strong backlighting with rim light around the subjects",
    "indoor_window": "soft indoor window light",
    "night_flash": "night, lit by direct on-camera flash against darkness",
}

LOCATION_PHRASES = {
    "beach": "on a beach",
    "forest": "in a forest",
    "urban": "on an urban street",
    "field": "in an open grassy field",
    "studio": "in a minimal photography studio",
    "home": "in a bright lived-in home interior",
    "mountain": "in mountain scenery",
}

SUBJECT_PHRASES = {
    "adult": "adult", "teen": "teenager", "child": "child",
    "toddler": "toddler", "pregnant": "pregnant woman",
    "senior_adult": "older adult", "pet": "dog",
}

DIFFICULTY_PHRASES = {
    "easy": "a simple, relaxed pose",
    "moderate": "a natural mid-movement pose",
    "advanced": "a dynamic, energetic pose",
}


def build_prompt(pose: dict) -> str:
    category = pose["categories"][0]
    # Slugs carry disambiguation suffixes (a location id, a counter);
    # strip those so only the pose concept reaches the prompt.
    tokens = pose["slug"].split("-")
    while tokens and (tokens[-1] in LOCATION_PHRASES or tokens[-1].isdigit()):
        tokens.pop()
    concept = " ".join(tokens)
    subjects = ", ".join(SUBJECT_PHRASES.get(t, t) for t in pose["subject_types"])
    n = pose["subject_count"]

    if category == "couples":
        who = "a couple, two adults"
    elif category == "senior":
        who = "one teenager, a high-school senior portrait"
    elif category == "maternity":
        who = ("an expectant mother with her partner" if n == 2
               else "an expectant mother")
    else:
        who = f"a family of {n} ({subjects})"

    # Only the record's primary (first) light condition drives the image:
    # secondary conditions describe when the pose also works, and joining
    # them produces physically contradictory lighting direction.
    primary_light = pose["light_conditions"][0]
    light = LIGHT_PHRASES.get(primary_light, primary_light.replace("_", " "))
    location = " / ".join(LOCATION_PHRASES[l] for l in pose["location_types"])
    arrangement = (
        "subjects arranged laterally across the frame"
        if pose["orientation"] == "horizontal"
        else "an upright composition"
    )
    return (
        f"Photograph of {who} {location}, posing: {concept} — "
        f"{DIFFICULTY_PHRASES[pose['difficulty']]}. Lighting: {light}. "
        f"{arrangement.capitalize()}. {STYLE_SUFFIX}"
    )


def api_call(prompt: str, reference_jpeg: bytes | None, image_size: str) -> bytes:
    """One Interactions API call; returns decoded image bytes. Raises on failure."""
    inputs: list[dict] = [{"type": "text", "text": prompt}]
    if reference_jpeg is not None:
        inputs.append({
            "type": "image",
            "mime_type": "image/jpeg",
            "data": base64.b64encode(reference_jpeg).decode(),
        })
        inputs[0]["text"] = (
            "Match the colour grade, lighting feel and photographic style of the "
            "attached reference image exactly. " + prompt
        )
    body = json.dumps({
        "model": MODEL,
        "input": inputs,
        "response_format": {
            "type": "image",
            "mime_type": "image/jpeg",
            "aspect_ratio": "4:5",
            "image_size": image_size,
        },
    }).encode()
    req = urllib.request.Request(
        API_URL, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": os.environ["GEMINI_API_KEY"],
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = json.load(resp)
    data = extract_image_b64(payload)
    if data is None:
        raise RuntimeError("response contained no image data")
    return base64.b64decode(data)


def extract_image_b64(payload) -> str | None:
    """Find base64 image data in the response. Primary path is
    output_image.data; fall back to scanning for any image-typed part so a
    minor response-shape change doesn't strand a paid call."""
    if isinstance(payload, dict):
        img = payload.get("output_image")
        if isinstance(img, dict) and img.get("data"):
            return img["data"]
        for key in ("output", "outputs", "steps", "content", "parts", "candidates"):
            found = extract_image_b64(payload.get(key))
            if found:
                return found
        if str(payload.get("type", "")).startswith("image") and payload.get("data"):
            return payload["data"]
        if str(payload.get("mime_type", payload.get("mimeType", ""))).startswith("image") \
                and payload.get("data"):
            return payload["data"]
    elif isinstance(payload, list):
        for item in payload:
            found = extract_image_b64(item)
            if found:
                return found
    return None


def generate_with_retry(pose_id: str, prompt: str, reference: bytes | None,
                        image_size: str) -> tuple[bytes | None, int]:
    """Returns (image_bytes or None, retries_used). Backs off on 429/5xx."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return api_call(prompt, reference, image_size), attempt
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or exc.code >= 500
            detail = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            retryable = True
            detail = type(exc).__name__
        if not retryable or attempt == MAX_RETRIES:
            print(f"  {pose_id}: FAILED after {attempt} retries ({detail})")
            return None, attempt
        wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
        print(f"  {pose_id}: {detail}, retrying in {wait}s "
              f"(attempt {attempt + 1}/{MAX_RETRIES})")
        time.sleep(wait)
    return None, MAX_RETRIES


def to_4x5(im: Image.Image) -> Image.Image:
    """Centre-crop to exactly 4:5 (defensive; the API is asked for 4:5)."""
    w, h = im.size
    target = 4 / 5
    if abs(w / h - target) < 1e-6:
        return im
    if w / h > target:
        new_w = int(h * target)
        x = (w - new_w) // 2
        return im.crop((x, 0, x + new_w, h))
    new_h = int(w / target)
    y = (h - new_h) // 2
    return im.crop((0, y, 0 + w, y + new_h))


def write_outputs(pose_dir, pose: dict, image_bytes: bytes) -> None:
    im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    im = to_4x5(im)
    exif = Image.Exif()
    exif[0x0131] = EXIF_SOFTWARE  # Software

    detail = im.resize(DETAIL_SIZE, Image.LANCZOS)
    detail.save(pose_dir / "detail_ai.jpg", quality=88, exif=exif)
    thumb = im.resize(THUMB_SIZE, Image.LANCZOS)
    thumb.save(pose_dir / "thumb_ai.jpg", quality=88, exif=exif)

    update_record(pose_dir, pose)


def update_record(pose_dir, pose: dict) -> None:
    """Point the record at the _ai files. Touches image.*, image_source and
    nothing else — metadata is never derived from the generated image."""
    with open(pose_dir / "thumb_ai.jpg", "rb") as fh:
        pose["image"]["blurhash"] = blurhash.encode(fh, x_components=4, y_components=5)
    pose["image"]["thumb"] = "thumb_ai.jpg"
    pose["image"]["detail"] = "detail_ai.jpg"
    pose["image_source"] = "ai"
    # Operator decision 2026-08-31: AI imagery ships (dev and prod) until
    # real photoshoots replace it, so placeholder is false. image_source
    # stays 'ai' so replacement can target these poses shoot by shoot.
    pose["placeholder"] = False
    (pose_dir / "pose.yaml").write_text(
        yaml.safe_dump(pose, sort_keys=False, allow_unicode=True, width=88)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print constructed image prompts; no API calls, no cost.")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="Generate at most N images this run.")
    parser.add_argument("--ids", metavar="ULID[,ULID...]",
                        help="Restrict this run to specific subset poses "
                             "(subset order, hero-first, is preserved).")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the interactive cost confirmation.")
    args = parser.parse_args()

    if not SUBSET_PATH.is_file():
        sys.exit("error: dist/ai_subset.json not found — run `make ai-select` first.")
    subset_ids = json.loads(SUBSET_PATH.read_text())["poses"]

    # Subset order groups poses by category; the first per category is the hero.
    work: list[tuple[str, dict, bool]] = []  # (pose_id, pose, is_hero)
    seen_categories: set[str] = set()
    for pose_id in subset_ids:
        pose_dir = POSES_DIR / pose_id
        if not pose_dir.is_dir():
            sys.exit(f"error: selected pose {pose_id} does not exist on disk.")
        pose = load_pose(pose_dir)
        category = pose["categories"][0]
        is_hero = category not in seen_categories
        seen_categories.add(category)
        work.append((pose_id, pose, is_hero))

    if args.dry_run:
        print(f"DRY RUN — {len(work)} prompts, no API calls.\n")
        for pose_id, pose, is_hero in work:
            tag = "HERO " if is_hero else ""
            print(f"--- {tag}{pose_id} [{pose['categories'][0]}] ---")
            print(build_prompt(pose) + "\n")
        return 0

    # Resumability: anything with both _ai files on disk is done.
    pending = [(pid, pose, hero) for pid, pose, hero in work
               if not ((POSES_DIR / pid / "detail_ai.jpg").is_file()
                       and (POSES_DIR / pid / "thumb_ai.jpg").is_file())]
    done_count = len(work) - len(pending)
    if done_count:
        print(f"{done_count} of {len(work)} already generated on disk; skipping those.")
    if args.ids:
        wanted = set(args.ids.split(","))
        unknown = wanted - {pid for pid, _, _ in work}
        if unknown:
            sys.exit(f"error: not in the AI subset: {', '.join(sorted(unknown))}")
        pending = [(pid, p, h) for pid, p, h in pending if pid in wanted]
    if args.limit is not None:
        pending = pending[:args.limit]
    if not pending:
        print("Nothing to generate.")
        return 0

    cost = len(pending) * COST_PER_1K
    print(f"About to generate {len(pending)} images at 1K "
          f"(≈ ${COST_PER_1K:.3f} each; heroes lead their category but are "
          f"also 1K by operator decision).")
    print(f"Estimated cost: ${cost:.2f} (standard tier, excludes retries).")
    if not args.yes:
        answer = input("Proceed and spend this? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted; nothing was generated.")
            return 1

    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("error: GEMINI_API_KEY environment variable is not set.")

    references: dict[str, bytes] = {}  # category -> hero detail_ai.jpg bytes
    for pose_id, pose, _ in work:  # pre-load heroes finished in earlier runs
        category = pose["categories"][0]
        hero_path = POSES_DIR / pose_id / "detail_ai.jpg"
        if category not in references and hero_path.is_file():
            references[category] = hero_path.read_bytes()

    ok = failed = 0
    actual_cost = 0.0
    for pose_id, pose, is_hero in pending:
        category = pose["categories"][0]
        reference = references.get(category)
        size = "1K"
        started = time.time()
        image_bytes, retries = generate_with_retry(
            pose_id, build_prompt(pose), reference, size
        )
        elapsed = time.time() - started
        if image_bytes is None:
            failed += 1
            print(f"  {pose_id}: skipped after repeated failure "
                  f"(retries={retries}, {elapsed:.1f}s) — continuing.")
            continue
        write_outputs(POSES_DIR / pose_id, pose, image_bytes)
        if category not in references:
            references[category] = (POSES_DIR / pose_id / "detail_ai.jpg").read_bytes()
        ok += 1
        actual_cost += COST_PER_1K
        print(f"  {pose_id}: ok ({size}, retries={retries}, {elapsed:.1f}s)")
        time.sleep(PACE_SECONDS)

    print(f"\nDone: {ok} generated, {failed} failed/skipped. "
          f"Cost incurred ≈ ${actual_cost:.2f} (rate-card estimate).")
    if failed:
        print("Re-run the same command to retry only the failures.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
