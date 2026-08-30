#!/usr/bin/env python3
"""Create inbox/<shoot-name>/_shoot.yaml interactively.

The manifest supplies what the camera cannot: where the shoot happened and
which timezone the camera clock was set to. EXIF DateTimeOriginal carries
no zone, so a wrong timezone shifts every derived light band by hours,
silently — which is why the timezone is auto-suggested from the
coordinates (timezonefinder) but always explicitly confirmed.

Saved locations live in locations.yaml at the repo root and are offered by
name; new locations are appended there so coordinates are typed once.

A shoot that moves mid-session (beach to forest) can add an `overrides`
list afterwards — each entry applies a different location to a local-time
range — and ingest_derive also honours a per-cluster override set at
review time. Coordinates are entered directly; there is deliberately no
reverse-geocoding network dependency.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import REPO_ROOT, load_taxonomy, taxonomy_ids  # noqa: E402
from ingest_common import INBOX_DIR, load_locations, save_locations  # noqa: E402


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or (default or "")


def ask_float(prompt: str, lo: float, hi: float) -> float:
    while True:
        raw = ask(prompt)
        try:
            value = float(raw)
        except ValueError:
            print(f"  not a number: {raw!r}")
            continue
        if lo <= value <= hi:
            return value
        print(f"  out of range [{lo}, {hi}]: {value}")


def choose_location(valid_location_types: set[str]) -> dict:
    saved = load_locations()
    if saved:
        print("Saved locations:")
        for i, loc in enumerate(saved, 1):
            print(f"  {i}. {loc['name']}  ({loc['lat']}, {loc['lon']}, {loc['location_type']})")
        choice = ask("Location number, or a name for a new location")
    else:
        choice = ask("Location name (no saved locations yet)")

    if saved and choice.isdigit() and 1 <= int(choice) <= len(saved):
        return dict(saved[int(choice) - 1])
    by_name = {loc["name"].lower(): loc for loc in saved}
    if choice.lower() in by_name:
        return dict(by_name[choice.lower()])

    location = {
        "name": choice,
        "lat": ask_float("Latitude", -90, 90),
        "lon": ask_float("Longitude", -180, 180),
    }
    while True:
        location_type = ask("Location type", "beach")
        if location_type in valid_location_types:
            location["location_type"] = location_type
            break
        print(f"  must be a taxonomy/location_types.yaml id: "
              f"{sorted(valid_location_types)}")
    save_locations(load_locations() + [location])
    print(f"  saved to locations.yaml as '{location['name']}'")
    return location


def confirm_timezone(lat: float, lon: float) -> str:
    from timezonefinder import TimezoneFinder  # deferred: slow import
    suggested = TimezoneFinder().timezone_at(lat=lat, lng=lon)
    while True:
        tz = ask("Timezone (IANA name — the zone the camera clock was set to)",
                 suggested)
        if not tz:
            print("  timezone is mandatory: every derived light band depends on it")
            continue
        try:
            ZoneInfo(tz)
            return tz
        except (ZoneInfoNotFoundError, ValueError):
            print(f"  unknown IANA timezone: {tz!r}")


def main() -> int:
    default_name = sys.argv[1] if len(sys.argv) > 1 else None
    while True:
        shoot_name = ask("Shoot name (e.g. 2026-09-14-cannon-beach)", default_name)
        if re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", shoot_name):
            break
        print("  kebab-case only (it becomes a folder and archive name)")

    shoot = INBOX_DIR / shoot_name
    manifest_path = shoot / "_shoot.yaml"
    if manifest_path.is_file():
        sys.exit(f"error: {manifest_path.relative_to(REPO_ROOT)} already exists; "
                 f"edit it directly instead.")

    location = choose_location(taxonomy_ids(load_taxonomy())["location_types"])
    timezone = confirm_timezone(location["lat"], location["lon"])
    notes = ask("Notes (light, plan — optional)", "")

    manifest = {
        "shoot_name": shoot_name,
        "location": location,
        "timezone": timezone,
    }
    if notes:
        manifest["notes"] = notes

    shoot.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        "# Shoot manifest — applies to every frame in this folder.\n"
        "# Optional per-time-range location overrides (local camera time):\n"
        "#   overrides:\n"
        "#     - from: '18:30'\n"
        "#       to: '20:00'\n"
        "#       location: { name: ..., lat: ..., lon: ..., location_type: forest }\n"
        + yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)
    )
    print(f"\nWrote {manifest_path.relative_to(REPO_ROOT)}")
    print(f"Drop the Lightroom exports into {shoot.relative_to(REPO_ROOT)}/ "
          f"and run make ingest-scan SHOOT={shoot_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
