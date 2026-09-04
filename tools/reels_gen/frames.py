"""Frame-by-frame Pillow composition for a single reel.

ffmpeg on this machine has no drawtext filter, so every pixel of text is
drawn here with Pillow; tools/reels_gen/video.py just pipes the finished
RGB frames into ffmpeg to encode. Colour and type choices reuse
tools/pinterest/render.py and config/pinterest_cohorts.yaml where they
apply directly (hex_rgb, load_font, text_width, the category palette, the
ink colour); the amber accent and the cream on-photo text colour are new
here (the pin renderer never draws text over a photo without a plain
background behind it), chosen to sit inside the same warm-neutral family.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pinterest.render import hex_rgb
from pinterest.text_fit import Fit, text_width, wrap

WIDTH, HEIGHT = 1080, 1920
DURATION = 9.0
IMAGE_END = 7.4                 # image portion; end card takes over after this
LABEL_FADE_END = 0.6
PROMPT_FADE_IN = (0.8, 1.2)
PROMPT_FADE_OUT = (7.0, 7.4)
ENDCARD_FADE = 0.3
KENBURNS_RANGE = (1.00, 1.08)   # slow push, ease in-out

SCRIM_HEIGHT_RATIO = 0.44       # bottom band; deeper than a third so the prompt can run large
SCRIM_ALPHA_MAX = 190           # "slightly" -- pins' photo-pin scrim goes to 200

PROMPT_BOTTOM_MARGIN = 140
PROMPT_TOP_GAP = 40
PROMPT_SIDE_MARGIN_RATIO = 0.07

LABEL_MARGIN = (56, 64)
PILL_MARGIN_RIGHT = 40
PILL_TOP = 60
CREDIT_BOTTOM = 90

AMBER = hex_rgb("#C17A2E")             # warm amber accent (label / branding)
AMBER_BRIGHT = hex_rgb("#E8A33D")      # the app accent, for text on dark
CREAM = (255, 250, 245)                # on-photo text, over the darkened scrim
PILL_BG = (245, 240, 231)
PILL_INK = hex_rgb("#2B2622")
CREDIT_COLOR = (238, 230, 220)


def ease_in_out(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return 3 * p * p - 2 * p * p * p


def cover_fit(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize+centre-crop `im` to exactly fill `size` (the classic CSS
    background-size: cover / object-fit: cover crop)."""
    w, h = size
    sw, sh = im.size
    scale = max(w / sw, h / sh)
    nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))
    resized = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return resized.crop((left, top, left + w, top + h))


def scrim_overlay() -> Image.Image:
    """A bottom-third black gradient, RGBA, transparent at its own top edge
    and SCRIM_ALPHA_MAX at the frame's bottom -- constant across a video's
    frames, so it is built once and alpha-composited onto each one."""
    band_h = int(HEIGHT * SCRIM_HEIGHT_RATIO)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    grad = Image.new("L", (1, band_h))
    for i in range(band_h):
        grad.putpixel((0, i), int(SCRIM_ALPHA_MAX * (i / band_h) ** 1.4))
    grad = grad.resize((WIDTH, band_h))
    black = Image.new("RGBA", (WIDTH, band_h), (14, 11, 9, 0))
    black.putalpha(grad)
    overlay.paste(black, (0, HEIGHT - band_h))
    return overlay


def prompt_safe_area() -> tuple[int, int]:
    side = int(WIDTH * PROMPT_SIDE_MARGIN_RATIO)
    band_h = int(HEIGHT * SCRIM_HEIGHT_RATIO)
    safe_h = band_h - PROMPT_BOTTOM_MARGIN - PROMPT_TOP_GAP
    return WIDTH - 2 * side, safe_h


@dataclass
class KenBurns:
    """Precomputed per-video state so each frame only pays for a small
    resize+crop rather than re-decoding the source image."""
    base: Image.Image  # cover-fit at scale 1.00 (t=0 framing)

    @classmethod
    def load(cls, path: Path) -> "KenBurns":
        with Image.open(path) as src:
            return cls(base=cover_fit(src.convert("RGB"), (WIDTH, HEIGHT)))

    def frame(self, t: float) -> Image.Image:
        p = ease_in_out(min(max(t, 0.0), IMAGE_END) / IMAGE_END)
        lo, hi = KENBURNS_RANGE
        scale = lo + (hi - lo) * p
        nw, nh = round(WIDTH * scale), round(HEIGHT * scale)
        up = self.base.resize((nw, nh), Image.LANCZOS)
        left, top = (nw - WIDTH) // 2, (nh - HEIGHT) // 2
        return up.crop((left, top, left + WIDTH, top + HEIGHT))


def draw_tracked(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font,
                 fill, tracking: int) -> None:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += text_width(font, ch) + tracking


def _prompt_alpha(t: float) -> int:
    lo_in, hi_in = PROMPT_FADE_IN
    lo_out, hi_out = PROMPT_FADE_OUT
    if t < lo_in or t >= hi_out:
        return 0
    if t < hi_in:
        return int(255 * (t - lo_in) / (hi_in - lo_in))
    if t < lo_out:
        return 255
    return int(255 * (1 - (t - lo_out) / (hi_out - lo_out)))


