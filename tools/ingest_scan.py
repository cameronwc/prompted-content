#!/usr/bin/env python3
"""Scan a shoot folder: extract EXIF per frame and compute UTC timestamps.

EXIF DateTimeOriginal is local camera time with no zone; the manifest's
confirmed timezone turns it into UTC, which is what the solar derivation
needs. Frames without DateTimeOriginal get utc: null and are flagged
downstream (light band underivable — never guessed).

Writes _scan.json into the shoot folder. Source files are never modified.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

import piexif
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_common import load_manifest, shoot_frames, write_step  # noqa: E402

EXIF_DATE_FORMAT = "%Y:%m:%d %H:%M:%S"


def _rational(value) -> float | None:
    try:
        num, denom = value
        return float(Fraction(num, denom)) if denom else None
    except (TypeError, ValueError):
        return None


def _text(value) -> str | None:
    if isinstance(value, bytes):
        return value.decode("ascii", "replace").strip("\x00 ") or None
    return value or None


def scan_frame(path: Path, tz: ZoneInfo) -> dict:
    with Image.open(path) as im:
        width, height = im.size

    try:
        exif = piexif.load(str(path))
    except Exception:
        exif = {"0th": {}, "Exif": {}}
    zeroth, sub = exif.get("0th", {}), exif.get("Exif", {})

    local = utc = None
    raw_dto = _text(sub.get(piexif.ExifIFD.DateTimeOriginal))
    if raw_dto:
        try:
            naive = datetime.strptime(raw_dto, EXIF_DATE_FORMAT)
            local = naive.replace(tzinfo=tz)
            utc = local.astimezone(timezone.utc)
        except ValueError:
            pass

    exposure = _rational(sub.get(piexif.ExifIFD.ExposureTime))
    flash = sub.get(piexif.ExifIFD.Flash)
    camera = " ".join(filter(None, (_text(zeroth.get(piexif.ImageIFD.Make)),
                                    _text(zeroth.get(piexif.ImageIFD.Model)))))

    return {
        "file": path.name,
        "width": width,
        "height": height,
        "aspect_ratio": round(width / height, 4),
        "datetime_original": raw_dto,
        "local": local.isoformat() if local else None,
        "utc": utc.isoformat() if utc else None,
        "focal_mm": _rational(sub.get(piexif.ExifIFD.FocalLength)),
        "focal_35mm": sub.get(piexif.ExifIFD.FocalLengthIn35mmFilm),
        "f_number": _rational(sub.get(piexif.ExifIFD.FNumber)),
        "iso": sub.get(piexif.ExifIFD.ISOSpeedRatings),
        "exposure_time": (str(Fraction(exposure).limit_denominator(8000))
                          if exposure else None),
        # EXIF Flash bit 0: flash fired
        "flash_fired": bool(flash & 1) if isinstance(flash, int) else False,
        "camera": camera or None,
        "lens": _text(sub.get(piexif.ExifIFD.LensModel)),
    }


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit("usage: ingest_scan.py <shoot-name>")
    shoot = sys.argv[1]
    manifest = load_manifest(shoot)
    tz = ZoneInfo(manifest["timezone"])

    frames = shoot_frames(shoot)
    if not frames:
        sys.exit(f"error: no JPEG frames found in inbox/{shoot}/")

    scanned = [scan_frame(path, tz) for path in frames]
    no_time = [f["file"] for f in scanned if not f["utc"]]

    path = write_step(shoot, "_scan.json", {
        "shoot_name": shoot,
        "timezone": manifest["timezone"],
        "frames": scanned,
    })
    print(f"Scanned {len(scanned)} frames -> {path.name} "
          f"(timezone {manifest['timezone']})")
    if no_time:
        print(f"  {len(no_time)} without DateTimeOriginal (light band will "
              f"need manual assignment): {', '.join(no_time)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
