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


def pose_title(pose: Pose) -> str:
    """Sentence case from the slug ('walking-hand-in-hand' -> 'Walking hand
    in hand'), matching dist/guides_data.json's titles."""
    return pose.slug.replace("-", " ").strip().capitalize()


def tone_label(tone: str) -> str:
    return tone.replace("_", " ").upper()


def build_assets(sel: select.Selection, cfg: dict, disclosure: str, credit: str | None,
                 gate: RightsGate) -> tuple[frames.ImageFrameAssets, frames.EndCardAssets]:
    """Everything render_image_frame/render_end_card need for one video.
    `guard_renderable` runs first and unconditionally -- this is the last
    line of defence before a pixel of the pose's image is ever opened."""
    select.guard_renderable(sel.pose, gate)

    r = cfg["cohorts"]["render"]
    fonts = r["fonts"]
    t = r["text"]

    # Sized so "PROMPTED · NERVOUS CLIENT" (the longest tone label) never
    # runs into the AI-disclosure pill on the opposite corner; the pin
    # renderer's label_tracking (config, tuned for its 42pt label) is too
    # wide at this size, so tracking is fixed here instead.
    label_font = load_font(fonts["label"], 22)
    pill_font = load_font(fonts["label"], 24)
    safe_w, safe_h = frames.prompt_safe_area()
    try:
        fit = textfx.fit_prompt(sel.prompt, fonts["prompt"], safe_w, safe_h)
    except FitError as exc:
        raise FitError(f"{sel.pose.slug}: {exc}") from exc

    is_ai = sel.pose.image_source == "ai"
    image_assets = frames.ImageFrameAssets(
        kenburns=frames.KenBurns.load(sel.pose.detail_path),
        scrim=frames.scrim_overlay(),
        label_text=f"PROMPTED · {tone_label(sel.tone)}",
        label_font=label_font,
        label_tracking=5,
        prompt_fit=fit,
        disclosure_text=disclosure if is_ai else None,
        credit_text=f"Photo: {credit}" if credit and not is_ai else None,
        pill_font=pill_font,
    )

    palette = t["palette"]
    bg_hex = "#FBFAF8"  # Prompted paper; the end card is brand, not category
    end_assets = frames.EndCardAssets(
        bg_rgb=hex_rgb(bg_hex),
        ink_rgb=hex_rgb(t["ink"]),
        wordmark_font=load_font(fonts["label"], 150),
        tagline_font=load_font(fonts["prompt"], 46),
        small_font=load_font(fonts["label"], 32),
    )
    return image_assets, end_assets


def make_frame_fn(image_assets: frames.ImageFrameAssets, end_assets: frames.EndCardAssets):
    def frame_at(t: float):
        if t < frames.IMAGE_END:
            return frames.render_image_frame(image_assets, t)
        return frames.render_end_card(end_assets, t)
    return frame_at


def _video_record(sel: select.Selection) -> csvs.VideoRecord:
    filename = f"{sel.category}-{sel.pose.slug}-{sel.tone}.mp4"
    return csvs.VideoRecord(
        file=filename, slug=sel.pose.slug, category=sel.category, tone=sel.tone,
        prompt=sel.prompt, title=pose_title(sel.pose), image_source=sel.pose.image_source,
        light_conditions=tuple(sel.pose.record.get("light_conditions") or ()),
        credit=sel.pose.record.get("photographer_credit"))


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
    records = [_video_record(sel) for sel in selections]

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    captions_path = csvs.write_captions(records, out / "captions.csv")
    start = args.start_date or (date.today() + timedelta(days=1))
    schedule_path = csvs.write_schedule(records, out / "schedule.csv", start)
    print(f"Wrote {captions_path} and {schedule_path} ({len(records)} rows, "
         f"schedule starting {start.isoformat()})")

    if args.dry_run:
        return _dry_run(selections, records, cfg, disclosure, gate, out)

    fps = args.fps or DEFAULT_FPS
    for sel, rec in zip(selections, records):
        image_assets, end_assets = build_assets(sel, cfg, disclosure, rec.credit, gate)
        frame_fn = make_frame_fn(image_assets, end_assets)
        path = out / rec.file
        video.encode(frame_fn, path, fps=fps, duration=frames.DURATION,
                    width=frames.WIDTH, height=frames.HEIGHT)
        print(f"  wrote {rec.file} ({path.stat().st_size / 1024:.0f} KB)")
    print(f"Rendered {len(records)} reels to {out}")
    return 0


def _dry_run(selections, records, cfg, disclosure, gate, out: Path) -> int:
    thumbs: list[tuple[str, "Image.Image"]] = []
    for sel, rec in list(zip(selections, records))[:3]:
        image_assets, _ = build_assets(sel, cfg, disclosure, rec.credit, gate)
        frame0 = frames.render_image_frame(image_assets, 0.0)
        png_path = out / f"{sel.pose.slug}-frame0.png"
        frame0.save(png_path, "PNG")
        thumbs.append((rec.file, frame0))
        print(f"  dry-run frame: {png_path}")
    sheet_path = frames.contact_sheet(thumbs, out / "contact_sheet.png")
    print(f"Dry run: {len(selections)} eligible, {len(thumbs)} frames rendered, "
         f"contact sheet at {sheet_path}; CSVs written; no MP4s.")
    return 0
