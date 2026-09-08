"""Pillow compositing for one UGC reaction video.

Timeline (1080x1920, 30fps), "open-then-split":

  OPEN   0.0-2.0s   the reaction clip cover-crops the *whole* frame
                    (anchored 12% down from the top -- faces sit in the
                    upper third of a 9:16 clip). A hook pill sits
                    bottom-left of the frame, visible 0.0-2.3s, fading out
                    over its last 0.3s.
  MOVE   2.0-2.6s   the app panel slides up from below the bottom edge to
                    its resting position while the reaction clip smoothly
                    reframes from full-frame down into the top band --
                    both animations are driven by one shared eased
                    progress value (`move_progress`) so they always meet
                    exactly at the SPLIT resting position with no gap or
                    overlap. The reaction keeps decoding and playing
                    throughout; it is never frozen during the move.
  SPLIT  2.6s-end   top band = reaction (52% of height), a 6px amber rule,
                    bottom band (48%) = the app-action panel. The
                    app-action clip has been playing from its own t=0
                    since OPEN began, so by the time the split settles it
                    is already ~2.6s into its own recording -- long enough
                    that a nervous_client recording's screen tap (~4s in)
                    lands inside the visible split, not before it. Either
                    clip holds its last frame once exhausted (see
                    VideoReader.read_held in video_io.py).

The reaction clip is decoded once, at the full OPEN framing (1080x1920);
the smaller MOVE/SPLIT framing is a second, Pillow-side crop of that same
decoded frame using the same 12%-from-top anchor applied to the shrinking
window (see compose_main_frame) -- one VideoReader per source clip,
always, never two decodes of the same file.

The app-action recording is decoded through a *fixed* crop window -- the
whole clip, never tracked frame to frame (see module docstring on
`app_crop_window`): the amber tone-chip row is located in the clip's LAST
frame and the window runs from 180px above it to 700px below (clamped to
the frame), so the panel always shows the tone chips and the prompt line
rather than the whole phone. That window is then scaled to fit the panel
width minus APP_PANEL_MARGIN px each side, aspect preserved.

ENDCARD_DURATION seconds at the end cross-fade the last composited frame
into a paper end card, same mechanism as before. Total duration =
max(app clip duration, MIN_MAIN_DURATION) + ENDCARD_DURATION, capped at
MAX_TOTAL_DURATION -- see `main_duration_seconds` / `total_duration_seconds`.

Text (the hook pill and the end card) is Pillow-rendered, never ffmpeg
drawtext (this machine's ffmpeg has no drawtext filter -- see
tools/reels_gen/frames.py); reused where it applies directly:
`pinterest.render.hex_rgb`, `pinterest.text_fit`'s auto-fit/wrap/load_font,
`reels_gen.frames.build_app_icon_card` for the icon's rounded-corner/shadow
treatment, and `reels_gen.frames.contact_sheet` for the dry-run sheet.
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from pinterest.render import hex_rgb
from pinterest.text_fit import fit_text, load_font, text_width, wrap
from reels_gen.frames import build_app_icon_card

from .select import Selection
from .video_io import Encoder, VideoReader, extract_last_frame, probe_video

WIDTH, HEIGHT = 1080, 1920
FPS = 30

# -- timeline -------------------------------------------------------------
OPEN_END = 2.0                # reaction fills the whole frame until this time
MOVE_END = 2.6                 # the split has fully settled by this time
REACTION_TOP_ANCHOR = 0.12     # faces sit in the upper third of a 9:16 clip

SPLIT_REACTION_RATIO = 0.52
REACTION_H = round(HEIGHT * SPLIT_REACTION_RATIO)     # 998, resting top-band height
RULE_H = 6
PANEL_TOP = REACTION_H + RULE_H                        # 1004
PANEL_H = HEIGHT - PANEL_TOP                           # 916, resting bottom-band height

APP_PANEL_MARGIN = 80          # each side, around the scaled app-panel content
PANEL_RADIUS = 36
PANEL_SHADOW_BLUR = 24
PANEL_SHADOW_ALPHA = 90
PANEL_SHADOW_OFFSET = (0, 12)

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

ENDCARD_DURATION = 1.2
ICON_SIZE = 200
ICON_RADIUS = 46
ICON_CENTER_Y_RATIO = 0.36

MIN_MAIN_DURATION = 7.5
MAX_TOTAL_DURATION = 10.0

# -- app-panel crop-window detector ---------------------------------------
# The panel never shows the whole phone: it crops to a band around the
# amber tone-chip row (colour-detected in the action clip's last frame) so
# the tone chips and the prompt line stay readable at panel size.
CHIP_MATCH_DIST = 40             # RGB euclidean distance counted as "amber"
CHIP_ROW_MIN_FRACTION = 0.05     # a row counts as chip-band once this much of its width is amber
CHIP_SEARCH_TOP_RATIO = 0.55
CHIP_SEARCH_BOTTOM_RATIO = 0.90
CHIP_ROW_GAP_TOLERANCE = 3       # amber rows within this many px merge into one band
CHIP_WINDOW_ABOVE = 180
CHIP_WINDOW_BELOW = 700
CHIP_FALLBACK_TOP_RATIO = 0.52
CHIP_FALLBACK_BOTTOM_RATIO = 0.92


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def _lerp(a: float, b: float, p: float) -> float:
    return a + (b - a) * p


def _ease_in_out(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


def move_progress(t: float) -> float:
    """0.0 before OPEN_END, 1.0 from MOVE_END on, eased in-out in between.
    Both the reaction reframe and the panel slide-up are driven by this
    single value so they always meet exactly at the SPLIT resting
    position, with no visible gap or overlap between them."""
    if MOVE_END <= OPEN_END:
        return 1.0 if t >= OPEN_END else 0.0
    return _ease_in_out((t - OPEN_END) / (MOVE_END - OPEN_END))


def reaction_band_height(t: float) -> int:
    """Height of the reaction's on-screen rectangle at time `t`: HEIGHT
    (the whole frame) during OPEN, shrinking to REACTION_H by MOVE_END."""
    return round(_lerp(HEIGHT, REACTION_H, move_progress(t)))


def panel_top_y(t: float) -> int:
    """Top edge of the app panel at time `t`: starts at HEIGHT + RULE_H
    (fully below the bottom edge, invisible) and slides up to PANEL_TOP by
    MOVE_END. Starting one rule-height below HEIGHT (rather than exactly
    at it) keeps the amber rule itself off screen during OPEN too."""
    return round(_lerp(HEIGHT + RULE_H, PANEL_TOP, move_progress(t)))


def main_duration_seconds(app_duration: float) -> float:
    """Length of the OPEN+MOVE+SPLIT segment, before the end-card
    crossfade: at least MIN_MAIN_DURATION, long enough to cover the whole
    app-action clip, but never so long that total_duration_seconds would
    exceed MAX_TOTAL_DURATION."""
    return min(max(app_duration, MIN_MAIN_DURATION), MAX_TOTAL_DURATION - ENDCARD_DURATION)


def total_duration_seconds(app_duration: float) -> float:
    return main_duration_seconds(app_duration) + ENDCARD_DURATION


def scaled_app_size(orig_w: int, crop_h: int) -> tuple[int, int]:
    """The (fixed) app-panel crop window -- orig_w wide, crop_h tall --
    scaled to fit the panel width minus APP_PANEL_MARGIN px each side,
    aspect preserved. Clamped to PANEL_H as a safety net: real crop
    windows fit comfortably within it, but a pathological detection
    shouldn't be able to blow out the panel band."""
    target_w = WIDTH - 2 * APP_PANEL_MARGIN
    target_h = round(crop_h * target_w / orig_w)
    if target_h > PANEL_H:
        target_w = round(target_w * PANEL_H / target_h)
        target_h = PANEL_H
    return max(1, target_w), max(1, target_h)


