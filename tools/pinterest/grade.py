"""Consistent colour grade for every photo pin, applied before the scrim.

The reference profile in config/pinterest_grade.yaml is measured from the
real photograph set (the brand): `pins grade-profile` prints the current
measurements so the targets can be re-derived. The same grade is applied to
photo_real and photo_ai alike — parity is the point.

Pipeline (each step blended by `strength`):
  1. white balance: move the R/B channel-mean ratio toward the target ratio
  2. shadow lift: out = in + lift * (1 - in)^3
  3. brightness: mean luminance toward the target
  4. contrast: luminance spread (std) toward the target
  5. saturation: mean HSV saturation toward the target
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageEnhance

from common import REPO_ROOT

GRADE_PATH = REPO_ROOT / "config" / "pinterest_grade.yaml"

DEFAULTS = {
    "enabled": True,
    "strength": 0.8,
    "target": {"rb_ratio": 1.35, "mean_luminance": 0.50, "luminance_std": 0.24,
               "saturation": 0.36},
    "shadow_lift": 0.035,
    "mono_saturation_floor": 0.06,
    "limits": {"wb_gain": [0.85, 1.25], "brightness": [0.8, 1.35],
               "contrast": [0.8, 1.25], "saturation": [0.8, 1.5]},
}


def load_grade(path: Path = GRADE_PATH) -> dict:
    cfg = dict(DEFAULTS)
    if path.is_file():
        data = yaml.safe_load(path.read_text()) or {}
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k] = {**cfg[k], **v}
            else:
                cfg[k] = v
    return cfg


def measure(im: Image.Image) -> dict:
    """Channel ratio, luminance mean/std and saturation of an image (0..1)."""
    small = im.convert("RGB").copy()
    small.thumbnail((400, 400))
    rgb = np.asarray(small, dtype=np.float32) / 255.0
    r, g, b = rgb[..., 0].mean(), rgb[..., 1].mean(), rgb[..., 2].mean()
    lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    hsv = np.asarray(small.convert("HSV"), dtype=np.float32) / 255.0
    return {"rb_ratio": float(r / max(b, 1e-4)), "mean_luminance": float(lum.mean()),
            "luminance_std": float(lum.std()), "saturation": float(hsv[..., 1].mean()),
            "r": float(r), "g": float(g), "b": float(b)}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _blend(factor: float, strength: float) -> float:
    return 1.0 + (factor - 1.0) * strength


def apply_grade(im: Image.Image, cfg: dict) -> Image.Image:
    if not cfg.get("enabled", True):
        return im
    s = float(cfg["strength"])
    t, lim = cfg["target"], cfg["limits"]
    im = im.convert("RGB")
    m = measure(im)
    mono = m["saturation"] < float(cfg.get("mono_saturation_floor", 0.06))

    # 1. white balance toward the target warmth (R/B ratio), split across R and B.
    arr = np.asarray(im, dtype=np.float32) / 255.0
    if not mono:
        ratio = (t["rb_ratio"] / max(m["rb_ratio"], 1e-4)) ** 0.5
        arr[..., 0] *= _clamp(_blend(ratio, s), *lim["wb_gain"])
        arr[..., 2] *= _clamp(_blend(1 / ratio, s), *lim["wb_gain"])

    # 2. shadow lift
    lift = float(cfg["shadow_lift"]) * s
    arr = arr + lift * (1.0 - arr) ** 3
    im = Image.fromarray((np.clip(arr, 0, 1) * 255).round().astype(np.uint8), "RGB")

    # 3–5. brightness, contrast, saturation toward the reference.
    m = measure(im)
    im = ImageEnhance.Brightness(im).enhance(
        _clamp(_blend(t["mean_luminance"] / max(m["mean_luminance"], 1e-4), s), *lim["brightness"]))
    m = measure(im)
    im = ImageEnhance.Contrast(im).enhance(
        _clamp(_blend(t["luminance_std"] / max(m["luminance_std"], 1e-4), s), *lim["contrast"]))
    if not mono:
        m = measure(im)
        im = ImageEnhance.Color(im).enhance(
            _clamp(_blend(t["saturation"] / max(m["saturation"], 1e-4), s), *lim["saturation"]))
    return im
