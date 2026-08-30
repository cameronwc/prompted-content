#!/usr/bin/env python3
"""Deterministically select 50 poses for AI image generation.

Writes the selected ULIDs to dist/ai_subset.json. Selection is a pure
greedy maximisation of UI coverage — no RNG anywhere — so two runs produce
byte-identical output.

Allocation: family 15, couples 15, senior 10, maternity 10 (family is
over-weighted because 3–6-subject frames are the hardest case for the grid
and the PDF contact sheet).

--extend grows an EXISTING subset instead of re-selecting: the stored
poses are kept verbatim (their images already exist; re-running the greedy
over retagged metadata would orphan them) and the extension allocation is
filled greedily from the remaining pool with the same scorer.

Stratification targets, asserted after selection:
  - >= 6 poses with harsh_overhead and >= 6 with blue or night_flash
    overall (the bright/dark extremes Shoot Mode's text scrim must survive)
  - family covers subject counts 3, 4, 5, and 6
  - horizontal poses are included wherever the category has them
  - no more than 3 poses share a location type within a category
  - >= 3 poses carry seated_variant, wheelchair, or limited_mobility
"""
from __future__ import annotations

import json
import sys

from common import DIST_DIR, iter_pose_dirs, load_pose

ALLOCATION = {"family": 15, "couples": 15, "senior": 10, "maternity": 10}
EXTEND_ALLOCATION = {"family": 3, "couples": 3, "senior": 2, "maternity": 2}
SUBSET_PATH = DIST_DIR / "ai_subset.json"

MIN_HARSH = 6
MIN_DARK = 6  # blue + night_flash
DARK_LIGHTS = {"blue", "night_flash"}
MOBILITY_TAGS = {"seated_variant", "wheelchair", "limited_mobility"}
MAX_PER_LOCATION = 3  # within a category
FAMILY_COUNTS_NEEDED = {3, 4, 5, 6}