def app_panel_geometry(video_w: int, video_h: int) -> tuple[int, int]:
    """Top-left of the scaled app clip within the panel canvas, centred."""
    return (WIDTH - video_w) // 2, (PANEL_H - video_h) // 2


def build_app_panel_base(video_w: int, video_h: int) -> tuple[Image.Image, tuple[int, int]]:
    """The paper panel background with its drop shadow baked in (constant
    across every frame of a video); the app clip itself is composited onto
    a fresh copy of this per frame."""
    bg = Image.new("RGBA", (WIDTH, PANEL_H), (*hex_rgb(PAPER_HEX), 255))
    x, y = app_panel_geometry(video_w, video_h)
    shadow = Image.new("RGBA", (WIDTH, PANEL_H), (0, 0, 0, 0))
    sx, sy = x + PANEL_SHADOW_OFFSET[0], y + PANEL_SHADOW_OFFSET[1]
    ImageDraw.Draw(shadow).rounded_rectangle((sx, sy, sx + video_w, sy + video_h),
                                             radius=PANEL_RADIUS, fill=(0, 0, 0, PANEL_SHADOW_ALPHA))
    shadow = shadow.filter(ImageFilter.GaussianBlur(PANEL_SHADOW_BLUR))
    bg = Image.alpha_composite(bg, shadow)
    return bg, (x, y)


