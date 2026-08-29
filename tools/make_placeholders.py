#!/usr/bin/env python3
"""Generate deliberately synthetic placeholder images for every pose.

For each pose directory: a 1200x1500 detail.jpg and 400x500 thumb.jpg
(4:5), solid category tint, with the pose id, category, subject count and
light conditions rendered on the tile plus an unmistakable PLACEHOLDER
treatment (diagonal banner and corner ticks) so nothing here can be taken
for photography. Computes the real blurhash of the thumb and writes it back
into pose.yaml.

Only touches poses with `placeholder: true`.
"""
from __future__ import annotations

import sys

import blurhash
import yaml
from PIL import Image, ImageDraw, ImageFont

from common import iter_pose_dirs, load_pose

DETAIL_SIZE = (1200, 1500)
THUMB_SIZE = (400, 500)

# Muted category tints; UI colour choices must stay readable on top of these.
TINTS = {
    "couples": (140, 117, 124),    # dusty rose
    "senior": (110, 124, 140),     # slate blue
    "family": (118, 133, 112),     # sage green
    "maternity": (150, 134, 112),  # warm sand
}
FALLBACK_TINT = (125, 125, 125)


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def render_detail(pose: dict) -> Image.Image:
    category = pose["categories"][0]
    tint = TINTS.get(category, FALLBACK_TINT)
    im = Image.new("RGB", DETAIL_SIZE, tint)
    draw = ImageDraw.Draw(im)
    w, h = DETAIL_SIZE

    # Slightly darker frame + corner ticks: reads as a spec sheet, not a photo
    dark = tuple(max(0, c - 28) for c in tint)
    light = tuple(min(255, c + 40) for c in tint)
    draw.rectangle([12, 12, w - 13, h - 13], outline=dark, width=6)
    tick = 90
    for cx, cy, dx, dy in [(12, 12, 1, 1), (w - 13, 12, -1, 1),
                           (12, h - 13, 1, -1), (w - 13, h - 13, -1, -1)]:
        draw.line([cx, cy, cx + dx * tick, cy], fill=light, width=10)
        draw.line([cx, cy, cx, cy + dy * tick], fill=light, width=10)

    # Diagonal PLACEHOLDER banner
    banner = Image.new("RGBA", (w * 2, 170), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(banner)
    bdraw.rectangle([0, 0, w * 2, 170], fill=dark + (200,))
    text = "PLACEHOLDER / NOT A PHOTOGRAPH   " * 3
    bdraw.text((30, 40), text, font=font(72), fill=light)
    banner = banner.rotate(30, expand=True)
    im.paste(banner, (w // 2 - banner.width // 2, h // 2 - banner.height // 2), banner)

    # Metadata block
    lights = ", ".join(pose["light_conditions"])
    cats = ", ".join(pose["categories"])
    lines = [
        (pose["id"], 54),
        (pose["slug"], 44),
        (f"category: {cats}", 44),
        (f"subjects: {pose['subject_count']}", 44),
        (f"light: {lights}", 44),
    ]
    y = 70
    for text, size in lines:
        draw.text((60, y), text, font=font(size), fill=(245, 242, 238))
        y += int(size * 1.5)

    draw.text((60, h - 120), "prompted dev asset", font=font(40), fill=light)
    return im


def main() -> int:
    done = skipped = 0
    for pose_dir in iter_pose_dirs():
        pose = load_pose(pose_dir)
        if pose.get("placeholder") is not True:
            skipped += 1
            continue
        detail = render_detail(pose)
        detail.save(pose_dir / "detail.jpg", quality=82)
        thumb = detail.resize(THUMB_SIZE, Image.LANCZOS)
        thumb.save(pose_dir / "thumb.jpg", quality=82)

        with open(pose_dir / "thumb.jpg", "rb") as fh:
            pose["image"]["blurhash"] = blurhash.encode(fh, x_components=4, y_components=5)
        (pose_dir / "pose.yaml").write_text(
            yaml.safe_dump(pose, sort_keys=False, allow_unicode=True, width=88)
        )
        done += 1
    print(f"Generated placeholder images for {done} poses"
          + (f" (skipped {skipped} non-placeholder)" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
