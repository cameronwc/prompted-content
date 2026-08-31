#!/usr/bin/env python3
"""Score every scanned frame and flag rejects. Nothing is ever deleted.

Per frame: Laplacian-variance sharpness, blown-highlight and crushed-shadow
percentages, resolution, 4:5 aspect, missing timestamp, and a face count
(checked against subject_count at finalize, not here).

Rejects (blur / blown / crushed / resolution) and flags (aspect / missing
timestamp) are recorded, never acted on destructively: _quality.json holds
every frame's scores and status, _rejects.json is the human report.
--keep <filename> force-keeps any frame. --auto-crop is an explicit opt-in
that writes a centre-cropped 4:5 COPY under _cropped/ (the source frame is
untouched) and marks it for mandatory review.

Thresholds come from ingest_config.yaml; every one is overridable per run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_common import load_ingest_config, read_step, shoot_dir, write_step  # noqa: E402

ASPECT = 4 / 5

_face_cascade = None


def face_count(gray: np.ndarray) -> int:
    global _face_cascade
    if _face_cascade is None:
        _face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    # Detect on a bounded copy with a real minimum face size: on full-res
    # frames the cascade otherwise finds dozens of tiny texture artifacts.
    height, width = gray.shape
    scale = min(1.0, 1280 / max(height, width))
    if scale < 1.0:
        gray = cv2.resize(gray, (int(width * scale), int(height * scale)))
    min_face = max(24, int(min(gray.shape) * 0.08))
    faces = _face_cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=6, minSize=(min_face, min_face))
    return len(faces)


def score_frame(path: Path, frame: dict, cfg: dict) -> dict:
    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return {"file": frame["file"], "status": "reject",
                "reasons": [{"check": "unreadable", "score": None}]}

    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    blown_pct = float((gray == 255).mean() * 100)
    crushed_pct = float((gray == 0).mean() * 100)
    short_edge = min(frame["width"], frame["height"])
    ratio = frame["width"] / frame["height"]
    aspect_off = abs(ratio - ASPECT) / ASPECT

    rejects, flags = [], []
    if sharpness < cfg["blur_floor"]:
        rejects.append({"check": "blur", "score": round(sharpness, 1),
                        "threshold": cfg["blur_floor"]})
    if blown_pct > cfg["blown_pct_max"]:
        rejects.append({"check": "blown_highlights", "score": round(blown_pct, 2),
                        "threshold": cfg["blown_pct_max"]})
    if crushed_pct > cfg["crushed_pct_max"]:
        rejects.append({"check": "crushed_shadows", "score": round(crushed_pct, 2),
                        "threshold": cfg["crushed_pct_max"]})
    if short_edge < cfg["min_short_edge"]:
        rejects.append({"check": "resolution", "score": short_edge,
                        "threshold": cfg["min_short_edge"]})
    if aspect_off > cfg["aspect_tolerance"]:
        flags.append({"check": "aspect", "score": round(ratio, 4),
                      "note": "not 4:5; use --auto-crop to write a reviewed 4:5 copy"})
    if not frame["utc"]:
        flags.append({"check": "missing_timestamp",
                      "note": "no DateTimeOriginal: light band underivable, "
                              "assign manually in the draft"})

    return {
        "file": frame["file"],
        "status": "reject" if rejects else ("flag" if flags else "ok"),
        "sharpness": round(sharpness, 1),
        "blown_pct": round(blown_pct, 2),
        "crushed_pct": round(crushed_pct, 2),
        "short_edge": short_edge,
        "face_count": face_count(gray),
        "reasons": rejects + flags,
    }


def auto_crop(shoot_path: Path, name: str) -> str:
    """Centre-crop a COPY to 4:5 under _cropped/; the source is untouched."""
    out_dir = shoot_path / "_cropped"
    out_dir.mkdir(exist_ok=True)
    with Image.open(shoot_path / name) as im:
        w, h = im.size
        if w / h > ASPECT:  # too wide
            new_w = int(h * ASPECT)
            box = ((w - new_w) // 2, 0, (w - new_w) // 2 + new_w, h)
        else:  # too tall
            new_h = int(w / ASPECT)
            box = (0, (h - new_h) // 2, w, (h - new_h) // 2 + new_h)
        im.crop(box).save(out_dir / name, "JPEG", quality=92)
    return f"_cropped/{name}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shoot")
    parser.add_argument("--keep", action="append", default=[], metavar="FILENAME",
                        help="Force-keep a frame the gates would reject")
    parser.add_argument("--auto-crop", action="store_true",
                        help="Write centre-cropped 4:5 copies of non-4:5 frames "
                             "under _cropped/ (marked for mandatory review)")
    parser.add_argument("--blur-floor", type=float)
    parser.add_argument("--blown-pct-max", type=float)
    parser.add_argument("--crushed-pct-max", type=float)
    parser.add_argument("--min-short-edge", type=int)
    args = parser.parse_args()

    cfg = load_ingest_config()["quality"]
    for key in ("blur_floor", "blown_pct_max", "crushed_pct_max", "min_short_edge"):
        value = getattr(args, key)
        if value is not None:
            cfg[key] = value

    shoot_path = shoot_dir(args.shoot)
    scan = read_step(args.shoot, "_scan.json")

    results = []
    for frame in scan["frames"]:
        result = score_frame(shoot_path / frame["file"], frame, cfg)
        if result["status"] == "reject" and frame["file"] in args.keep:
            result["status"] = "kept"
            result["kept_by_operator"] = True
        if args.auto_crop and any(r["check"] == "aspect" for r in result["reasons"]):
            result["use_file"] = auto_crop(shoot_path, frame["file"])
            result["needs_review"] = True
        results.append(result)

    rejected = [r for r in results if r["status"] == "reject"]
    flagged = [r for r in results if r["status"] == "flag"]
    kept = [r for r in results if r["status"] == "kept"]

    write_step(args.shoot, "_quality.json",
               {"thresholds": cfg, "frames": results})
    write_step(args.shoot, "_rejects.json", {
        "thresholds": cfg,
        "rejected": rejected,
        "flagged": flagged,
        "force_kept": kept,
    })

    print(f"Scored {len(results)} frames: "
          f"{len(results) - len(rejected) - len(flagged) - len(kept)} ok, "
          f"{len(flagged)} flagged, {len(rejected)} rejected, "
          f"{len(kept)} force-kept")
    for r in rejected + flagged:
        reasons = "; ".join(
            f"{x['check']}={x.get('score')}"
            + (f" (limit {x['threshold']})" if "threshold" in x else "")
            for x in r["reasons"])
        print(f"  {r['status'].upper():6} {r['file']}: {reasons}")
    print("Nothing was deleted; rejects are flags only (see _rejects.json). "
          "Use --keep <filename> to force-keep.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
