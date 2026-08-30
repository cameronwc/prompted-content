#!/usr/bin/env python3
"""Retag every pose's light_conditions to satisfy the grouped light rules.

Why this exists
---------------
Before the light taxonomy was grouped, records accumulated contradictory
solar bands (one pose tagged golden + harsh_overhead + mid + soft_low): the
original seed drew up to two light tags freely, and the daylight-band
migration then added `soft_low` to every golden pose and `mid` to every
overcast/open_shade/harsh_overhead pose. With an average of ~2 solar-band
claims per pose, the "works in this light right now" filter matched nearly
the whole library.

This is a re-tag, NOT a re-seed, for the same reason as
migrate_add_daylight_bands.py: pose ids are permanent. Each pose keeps the
single most appropriate solar band (slug hint first, then the deliberately
chosen band over the derived ones), at most one sky tag, and up to two
modifiers — the exact logic lives in light_rules.resolve_light_conditions,
which generate_seed.py now shares, so a re-seed reproduces this result.

Poses that change get `version` bumped. Idempotent: re-running makes no
further edits. --dry-run reports without writing.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import POSES_DIR  # noqa: E402
from light_rules import resolve_light_conditions  # noqa: E402


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]
    changed = 0
    total = 0
    tags_before = 0
    tags_after = 0
    dist_before: Counter[int] = Counter()
    dist_after: Counter[int] = Counter()
    samples: list[tuple[str, str, list[str], list[str]]] = []

    for pose_dir in sorted(POSES_DIR.iterdir()):
        path = pose_dir / "pose.yaml"
        if not path.is_file():
            continue
        pose = yaml.safe_load(path.read_text())
        total += 1

        old = pose["light_conditions"]
        new = resolve_light_conditions(old, pose["location_types"], pose["slug"])
        tags_before += len(old)
        tags_after += len(new)
        dist_before[len(old)] += 1
        dist_after[len(new)] += 1
        if new == old:
            continue

        samples.append((pose_dir.name, pose["slug"], old, new))
        changed += 1
        if dry_run:
            continue
        pose["light_conditions"] = new
        pose["version"] = int(pose["version"]) + 1
        path.write_text(
            yaml.safe_dump(pose, sort_keys=False, allow_unicode=True, width=88)
        )

    def dist(c: Counter[int]) -> str:
        return ", ".join(f"{k} tags: {c[k]}" for k in sorted(c))

    print(f"{'would retag' if dry_run else 'retagged'} {changed} of {total} poses")
    print(f"light tags: {tags_before} -> {tags_after} "
          f"(avg {tags_before / total:.2f} -> {tags_after / total:.2f})")
    print(f"before  {dist(dist_before)}")
    print(f"after   {dist(dist_after)}")
    if "--samples" in sys.argv[1:]:
        for name, slug, old, new in samples[::max(1, len(samples) // 15)][:15]:
            print(f"  {name} {slug}\n    {old} -> {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
