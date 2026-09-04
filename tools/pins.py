#!/usr/bin/env python3
"""Pinterest pin pipeline: generate pins from the pose catalog, upload to R2,
emit bulk-upload CSVs on a ramped schedule, tracked per cohort.

  pins [--workdir DIR] generate [--limit N | --per-cohort N] [--cohort C]
                [--start-date YYYY-MM-DD] [--pins-per-day N] [--regenerate ID ...]
                [--dry-run] [--preview-scale [PX]] [--no-upload]
  pins upload   [--env dev|prod] [--confirm]
  pins csv      [--batch-size N] [--out DIR] [--no-verify] [--print]
  pins status
  pins scan-rights
  pins grade-profile
  pins seasons

--workdir puts the manifest, rendered pins and CSVs under DIR instead of
state/ and dist/ (scratch runs that must not touch the real schedule).

Config: config/pinterest_*.yaml. State: state/pinterest_manifest.json.
Rendered pins: dist/pins/. CSVs: dist/pins_csv/.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pinterest import commands  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pins", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workdir", type=Path,
                        help="scratch root for manifest/pins/CSVs (default: state/ + dist/)")
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="render new pins and schedule them")
    g.add_argument("--limit", type=int)
    g.add_argument("--per-cohort", type=int, metavar="N",
                   help="pick N per cohort instead of share-weighted selection")
    g.add_argument("--cohort", choices=("text", "photo_real", "photo_ai"))
    g.add_argument("--start-date", type=date.fromisoformat, metavar="YYYY-MM-DD")
    g.add_argument("--pins-per-day", type=int, help="flat cap, overrides the ramp")
    g.add_argument("--regenerate", nargs="+", default=[], metavar="ID",
                   help="force a rebuild of these pin ids")
    g.add_argument("--dry-run", action="store_true",
                   help="render a 12-pin contact sheet (4 per cohort); no manifest change")
    g.add_argument("--preview-scale", type=int, nargs="?", const=236, metavar="PX",
                   help="with --dry-run: also render the sheet at PX-wide thumbnails (default 236)")
    g.add_argument("--no-upload", action="store_true",
                   help="record local file URLs so `pins csv --no-verify` works offline")

    u = sub.add_parser("upload", help="upload rendered pins to R2 under pins/")
    u.add_argument("--env", choices=("dev", "prod"), default="dev")
    u.add_argument("--tf-dir")
    u.add_argument("--confirm", action="store_true", help="actually upload (default: dry run)")

    c = sub.add_parser("csv", help="write Pinterest bulk-upload CSV batches")
    c.add_argument("--batch-size", type=int)
    c.add_argument("--out", type=Path)
    c.add_argument("--no-verify", action="store_true",
                   help="skip the HEAD reachability check on image_url (local testing only)")
    c.add_argument("--print", action="store_true", dest="print_rows",
                   help="print the raw CSV rows to stdout after writing")

    sub.add_parser("status", help="counts by cohort, category, board; schedule")
    sub.add_parser("scan-rights", help="report exclusions and shoot->pose drift")
    sub.add_parser("grade-profile", help="measure the real vs AI sets for grade targets")
    sub.add_parser("seasons", help="report which poses are tagged seasonal, and why")

    args = parser.parse_args(argv)
    if args.workdir:
        ctx = commands.Context(manifest_path=args.workdir / "pinterest_manifest.json",
                               pins_dir=args.workdir / "pins", csv_dir=args.workdir / "pins_csv")
    else:
        ctx = commands.Context()
    if args.command == "generate":
        return commands.cmd_generate(
            ctx, limit=args.limit, cohort=args.cohort, dry_run=args.dry_run,
            start_date=args.start_date, pins_per_day=args.pins_per_day,
            regenerate=args.regenerate, no_upload=args.no_upload,
            per_cohort=args.per_cohort, preview_scale=args.preview_scale)
    if args.command == "upload":
        return commands.cmd_upload(ctx, env=args.env, tf_dir=args.tf_dir, confirm=args.confirm)
    if args.command == "csv":
        return commands.cmd_csv(ctx, batch_size=args.batch_size, out_dir=args.out,
                                verify=not args.no_verify, print_rows=args.print_rows)
    if args.command == "status":
        return commands.cmd_status(ctx)
    if args.command == "scan-rights":
        return commands.cmd_scan_rights(ctx)
    if args.command == "grade-profile":
        return commands.cmd_grade_profile(ctx)
    if args.command == "seasons":
        return commands.cmd_seasons(ctx)
    return 2


if __name__ == "__main__":
    sys.exit(main())
