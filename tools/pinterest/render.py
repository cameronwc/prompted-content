"""Render text pins, photo pins and the dry-run contact sheet.

Output is a 1000x1500 sRGB image under the configured byte ceiling: PNG for
text pins (flat colour compresses well), JPEG for photo pins.
PNG rendering carries no EXIF from the source JPEG; nothing here touches
metadata to evade detection — AI pins are disclosed in the description.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .catalog import Pose, PromptText
from .grade import apply_grade
from .text_fit import Fit, fit_text, load_font, text_width, wrap


FORMAT = {"text": ("png", "image/png"), "photo": ("jpg", "image/jpeg")}


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _png_bytes(im: Image.Image, max_bytes: int) -> bytes:
    """Encode as PNG; quantise progressively if the byte ceiling is exceeded."""
    for attempt in range(4):
        buf = io.BytesIO()
        target = im if attempt == 0 else im.convert("RGB").quantize(
            colors=[256, 192, 128][attempt - 1], method=Image.Quantize.MEDIANCUT).convert("RGB")
        target.save(buf, "PNG", optimize=True)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            return data
    raise RuntimeError(f"rendered pin exceeds {max_bytes} bytes even after quantising")


def _jpeg_bytes(im: Image.Image, max_bytes: int) -> bytes:
    for quality in (92, 88, 84, 80, 76, 72, 68):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
        if len(buf.getvalue()) <= max_bytes:
            return buf.getvalue()
    raise RuntimeError(f"rendered photo pin exceeds {max_bytes} bytes even at quality 68")


def _draw_wordmark(draw: ImageDraw.ImageDraw, cfg: dict, w: int, h: int, ink, light: bool):
    r = cfg["render"]
    font = load_font(r["fonts"]["label"], r["text"]["wordmark_size"])
    mark = r.get("wordmark", "Prompted")
    tw = text_width(font, mark)
    y = h - r["text"]["padding_bottom"] - r["text"]["wordmark_size"] // 2
    draw.text(((w - tw) / 2, y), mark, font=font, fill=ink)


# -- text pin ----------------------------------------------------------------

def draw_tracked(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, font, fill,
                 tracking: int) -> None:
    """Draw text with extra letter-spacing (Pillow has no tracking option)."""
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += text_width(font, ch) + tracking


def tracked_width(font, text: str, tracking: int) -> int:
    return sum(text_width(font, ch) for ch in text) + tracking * max(len(text) - 1, 0)


def text_safe_area(cfg: dict) -> tuple[int, int, int]:
    """(safe_width, safe_height, prompt_top) for the prompt block."""
    r = cfg["render"]
    t = r["text"]
    w, h = r["width"], r["height"]
    label_block = int(t["label_size"] * 1.9)
    wordmark_block = int(t["wordmark_size"] * 2)
    side = int(w * 0.07)
    top = t["padding_top"] + label_block
    safe_h = h - top - t["padding_bottom"] - wordmark_block
    return w - 2 * side, safe_h, top


def layout_text_pin(prompt: PromptText, cfg: dict) -> Fit:
    """The auto-fit result for a prompt (raises FitError when it cannot meet
    the cap-height floor). Exposed so tests can assert on it."""
    r = cfg["render"]
    t = r["text"]
    safe_w, safe_h, _ = text_safe_area(cfg)
    return fit_text(prompt.text, r["fonts"]["prompt"], safe_w, safe_h,
                    t["max_point_size"], t["min_cap_height"], t["step"],
                    t["max_lines"], t["leading"])


def render_text_pin(prompt: PromptText, label: str, cfg: dict) -> bytes:
    r = cfg["render"]
    t = r["text"]
    w, h = r["width"], r["height"]
    bg = hex_rgb(t["palette"].get(prompt.category, t["palette"]["default"]))
    ink = hex_rgb(t["ink"])
    label_ink = hex_rgb(t["label_ink"])
    im = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(im)

    fit = layout_text_pin(prompt, cfg)
    safe_w, safe_h, prompt_top = text_safe_area(cfg)

    label_font = load_font(r["fonts"]["label"], t["label_size"])
    tracking = int(t.get("label_tracking", 0))
    lw = tracked_width(label_font, label, tracking)
    draw_tracked(draw, (w - lw) / 2, t["padding_top"], label, label_font, label_ink, tracking)

    # Centre the prompt block in its safe area.
    y = prompt_top + (safe_h - fit.height) / 2
    for line in fit.lines:
        lw = text_width(fit.font, line)
        draw.text(((w - lw) / 2, y), line, font=fit.font, fill=ink)
        y += fit.line_height
    assert y <= prompt_top + safe_h + 1, "text overflowed the safe area"

    _draw_wordmark(draw, cfg, w, h, label_ink, light=False)
    return _png_bytes(im, r["max_bytes"])


# -- photo pin ---------------------------------------------------------------

def detect_focus(path: Path) -> tuple[float, float] | None:
    """Face-weighted focus (fractions) using the same Haar cascade the ingest
    quality gate uses; None when nothing is detected or cv2 is unavailable."""
    try:
        import cv2  # noqa: WPS433
        import numpy as np  # noqa: F401
    except ImportError:
        return None
    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return None
    height, width = gray.shape
    scale = min(1.0, 1280 / max(height, width))
    if scale < 1.0:
        gray = cv2.resize(gray, (int(width * scale), int(height * scale)))
    min_face = max(24, int(min(gray.shape) * 0.08))
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6,
                                     minSize=(min_face, min_face))
    if len(faces) == 0:
        return None
    xs = [x + fw / 2 for x, y, fw, fh in faces]
    ys = [y + fh / 2 for x, y, fw, fh in faces]
    gh, gw = gray.shape
    return (sum(xs) / len(xs) / gw, sum(ys) / len(ys) / gh)


def smart_crop(im: Image.Image, target: tuple[int, int], focus: tuple[float, float]) -> Image.Image:
    tw, th = target
    w, h = im.size
    target_ratio = tw / th
    if w / h > target_ratio:
        cw, ch = int(h * target_ratio), h
    else:
        cw, ch = w, int(w / target_ratio)
    fx, fy = focus
    left = min(max(int(fx * w - cw / 2), 0), w - cw)
    top = min(max(int(fy * h - ch / 2), 0), h - ch)
    return im.crop((left, top, left + cw, top + ch)).resize((tw, th), Image.LANCZOS)


def crop_photo(pose: Pose, cfg: dict, gate) -> Image.Image:
    """Rights gate, then the smart 2:3 crop. Ungraded."""
    gate.check(pose)  # the rights gate runs before any pixel is read
    r = cfg["render"]
    p = r["photo"]
    w, h = r["width"], r["height"]
    override = (p.get("crop_overrides") or {}).get(pose.id)
    if override:
        focus = (float(override.get("focus_x", 0.5)), float(override.get("focus_y", 0.5)))
    else:
        focus = detect_focus(pose.detail_path) or (0.5, 0.5)
    with Image.open(pose.detail_path) as src:
        return smart_crop(src.convert("RGB"), (w, h), focus)  # EXIF is not carried; see module doc


def render_photo_pin(pose: Pose, cfg: dict, gate, grade_cfg: dict | None = None) -> bytes:
    """JPEG bytes (photo pins are JPEG; see FORMAT). The colour grade is
    applied to every photo pin, both cohorts, before the scrim."""
    r = cfg["render"]
    p = r["photo"]
    w, h = r["width"], r["height"]
    im = crop_photo(pose, cfg, gate)
    if grade_cfg is not None:
        im = apply_grade(im, grade_cfg)

    # Bottom-third gradient scrim.
    scrim_h = int(h * p["scrim_height_ratio"])
    grad = Image.new("L", (1, scrim_h))
    for i in range(scrim_h):
        grad.putpixel((0, i), int(200 * (i / scrim_h) ** 1.4))
    grad = grad.resize((w, scrim_h))
    black = Image.new("RGB", (w, scrim_h), (18, 14, 12))
    im.paste(black, (0, h - scrim_h), grad.filter(ImageFilter.GaussianBlur(2)))

    draw = ImageDraw.Draw(im)
    margin = 70
    safe_w = w - 2 * margin
    name_font = load_font(r["fonts"]["label"], p["name_size"])
    name_lines = wrap(name_font, pose.name, safe_w) or [pose.name]
    prompt_font = load_font(r["fonts"]["prompt"], p["prompt_size"])
    prompt_lines = wrap(prompt_font, pose.primary_prompt, safe_w) or []
    prompt_lines = prompt_lines[:3]
    if len(prompt_lines) == 3 and len(wrap(prompt_font, pose.primary_prompt, safe_w)) > 3:
        prompt_lines[-1] = prompt_lines[-1].rstrip(",.") + "…"

    name_lh, prompt_lh = int(p["name_size"] * 1.15), int(p["prompt_size"] * 1.35)
    block = name_lh * len(name_lines) + 16 + prompt_lh * len(prompt_lines)
    y = h - r["text"]["padding_bottom"] - r["text"]["wordmark_size"] * 2 - block
    for line in name_lines:
        draw.text((margin, y), line, font=name_font, fill=(255, 250, 245))
        y += name_lh
    y += 16
    for line in prompt_lines:
        draw.text((margin, y), line, font=prompt_font, fill=(236, 226, 216))
        y += prompt_lh
    _draw_wordmark(draw, cfg, w, h, (222, 212, 202), light=True)
    return _jpeg_bytes(im, r["max_bytes"])


# -- contact sheet -----------------------------------------------------------

def contact_sheet(groups: dict[str, list[tuple[str, bytes]]], out: Path,
                  thumb_w: int = 500) -> Path:
    """Rows per cohort, one thumbnail per pin, labelled. `thumb_w=236`
    reproduces Pinterest's desktop feed width for a legibility check."""
    thumb_h = int(thumb_w * 1.5)
    pad, label_h = 24, 40
    cols = max((len(v) for v in groups.values()), default=1)
    rows = len(groups)
    sheet = Image.new("RGB", (pad + cols * (thumb_w + pad), pad + rows * (thumb_h + label_h + pad)),
                      (245, 242, 238))
    draw = ImageDraw.Draw(sheet)
    font = load_font([], 22)
    y = pad
    for cohort, pins in groups.items():
        draw.text((pad, y), f"cohort: {cohort}  ({len(pins)} pins)", font=font, fill=(40, 36, 32))
        x = pad
        for pin_id, data in pins:
            im = Image.open(io.BytesIO(data)).convert("RGB").resize((thumb_w, thumb_h), Image.LANCZOS)
            sheet.paste(im, (x, y + label_h))
            draw.text((x, y + label_h + thumb_h + 2), pin_id[:28], font=load_font([], 14),
                      fill=(90, 84, 78))
            x += thumb_w + pad
        y += thumb_h + label_h + pad
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "PNG", optimize=True)
    return out


