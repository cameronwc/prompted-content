"""Pillow compositing for one UGC reaction video.

Timeline (1080x1920, 30fps), "open-then-takeover":

  OPEN   0.0-2.0s   the reaction clip cover-crops the whole frame (anchored
                    12% down from the top -- faces sit in the upper third
                    of a 9:16 clip). A hook pill sits bottom-left of the
                    frame, visible 0.0-2.3s, fading out over its last 0.3s.
  MOVE   2.0-2.6s   the WHOLE app-action recording (uncropped) scales up
                    from below into the frame -- its final size fits
                    APP_CARD_FINAL_H px tall, centred horizontally, top at
                    APP_CARD_TOP -- while the reaction simultaneously
                    shrinks, with the same eased progress, into a
                    picture-in-picture square anchored bottom-right. Both
                    animations share `move_progress`, so they always meet
                    their resting positions at exactly MOVE_END. The app
                    clip is FROZEN on its own first frame until t=OPEN_END
                    and only starts playing (from its own t=0) at that
                    instant; the reaction keeps playing throughout, never
                    frozen, holding its last frame once exhausted.
  TAKEOVER 2.6s-end the app-action recording fills the frame (at its
                    resting card position/size); the reaction stays a
                    small picture-in-picture, always drawn on top. From
                    PROMPT_START (see below) until the end card, a
                    near-opaque ink band across the lower part of the
                    frame carries the pose's prompt under a small tracked
                    "SAY THIS · <TONE>" header (reels_gen's fit_prompt,
                    verbatim) -- draw order is app card, then band+text,
                    then the picture-in-picture reaction on top of both.

Because the app clip is frozen until OPEN_END and only then starts
playing, its own t=0 lands at global t=OPEN_END: the chip tap a
nervous_client recording shows ~2.1s into its own runtime lands at about
global t = OPEN_END + 2.1 = 4.1s, fully inside the TAKEOVER. PROMPT_START
(4.6s) sits just after that so the tap reads clearly before the prompt
overlay appears.

Total duration = OPEN_END + the app clip's own length + ENDCARD_DURATION,
trimmed off the app clip's tail (never OPEN or the end card) so it never
exceeds TOTAL_DURATION_CAP -- see `total_duration_seconds` /
`app_playback_duration`. Every real 7.0s app recording is trimmed by
~0.2s in practice (10.2s naive -> 10.0s capped).

The reaction clip is decoded once, at the full OPEN framing (1080x1920);
the smaller picture-in-picture framing is a second, Pillow-side crop of
that same decoded frame (`_cover_crop_reaction`, reusing the 12%-from-top
anchor for whatever target aspect the PiP currently has) -- one
VideoReader per source clip, always, never two decodes of the same file.
The app-action recording is decoded pre-scaled to its final card size
(uncropped -- the whole recording, just resized) and further downscaled in
Pillow for the smaller sizes it passes through during MOVE.

detect_chip_window/app_crop_window/scaled_app_size (an earlier layout's
crop-to-the-chip-row treatment) are kept only because tests still exercise
them directly -- the render path below never calls them, so they cannot
affect any pixel of output.

Text (the hook pill, the prompt overlay, the end card) is Pillow-rendered,
never ffmpeg drawtext (this machine's ffmpeg has no drawtext filter -- see
tools/reels_gen/frames.py); reused where it applies directly:
`pinterest.render.hex_rgb`, `pinterest.text_fit`'s auto-fit/wrap/load_font,
`reels_gen.frames.build_app_icon_card` for the icon's rounded-corner/shadow
treatment, `reels_gen.frames.contact_sheet` for the dry-run sheet, and
`reels_gen`'s prompt-overlay building blocks (`draw_tracked`, the panel
alpha/feather constants, `textfx.fit_prompt`, `commands.tone_label`) for
the "SAY THIS · <TONE>" band.
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from pinterest.render import hex_rgb
from pinterest.text_fit import fit_text, load_font, text_width, wrap
from reels_gen import frames as reel_frames
from reels_gen.commands import tone_label
from reels_gen.frames import build_app_icon_card
from reels_gen.textfx import fit_prompt

from .select import Selection
from .video_io import Encoder, VideoReader, extract_last_frame, probe_video

WIDTH, HEIGHT = 1080, 1920
FPS = 30

# -- timeline -------------------------------------------------------------
OPEN_END = 2.0                 # reaction fills the whole frame until this time
MOVE_END = 2.6                  # the takeover has fully settled by this time
REACTION_TOP_ANCHOR = 0.12      # faces sit in the upper third of a 9:16 clip

ENDCARD_DURATION = 1.2
TOTAL_DURATION_CAP = 10.0       # total never exceeds this; the app clip's tail is trimmed to fit

AMBER_HEX = "#E8A33D"
PAPER_HEX = "#FBFAF8"

HOOK_PILL_END = 2.3
HOOK_PILL_FADE = 0.3
HOOK_PILL_BG = (10, 9, 8, 235)
HOOK_PILL_INK = (255, 255, 255, 255)
HOOK_PILL_RADIUS = 28
HOOK_MAX_LINES = 3
HOOK_MAX_POINT_SIZE = 44
HOOK_MIN_CAP_HEIGHT = 22
HOOK_SIDE_MARGIN = 40
HOOK_BOTTOM_MARGIN = 40
HOOK_MAX_WIDTH_RATIO = 0.62
HOOK_PAD_X = 28
HOOK_PAD_Y = 20

ICON_SIZE = 200
ICON_RADIUS = 46
ICON_CENTER_Y_RATIO = 0.36

# -- MOVE/TAKEOVER: the app-action card ------------------------------------
APP_CARD_FINAL_H = 1640          # resting height; width follows the source aspect (~755px)
APP_CARD_TOP = 60                # resting top edge
APP_CARD_RADIUS = 44
APP_CARD_START_SCALE = 0.5       # size at the start of MOVE, relative to the resting size
APP_CARD_SHADOW_BLUR = 28
APP_CARD_SHADOW_ALPHA = 110
APP_CARD_SHADOW_OFFSET = (0, 16)

# -- MOVE/TAKEOVER: the reaction picture-in-picture ------------------------
PIP_SIZE = 300
PIP_MARGIN = 40                  # from the right edge; PIP_TOP from the top
PIP_RADIUS = 40
PIP_TOP = 96                     # top-right, over the phone card's status bar, clear of the prompt band
PIP_BORDER = 4                   # paper-coloured ring, inside the PIP_SIZE footprint
PIP_SHADOW_BLUR = 20
PIP_SHADOW_ALPHA = 120
PIP_SHADOW_OFFSET = (0, 10)

# -- prompt overlay (reuses reels_gen.frames' panel alpha/feather and text
# metrics verbatim; only the band's height ratio is its own) --------------
PROMPT_BAND_HEIGHT_RATIO = 0.34
PROMPT_START = OPEN_END + 2.6    # 4.6s -- lets the ~t=4.1s chip tap read before the overlay appears
PROMPT_FADE_IN = 0.25
# reels_gen's own PANEL_FEATHER (140px) is tuned for its much taller band
# sitting over a plain photo; at that feather, our shorter band would still
# be <30% opaque under the tracked header (reel_frames.PROMPT_TOP_GAP,
# 40px, is well inside it) -- fine over a photo, illegible over the
# app screenshot's own on-screen text underneath. A tighter feather keeps
# the soft top edge but puts the header safely past it.
PROMPT_PANEL_FEATHER = 32

# -- legacy: an earlier ("open-then-split") layout's app-panel crop-window
# detector. The render path below never calls these; kept only because
# tests still exercise the detector directly. --------------------------
CHIP_MATCH_DIST = 40
CHIP_ROW_MIN_FRACTION = 0.05
CHIP_SEARCH_TOP_RATIO = 0.55
CHIP_SEARCH_BOTTOM_RATIO = 0.90
CHIP_ROW_GAP_TOLERANCE = 3
CHIP_WINDOW_ABOVE = 180
CHIP_WINDOW_BELOW = 700
CHIP_FALLBACK_TOP_RATIO = 0.52
CHIP_FALLBACK_BOTTOM_RATIO = 0.92
APP_PANEL_MARGIN = 80
PANEL_H = 916  # HEIGHT - (REACTION_H + RULE_H) of the retired split layout


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1),
                                           radius=min(radius, size[0] // 2, size[1] // 2), fill=255)
    return mask


def _lerp(a: float, b: float, p: float) -> float:
    return a + (b - a) * p


def _ease_in_out(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


def move_progress(t: float) -> float:
    """0.0 before OPEN_END, 1.0 from MOVE_END on, eased in-out in between.
    Both the app card's rise and the reaction's shrink-into-PiP are driven
    by this single value so they always meet their resting positions
    together, at exactly MOVE_END."""
    if MOVE_END <= OPEN_END:
        return 1.0 if t >= OPEN_END else 0.0
    return _ease_in_out((t - OPEN_END) / (MOVE_END - OPEN_END))


def total_duration_seconds(app_duration: float) -> float:
    """OPEN_END (fixed) + the app clip's own length (played from its own
    t=0, which starts at global t=OPEN_END) + ENDCARD_DURATION -- capped at
    TOTAL_DURATION_CAP by trimming the app clip's tail, never OPEN or the
    end card."""
    return min(OPEN_END + app_duration + ENDCARD_DURATION, TOTAL_DURATION_CAP)


def main_duration_seconds(app_duration: float) -> float:
    """Length of the OPEN+MOVE+TAKEOVER segment, before the end-card
    crossfade."""
    return total_duration_seconds(app_duration) - ENDCARD_DURATION


def app_playback_duration(app_duration: float) -> float:
    """How much of the app clip actually plays (from its own t=0) before
    the end-card crossfade begins -- app_duration itself, unless the total
    duration budget forces a trim off its tail."""
    return max(0.0, main_duration_seconds(app_duration) - OPEN_END)


def app_card_final_size(orig_w: int, orig_h: int) -> tuple[int, int]:
    """The app-action card's resting size: APP_CARD_FINAL_H tall, aspect
    preserved (uncropped -- the whole recording, just resized)."""
    h = APP_CARD_FINAL_H
    w = max(1, round(h * orig_w / orig_h))
    return w, h


def app_card_rect(t: float, final_w: int, final_h: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the app card at time `t`: starts at APP_CARD_START_SCALE
    of its resting size, centred horizontally, fully below the bottom edge
    (invisible); ends at its resting size/position. Scale and slide both
    ease with `move_progress`."""
    p = move_progress(t)
    fx, fy = (WIDTH - final_w) // 2, APP_CARD_TOP
    sw, sh = max(1, round(final_w * APP_CARD_START_SCALE)), max(1, round(final_h * APP_CARD_START_SCALE))
    sx, sy = (WIDTH - sw) // 2, HEIGHT
    x = round(_lerp(sx, fx, p))
    y = round(_lerp(sy, fy, p))
    w = round(_lerp(sw, final_w, p))
    h = round(_lerp(sh, final_h, p))
    return x, y, w, h