def _draw_pill(draw: ImageDraw.ImageDraw, text: str, font) -> None:
    pad_x, pad_y = 20, 12
    left, top, right, bottom = font.getbbox(text)
    tw, th = right - left, bottom - top
    x2 = WIDTH - PILL_MARGIN_RIGHT
    x1 = x2 - tw - 2 * pad_x
    y1 = PILL_TOP
    y2 = y1 + th + 2 * pad_y
    draw.rounded_rectangle((x1, y1, x2, y2), radius=(y2 - y1) / 2,
                           fill=(*PILL_BG, 235))
    draw.text((x1 + pad_x, y1 + pad_y - top), text, font=font, fill=(*PILL_INK, 255))


def _draw_credit(draw: ImageDraw.ImageDraw, text: str, font) -> None:
    tw = text_width(font, text)
    x = WIDTH - PILL_MARGIN_RIGHT - tw
    draw.text((x, HEIGHT - CREDIT_BOTTOM), text, font=font, fill=(*CREDIT_COLOR, 235))


@dataclass
class ImageFrameAssets:
    kenburns: KenBurns
    scrim: Image.Image
    label_text: str
    label_font: ImageFont.FreeTypeFont
    label_tracking: int
    prompt_fit: Fit
    disclosure_text: str | None
    credit_text: str | None
    pill_font: ImageFont.FreeTypeFont


def render_image_frame(assets: ImageFrameAssets, t: float) -> Image.Image:
    frame = assets.kenburns.frame(t).convert("RGBA")
    frame = Image.alpha_composite(frame, assets.scrim)

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    label_alpha = int(255 * min(1.0, max(0.0, t / LABEL_FADE_END)))
    if label_alpha > 0:
        lx, ly = LABEL_MARGIN
        lw = text_width(assets.label_font, assets.label_text) + assets.label_tracking * max(0, len(assets.label_text) - 1)
        lh = assets.label_font.size
        draw.rounded_rectangle([lx - 22, ly - 14, lx + lw + 22, ly + lh + 16], radius=20,
                               fill=(*PILL_INK, int(label_alpha * 0.82)))
        draw_tracked(draw, LABEL_MARGIN, assets.label_text, assets.label_font,
                    (*AMBER_BRIGHT, label_alpha), assets.label_tracking)

    fade = _prompt_alpha(t)
    if fade > 0:
        fit = assets.prompt_fit
        y = HEIGHT - PROMPT_BOTTOM_MARGIN - fit.height
        for line in fit.lines:
            lw = text_width(fit.font, line)
            draw.text(((WIDTH - lw) / 2, y), line, font=fit.font, fill=(*CREAM, fade))
            y += fit.line_height

    if t < IMAGE_END:
        if assets.disclosure_text:
            _draw_pill(draw, assets.disclosure_text, assets.pill_font)
        elif assets.credit_text:
            _draw_credit(draw, assets.credit_text, assets.pill_font)

    return Image.alpha_composite(frame, overlay).convert("RGB")


@dataclass
class EndCardAssets:
    bg_rgb: tuple[int, int, int]
    ink_rgb: tuple[int, int, int]
    wordmark_font: ImageFont.FreeTypeFont
    tagline_font: ImageFont.FreeTypeFont
    small_font: ImageFont.FreeTypeFont
    wordmark: str = "Prompted"
    tagline: str = "The posing app that is only a posing app."
    cta: str = "Free on the App Store"


def _draw_centered(draw: ImageDraw.ImageDraw, y: float, text: str, font, fill,
                   max_width: int) -> float:
    lines = wrap(font, text, max_width) or [text]
    line_h = int(font.size * 1.25)
    for line in lines:
        lw = text_width(font, line)
        draw.text(((WIDTH - lw) / 2, y), line, font=font, fill=fill)
        y += line_h
    return y


def render_end_card(assets: EndCardAssets, t: float) -> Image.Image:
    im = Image.new("RGBA", (WIDTH, HEIGHT), (*assets.bg_rgb, 255))
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    alpha = int(255 * min(1.0, max(0.0, (t - IMAGE_END) / ENDCARD_FADE)))
    ink = (*assets.ink_rgb, alpha)

    max_width = int(WIDTH * 0.82)
    y = HEIGHT * 0.40
    y = _draw_centered(draw, y, assets.wordmark, assets.wordmark_font, (*AMBER_BRIGHT, alpha), max_width) + 20
    y = _draw_centered(draw, y, assets.tagline, assets.tagline_font, ink, max_width) + 16
    _draw_centered(draw, y, assets.cta, assets.small_font, ink, max_width)

    return Image.alpha_composite(im, layer).convert("RGB")


def contact_sheet(entries: list[tuple[str, Image.Image]], out: Path,
                  thumb_w: int = 270) -> Path:
    thumb_h = round(thumb_w * HEIGHT / WIDTH)
    pad, label_h = 20, 28
    n = max(len(entries), 1)
    sheet = Image.new("RGB", (pad + n * (thumb_w + pad), pad + thumb_h + label_h + pad),
                      (245, 242, 238))
    draw = ImageDraw.Draw(sheet)
    from pinterest.text_fit import load_font
    font = load_font([], 16)
    x = pad
    for label, im in entries:
        thumb = im.resize((thumb_w, thumb_h), Image.LANCZOS)
        sheet.paste(thumb, (x, pad))
        draw.text((x, pad + thumb_h + 4), label[:28], font=font, fill=(60, 54, 48))
        x += thumb_w + pad
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "PNG", optimize=True)
    return out
