"""`reels generate` orchestration."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from pinterest import config as pin_config
from pinterest.catalog import Pose, load_poses
from pinterest.provenance import load_provenance
from pinterest.render import hex_rgb
from pinterest.rights import RightsGate
from pinterest.text_fit import FitError, load_font

from . import csvs, frames, select, textfx, video

DEFAULT_OUT = Path("dist/reels")
DEFAULT_FPS = 30
DEFAULT_APPSHOTS_DIR = Path("dist/appshots")
DEFAULT_ICON_PATH = DEFAULT_APPSHOTS_DIR / "app-icon.png"


def pose_title(pose: Pose) -> str:
    """Sentence case from the slug ('walking-hand-in-hand' -> 'Walking hand
    in hand'), matching dist/guides_data.json's titles."""
    return pose.slug.replace("-", " ").strip().capitalize()


def tone_label(tone: str) -> str:
    return tone.replace("_", " ").upper()


def pose_steps(pose: Pose) -> list[str]:
    """Up to MAX_STEPS setup instructions, verbatim and in order, for one
    pose. A pose with fewer than MAX_STEPS instructions simply yields fewer
    steps -- build_timeline shortens the steps segment (and the video)
    proportionally rather than padding."""
    return list(pose.record.get("instructions") or [])[: frames.MAX_STEPS]


def appshot_path(pose: Pose, tone: str, appshots_dir: Path) -> Path | None:
    """dist/appshots/<slug>__<tone>.png, falling back to <slug>.png; None
    if neither exists (the app-screen segment is then skipped entirely --
    it is a nice-to-have, never a reason to fail a render)."""
    for name in (f"{pose.slug}__{tone}.png", f"{pose.slug}.png"):
        candidate = appshots_dir / name
        if candidate.is_file():
            return candidate
    return None


def build_assets(sel: select.Selection, cfg: dict, disclosure: str, credit: str | None,
                 gate: RightsGate, appshots_dir: Path = DEFAULT_APPSHOTS_DIR,
                 icon_card=None) -> tuple[frames.ImageFrameAssets, "frames.AppScreenAssets | None",
                                          frames.EndCardAssets]:
    """Everything render_image_frame/render_app_screen/render_end_card need
    for one video: (image_assets, app_assets, end_assets); app_assets is
    None when the pose has no screenshot on disk. `guard_renderable` runs
    first and unconditionally -- this is the last line of defence before a
    pixel of the pose's image is ever opened. `icon_card` is the app-icon's
    pre-built rounded/shadowed image (shared across every video in a run);
    pass None to render the end card without it (e.g. the icon file is
    missing)."""
    select.guard_renderable(sel.pose, gate)

    r = cfg["cohorts"]["render"]
    fonts = r["fonts"]
    t = r["text"]

    steps = pose_steps(sel.pose)
    shot_path = appshot_path(sel.pose, sel.tone, appshots_dir)
    timeline = frames.build_timeline(len(steps), has_appshot=shot_path is not None)

    # Sized so "PROMPTED · NERVOUS CLIENT" (the longest tone label) never
    # runs into the AI-disclosure pill on the opposite corner; the pin
    # renderer's label_tracking (config, tuned for its 42pt label) is too
    # wide at this size, so tracking is fixed here instead.
    label_font = load_font(fonts["label"], 22)
    pill_font = load_font(fonts["label"], 24)
    header_font = load_font(fonts["label"], 26)
    step_number_font = load_font(fonts["label"], frames.STEP_NUMBER_FONT_SIZE)

    safe_w, safe_h = frames.prompt_safe_area()
    try:
        fit = textfx.fit_prompt(sel.prompt, fonts["prompt"], safe_w, safe_h)
    except FitError as exc:
        raise FitError(f"{sel.pose.slug}: {exc}") from exc

    step_safe_w, step_safe_h = frames.steps_text_safe_area()
    step_fits = []
    for i, step_text in enumerate(steps):
        try:
            step_fits.append(textfx.fit_step(step_text, fonts["label"], step_safe_w, step_safe_h))
        except FitError as exc:
            raise FitError(f"{sel.pose.slug}: step {i + 1}: {exc}") from exc

    is_ai = sel.pose.image_source == "ai"
    image_assets = frames.ImageFrameAssets(
        kenburns=frames.KenBurns.load(sel.pose.detail_path, timeline.image_end),
        scrim=frames.scrim_overlay(),
        timeline=timeline,
        label_text=f"PROMPTED · {tone_label(sel.tone)}",
        label_font=label_font,
        label_tracking=5,
        header_font=header_font,
        header_tracking=5,
        steps=steps,
        step_fits=step_fits,
        step_number_font=step_number_font,
        steps_header_text=frames.STEPS_HEADER_TEXT,
        prompt_fit=fit,
        prompt_header_text=f"SAY THIS · {tone_label(sel.tone)}",
        disclosure_text=disclosure if is_ai else None,
        credit_text=f"Photo: {credit}" if credit and not is_ai else None,
        pill_font=pill_font,
    )

    bg_hex = "#FBFAF8"  # Prompted paper; the end card (and app screen) are brand, not category
    bg_rgb = hex_rgb(bg_hex)

    app_assets = None
    if shot_path is not None:
        phone_card = frames.build_phone_card(shot_path)
        cx, cy, header_y, line_y = frames.app_screen_phone_center(phone_card.size)
        app_assets = frames.AppScreenAssets(
            phone=frames.AppScreenPhone.build(phone_card, (cx, cy)),
            bg_rgb=bg_rgb,
            header_font=header_font,
            header_tracking=5,
            header_text=frames.APP_SCREEN_HEADER_TEXT,
            header_y=header_y,
            line_font=load_font(fonts["label"], 42),
            line_text=frames.APP_SCREEN_LINE_TEXT,
            line_y=line_y,
            ink_rgb=hex_rgb(t["ink"]),
            appshot_start=timeline.appshot_start,
            appshot_end=timeline.appshot_end,
        )

    end_assets = frames.EndCardAssets(
        bg_rgb=bg_rgb,
        ink_rgb=hex_rgb(t["ink"]),
        wordmark_font=load_font(fonts["label"], 150),
        tagline_font=load_font(fonts["prompt"], 44),
        endcard_start=timeline.endcard_start,
        icon_card=icon_card,
        badge_small_font=load_font(fonts["label"], 28),
        badge_large_font=load_font(fonts["label"], 52),
        search_font=load_font(fonts["label"], 36),
    )
    return image_assets, app_assets, end_assets


def make_frame_fn(image_assets: frames.ImageFrameAssets, app_assets, end_assets: frames.EndCardAssets):
    tl = image_assets.timeline

    def frame_at(t: float):
        if t < tl.image_end:
            return frames.render_image_frame(image_assets, t)
        if tl.has_appshot and t < tl.appshot_end:
            return frames.render_app_screen(app_assets, t)
        return frames.render_end_card(end_assets, t)
    return frame_at


def _video_record(sel: select.Selection, appshots_dir: Path) -> csvs.VideoRecord:
    filename = f"{sel.category}-{sel.pose.slug}-{sel.tone}.mp4"
    has_shot = appshot_path(sel.pose, sel.tone, appshots_dir) is not None
    return csvs.VideoRecord(
        file=filename, slug=sel.pose.slug, category=sel.category, tone=sel.tone,
        prompt=sel.prompt, title=pose_title(sel.pose), image_source=sel.pose.image_source,
        light_conditions=tuple(sel.pose.record.get("light_conditions") or ()),
        credit=sel.pose.record.get("photographer_credit"),
        steps=tuple(pose_steps(sel.pose)),
        appshot="yes" if has_shot else "missing")


def cmd_generate(args) -> int:
    cfg = pin_config.load_all()
    poses = load_poses()
    provenance = load_provenance()
    gate = RightsGate.from_config(cfg, provenance)

    selections, skipped = select.eligible_poses(
        poses, gate, category=args.category, tone=args.tone, slug=args.slug)
    if args.limit:
        selections = selections[: args.limit]

    print(f"Catalog: {len(poses)} active poses; {len(selections)} eligible for reels "
         f"({len(skipped)} skipped)")
    for pose, reason in skipped:
        print(f"  skip {pose.slug}: {reason}")
    if not selections:
        print("Nothing to generate.")
        return 0

    disclosure = pin_config.require_disclosure(cfg).rstrip(".")
    appshots_dir: Path = getattr(args, "appshots", None) or DEFAULT_APPSHOTS_DIR
    icon_path: Path = getattr(args, "icon", None) or DEFAULT_ICON_PATH
    icon_card = frames.build_app_icon_card(icon_path) if icon_path.is_file() else None
    if icon_card is None:
        print(f"  note: no app icon at {icon_path}; end card renders without one")

    records = [_video_record(sel, appshots_dir) for sel in selections]
    n_with_shots = sum(1 for rec in records if rec.appshot == "yes")
    print(f"App screenshots: {n_with_shots}/{len(records)} poses have one in {appshots_dir}")

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    captions_path = csvs.write_captions(records, out / "captions.csv")
    start = args.start_date or (date.today() + timedelta(days=1))
    schedule_path = csvs.write_schedule(records, out / "schedule.csv", start)
    print(f"Wrote {captions_path} and {schedule_path} ({len(records)} rows, "
         f"schedule starting {start.isoformat()})")

    if args.dry_run:
        return _dry_run(selections, records, cfg, disclosure, gate, out, appshots_dir, icon_card)

    fps = args.fps or DEFAULT_FPS
    for sel, rec in zip(selections, records):
        image_assets, app_assets, end_assets = build_assets(
            sel, cfg, disclosure, rec.credit, gate, appshots_dir=appshots_dir, icon_card=icon_card)
        frame_fn = make_frame_fn(image_assets, app_assets, end_assets)
        path = out / rec.file
        video.encode(frame_fn, path, fps=fps, duration=image_assets.timeline.duration,
                    width=frames.WIDTH, height=frames.HEIGHT)
        print(f"  wrote {rec.file} ({path.stat().st_size / 1024:.0f} KB)")
    print(f"Rendered {len(records)} reels to {out}")
    return 0


def _dry_run(selections, records, cfg, disclosure, gate, out: Path, appshots_dir: Path,
            icon_card) -> int:
    thumbs: list[tuple[str, "Image.Image"]] = []
    for sel, rec in list(zip(selections, records))[:3]:
        image_assets, _app_assets, _end_assets = build_assets(
            sel, cfg, disclosure, rec.credit, gate, appshots_dir=appshots_dir, icon_card=icon_card)
        frame0 = frames.render_image_frame(image_assets, 0.0)
        png_path = out / f"{sel.pose.slug}-frame0.png"
        frame0.save(png_path, "PNG")
        thumbs.append((rec.file, frame0))
        print(f"  dry-run frame: {png_path}")
    sheet_path = frames.contact_sheet(thumbs, out / "contact_sheet.png")
    print(f"Dry run: {len(selections)} eligible, {len(thumbs)} frames rendered, "
         f"contact sheet at {sheet_path}; CSVs written; no MP4s.")
    return 0
