#!/usr/bin/env python3
"""Reels/Shorts renderer: short vertical videos (Instagram Reels / TikTok /
YouTube Shorts) from the Prompted pose catalog.

  reels generate [--limit N] [--category family|couples|engagement|maternity|senior]
                 [--tone nervous_client|playful|calm|romantic] [--slug SLUG]
                 [--out dist/reels] [--fps 30] [--start-date YYYY-MM-DD] [--dry-run]

Every frame is rendered with Pillow (this machine's ffmpeg has no drawtext
filter) and piped to ffmpeg for H.264 encoding. Rights are gated through
tools/pinterest/rights.py exactly as tools/pins.py does: an excluded pose
(anything under ACTNATURALLY_PHOTOS) can never reach the renderer.

Output: 1080x1920 H.264 MP4, yuv420p, 30fps, 9.0s, no audio, faststart, under
6 MB, named <category>-<slug>-<tone>.mp4, plus captions.csv and schedule.csv.

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
    g.add_argument("--dry-run", action="store_true",
                   help="first frame of the first 3 videos + contact sheet; CSVs; no MP4s")

    args = parser.parse_args(argv)
    if args.command == "generate":
        return commands.cmd_generate(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
