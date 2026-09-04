#!/usr/bin/env python3
"""Reels/Shorts renderer: short vertical videos (Instagram Reels / TikTok /
YouTube Shorts) from the Prompted pose catalog.

  reels generate [--limit N] [--category family|couples|engagement|maternity|senior]
                 [--tone nervous_client|playful|calm|romantic] [--slug SLUG]
                 [--out dist/reels] [--fps 30] [--start-date YYYY-MM-DD]
                 [--appshots DIR] [--icon PATH] [--dry-run]

Every frame is rendered with Pillow (this machine's ffmpeg has no drawtext
filter) and piped to ffmpeg for H.264 encoding. Rights are gated through
tools/pinterest/rights.py exactly as tools/pins.py does: an excluded pose
(anything under ACTNATURALLY_PHOTOS) can never reach the renderer.

Each reel opens on the setup steps (up to 3, quoted verbatim from the
pose's `instructions`) before it says the verbal prompt, then -- when a
screenshot of the pose's detail view exists -- an app-screen segment, then
an end card that promotes the app itself. See tools/reels_gen/frames.py's
module docstring for the full timeline.

Output: 1080x1920 H.264 MP4, yuv420p, 30fps, up to 19.0s (a pose with fewer
than 3 instructions renders a shorter steps segment, and a pose with no
screenshot in --appshots skips the app-screen segment entirely -- either
shortens the video rather than padding it; every pose in the current
catalog has >=3 instructions, so today the only variable is the
screenshot), no audio, faststart, under 6 MB, named
<category>-<slug>-<tone>.mp4, plus captions.csv and schedule.csv.

--appshots (default dist/appshots) is searched for <slug>__<tone>.png, then
<slug>.png, per pose; --icon (default dist/appshots/app-icon.png) is the
1024px app icon used on the end card.

--dry-run renders only the first frame of the first 3 videos as PNGs plus a
contact sheet, and writes the CSVs; no MP4s.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reels_gen import commands  # noqa: E402
from reels_gen.select import CATEGORIES, TONES  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reels", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="render reels from the pose catalog")
    g.add_argument("--limit", type=int)
    g.add_argument("--category", choices=CATEGORIES)
    g.add_argument("--tone", choices=TONES)
    g.add_argument("--slug")
    g.add_argument("--out", type=Path, default=commands.DEFAULT_OUT)
    g.add_argument("--fps", type=int, default=commands.DEFAULT_FPS)
    g.add_argument("--start-date", type=date.fromisoformat, metavar="YYYY-MM-DD",
                   help="schedule.csv start date (default: tomorrow)")
    g.add_argument("--appshots", type=Path, default=None,
                   help=f"app-screenshot directory (default {commands.DEFAULT_APPSHOTS_DIR})")
    g.add_argument("--icon", type=Path, default=None,
                   help=f"app icon PNG for the end card (default {commands.DEFAULT_ICON_PATH})")
    g.add_argument("--dry-run", action="store_true",
                   help="first frame of the first 3 videos + contact sheet; CSVs; no MP4s")

    args = parser.parse_args(argv)
    if args.command == "generate":
        return commands.cmd_generate(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
