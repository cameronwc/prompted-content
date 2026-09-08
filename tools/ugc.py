#!/usr/bin/env python3
"""UGC reaction video composer: pairs an AI-generated "creator reaction"
clip with a recording of the Prompted app in action, for short-form social
(Reels / TikTok / Shorts).

  ugc generate [--count N] [--seed S] [--category family|couples|engagement|maternity|senior]
              [--out dist/ugc] [--fps 30] [--start-date YYYY-MM-DD]
              [--icon PATH] [--hooks PATH] [--reactions DIR] [--actions DIR] [--guides PATH]
              [--dry-run]

Every frame of text (the hook pill, the end card) is rendered with Pillow
and composited over real decoded video frames -- this machine's ffmpeg has
no drawtext filter, the same constraint tools/reels.py works around -- so
this tool reuses tools/pinterest's font loading/text-fit and
tools/reels_gen's rights gate, CSV shape (first_comment, hashtags) and
app-icon treatment rather than duplicating them.

Output: 1080x1920 H.264 MP4, yuv420p, 30fps, no audio, faststart, 8.7-10.0s
(max(app-action clip duration, 7.5s) plus a 1.2s end-card crossfade,
capped at 10s), named ugc-<NNN>-<hookslug>-<poseslug>.mp4, plus
captions.csv and schedule.csv. Layout is "open-then-split": the reaction
clip opens full-frame with a hook pill, then the app-action panel slides
up into a 52/48 split (tools/ugc_gen/compose.py has the full timeline).

Rights: an app-action clip (dist/actions/<slug>__<tone>.mp4) is only ever a
candidate when its slug is present in the rights-filtered
dist/guides_data.json AND its pose clears config/pinterest_exclusions.yaml
-- re-checked independently right before compositing, exactly like
tools/reels.py's guard_renderable.

--dry-run renders only the first frame of the first 3 videos as PNGs plus a
contact sheet, and writes the CSVs; no MP4s.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reels_gen.select import CATEGORIES  # noqa: E402

from ugc_gen import commands  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ugc", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="render UGC reaction videos")
    g.add_argument("--count", type=int, default=commands.DEFAULT_COUNT)
    g.add_argument("--seed", type=int, default=commands.DEFAULT_SEED)
    g.add_argument("--category", choices=CATEGORIES)
    g.add_argument("--out", type=Path, default=commands.DEFAULT_OUT)
    g.add_argument("--fps", type=int, default=commands.DEFAULT_FPS)
    g.add_argument("--start-date", type=date.fromisoformat, metavar="YYYY-MM-DD",
                   help="schedule.csv start date (default: tomorrow)")
    g.add_argument("--icon", type=Path, default=None,
                   help=f"app icon PNG for the end card (default {commands.DEFAULT_ICON_PATH})")
    g.add_argument("--hooks", type=Path, default=None, help="hook bank YAML (default config/ugc_hooks.yaml)")
    g.add_argument("--reactions", type=Path, default=None, help="reaction clip dir (default dist/reactions)")
    g.add_argument("--actions", type=Path, default=None, help="app-action clip dir (default dist/actions)")
    g.add_argument("--guides", type=Path, default=None,
                   help="rights-filtered pose export (default dist/guides_data.json)")
    g.add_argument("--dry-run", action="store_true",
                   help="first frame of the first 3 videos + contact sheet; CSVs; no MP4s")

    args = parser.parse_args(argv)
    if args.command == "generate":
        return commands.cmd_generate(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