def reaction_pip_rect(t: float) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the reaction's on-screen rectangle at time `t`:
    the whole frame at t<=OPEN_END (matching the OPEN framing exactly, so
    there is no visible seam), shrinking with `move_progress` into a
    PIP_SIZE square anchored top-right at (WIDTH-PIP_MARGIN, PIP_TOP)
    by MOVE_END, clear of the prompt band."""
    p = move_progress(t)
    fx = WIDTH - PIP_MARGIN - PIP_SIZE
    fy = PIP_TOP
    x = round(_lerp(0, fx, p))
    y = round(_lerp(0, fy, p))
    w = round(_lerp(WIDTH, PIP_SIZE, p))
    h = round(_lerp(HEIGHT, PIP_SIZE, p))
    return x, y, w, h


def _cover_crop_reaction(reaction_full: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Cover-crop `reaction_full` (WIDTHxHEIGHT, already anchored 12% from
    the top) down to `target_w`x`target_h`'s aspect -- anchored the same
    way (12% from the top of the vertical excess, centred horizontally) --
    then resize to exactly that pixel size. At target=(WIDTH, HEIGHT) this
    is the identity crop, so the same helper covers the OPEN framing and
    every intermediate PiP size during MOVE."""
    src_w, src_h = reaction_full.size
    target_w, target_h = max(1, target_w), max(1, target_h)
    scale = max(target_w / src_w, target_h / src_h)
    crop_w = min(src_w, max(1, round(target_w / scale)))
    crop_h = min(src_h, max(1, round(target_h / scale)))
    x0 = round((src_w - crop_w) / 2)
    y0 = round((src_h - crop_h) * REACTION_TOP_ANCHOR)
    crop = reaction_full.crop((x0, y0, x0 + crop_w, y0 + crop_h))
    if crop.size != (target_w, target_h):
        crop = crop.resize((target_w, target_h), Image.LANCZOS)
    return crop