def grade_strip(pairs: list[tuple[str, Image.Image, Image.Image]], out: Path,
                thumb_w: int = 300) -> Path:
    """Before/after strip: each photo pin's crop ungraded (top) and graded
    (bottom), no scrim, so the grade itself is reviewable."""
    thumb_h = int(thumb_w * 1.5)
    pad, label_h = 20, 34
    n = max(len(pairs), 1)
    sheet = Image.new("RGB", (pad + n * (thumb_w + pad), pad + 2 * (thumb_h + label_h + pad)),
                      (245, 242, 238))
    draw = ImageDraw.Draw(sheet)
    font = load_font([], 20)
    small = load_font([], 14)
    for row, title in enumerate(("before (ungraded crop)", "after (graded, both cohorts)")):
        y = pad + row * (thumb_h + label_h + pad)
        draw.text((pad, y), title, font=font, fill=(40, 36, 32))
        for i, (pin_id, before, after) in enumerate(pairs):
            im = (before if row == 0 else after).resize((thumb_w, thumb_h), Image.LANCZOS)
            x = pad + i * (thumb_w + pad)
            sheet.paste(im, (x, y + label_h))
            if row == 0:
                draw.text((x + 4, y + label_h + 4), pin_id[:24], font=small, fill=(255, 255, 255))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "PNG", optimize=True)
    return out
