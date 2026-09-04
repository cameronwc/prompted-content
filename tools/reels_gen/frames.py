"""Frame-by-frame Pillow composition for a single reel.

ffmpeg on this machine has no drawtext filter, so every pixel of text is
drawn here with Pillow; tools/reels_gen/video.py just pipes the finished
RGB frames into ffmpeg to encode. Colour and type choices reuse
tools/pinterest/render.py and config/pinterest_cohorts.yaml where they
apply directly (hex_rgb, load_font, text_width, the category palette, the
ink colour); the amber accent and the cream on-photo text colour are new
here (the pin renderer never draws text over a photo without a plain
background behind it), chosen to sit inside the same warm-neutral family.

Timeline (see build_timeline): brand label fade-in, then a SETUP STEPS
segment (up to MAX_STEPS instructions shown one at a time under a tracked
"SET IT UP" header), then THE PROMPT (the verbal prompt under a "SAY THIS
· <TONE>" header), then -- when a screenshot exists for the pose -- an APP
SCREEN segment (a phone-framed screenshot of the pose's detail view on a
paper background, under "IN THE APP"), then the end card. A pose with fewer
than MAX_STEPS instructions gets a proportionally shorter steps segment --
and therefore a shorter total video -- rather than padding; a pose with no
screenshot simply skips the app-screen segment (build_timeline's
has_appshot=False collapses appshot_start==appshot_end, so the timeline
falls straight from the prompt to the end card, exactly as it did before
this segment existed). Every pose in the live catalog carries >=3
instructions, so in practice every rendered reel today runs the full
NOMINAL_DURATION (or NOMINAL_DURATION_NO_APPSHOT for a pose with no
screenshot on disk).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from pinterest.render import hex_rgb
from pinterest.text_fit import Fit, text_width, wrap

WIDTH, HEIGHT = 1080, 1920

LABEL_FADE_END = 0.6

# -- setup steps segment --
STEPS_START = 0.8
MAX_STEPS = 3
STEPS_HEADER_TEXT = "SET IT UP"
_STEPS_NOMINAL_END = 9.6        # where the steps segment ends when n_steps == MAX_STEPS
STEP_SLOT_DURATION = (_STEPS_NOMINAL_END - STEPS_START) / MAX_STEPS  # ~2.93s/step
STEP_FADE = 0.3

# -- the prompt segment --
PROMPT_DURATION = 3.6           # 9.6-13.2 in the full (3-step) timeline
PROMPT_FADE_IN_DUR = 0.4
PROMPT_FADE_OUT_DUR = 0.4

# -- app screen segment (skipped when the pose has no screenshot) --
APP_SCREEN_DURATION = 4.0       # 13.2-17.2 in the full (3-step, has-appshot) timeline
APP_SCREEN_FADE_IN = 0.35
APP_SCREEN_FADE_OUT = 0.3
APP_SCREEN_HEADER_TEXT = "IN THE APP"
APP_SCREEN_LINE_TEXT = "250+ poses · four tones · filtered to your light"
APP_SCREEN_HEIGHT_RATIO = 0.78  # screenshot height as a fraction of the frame
APP_SCREEN_ZOOM_RANGE = (1.00, 1.03)
APP_SCREEN_BEZEL_WIDTH = 12
APP_SCREEN_BEZEL_COLOR = (9, 8, 7)
APP_SCREEN_CORNER_RADIUS = 64   # on the screenshot itself, at its rendered size
APP_SCREEN_SHADOW_BLUR = 40
APP_SCREEN_SHADOW_ALPHA = 110
APP_SCREEN_SHADOW_OFFSET = (0, 26)
APP_SCREEN_TOP_MARGIN = 96
APP_SCREEN_HEADER_GAP = 40
APP_SCREEN_CAPTION_GAP = 40
APP_SCREEN_HEADER_FONT_SIZE = 32  # matches the steps/prompt section headers

# -- end card --
ENDCARD_DURATION = 1.8          # last 1.8s of any timeline
ENDCARD_FADE = 0.3
ICON_SIZE = 260
ICON_RADIUS = 58
ICON_CENTER_Y_RATIO = 0.38
ICON_SHADOW_BLUR = 28
ICON_SHADOW_ALPHA = 90
ICON_SHADOW_OFFSET = (0, 14)
ICON_WORDMARK_GAP = 36
BADGE_W, BADGE_H = 520, 150
BADGE_BG = (10, 9, 8)
MUTED_INK = hex_rgb("#8A8074")

NOMINAL_DURATION = (STEPS_START + MAX_STEPS * STEP_SLOT_DURATION + PROMPT_DURATION
                    + APP_SCREEN_DURATION + ENDCARD_DURATION)              # 19.0
NOMINAL_DURATION_NO_APPSHOT = (STEPS_START + MAX_STEPS * STEP_SLOT_DURATION
                               + PROMPT_DURATION + ENDCARD_DURATION)       # 15.0

KENBURNS_RANGE = (1.00, 1.08)   # slow push, ease in-out

SCRIM_HEIGHT_RATIO = 0.44       # bottom band; deeper than a third so the prompt can run large
PANEL_ALPHA = 200               # near-opaque ink panel behind the text band
PANEL_FEATHER = 140              # px of soft edge at the top of the panel

PROMPT_BOTTOM_MARGIN = 140
PROMPT_TOP_GAP = 40
PROMPT_SIDE_MARGIN_RATIO = 0.07

# Tracked section header ("SET IT UP" / "SAY THIS · <TONE>") above the
# fitted text block; a fixed pixel reserve (not derived from font metrics,
# matching the rest of this module's fixed-margin style) is subtracted from
# the text safe area for it.
HEADER_RESERVE = 64

# Setup-step number column, left of the step text.
STEP_NUMBER_COL_WIDTH = 150
STEP_NUMBER_GAP = 28
STEP_NUMBER_FONT_SIZE = 128

LABEL_MARGIN = (56, 64)
PILL_MARGIN_RIGHT = 40
PILL_TOP = 60
PILL_HEIGHT = 66                # both top pills share this height and top edge
CREDIT_BOTTOM = 90

AMBER = hex_rgb("#C17A2E")             # warm amber accent (label / branding)
AMBER_BRIGHT = hex_rgb("#E8A33D")      # the app accent, for text on dark
CREAM = (255, 250, 245)                # on-photo text, over the darkened scrim
PILL_BG = (245, 240, 231)
PILL_INK = hex_rgb("#2B2622")
CREDIT_COLOR = (238, 230, 220)


@dataclass
class Timeline:
    """Per-video timing marks. Everything downstream of the steps segment
    shifts to follow it, so a pose with fewer than MAX_STEPS instructions
    renders a shorter steps segment *and* a shorter total video, not a
    padded one. Likewise, a pose with no app screenshot collapses
    appshot_start == appshot_end and the video runs straight from the
    prompt into the end card."""
    n_steps: int
    step_slots: list[tuple[float, float]]  # one (start, end) per step, contiguous
    steps_start: float
    steps_end: float
    prompt_start: float
    prompt_end: float
    image_end: float       # end of the Ken Burns image portion (photo + overlay text)
    has_appshot: bool
    appshot_start: float
    appshot_end: float     # == appshot_start when has_appshot is False
    endcard_start: float
    duration: float


def build_timeline(n_steps: int, has_appshot: bool = False) -> Timeline:
    n = max(0, min(MAX_STEPS, n_steps))
    step_slots = [(STEPS_START + i * STEP_SLOT_DURATION, STEPS_START + (i + 1) * STEP_SLOT_DURATION)
                 for i in range(n)]
    steps_end = STEPS_START + n * STEP_SLOT_DURATION
    prompt_start = steps_end
    prompt_end = prompt_start + PROMPT_DURATION
    image_end = prompt_end
    appshot_start = image_end
    appshot_end = appshot_start + (APP_SCREEN_DURATION if has_appshot else 0.0)
    endcard_start = appshot_end
    duration = endcard_start + ENDCARD_DURATION
    return Timeline(n_steps=n, step_slots=step_slots, steps_start=STEPS_START, steps_end=steps_end,
                    prompt_start=prompt_start, prompt_end=prompt_end, image_end=image_end,
                    has_appshot=has_appshot, appshot_start=appshot_start, appshot_end=appshot_end,
                    endcard_start=endcard_start, duration=duration)


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
    """A near-opaque ink panel over the bottom band so the text reads on any
    photograph, with a short feathered top edge (PANEL_FEATHER px) so it
    does not cut the image with a hard line. Constant across a video's
    frames, so it is built once and alpha-composited onto each one."""
    band_h = int(HEIGHT * SCRIM_HEIGHT_RATIO)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    grad = Image.new("L", (1, band_h))
    for i in range(band_h):
        a = PANEL_ALPHA if i >= PANEL_FEATHER else int(PANEL_ALPHA * (i / PANEL_FEATHER) ** 1.2)
        grad.putpixel((0, i), a)
    grad = grad.resize((WIDTH, band_h))
    black = Image.new("RGBA", (WIDTH, band_h), (18, 15, 12, 0))
    black.putalpha(grad)
    overlay.paste(black, (0, HEIGHT - band_h))
    return overlay


def prompt_safe_area() -> tuple[int, int]:
    """Safe (width, height) for the fitted text block below the tracked
    section header, in the bottom scrim band. Shared by the prompt and (via
    steps_text_safe_area) the setup-step text."""
    side = int(WIDTH * PROMPT_SIDE_MARGIN_RATIO)
    band_h = int(HEIGHT * SCRIM_HEIGHT_RATIO)
    safe_h = band_h - PROMPT_BOTTOM_MARGIN - PROMPT_TOP_GAP - HEADER_RESERVE
    return WIDTH - 2 * side, safe_h


def steps_text_safe_area() -> tuple[int, int]:
    """Safe (width, height) for a setup-step's text, to the right of the
    step-number column; same vertical safe area as the prompt."""
    w, h = prompt_safe_area()
    return w - STEP_NUMBER_COL_WIDTH - STEP_NUMBER_GAP, h


def _text_top() -> int:
    """Top of the text block in the band: fixed, directly under the header,
    so steps of different lengths and the prompt all start at the same y."""
    return _header_xy()[1] + HEADER_RESERVE


def _header_xy() -> tuple[int, int]:
    side = int(WIDTH * PROMPT_SIDE_MARGIN_RATIO)
    band_h = int(HEIGHT * SCRIM_HEIGHT_RATIO)
    band_top = HEIGHT - band_h
    return side, band_top + PROMPT_TOP_GAP


@dataclass
class KenBurns:
    """Precomputed per-video state. The base is oversampled at the maximum
    zoom so every frame is a sub-pixel crop (Image.transform EXTENT with
    float bounds), never an integer resize; that is what keeps the push
    smooth instead of stepping a pixel at a time."""
    base: Image.Image  # cover-fit at KENBURNS_RANGE[1] * (WIDTH, HEIGHT)
    image_end: float   # Ken Burns spans the whole image portion, 0..image_end

    @classmethod
    def load(cls, path: Path, image_end: float) -> "KenBurns":
        hi = KENBURNS_RANGE[1]
        big = (int(round(WIDTH * hi)), int(round(HEIGHT * hi)))
        with Image.open(path) as src:
            return cls(base=cover_fit(src.convert("RGB"), big), image_end=image_end)

    def frame(self, t: float) -> Image.Image:
        p = ease_in_out(min(max(t, 0.0), self.image_end) / self.image_end)
        lo, hi = KENBURNS_RANGE
        scale = lo + (hi - lo) * p            # 1.00 -> 1.08 of the output frame
        bw, bh = self.base.size
        # Window in base pixels that maps onto the output at this zoom.
        # At scale=lo the window is the whole base scaled to output (1.08x
        # oversample); at scale=hi it is the central 1/1.08 of it.
        ww, wh = bw / (scale / lo), bh / (scale / lo)
        left, top = (bw - ww) / 2.0, (bh - wh) / 2.0
        return self.base.transform((WIDTH, HEIGHT), Image.EXTENT,
                                   (left, top, left + ww, top + wh), Image.BICUBIC)


def draw_tracked(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font,
                 fill, tracking: int) -> None:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += text_width(font, ch) + tracking


def tracked_text_width(font, text: str, tracking: int) -> int:
    return text_width(font, text) + tracking * max(0, len(text) - 1)


def draw_tracked_centered(draw: ImageDraw.ImageDraw, y: float, text: str, font, fill,
                          tracking: int) -> None:
    w = tracked_text_width(font, text, tracking)
    draw_tracked(draw, ((WIDTH - w) / 2, y), text, font, fill, tracking)


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def _drop_shadow(canvas_size: tuple[int, int], rect_size: tuple[int, int], pad: int, radius: int,
                 blur: int, alpha: int, offset: tuple[int, int]) -> Image.Image:
    """A blurred rounded-rectangle shadow, sized to `canvas_size` (which must
    already include room for the blur to bleed into), centred behind a
    rect_size element sitting at `pad` from the canvas edge plus `offset`."""
    shadow = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    d = ImageDraw.Draw(shadow)
    x, y = pad + offset[0], pad + offset[1]
    w, h = rect_size
    d.rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=(0, 0, 0, alpha))
    return shadow.filter(ImageFilter.GaussianBlur(blur))


def _segment_alpha(t: float, start: float, end: float, fade_in: float, fade_out: float) -> int:
    """0..255: 0 outside [start, end), ramping up over `fade_in` at the
    start, full in the middle, ramping down over `fade_out` at the end.
    Used for both the setup-step slots (fade_in=fade_out=STEP_FADE) and the
    prompt (fade_in=PROMPT_FADE_IN_DUR, fade_out=PROMPT_FADE_OUT_DUR) -- and
    for each segment's tracked header, which shares its section's alpha."""
    if t < start or t >= end:
        return 0
    if t < start + fade_in:
        return int(255 * (t - start) / fade_in)
    if t < end - fade_out:
        return 255
    return int(255 * (1 - (t - (end - fade_out)) / fade_out))


def _pill_rect(draw: ImageDraw.ImageDraw, x1: float, x2: float, fill) -> None:
    draw.rounded_rectangle((x1, PILL_TOP, x2, PILL_TOP + PILL_HEIGHT),
                           radius=PILL_HEIGHT / 2, fill=fill)


def _pill_text_y(font, text: str) -> float:
    left, top, right, bottom = font.getbbox(text)
    return PILL_TOP + (PILL_HEIGHT - (bottom - top)) / 2 - top


def _draw_pill(draw: ImageDraw.ImageDraw, text: str, font) -> None:
    """Disclosure pill, top-right, on the shared pill baseline."""
    pad_x = 22
    tw = text_width(font, text)
    x2 = WIDTH - PILL_MARGIN_RIGHT
    x1 = x2 - tw - 2 * pad_x
    _pill_rect(draw, x1, x2, (*PILL_BG, 235))
    draw.text((x1 + pad_x, _pill_text_y(font, text)), text, font=font, fill=(*PILL_INK, 255))


def _draw_credit(draw: ImageDraw.ImageDraw, text: str, font) -> None:
    tw = text_width(font, text)
    x = WIDTH - PILL_MARGIN_RIGHT - tw
    draw.text((x, HEIGHT - CREDIT_BOTTOM), text, font=font, fill=(*CREDIT_COLOR, 235))


@dataclass
class ImageFrameAssets:
    kenburns: KenBurns
    scrim: Image.Image
    timeline: Timeline
    label_text: str
    label_font: ImageFont.FreeTypeFont
    label_tracking: int
    header_font: ImageFont.FreeTypeFont
    header_tracking: int
    steps: list[str]              # verbatim instruction text, one per step_slot
    step_fits: list[Fit]          # fitted (SANS) text, aligned with `steps`
    step_number_font: ImageFont.FreeTypeFont
    steps_header_text: str
    prompt_fit: Fit
    prompt_header_text: str
    disclosure_text: str | None
    credit_text: str | None
    pill_font: ImageFont.FreeTypeFont


def _draw_step(draw: ImageDraw.ImageDraw, index: int, fit: Fit, assets: ImageFrameAssets,
              alpha: int) -> None:
    side = int(WIDTH * PROMPT_SIDE_MARGIN_RATIO)
    y = _text_top()                       # anchored: never moves between steps
    digit = str(index + 1)
    draw.text((side, y), digit, font=assets.step_number_font, fill=(*AMBER_BRIGHT, alpha))
    text_x = side + STEP_NUMBER_COL_WIDTH + STEP_NUMBER_GAP
    ty = y
    for line in fit.lines:
        draw.text((text_x, ty), line, font=fit.font, fill=(*CREAM, alpha))
        ty += fit.line_height


def render_image_frame(assets: ImageFrameAssets, t: float) -> Image.Image:
    frame = assets.kenburns.frame(t).convert("RGBA")
    frame = Image.alpha_composite(frame, assets.scrim)

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    tl = assets.timeline

    label_alpha = int(255 * min(1.0, max(0.0, t / LABEL_FADE_END)))
    if label_alpha > 0:
        pad_x = 22
        lw = tracked_text_width(assets.label_font, assets.label_text, assets.label_tracking)
        x1 = PILL_MARGIN_RIGHT
        _pill_rect(draw, x1, x1 + lw + 2 * pad_x, (*PILL_INK, int(label_alpha * 0.82)))
        ly = _pill_text_y(assets.label_font, assets.label_text)
        draw_tracked(draw, (x1 + pad_x, ly), assets.label_text, assets.label_font,
                    (*AMBER_BRIGHT, label_alpha), assets.label_tracking)

    if tl.n_steps and tl.steps_start <= t < tl.steps_end:
        header_alpha = _segment_alpha(t, tl.steps_start, tl.steps_end, STEP_FADE, STEP_FADE)
        if header_alpha > 0:
            draw_tracked(draw, _header_xy(), assets.steps_header_text, assets.header_font,
                        (*AMBER_BRIGHT, header_alpha), assets.header_tracking)
        for i, (start, end) in enumerate(tl.step_slots):
            step_alpha = _segment_alpha(t, start, end, STEP_FADE, STEP_FADE)
            if step_alpha > 0:
                _draw_step(draw, i, assets.step_fits[i], assets, step_alpha)

    prompt_alpha = _segment_alpha(t, tl.prompt_start, tl.prompt_end,
                                  PROMPT_FADE_IN_DUR, PROMPT_FADE_OUT_DUR)
    if prompt_alpha > 0:
        draw_tracked(draw, _header_xy(), assets.prompt_header_text, assets.header_font,
                    (*AMBER_BRIGHT, prompt_alpha), assets.header_tracking)
        fit = assets.prompt_fit
        y = _text_top()
        for line in fit.lines:
            lw = text_width(fit.font, line)
            draw.text(((WIDTH - lw) / 2, y), line, font=fit.font, fill=(*CREAM, prompt_alpha))
            y += fit.line_height

    if t < tl.image_end:
        if assets.disclosure_text:
            _draw_pill(draw, assets.disclosure_text, assets.pill_font)
        elif assets.credit_text:
            _draw_credit(draw, assets.credit_text, assets.pill_font)

    return Image.alpha_composite(frame, overlay).convert("RGB")


def build_phone_card(screenshot_path: Path, target_h: int = None) -> Image.Image:
    """A padded RGBA composite: near-black bezel, the screenshot inset with
    rounded corners, and a soft blurred drop shadow -- one static image,
    built once per video and simply rescaled per frame for the slow push
    (see AppScreenPhone). Padding is symmetric on all sides so the image's
    own centre is always the visual centre of the phone, shadow included."""
    target_h = target_h or round(HEIGHT * APP_SCREEN_HEIGHT_RATIO)
    with Image.open(screenshot_path) as src:
        src = src.convert("RGB")
    sw, sh = src.size
    scale = target_h / sh
    tw = round(sw * scale)
    shot = src.resize((tw, target_h), Image.LANCZOS)
    shot_rgba = shot.convert("RGBA")
    shot_rgba.putalpha(_rounded_mask((tw, target_h), APP_SCREEN_CORNER_RADIUS))

    bw, bh = tw + 2 * APP_SCREEN_BEZEL_WIDTH, target_h + 2 * APP_SCREEN_BEZEL_WIDTH
    bezel_radius = APP_SCREEN_CORNER_RADIUS + APP_SCREEN_BEZEL_WIDTH
    pad = APP_SCREEN_SHADOW_BLUR * 2
    canvas_size = (bw + 2 * pad, bh + 2 * pad)

    canvas = _drop_shadow(canvas_size, (bw, bh), pad, bezel_radius, APP_SCREEN_SHADOW_BLUR,
                          APP_SCREEN_SHADOW_ALPHA, APP_SCREEN_SHADOW_OFFSET)
    bezel = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    ImageDraw.Draw(bezel).rounded_rectangle((pad, pad, pad + bw, pad + bh), radius=bezel_radius,
                                            fill=(*APP_SCREEN_BEZEL_COLOR, 255))
    canvas = Image.alpha_composite(canvas, bezel)
    canvas.alpha_composite(shot_rgba, (pad + APP_SCREEN_BEZEL_WIDTH, pad + APP_SCREEN_BEZEL_WIDTH))
    return canvas


@dataclass
class AppScreenPhone:
    """The phone card pre-rendered once at the maximum zoom onto a
    transparent full-canvas layer; each frame is a sub-pixel EXTENT crop of
    that layer, so the 1.00->1.03 push glides instead of stepping."""
    layer: Image.Image        # WIDTH x HEIGHT RGBA, card at APP_SCREEN_ZOOM_RANGE[1]
    center: tuple[float, float]

    @classmethod
    def build(cls, card: Image.Image, center: tuple[float, float]) -> "AppScreenPhone":
        hi = APP_SCREEN_ZOOM_RANGE[1]
        w, h = card.size
        big = card.resize((int(round(w * hi)), int(round(h * hi))), Image.LANCZOS)
        layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        cx, cy = center
        layer.alpha_composite(big, (int(round(cx - big.width / 2)), int(round(cy - big.height / 2))))
        return cls(layer=layer, center=center)

    def frame(self, p: float) -> tuple[Image.Image, tuple[int, int]]:
        lo, hi = APP_SCREEN_ZOOM_RANGE
        scale = lo + (hi - lo) * ease_in_out(p)
        k = hi / scale                      # window is k times the output, centred on the card
        cx, cy = self.center
        left, top = cx - cx * k, cy - cy * k
        box = (left, top, left + WIDTH * k, top + HEIGHT * k)
        return self.layer.transform((WIDTH, HEIGHT), Image.EXTENT, box, Image.BICUBIC), (0, 0)


@dataclass
class AppScreenAssets:
    phone: AppScreenPhone
    bg_rgb: tuple[int, int, int]
    header_font: ImageFont.FreeTypeFont
    header_tracking: int
    header_text: str
    header_y: float
    line_font: ImageFont.FreeTypeFont
    line_text: str
    line_y: float
    ink_rgb: tuple[int, int, int]
    appshot_start: float
    appshot_end: float


def app_screen_phone_center(card_size: tuple[int, int]) -> tuple[float, float, float, float]:
    """(centre_x, centre_y, header_y, line_y) for a phone card of
    `card_size` (its full padded size, including the shadow's blur bleed --
    the padding is symmetric, so the card's own centre is the phone's
    visual centre too). Screenshot height is a fixed fraction of the frame,
    so a fixed top margin plus header/caption gaps lay out cleanly."""
    target_h = round(HEIGHT * APP_SCREEN_HEIGHT_RATIO)
    visible_h = target_h + 2 * APP_SCREEN_BEZEL_WIDTH
    header_line_h = int(round(APP_SCREEN_HEADER_FONT_SIZE * 1.3))
    phone_top = APP_SCREEN_TOP_MARGIN + header_line_h + APP_SCREEN_HEADER_GAP
    center_y = phone_top + visible_h / 2
    header_y = APP_SCREEN_TOP_MARGIN
    line_y = phone_top + visible_h + APP_SCREEN_CAPTION_GAP
    return WIDTH / 2, center_y, header_y, line_y


def render_app_screen(assets: AppScreenAssets, t: float) -> Image.Image:
    bg = Image.new("RGBA", (WIDTH, HEIGHT), (*assets.bg_rgb, 255))
    fade = _segment_alpha(t, assets.appshot_start, assets.appshot_end,
                          APP_SCREEN_FADE_IN, APP_SCREEN_FADE_OUT)
    if fade <= 0:
        return bg.convert("RGB")

    content = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(content)
    span = max(1e-6, assets.appshot_end - assets.appshot_start)
    p = min(1.0, max(0.0, (t - assets.appshot_start) / span))
    scaled, top_left = assets.phone.frame(p)
    content.alpha_composite(scaled, top_left)

    draw_tracked_centered(draw, assets.header_y, assets.header_text, assets.header_font,
                          (*AMBER_BRIGHT, 255), assets.header_tracking)
    lw = text_width(assets.line_font, assets.line_text)
    draw.text(((WIDTH - lw) / 2, assets.line_y), assets.line_text, font=assets.line_font,
             fill=(*assets.ink_rgb, 255))

    if fade < 255:
        r, g, b, a = content.split()
        a = a.point(lambda v: v * fade // 255)
        content = Image.merge("RGBA", (r, g, b, a))
    return Image.alpha_composite(bg, content).convert("RGB")


def build_app_icon_card(icon_path: Path, size: int = ICON_SIZE, radius: int = ICON_RADIUS) -> Image.Image:
    """Same shadow/rounded-corner treatment as build_phone_card, for the
    (much simpler) square app icon."""
    with Image.open(icon_path) as src:
        icon = src.convert("RGB").resize((size, size), Image.LANCZOS)
    icon_rgba = icon.convert("RGBA")
    icon_rgba.putalpha(_rounded_mask((size, size), radius))

    pad = ICON_SHADOW_BLUR * 2
    canvas_size = (size + 2 * pad, size + 2 * pad)
    canvas = _drop_shadow(canvas_size, (size, size), pad, radius, ICON_SHADOW_BLUR,
                          ICON_SHADOW_ALPHA, ICON_SHADOW_OFFSET)
    canvas.alpha_composite(icon_rgba, (pad, pad))
    return canvas


def _draw_app_store_badge(draw: ImageDraw.ImageDraw, center_xy: tuple[float, float], small_font,
                          large_font) -> None:
    """A simple black rounded (pill) App Store badge, Apple-glyph-free:
    "Download on the" over "App Store" in white, both centred."""
    cx, cy = center_xy
    x1, y1 = cx - BADGE_W / 2, cy - BADGE_H / 2
    x2, y2 = cx + BADGE_W / 2, cy + BADGE_H / 2
    draw.rounded_rectangle((x1, y1, x2, y2), radius=BADGE_H / 2, fill=(*BADGE_BG, 255))

    small_text, large_text = "Download on the", "App Store"
    small_h, large_h = int(small_font.size * 1.15), int(large_font.size * 1.15)
    ty = cy - (small_h + large_h) / 2
    sw = text_width(small_font, small_text)
    draw.text((cx - sw / 2, ty), small_text, font=small_font, fill=(255, 255, 255, 255))
    ty += small_h
    lw = text_width(large_font, large_text)
    draw.text((cx - lw / 2, ty), large_text, font=large_font, fill=(255, 255, 255, 255))


@dataclass
class EndCardAssets:
    bg_rgb: tuple[int, int, int]
    ink_rgb: tuple[int, int, int]
    wordmark_font: ImageFont.FreeTypeFont
    tagline_font: ImageFont.FreeTypeFont
    endcard_start: float  # when the previous segment ends and the end card's own fade-in begins
    icon_card: Image.Image | None       # None when no app-icon.png is available (renders without it)
    badge_small_font: ImageFont.FreeTypeFont | None = None
    badge_large_font: ImageFont.FreeTypeFont | None = None
    search_font: ImageFont.FreeTypeFont | None = None
    wordmark: str = "Prompted"
    tagline: str = "The posing app that is only a posing app."
    search_line: str = "Search “Prompted” on the App Store"


def _draw_centered(draw: ImageDraw.ImageDraw, y: float, text: str, font, fill,
                   max_width: int) -> float:
    lines = wrap(font, text, max_width) or [text]
    line_h = int(font.size * 1.25)
    for line in lines:
        lw = text_width(font, line)
        draw.text(((WIDTH - lw) / 2, y), line, font=font, fill=fill)
        y += line_h
    return y


def _ease_out_back(p: float, k: float = 1.4) -> float:
    """Overshoot ease: lands at 1.0 after a small bounce past it."""
    p = min(max(p, 0.0), 1.0)
    q = p - 1.0
    return 1.0 + (k + 1.0) * q ** 3 + k * q ** 2


def _alpha_at(t: float, start: float, dur: float) -> int:
    return int(255 * min(1.0, max(0.0, (t - start) / dur)))


def render_end_card(assets: EndCardAssets, t: float) -> Image.Image:
    """Paper card. The icon scales in with a slight overshoot, then the
    wordmark, an amber rule that draws itself, the tagline, the badge and the
    search line stagger in a beat apart."""
    bg = Image.new("RGBA", (WIDTH, HEIGHT), (*assets.bg_rgb, 255))
    el = t - assets.endcard_start
    if el <= 0:
        return bg.convert("RGB")

    content = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(content)
    max_width = int(WIDTH * 0.82)

    # 1) icon: 0.0-0.5 s, scale 0.85 -> 1.0 with overshoot, alpha 0 -> 1
    if assets.icon_card is not None:
        icon_cy = round(HEIGHT * ICON_CENTER_Y_RATIO)
        p = _ease_out_back(el / 0.5)
        scale = 0.85 + 0.15 * p
        iw, ih = assets.icon_card.size
        sw, sh = max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))
        icon = assets.icon_card.resize((sw, sh), Image.LANCZOS)
        a = _alpha_at(el, 0.0, 0.25)
        if a < 255:
            r, g, b, al = icon.split(); icon = Image.merge("RGBA", (r, g, b, al.point(lambda v: v * a // 255)))
        content.alpha_composite(icon, (WIDTH // 2 - sw // 2, icon_cy - sh // 2))
        y = icon_cy + ICON_SIZE / 2 + ICON_WORDMARK_GAP
    else:
        y = HEIGHT * 0.40

    # 2) wordmark at 0.30 s, then the amber rule draws 0.45-0.85 s
    wa = _alpha_at(el, 0.30, 0.25)
    y_word = y
    y = _draw_centered(draw, y, assets.wordmark, assets.wordmark_font, (*AMBER_BRIGHT, wa), max_width)
    rule_w_full = min(int(text_width(assets.wordmark_font, assets.wordmark) * 0.62), 360)
    rp = min(1.0, max(0.0, (el - 0.45) / 0.40))
    rp = 1 - (1 - rp) ** 3
    if rp > 0:
        rw = int(rule_w_full * rp)
        rx = WIDTH / 2 - rule_w_full / 2
        draw.rounded_rectangle((rx, y + 10, rx + rw, y + 18), radius=4, fill=(*AMBER_BRIGHT, 255))
    y += 20 + 26

    # 3) tagline, badge, search line stagger
    ta = _alpha_at(el, 0.55, 0.25)
    y = _draw_centered(draw, y, assets.tagline, assets.tagline_font, (*assets.ink_rgb, ta), max_width) + 34
    if assets.badge_small_font is not None:
        ba = _alpha_at(el, 0.75, 0.25)
        badge_cy = y + BADGE_H / 2
        layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        _draw_app_store_badge(ImageDraw.Draw(layer), (WIDTH / 2, badge_cy), assets.badge_small_font, assets.badge_large_font)
        if ba < 255:
            r, g, b, al = layer.split(); layer = Image.merge("RGBA", (r, g, b, al.point(lambda v: v * ba // 255)))
        content.alpha_composite(layer)
        y = badge_cy + BADGE_H / 2 + 30
        sa = _alpha_at(el, 0.95, 0.25)
        _draw_centered(draw, y, assets.search_line, assets.search_font, (*MUTED_INK, sa), max_width)

    return Image.alpha_composite(bg, content).convert("RGB")


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