def _with_shadow(content_rgba: Image.Image, radius: int, blur: int, alpha: int,
                 offset: tuple[int, int]) -> tuple[Image.Image, tuple[int, int]]:
    """Pads `content_rgba` (already its own final shape -- rounded, bordered,
    whatever) with room for a blurred rounded-rect shadow behind it.
    Returns (padded RGBA canvas, offset of content's top-left within it) so
    the caller can align the *content*, not the padded canvas, to a
    target position."""
    w, h = content_rgba.size
    pad = blur * 2
    canvas = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    sx, sy = pad + offset[0], pad + offset[1]
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((sx, sy, sx + w, sy + h),
                                             radius=min(radius, w // 2, h // 2), fill=(0, 0, 0, alpha))
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    canvas = Image.alpha_composite(canvas, shadow)
    canvas.alpha_composite(content_rgba, (pad, pad))
    return canvas, (pad, pad)


def _build_app_card(app_frame: Image.Image, w: int, h: int) -> tuple[Image.Image, tuple[int, int]]:
    resized = app_frame.resize((w, h), Image.LANCZOS).convert("RGBA")
    resized.putalpha(_rounded_mask((w, h), APP_CARD_RADIUS))
    return _with_shadow(resized, APP_CARD_RADIUS, APP_CARD_SHADOW_BLUR, APP_CARD_SHADOW_ALPHA,
                        APP_CARD_SHADOW_OFFSET)


def _build_pip_card(reaction_full: Image.Image, w: int, h: int) -> tuple[Image.Image, tuple[int, int]]:
    inner_w, inner_h = max(1, w - 2 * PIP_BORDER), max(1, h - 2 * PIP_BORDER)
    inner = _cover_crop_reaction(reaction_full, inner_w, inner_h).convert("RGBA")
    inner.putalpha(_rounded_mask((inner_w, inner_h), max(1, PIP_RADIUS - PIP_BORDER)))
    card = Image.new("RGBA", (w, h), (*hex_rgb(PAPER_HEX), 255))
    card.putalpha(_rounded_mask((w, h), PIP_RADIUS))
    card.alpha_composite(inner, (PIP_BORDER, PIP_BORDER))
    return _with_shadow(card, PIP_RADIUS, PIP_SHADOW_BLUR, PIP_SHADOW_ALPHA, PIP_SHADOW_OFFSET)


# -- prompt overlay --------------------------------------------------------

def _prompt_band_overlay() -> Image.Image:
    """A near-opaque ink panel over the lower PROMPT_BAND_HEIGHT_RATIO of
    the frame, with a feathered top edge -- reels_gen.frames.scrim_overlay's
    treatment (same PANEL_ALPHA), just a shorter band with its own tighter
    feather (PROMPT_PANEL_FEATHER; see its comment)."""
    band_h = round(HEIGHT * PROMPT_BAND_HEIGHT_RATIO)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    grad = Image.new("L", (1, band_h))
    for i in range(band_h):
        a = (reel_frames.PANEL_ALPHA if i >= PROMPT_PANEL_FEATHER
            else int(reel_frames.PANEL_ALPHA * (i / PROMPT_PANEL_FEATHER) ** 1.2))
        grad.putpixel((0, i), a)
    grad = grad.resize((WIDTH, band_h))
    black = Image.new("RGBA", (WIDTH, band_h), (18, 15, 12, 0))
    black.putalpha(grad)
    overlay.paste(black, (0, HEIGHT - band_h))
    return overlay


def _prompt_header_xy() -> tuple[int, int]:
    side = int(WIDTH * reel_frames.PROMPT_SIDE_MARGIN_RATIO)
    band_h = round(HEIGHT * PROMPT_BAND_HEIGHT_RATIO)
    band_top = HEIGHT - band_h
    return side, band_top + reel_frames.PROMPT_TOP_GAP


def _prompt_safe_area() -> tuple[int, int]:
    side = int(WIDTH * reel_frames.PROMPT_SIDE_MARGIN_RATIO)
    band_h = round(HEIGHT * PROMPT_BAND_HEIGHT_RATIO)
    safe_h = band_h - reel_frames.PROMPT_BOTTOM_MARGIN - reel_frames.PROMPT_TOP_GAP - reel_frames.HEADER_RESERVE
    return WIDTH - 2 * side, safe_h


def build_prompt_overlay(prompt_text: str, tone: str, prompt_font_candidates,
                         label_font_candidates) -> Image.Image:
    """Precomposited (WIDTH x HEIGHT) RGBA: the ink band, a small tracked
    "SAY THIS · <TONE>" header, and the pose's prompt (verbatim,
    curly-quoted, serif) fitted below it -- same fit rules/sizes as
    reels_gen.textfx.fit_prompt. Built once per video; drawn with a fading
    global alpha per frame (see compose_main_frame)."""
    overlay = _prompt_band_overlay()
    draw = ImageDraw.Draw(overlay)

    header_font = load_font(label_font_candidates, 32)
    header_text = f"SAY THIS · {tone_label(tone)}"
    hx, hy = _prompt_header_xy()
    reel_frames.draw_tracked(draw, (hx, hy), header_text, header_font,
                             (*reel_frames.AMBER_BRIGHT, 255), 5)

    safe_w, safe_h = _prompt_safe_area()
    fit = fit_prompt(prompt_text, prompt_font_candidates, safe_w, safe_h)
    y = hy + reel_frames.HEADER_RESERVE
    for line in fit.lines:
        lw = text_width(fit.font, line)
        draw.text(((WIDTH - lw) / 2, y), line, font=fit.font, fill=(*reel_frames.CREAM, 255))
        y += fit.line_height
    return overlay


def _prompt_alpha_fraction(t: float) -> float:
    if t < PROMPT_START:
        return 0.0
    if PROMPT_FADE_IN <= 0 or t >= PROMPT_START + PROMPT_FADE_IN:
        return 1.0
    return (t - PROMPT_START) / PROMPT_FADE_IN


# -- hook pill (unchanged: bottom-left of the whole frame, 0.0-2.3s) -------

def build_hook_pill(text: str, font_candidates) -> tuple[Image.Image, tuple[int, int]]:
    max_w = int(WIDTH * HOOK_MAX_WIDTH_RATIO)
    fit = fit_text(text, font_candidates, max_w, HOOK_MAX_POINT_SIZE * 4, HOOK_MAX_POINT_SIZE,
                   HOOK_MIN_CAP_HEIGHT, 2, HOOK_MAX_LINES, 1.25)
    text_w = max(text_width(fit.font, line) for line in fit.lines)
    pill_w = text_w + 2 * HOOK_PAD_X
    pill_h = fit.height + 2 * HOOK_PAD_Y
    img = Image.new("RGBA", (pill_w, pill_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, pill_w - 1, pill_h - 1), radius=HOOK_PILL_RADIUS, fill=HOOK_PILL_BG)
    y = HOOK_PAD_Y
    for line in fit.lines:
        draw.text((HOOK_PAD_X, y), line, font=fit.font, fill=HOOK_PILL_INK)
        y += fit.line_height
    x = HOOK_SIDE_MARGIN
    y0 = HEIGHT - HOOK_BOTTOM_MARGIN - pill_h
    return img, (x, y0)


def hook_pill_alpha(t: float) -> int:
    if t >= HOOK_PILL_END:
        return 0
    fade_start = HOOK_PILL_END - HOOK_PILL_FADE
    if t < fade_start:
        return 255
    return max(0, int(255 * (1 - (t - fade_start) / HOOK_PILL_FADE)))


def compose_main_frame(reaction_full: Image.Image, app_frame: Image.Image,
                       overlay_base: Image.Image, pill_img: Image.Image, pill_pos: tuple[int, int],
                       t: float) -> Image.Image:
    """`reaction_full` is the reaction clip's full-frame (1080x1920) OPEN
    framing decoded for this instant; `app_frame` is the app-action card's
    content at its resting card size (resized down for smaller MOVE sizes
    here, never re-decoded). Draw order once the takeover begins: app
    card, then the prompt band+text, then the reaction PiP on top of both."""
    p = move_progress(t)

    if p <= 0.0:
        # Pure OPEN: the reaction fills the whole frame, exactly as before
        # the app card or PiP exist -- no rounding, no shadow, no seam.
        canvas = reaction_full.copy()
    else:
        canvas = Image.new("RGB", (WIDTH, HEIGHT), hex_rgb(PAPER_HEX))
        canvas_rgba = canvas.convert("RGBA")

        ax, ay, aw, ah = app_card_rect(t, app_frame.width, app_frame.height)
        app_card, (apad_x, apad_y) = _build_app_card(app_frame, aw, ah)
        canvas_rgba.alpha_composite(app_card, (ax - apad_x, ay - apad_y))

        fade = _prompt_alpha_fraction(t)
        if fade > 0.0:
            layer = overlay_base
            if fade < 1.0:
                r, g, b, a = layer.split()
                layer = Image.merge("RGBA", (r, g, b, a.point(lambda v: int(v * fade))))
            canvas_rgba.alpha_composite(layer, (0, 0))

        px, py, pw, ph = reaction_pip_rect(t)
        pip_card, (ppad_x, ppad_y) = _build_pip_card(reaction_full, pw, ph)
        canvas_rgba.alpha_composite(pip_card, (px - ppad_x, py - ppad_y))

        canvas = canvas_rgba.convert("RGB")

    alpha = hook_pill_alpha(t)
    if alpha > 0:
        layer = pill_img
        if alpha < 255:
            r, g, b, a = layer.split()
            layer = Image.merge("RGBA", (r, g, b, a.point(lambda v: v * alpha // 255)))
        canvas_rgba = canvas.convert("RGBA")
        canvas_rgba.alpha_composite(layer, pill_pos)
        canvas = canvas_rgba.convert("RGB")
    return canvas


def build_end_card(icon_path: Path | None, font_candidates, ink_hex: str) -> Image.Image:
    bg = Image.new("RGBA", (WIDTH, HEIGHT), (*hex_rgb(PAPER_HEX), 255))
    draw = ImageDraw.Draw(bg)
    y = round(HEIGHT * 0.42)

    if icon_path is not None and Path(icon_path).is_file():
        icon_card = build_app_icon_card(Path(icon_path), size=ICON_SIZE, radius=ICON_RADIUS)
        icon_cy = round(HEIGHT * ICON_CENTER_Y_RATIO)
        iw, ih = icon_card.size
        bg.alpha_composite(icon_card, (WIDTH // 2 - iw // 2, icon_cy - ih // 2))
        y = icon_cy + ICON_SIZE // 2 + 44

    wordmark_font = load_font(font_candidates, 96)
    wm = "Prompted"
    tw = text_width(wordmark_font, wm)
    draw.text(((WIDTH - tw) / 2, y), wm, font=wordmark_font, fill=(*hex_rgb(AMBER_HEX), 255))
    y += int(96 * 1.25) + 34

    search_font = load_font(font_candidates, 34)
    search = "Search “Prompted” on the App Store"
    lines = wrap(search_font, search, int(WIDTH * 0.8)) or [search]
    lh = int(34 * 1.3)
    for line in lines:
        lw = text_width(search_font, line)
        draw.text(((WIDTH - lw) / 2, y), line, font=search_font, fill=(*hex_rgb(ink_hex), 255))
        y += lh

    return bg.convert("RGB")


# -- legacy crop-window detector (see module docstring): kept for tests,
# never called by the render path below. ----------------------------------

def detect_chip_window(frame: Image.Image) -> tuple[int, int] | None:
    """Scan `frame` (a decoded video frame, RGB, native resolution) for the
    amber tone-chip row: contiguous rows between CHIP_SEARCH_TOP_RATIO and
    CHIP_SEARCH_BOTTOM_RATIO of height whose amber-pixel fraction clears
    CHIP_ROW_MIN_FRACTION. Returns (y0, y1) -- CHIP_WINDOW_ABOVE px above
    the strongest such band to CHIP_WINDOW_BELOW px below it, clamped to
    the frame -- or None if no such band is found."""
    arr = np.asarray(frame.convert("RGB"), dtype=np.int32)
    h, w, _ = arr.shape
    target = np.array(hex_rgb(AMBER_HEX), dtype=np.int32)
    dist = np.sqrt(((arr - target) ** 2).sum(axis=2))
    row_frac = (dist < CHIP_MATCH_DIST).sum(axis=1) / w

    lo = int(h * CHIP_SEARCH_TOP_RATIO)
    hi = int(h * CHIP_SEARCH_BOTTOM_RATIO)
    rows = [y for y in range(lo, hi) if row_frac[y] > CHIP_ROW_MIN_FRACTION]
    if not rows:
        return None

    runs: list[list[int]] = [[rows[0]]]
    for y in rows[1:]:
        if y - runs[-1][-1] <= CHIP_ROW_GAP_TOLERANCE:
            runs[-1].append(y)
        else:
            runs.append([y])
    band = max(runs, key=lambda run: sum(row_frac[y] for y in run))

    y0 = max(0, band[0] - CHIP_WINDOW_ABOVE)
    y1 = min(h, band[-1] + CHIP_WINDOW_BELOW)
    return y0, y1


def app_crop_window(path: Path, orig_w: int, orig_h: int) -> tuple[int, int]:
    """(y0, y1): a fixed vertical crop window detected from the amber
    tone-chip row in the clip's LAST frame, or a fallback band. Not used by
    the render path (see module docstring); kept for test coverage."""
    frame = extract_last_frame(path, orig_w, orig_h)
    window = detect_chip_window(frame) if frame is not None else None
    if window is not None:
        return window
    return round(orig_h * CHIP_FALLBACK_TOP_RATIO), round(orig_h * CHIP_FALLBACK_BOTTOM_RATIO)


def scaled_app_size(orig_w: int, crop_h: int) -> tuple[int, int]:
    """A crop window scaled to fit the (retired) split layout's panel
    width minus APP_PANEL_MARGIN px each side, clamped to PANEL_H. Not
    used by the render path; kept for test coverage."""
    target_w = WIDTH - 2 * APP_PANEL_MARGIN
    target_h = round(crop_h * target_w / orig_w)
    if target_h > PANEL_H:
        target_w = round(target_w * PANEL_H / target_h)
        target_h = PANEL_H
    return max(1, target_w), max(1, target_h)


# -- rendering --------------------------------------------------------------

class _Readers(NamedTuple):
    app: VideoReader
    reaction: VideoReader
    app_w: int
    app_h: int
    app_duration: float


def _reaction_vf() -> str:
    # Faces sit in the upper third of a 9:16 reaction clip, so anchor the
    # crop 12% down from the top rather than at the centre -- this is the
    # OPEN framing; the smaller PiP framing crops a window out of the same
    # decoded frame in Pillow rather than decoding a second time.
    return (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
           f"crop={WIDTH}:{HEIGHT}:(in_w-out_w)/2:(in_h-out_h)*{REACTION_TOP_ANCHOR},fps={FPS}")


def _open_readers(sel: Selection) -> _Readers:
    orig = probe_video(sel.action.path)
    final_w, final_h = app_card_final_size(orig["width"], orig["height"])
    # Uncropped -- the whole recording, pre-scaled to its resting card size
    # (Pillow downsizes further for the smaller MOVE sizes, never re-decoded).
    app_vf = f"scale={final_w}:{final_h},fps={FPS}"
    app_reader = VideoReader(sel.action.path, app_vf, final_w, final_h)
    reaction_reader = VideoReader(sel.reaction.path, _reaction_vf(), WIDTH, HEIGHT)
    return _Readers(app_reader, reaction_reader, final_w, final_h, orig["duration"])


def render_first_frame(sel: Selection, font_candidates, prompt_font_candidates) -> Image.Image:
    """The dry-run frame: the composited main frame at t=0 -- pure OPEN
    (full-frame reaction, hook pill, no app card/PiP/prompt visible yet) --
    without ever opening an output encoder."""
    r = _open_readers(sel)
    try:
        app_frame = r.app.read()
        reaction_frame = r.reaction.read_held()
        if app_frame is None:
            raise RuntimeError(f"{sel.action.path} produced no frames")
        if reaction_frame is None:
            raise RuntimeError(f"{sel.reaction.path} produced no frames")
        overlay_base = build_prompt_overlay(sel.prompt, sel.action.tone, prompt_font_candidates,
                                            font_candidates)
        pill_img, pill_pos = build_hook_pill(sel.hook.text, font_candidates)
        return compose_main_frame(reaction_frame, app_frame, overlay_base, pill_img, pill_pos, 0.0)
    finally:
        r.app.close()
        r.reaction.close()


def render_ugc_video(sel: Selection, out_path: Path, icon_path: Path | None, font_candidates,
                     prompt_font_candidates, ink_hex: str, fps: int = FPS) -> float:
    """Renders the full video and returns its duration in seconds. The app
    clip is frozen on its first frame through OPEN_END, then plays from
    its own t=0; the reaction plays throughout, holding its last frame
    once exhausted (VideoReader.read_held)."""
    r = _open_readers(sel)
    main_duration = main_duration_seconds(r.app_duration)
    n_main = round(main_duration * fps)
    freeze_frames = round(OPEN_END * fps)

    overlay_base = build_prompt_overlay(sel.prompt, sel.action.tone, prompt_font_candidates,
                                        font_candidates)
    pill_img, pill_pos = build_hook_pill(sel.hook.text, font_candidates)

    enc = Encoder(out_path, fps, WIDTH, HEIGHT)
    last_frame: Image.Image | None = None
    try:
        app_current = r.app.read_held()
        if app_current is None:
            raise RuntimeError(f"{sel.action.path} produced no frames")
        reaction_current = r.reaction.read_held()
        if reaction_current is None:
            raise RuntimeError(f"{sel.reaction.path} produced no frames")

        for n in range(n_main):
            t = n / fps
            if n > 0:
                reaction_current = r.reaction.read_held()
                if n > freeze_frames:
                    app_current = r.app.read_held()
            frame = compose_main_frame(reaction_current, app_current, overlay_base,
                                       pill_img, pill_pos, t)
            enc.write(frame)
            last_frame = frame
    finally:
        r.app.close()
        r.reaction.close()

    end_card = build_end_card(icon_path, font_candidates, ink_hex)
    n_endcard = round(ENDCARD_DURATION * fps)
    for k in range(n_endcard):
        p = (k + 1) / n_endcard
        enc.write(Image.blend(last_frame, end_card, p))
    enc.close()

    return (n_main + n_endcard) / fps