def build_hook_pill(text: str, font_candidates) -> tuple[Image.Image, tuple[int, int]]:
    """(RGBA pill image, top-left position on the full canvas): white text
    on a near-black rounded rect, up to HOOK_MAX_LINES lines, bottom-left
    of the whole frame (the reaction fills the whole frame for the pill's
    entire visible window, 0.0-2.3s)."""
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
                       panel_base: Image.Image, panel_content_pos: tuple[int, int], mask: Image.Image,
                       pill_img: Image.Image, pill_pos: tuple[int, int], t: float) -> Image.Image:
    """`reaction_full` is the reaction clip's full-frame (1080x1920) OPEN
    framing decoded for this instant -- the smaller MOVE/SPLIT framing is a
    crop of it (same 12%-from-top anchor, applied to the shrinking window),
    not a second decode, so the framing stays continuous throughout."""
    reaction_h = reaction_band_height(t)
    off_y = round((HEIGHT - reaction_h) * REACTION_TOP_ANCHOR)
    reaction_crop = reaction_full.crop((0, off_y, WIDTH, off_y + reaction_h))

    canvas = Image.new("RGB", (WIDTH, HEIGHT), hex_rgb(PAPER_HEX))
    canvas.paste(reaction_crop, (0, 0))

    panel_y = panel_top_y(t)
    if panel_y < HEIGHT:  # any part of the rule/panel has slid into view
        draw = ImageDraw.Draw(canvas)
        rule_top = max(0, panel_y - RULE_H)
        draw.rectangle((0, rule_top, WIDTH, panel_y), fill=hex_rgb(AMBER_HEX))

        panel = panel_base.copy()
        app_rgba = app_frame.convert("RGBA")
        app_rgba.putalpha(mask)
        panel.alpha_composite(app_rgba, panel_content_pos)
        canvas.paste(panel.convert("RGB"), (0, panel_y))

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


def detect_chip_window(frame: Image.Image) -> tuple[int, int] | None:
    """Scan `frame` (a decoded video frame, RGB, native resolution) for the
    amber tone-chip row: contiguous rows between CHIP_SEARCH_TOP_RATIO and
    CHIP_SEARCH_BOTTOM_RATIO of height whose amber-pixel fraction clears
    CHIP_ROW_MIN_FRACTION. Returns (y0, y1) -- CHIP_WINDOW_ABOVE px above
    the strongest such band to CHIP_WINDOW_BELOW px below it, clamped to
    the frame -- or None if no such band is found (caller falls back to a
    fixed band)."""
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
    """(y0, y1): the fixed vertical crop window used for the whole
    app-action clip -- detected from the amber tone-chip row in its LAST
    frame, or CHIP_FALLBACK_TOP_RATIO..CHIP_FALLBACK_BOTTOM_RATIO of height
    if detection fails (unreadable last frame, no amber chip on screen)."""
    frame = extract_last_frame(path, orig_w, orig_h)
    window = detect_chip_window(frame) if frame is not None else None
    if window is not None:
        return window
    return round(orig_h * CHIP_FALLBACK_TOP_RATIO), round(orig_h * CHIP_FALLBACK_BOTTOM_RATIO)


