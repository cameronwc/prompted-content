"""`ugc generate` orchestration."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from pinterest import config as pin_config
from pinterest.catalog import load_poses
from pinterest.provenance import load_provenance
from pinterest.rights import RightsGate
from reels_gen.frames import contact_sheet

from . import compose, csvs
from .hooks import DEFAULT_PATH as DEFAULT_HOOKS_PATH
from .hooks import load_hooks
from .select import (DEFAULT_ACTIONS_DIR, DEFAULT_GUIDES_PATH, DEFAULT_REACTIONS_DIR, Selection,
                     build_selections, guard_action_clip, load_action_clips, load_reaction_clips)

DEFAULT_OUT = Path("dist/ugc")
DEFAULT_FPS = compose.FPS
DEFAULT_ICON_PATH = Path("dist/appshots/app-icon.png")
DEFAULT_COUNT = 10
DEFAULT_SEED = 0


def video_filename(n: int, sel: Selection) -> str:
    return f"ugc-{n:03d}-{sel.hook.slug}-{sel.action.slug}.mp4"


def video_record(n: int, sel: Selection) -> csvs.VideoRecord:
    return csvs.VideoRecord(
        file=video_filename(n, sel), hook=sel.hook.text, hook_slug=sel.hook.slug,
        pose_slug=sel.action.slug, prompt=sel.prompt, reaction_file=sel.reaction.path.name,
        image_source=sel.action.pose.image_source, category=sel.action.category,
        light_conditions=tuple(sel.action.pose.record.get("light_conditions") or ()))


def cmd_generate(args) -> int:
    cfg = pin_config.load_all()
    provenance = load_provenance()
    gate = RightsGate.from_config(cfg, provenance)
    poses = load_poses()

    hooks_path = getattr(args, "hooks", None) or DEFAULT_HOOKS_PATH
    reactions_dir = getattr(args, "reactions", None) or DEFAULT_REACTIONS_DIR
    actions_dir = getattr(args, "actions", None) or DEFAULT_ACTIONS_DIR
    guides_path = getattr(args, "guides", None) or DEFAULT_GUIDES_PATH

    hooks = load_hooks(hooks_path)
    reactions = load_reaction_clips(reactions_dir)
    actions, skipped = load_action_clips(actions_dir, guides_path, gate, poses)

    print(f"Hooks: {len(hooks)} in {hooks_path}")
    print(f"Reactions: {len(reactions)} in {reactions_dir}")
    print(f"App-action clips: {len(actions)} eligible in {actions_dir} ({len(skipped)} skipped)")
    for path, reason in skipped:
        print(f"  skip {path.name}: {reason}")

    count = args.count or DEFAULT_COUNT
    seed = DEFAULT_SEED if args.seed is None else args.seed
    selections = build_selections(hooks, reactions, actions, count, seed, category=args.category)
    records = [video_record(i + 1, sel) for i, sel in enumerate(selections)]

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    captions_path = csvs.write_captions(records, out / "captions.csv")
    start = args.start_date or (date.today() + timedelta(days=1))
    schedule_path = csvs.write_schedule(records, out / "schedule.csv", start)
    print(f"Wrote {captions_path} and {schedule_path} ({len(records)} rows, "
         f"schedule starting {start.isoformat()})")

    font_candidates = cfg["cohorts"]["render"]["fonts"]["label"]
    ink_hex = cfg["cohorts"]["render"]["text"]["ink"]
    icon_path = getattr(args, "icon", None) or DEFAULT_ICON_PATH
    if not icon_path.is_file():
        icon_path = None
        print(f"  note: no app icon at {getattr(args, 'icon', None) or DEFAULT_ICON_PATH}; "
             f"end card renders without one")

    if args.dry_run:
        return _dry_run(selections, records, font_candidates, out)

    fps = args.fps or DEFAULT_FPS
    for sel, rec in zip(selections, records):
        guard_action_clip(sel.action.pose, gate)  # last line of defence, right before compositing
        path = out / rec.file
        duration = compose.render_ugc_video(sel, path, icon_path, font_candidates, ink_hex, fps=fps)
        print(f"  wrote {rec.file} ({path.stat().st_size / 1024:.0f} KB, {duration:.1f}s)")
    print(f"Rendered {len(records)} UGC reaction videos to {out}")
    return 0


def _dry_run(selections: list[Selection], records: list[csvs.VideoRecord], font_candidates,
            out: Path) -> int:
    thumbs = []
    for sel, rec in list(zip(selections, records))[:3]:
        frame = compose.render_first_frame(sel, font_candidates)
        png_path = out / f"{sel.action.slug}-{sel.hook.slug}-frame0.png"
        frame.save(png_path, "PNG")
        thumbs.append((rec.file, frame))
        print(f"  dry-run frame: {png_path}")
    sheet_path = contact_sheet(thumbs, out / "contact_sheet.png")
    print(f"Dry run: {len(selections)} selected, {len(thumbs)} frames rendered, "
         f"contact sheet at {sheet_path}; CSVs written; no MP4s.")
    return 0
