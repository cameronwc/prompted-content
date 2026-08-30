"""Light-condition group rules: validation and deterministic resolution.

The taxonomy (taxonomy/light_conditions.yaml) groups light ids:

  solar    — mutually exclusive solar-elevation bands. An outdoor daytime
             pose carries exactly one; indoor or after-dark poses carry none.
  sky      — zero or one (`overcast`).
  modifier — photographer-controlled, zero to two.

`light_condition_errors` enforces the group rules (used by validate.py);
`resolve_light_conditions` reduces any tag set to a compliant one (used by
the retag migration and the seed generator). Resolution consumes no RNG, so
seeding reproduces the retag byte-for-byte and pose ULIDs are unaffected.
"""
from __future__ import annotations

import zlib

from common import load_taxonomy

INDOOR_LOCATIONS = {"studio", "home"}

# Solar bands in taxonomy order (used for output ordering) and in the
# priority order used when a legacy record carries several: the specific,
# deliberately-chosen bands outrank the ones the daylight-band migration
# derived wholesale (soft_low from golden; mid from overcast/shade/harsh).
SOLAR_ORDER = ["golden", "blue", "soft_low", "mid", "harsh_overhead"]
SOLAR_PRIORITY = ["golden", "blue", "harsh_overhead", "soft_low", "mid"]
DERIVED_FROM = {
    "soft_low": {"golden"},
    "mid": {"open_shade", "overcast", "harsh_overhead"},
}
MODIFIER_PRIORITY = ["night_flash", "indoor_window", "backlit", "open_shade"]

# A slug that names a time of day states the pose's intent outright.
SLUG_BAND_HINTS = {
    "golden": "golden",
    "sunset": "golden",
    "sunrise": "golden",
    "dusk": "blue",
    "dawn": "blue",
    "midday": "mid",
    "noon": "mid",
}

GROUP_LIMITS = {"solar": 1, "sky": 1, "modifier": 2}
MAX_LIGHT_TAGS = 3


def light_groups(taxonomy: dict[str, list[dict]] | None = None) -> dict[str, dict]:
    """{light id: taxonomy entry} for taxonomy/light_conditions.yaml."""
    taxonomy = taxonomy or load_taxonomy()
    return {e["id"]: e for e in taxonomy["light_conditions"]}


def is_outdoor(location_types: list[str]) -> bool:
    """True when the pose can be shot under open sky at all."""
    return any(loc not in INDOOR_LOCATIONS for loc in location_types)


def light_condition_errors(
    light_conditions: list[str],
    location_types: list[str],
    groups: dict[str, dict],
) -> list[str]:
    """Group-rule violations for one pose's light tags. Unknown ids are
    ignored here; referential integrity is reported separately."""
    errors: list[str] = []
    tags = [t for t in light_conditions if t in groups]
    by_group: dict[str, list[str]] = {}
    for tag in tags:
        by_group.setdefault(groups[tag]["group"], []).append(tag)

    for group, limit in GROUP_LIMITS.items():
        got = by_group.get(group, [])
        if len(got) > limit:
            errors.append(
                f"light_conditions: {len(got)} '{group}' tags ({got}); "
                f"at most {limit} allowed"
            )
    if len(tags) > MAX_LIGHT_TAGS:
        errors.append(
            f"light_conditions: {len(tags)} light tags; at most {MAX_LIGHT_TAGS} allowed"
        )

    tag_set = set(tags)
    for tag in tags:
        entry = groups[tag]
        clashes = sorted(tag_set & set(entry.get("excludes", [])))
        for other in clashes:
            errors.append(f"light_conditions: '{tag}' excludes '{other}'")
        for group in entry.get("excludes_groups", []):
            for other in by_group.get(group, []):
                errors.append(
                    f"light_conditions: '{tag}' excludes all '{group}' tags "
                    f"(found '{other}')"
                )

    # An outdoor daytime pose must carry exactly one solar band. night_flash
    # and indoor_window mark the pose as after-dark / indoor respectively.
    daytime = not (tag_set & {"night_flash", "indoor_window"})
    if is_outdoor(location_types) and daytime and not by_group.get("solar"):
        errors.append(
            "light_conditions: outdoor daytime pose has no solar band "
            f"(exactly one of {SOLAR_ORDER} required)"
        )
    return errors


def resolve_light_conditions(
    light_conditions: list[str],
    location_types: list[str],
    slug: str = "",
) -> list[str]:
    """Reduce an arbitrary tag set to one that satisfies the group rules.

    Deterministic and RNG-free. Keeps the single most appropriate solar
    band (slug hint first, then the deliberately-chosen band over the
    migration-derived ones), at most one sky tag, and the two highest-
    priority modifiers, capped at MAX_LIGHT_TAGS total.
    """
    tag_set = set(light_conditions)
    modifiers = [t for t in MODIFIER_PRIORITY if t in tag_set][:2]
    # night_flash / indoor_window mark after-dark / indoor: no solar or sky.
    if tag_set & {"night_flash", "indoor_window"}:
        return modifiers

    present = [t for t in SOLAR_ORDER if t in tag_set]
    # Prefer bands that were chosen for the pose over ones the daylight-band
    # migration derived from other tags; fall back to whatever is present.
    chosen = [t for t in present if not (DERIVED_FROM.get(t, set()) & tag_set)]
    candidates = chosen or present
    if not candidates and is_outdoor(location_types):
        # No elevation information at all: infer the natural band.
        if tag_set & {"overcast", "open_shade"}:
            candidates = ["mid"]
        elif "backlit" in tag_set:
            candidates = ["golden"]

    hinted = next((b for word, b in SLUG_BAND_HINTS.items() if word in slug), None)
    if hinted and (hinted in candidates or not candidates):
        band = hinted
    else:
        band = next((t for t in SOLAR_PRIORITY if t in candidates), None)
        # The daylight-band migration asserted every golden pose also works
        # in soft_low, so for a pose whose slug does not insist on golden
        # the two bands are equally true of it. Splitting them by a stable
        # slug hash keeps the soft_low band populated — otherwise the
        # "works in this light right now" filter would match zero poses
        # for the entire 6-20 degree stretch of the shooting day.
        if band == "golden" and zlib.crc32(slug.encode()) % 2:
            band = "soft_low"

    # overcast excludes golden and harsh_overhead: the solar band is the
    # pose's stated intent, so it wins and overcast is dropped.
    sky = "overcast" if ("overcast" in tag_set
                         and band not in ("golden", "harsh_overhead")) else None

    resolved = ([band] if band else []) + ([sky] if sky else []) + modifiers
    while len(resolved) > MAX_LIGHT_TAGS:
        resolved.pop()  # lowest-priority modifier goes first
    return resolved
