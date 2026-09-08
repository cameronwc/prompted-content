"""Pillow compositing for one UGC reaction video.

Layout (1080x1920, 30fps): the reaction clip cover-crops the top 46%; a 6px
amber rule; the app-action clip fits (height-limited, aspect preserved) into
a phone-panel card -- rounded corners, a soft drop shadow, centred on the
Prompted paper (#FBFAF8) -- filling the bottom 54%. A hook pill (white text
on a near-black rounded rect) sits bottom-left over the reaction half for
the opening 1.8s, fading out over its last 0.3s. The reaction and app clips
start together at t=0 (the app clip drives the main segment's length --
"App clip plays in full" -- the reaction holds its last frame if shorter);
the final ENDCARD_DURATION seconds cross-fade the last composited frame into
a paper end card (app icon, "Prompted" in amber, the App Store search line).

Text (the hook pill and the end card) is Pillow-rendered, never
ffmpeg drawtext (this machine's ffmpeg has no drawtext filter -- see
tools/reels_gen/frames.py); reused where it applies directly:
`pinterest.render.hex_rgb`, `pinterest.text_fit`'s auto-fit/wrap/load_font,
`reels_gen.frames.build_app_icon_card` for the icon's rounded-corner/shadow
treatment, and `reels_gen.frames.contact_sheet` for the dry-run sheet.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from pinterest.render import hex_rgb
from pinterest.text_fit import fit_text, load_font, text_width, wrap
from reels_gen.frames import build_app_icon_card

from .select import Selection
from .video_io import Encoder, VideoReader, probe_video

WIDTH, HEIGHT = 1080, 1920
FPS = 30

REACTION_H = round(HEIGHT * 0.46)          # 883
RULE_H = 6
PANEL_TOP = REACTION_H + RULE_H            # 889
PANEL_H = HEIGHT - PANEL_TOP               # 1031
APP_MARGIN = 48                            # panel height reserved around the scaled app clip
PANEL_RADIUS = 40
PANEL_SHADOW_BLUR = 24
PANEL_SHADOW_ALPHA = 90
PANEL_SHADOW_OFFSET = (0, 12)

AMBER_HEX = "#E8A33D"
PAPER_HEX = "#FBFAF8"

HOOK_PILL_END = 1.8
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


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def scaled_app_size(orig_w: int, orig_h: int) -> tuple[int, int]:
    """The app-action clip scaled to fit the panel height minus
    APP_MARGIN px, aspect preserved."""
    video_h = PANEL_H - APP_MARGIN
    video_w = max(1, round(orig_w * video_h / orig_h))
    return video_w, video_h


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
    over the reaction half."""
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
    y0 = REACTION_H - HOOK_BOTTOM_MARGIN - pill_h
    return img, (x, y0)


def hook_pill_alpha(t: float) -> int:
    if t >= HOOK_PILL_END:
        return 0
    fade_start = HOOK_PILL_END - HOOK_PILL_FADE
    if t < fade_start:
        return 255
    return max(0, int(255 * (1 - (t - fade_start) / HOOK_PILL_FADE)))


def compose_main_frame(reaction_frame: Image.Image, app_frame: Image.Image,
                       panel_base: Image.Image, panel_pos: tuple[int, int], mask: Image.Image,
                       pill_img: Image.Image, pill_pos: tuple[int, int], t: float) -> Image.Image:
    canvas = Image.new("RGB", (WIDTH, HEIGHT))
    canvas.paste(reaction_frame, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, REACTION_H, WIDTH, PANEL_TOP), fill=hex_rgb(AMBER_HEX))

    panel = panel_base.copy()
    app_rgba = app_frame.convert("RGBA")
    app_rgba.putalpha(mask)
    panel.alpha_composite(app_rgba, panel_pos)
    canvas.paste(panel.convert("RGB"), (0, PANEL_TOP))

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


def _open_readers(sel: Selection) -> tuple[VideoReader, VideoReader, int, int]:
    orig = probe_video(sel.action.path)
    video_w, video_h = scaled_app_size(orig["width"], orig["height"])
    app_vf = f"scale={video_w}:{video_h},fps={FPS}"
    # Faces sit in the upper third of a 9:16 reaction clip, so anchor the
    # crop 12% down from the top rather than at the centre.
    reaction_vf = (f"scale={WIDTH}:{REACTION_H}:force_original_aspect_ratio=increase,"
                  f"crop={WIDTH}:{REACTION_H}:(in_w-out_w)/2:(in_h-out_h)*0.12,fps={FPS}")
    app_reader = VideoReader(sel.action.path, app_vf, video_w, video_h)
    reaction_reader = VideoReader(sel.reaction.path, reaction_vf, WIDTH, REACTION_H)
    return app_reader, reaction_reader, video_w, video_h


def render_first_frame(sel: Selection, font_candidates) -> Image.Image:
    """The dry-run frame: the composited main frame at t=0, without ever
    opening an output encoder."""
    app_reader, reaction_reader, video_w, video_h = _open_readers(sel)
    try:
        app_frame = app_reader.read()
        reaction_frame = reaction_reader.read_held()
        if app_frame is None:
            raise RuntimeError(f"{sel.action.path} produced no frames")
        if reaction_frame is None:
            raise RuntimeError(f"{sel.reaction.path} produced no frames")
        panel_base, panel_pos = build_app_panel_base(video_w, video_h)
        mask = _rounded_mask((video_w, video_h), PANEL_RADIUS)
        pill_img, pill_pos = build_hook_pill(sel.hook.text, font_candidates)
        return compose_main_frame(reaction_frame, app_frame, panel_base, panel_pos, mask,
                                  pill_img, pill_pos, 0.0)
    finally:
        app_reader.close()
        reaction_reader.close()


def render_ugc_video(sel: Selection, out_path: Path, icon_path: Path | None, font_candidates,
                     ink_hex: str, fps: int = FPS) -> float:
    """Renders the full video and returns its duration in seconds."""
    app_reader, reaction_reader, video_w, video_h = _open_readers(sel)
    panel_base, panel_pos = build_app_panel_base(video_w, video_h)
    mask = _rounded_mask((video_w, video_h), PANEL_RADIUS)
    pill_img, pill_pos = build_hook_pill(sel.hook.text, font_candidates)

    enc = Encoder(out_path, fps, WIDTH, HEIGHT)
    last_frame: Image.Image | None = None
    n_main = 0
    try:
        while True:
            app_frame = app_reader.read()
            if app_frame is None:
                break
            reaction_frame = reaction_reader.read_held()
            if reaction_frame is None:
                raise RuntimeError(f"{sel.reaction.path} produced no frames")
            frame = compose_main_frame(reaction_frame, app_frame, panel_base, panel_pos, mask,
                                       pill_img, pill_pos, n_main / fps)
            enc.write(frame)
            last_frame = frame
            n_main += 1
    finally:
        app_reader.close()
        reaction_reader.close()

    if last_frame is None or n_main == 0:
        raise RuntimeError(f"{sel.action.path} produced no frames")

    end_card = build_end_card(icon_path, font_candidates, ink_hex)
    n_endcard = round(ENDCARD_DURATION * fps)
    for k in range(n_endcard):
        p = (k + 1) / n_endcard
        enc.write(Image.blend(last_frame, end_card, p))
    enc.close()

    return (n_main + n_endcard) / fps