def score(pose: dict, state: dict, cat_sel: list[dict]) -> float | None:
    """Marginal coverage value of adding `pose`; None = ineligible."""
    cat_locations = {}
    for p in cat_sel:
        for loc in p["location_types"]:
            cat_locations[loc] = cat_locations.get(loc, 0) + 1
    if any(cat_locations.get(loc, 0) >= MAX_PER_LOCATION for loc in pose["location_types"]):
        return None

    lights = set(pose["light_conditions"])
    cat_lights = {l for p in cat_sel for l in p["light_conditions"]}
    s = 0.0
    if state["harsh_needed"] > 0 and "harsh_overhead" in lights:
        s += 40
    if state["dark_needed"] > 0 and lights & DARK_LIGHTS:
        s += 40
    if pose["categories"][0] == "family" and pose["subject_count"] in state["family_counts_needed"]:
        s += 30
    if state["mobility_needed"] > 0 and set(pose["accessibility"]) & MOBILITY_TAGS:
        s += 15
    # orientation mix: reward horizontal until ~1/3 of the category
    n_horizontal = sum(1 for p in cat_sel if p["orientation"] == "horizontal")
    if pose["orientation"] == "horizontal" and n_horizontal < max(2, len(cat_sel) // 3 + 1):
        s += 12
    # variety within the category
    s += 6 * len(lights - cat_lights)
    s += 4 * sum(1 for loc in pose["location_types"] if loc not in cat_locations)
    return s


def select(extend: bool = False) -> dict[str, list[dict]]:
    poses = [load_pose(d) for d in iter_pose_dirs()]
    by_category = {cat: sorted(
        (p for p in poses if p["categories"][0] == cat), key=lambda p: p["id"]
    ) for cat in ALLOCATION}

    existing_ids: list[str] = []
    if extend:
        existing_ids = json.loads(SUBSET_PATH.read_text())["poses"]
    by_id = {p["id"]: p for p in poses}

    state = {
        "harsh_needed": MIN_HARSH,
        "dark_needed": MIN_DARK,
        "mobility_needed": 3,
        "family_counts_needed": set(FAMILY_COUNTS_NEEDED),
    }
    selected: dict[str, list[dict]] = {}
    for cat, quota in ALLOCATION.items():
        # Frozen base when extending: stored order, no re-selection.
        cat_sel: list[dict] = [by_id[i] for i in existing_ids
                               if by_id[i]["categories"][0] == cat]
        for p in cat_sel:
            lights = set(p["light_conditions"])
            if "harsh_overhead" in lights:
                state["harsh_needed"] -= 1
            if lights & DARK_LIGHTS:
                state["dark_needed"] -= 1
            if set(p["accessibility"]) & MOBILITY_TAGS:
                state["mobility_needed"] -= 1
            if cat == "family":
                state["family_counts_needed"].discard(p["subject_count"])
        quota = len(cat_sel) + EXTEND_ALLOCATION[cat] if extend else quota
        pool = [p for p in by_category[cat] if p not in cat_sel]
        while len(cat_sel) < quota:
            best = None
            best_score = None
            for p in pool:
                s = score(p, state, cat_sel)
                if s is None:
                    continue
                # deterministic: strictly-greater wins, so ties keep the
                # earliest ULID (pool is id-sorted)
                if best_score is None or s > best_score:
                    best, best_score = p, s
            if best is None:
                sys.exit(f"error: cannot fill {cat} quota under the location cap")
            pool.remove(best)
            cat_sel.append(best)
            lights = set(best["light_conditions"])
            if "harsh_overhead" in lights:
                state["harsh_needed"] -= 1
            if lights & DARK_LIGHTS:
                state["dark_needed"] -= 1
            if set(best["accessibility"]) & MOBILITY_TAGS:
                state["mobility_needed"] -= 1
            if cat == "family":
                state["family_counts_needed"].discard(best["subject_count"])
        selected[cat] = cat_sel
    return selected


def main() -> int:
    extend = "--extend" in sys.argv[1:]
    selected = select(extend)
    flat = [p for cat_sel in selected.values() for p in cat_sel]
    expected = sum(ALLOCATION.values()) + (sum(EXTEND_ALLOCATION.values()) if extend else 0)

    # Assert every stratification target; fail loudly rather than emit a
    # subset that gives a false read on UI coverage.
    harsh = sum(1 for p in flat if "harsh_overhead" in p["light_conditions"])
    dark = sum(1 for p in flat if set(p["light_conditions"]) & DARK_LIGHTS)
    mobility = sum(1 for p in flat if set(p["accessibility"]) & MOBILITY_TAGS)
    family_counts = {p["subject_count"] for p in selected["family"]}
    problems = []
    if len(flat) != expected:
        problems.append(f"selected {len(flat)} poses, expected {expected}")
    if harsh < MIN_HARSH:
        problems.append(f"only {harsh} harsh_overhead poses (need >= {MIN_HARSH})")
    if dark < MIN_DARK:
        problems.append(f"only {dark} blue/night_flash poses (need >= {MIN_DARK})")
    if mobility < 3:
        problems.append(f"only {mobility} mobility-tagged poses (need >= 3)")
    if not FAMILY_COUNTS_NEEDED <= family_counts:
        problems.append(f"family subject counts {sorted(family_counts)} missing "
                        f"{sorted(FAMILY_COUNTS_NEEDED - family_counts)}")
    for cat, cat_sel in selected.items():
        locs: dict[str, int] = {}
        for p in cat_sel:
            for loc in p["location_types"]:
                locs[loc] = locs.get(loc, 0) + 1
        for loc, n in locs.items():
            if n > MAX_PER_LOCATION:
                problems.append(f"{cat}: {n} poses share location '{loc}'")
    if problems:
        for p in problems:
            print(f"STRATIFICATION FAILURE: {p}", file=sys.stderr)
        return 1

    print(f"{'id':<26} {'category':<10} {'subj':>4} {'orient':<10} light")
    for cat, cat_sel in selected.items():
        for p in cat_sel:
            print(f"{p['id']:<26} {cat:<10} {p['subject_count']:>4} "
                  f"{p['orientation']:<10} {','.join(p['light_conditions'])}")

    horizontal = sum(1 for p in flat if p["orientation"] == "horizontal")
    print(f"\nTargets: harsh_overhead={harsh} (>=6), blue/night_flash={dark} (>=6), "
          f"mobility-tagged={mobility} (>=3), horizontal={horizontal}, "
          f"family subject counts={sorted(family_counts)}")

    DIST_DIR.mkdir(exist_ok=True)
    SUBSET_PATH.write_text(json.dumps({
        "count": len(flat),
        "allocation": {cat: len(cat_sel) for cat, cat_sel in selected.items()},
        "poses": [p["id"] for p in flat],
    }, indent=2) + "\n")
    print(f"Wrote {SUBSET_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