class _Readers(NamedTuple):
    app: VideoReader
    reaction: VideoReader
    video_w: int
    video_h: int
    app_duration: float


def _reaction_vf() -> str:
    # Faces sit in the upper third of a 9:16 reaction clip, so anchor the
    # crop 12% down from the top rather than at the centre -- this is the
    # OPEN framing; MOVE/SPLIT crop a smaller window out of the same
    # decoded frame in Pillow rather than decoding a second time.
    return (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
           f"crop={WIDTH}:{HEIGHT}:(in_w-out_w)/2:(in_h-out_h)*{REACTION_TOP_ANCHOR},fps={FPS}")


def _open_readers(sel: Selection) -> _Readers:
    orig = probe_video(sel.action.path)
    orig_w, orig_h = orig["width"], orig["height"]
    y0, y1 = app_crop_window(sel.action.path, orig_w, orig_h)
    crop_h = y1 - y0
    video_w, video_h = scaled_app_size(orig_w, crop_h)
    app_vf = f"crop={orig_w}:{crop_h}:0:{y0},scale={video_w}:{video_h},fps={FPS}"
    app_reader = VideoReader(sel.action.path, app_vf, video_w, video_h)
    reaction_reader = VideoReader(sel.reaction.path, _reaction_vf(), WIDTH, HEIGHT)
    return _Readers(app_reader, reaction_reader, video_w, video_h, orig["duration"])


def render_first_frame(sel: Selection, font_candidates) -> Image.Image:
    """The dry-run frame: the composited main frame at t=0 -- pure OPEN
    (full-frame reaction, hook pill, no panel visible yet) -- without ever
    opening an output encoder."""
    r = _open_readers(sel)
    try:
        app_frame = r.app.read()
        reaction_frame = r.reaction.read_held()
        if app_frame is None:
            raise RuntimeError(f"{sel.action.path} produced no frames")
        if reaction_frame is None:
            raise RuntimeError(f"{sel.reaction.path} produced no frames")
        panel_base, panel_pos = build_app_panel_base(r.video_w, r.video_h)
        mask = _rounded_mask((r.video_w, r.video_h), PANEL_RADIUS)
        pill_img, pill_pos = build_hook_pill(sel.hook.text, font_candidates)
        return compose_main_frame(reaction_frame, app_frame, panel_base, panel_pos, mask,
                                  pill_img, pill_pos, 0.0)
    finally:
        r.app.close()
        r.reaction.close()


def render_ugc_video(sel: Selection, out_path: Path, icon_path: Path | None, font_candidates,
                     ink_hex: str, fps: int = FPS) -> float:
    """Renders the full video and returns its duration in seconds. The
    OPEN+MOVE+SPLIT segment runs for main_duration_seconds(app clip
    duration) frames regardless of either source clip's own length --
    both readers hold their last frame once exhausted (VideoReader.read_held)."""
    r = _open_readers(sel)
    main_duration = main_duration_seconds(r.app_duration)
    n_main = round(main_duration * fps)

    panel_base, panel_pos = build_app_panel_base(r.video_w, r.video_h)
    mask = _rounded_mask((r.video_w, r.video_h), PANEL_RADIUS)
    pill_img, pill_pos = build_hook_pill(sel.hook.text, font_candidates)

    enc = Encoder(out_path, fps, WIDTH, HEIGHT)
    last_frame: Image.Image | None = None
    try:
        app_frame = r.app.read_held()
        reaction_frame = r.reaction.read_held()
        if app_frame is None:
            raise RuntimeError(f"{sel.action.path} produced no frames")
        if reaction_frame is None:
            raise RuntimeError(f"{sel.reaction.path} produced no frames")
        frame = compose_main_frame(reaction_frame, app_frame, panel_base, panel_pos, mask,
                                   pill_img, pill_pos, 0.0)
        enc.write(frame)
        last_frame = frame
        for n in range(1, n_main):
            t = n / fps
            app_frame = r.app.read_held()
            reaction_frame = r.reaction.read_held()
            frame = compose_main_frame(reaction_frame, app_frame, panel_base, panel_pos, mask,
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
