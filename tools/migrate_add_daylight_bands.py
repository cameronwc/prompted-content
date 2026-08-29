#!/usr/bin/env python3
"""Add the `soft_low` and `mid` daylight bands to existing pose records.

Why this exists
---------------
The solar engine in the iOS app classifies sun elevation into six bands
(night, blue, golden, soft_low, mid, harsh_overhead) and the Browse
"works in this light right now" chip maps the current band onto a pose
filter. Two of those bands, `soft_low` (6-20 deg) and `mid` (20-45 deg),
had no counterpart in this taxonomy, so the chip matched zero poses for
most of the shooting day.

This is a re-tag, NOT a re-seed. `generate_seed.py` draws pose ULIDs from
the same RNG stream that assigns light conditions, so changing the light
logic there and regenerating would shift every subsequent draw and
renumber all 240 ids. Pose ids are permanent (invariant I4), so this
script edits the existing pose.yaml files in place and leaves id, slug,
prompts, and images untouched.

Assignment rules
----------------
Both new bands are outdoor ambient daylight, so indoor-only poses are
never tagged.

  soft_low  <- poses tagged `golden`
               The band directly above golden hour: same low, warm,
               directional sun, an hour later.

  mid       <- poses tagged `open_shade`, `overcast`, or `harsh_overhead`
               All three are "the sun is well up and you are managing
               it". A pose that holds up under harsh overhead sun
               certainly holds up at 30 degrees, so the relationship is a
               superset, not an approximation.

Poses that change get `version` bumped. Idempotent: re-running makes no
further edits.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import POSES_DIR, load_taxonomy, taxonomy_ids  # noqa: E402

INDOOR_LOCATIONS = {"studio", "home"}

SOFT_LOW_SOURCES = {"golden"}
MID_SOURCES = {"open_shade", "overcast", "harsh_overhead"}


def is_outdoor(pose: dict) -> bool:
    """True when the pose can be shot under open sky at all."""
    return any(loc not in INDOOR_LOCATIONS for loc in pose["location_types"])


def bands_for(pose: dict) -> list[str]:
    """The daylight bands this pose should carry, in taxonomy order."""
    if not is_outdoor(pose):
        return []
    existing = set(pose["light_conditions"])
    bands = []
    if existing & SOFT_LOW_SOURCES:
        bands.append("soft_low")
    if existing & MID_SOURCES:
        bands.append("mid")
    return bands


def main() -> int:
    valid = taxonomy_ids(load_taxonomy())["light_conditions"]
    missing = {"soft_low", "mid"} - valid
    if missing:
        print(
            f"error: taxonomy/light_conditions.yaml is missing {sorted(missing)}.\n"
            "       Add the entries before running this migration.",
            file=sys.stderr,
        )
        return 1

    changed = 0
    counts = {"soft_low": 0, "mid": 0}

    for pose_dir in sorted(POSES_DIR.iterdir()):
        path = pose_dir / "pose.yaml"
        if not path.is_file():
            continue
        pose = yaml.safe_load(path.read_text())

        additions = [b for b in bands_for(pose) if b not in pose["light_conditions"]]
        if not additions:
            continue

        pose["light_conditions"].extend(additions)
        pose["version"] = int(pose["version"]) + 1
        path.write_text(
            yaml.safe_dump(pose, sort_keys=False, allow_unicode=True, width=88)
        )

        changed += 1
        for band in additions:
            counts[band] += 1

    print(f"retagged {changed} poses  soft_low+{counts['soft_low']}  mid+{counts['mid']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
