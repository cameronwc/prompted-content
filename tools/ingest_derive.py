#!/usr/bin/env python3
"""Derive the solar light band and gear for each cluster candidate.

Location comes from the shoot manifest, overridden per local-time range by
the manifest's optional `overrides` list, and per cluster by a
`location_override` set at review time (--set-location CLUSTER NAME picks
a saved location from locations.yaml). Elevation is computed with the NOAA
math in solar.py and mapped through taxonomy/light_bands.json.

Rules applied:
  - flash fired      -> night_flash, no solar band (taxonomy rule 5)
  - band 'night'     -> no solar band (after dark without flash)
  - no timestamp     -> no band, flagged for manual assignment; never guessed
  - backlit / open_shade are never inferred: photographer-supplied only

Writes _derived.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_common import load_locations, load_manifest, read_step, shoot_dir, write_step  # noqa: E402
from light_rules import SOLAR_ORDER  # noqa: E402
from solar import band_for_elevation, solar_position  # noqa: E402


def location_for(manifest: dict, cluster: dict, local_iso: str | None) -> dict:
    if cluster.get("location_override"):
        return cluster["location_override"]
    if local_iso:
        local = datetime.fromisoformat(local_iso)
        for override in manifest.get("overrides") or []:
            start = time.fromisoformat(override["from"])
            end = time.fromisoformat(override["to"])
            if start <= local.time() <= end:
                return override["location"]
    return manifest["location"]


def set_location(shoot: str, cluster_id: str, name: str) -> int:
    match = next((loc for loc in load_locations()
                  if loc["name"].lower() == name.lower()), None)
    if not match:
        sys.exit(f"error: no saved location named '{name}' in locations.yaml")
    path = shoot_dir(shoot) / "_clusters.json"
    data = json.loads(path.read_text())
    cluster = next((c for c in data["clusters"] if c["id"] == cluster_id), None)
    if not cluster:
        sys.exit(f"error: no cluster '{cluster_id}' in _clusters.json")
    cluster["location_override"] = match
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"{cluster_id}: location override -> {match['name']} "
          f"(re-run ingest-derive to apply)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shoot")
    parser.add_argument("--set-location", nargs=2, metavar=("CLUSTER", "NAME"),
                        help="Override one cluster's location with a saved location")
    args = parser.parse_args()

    if args.set_location:
        return set_location(args.shoot, *args.set_location)

    manifest = load_manifest(args.shoot)
    scan = {f["file"]: f for f in read_step(args.shoot, "_scan.json")["frames"]}
    clusters = read_step(args.shoot, "_clusters.json")["clusters"]

    candidates = []
    manual = []
    for cluster in clusters:
        frame = scan[cluster["candidate"]]
        location = location_for(manifest, cluster, frame["local"])

        elevation = azimuth = band = None
        light_conditions: list[str] = []
        if frame["flash_fired"]:
            light_conditions = ["night_flash"]
        elif frame["utc"]:
            elevation, azimuth = solar_position(
                location["lat"], location["lon"],
                datetime.fromisoformat(frame["utc"]))
            band = band_for_elevation(elevation)
            if band in SOLAR_ORDER:
                light_conditions = [band]
            # 'night' without flash: no band, nothing to tag
        else:
            manual.append(cluster["id"])

        focal = frame["focal_35mm"] or frame["focal_mm"]
        candidates.append({
            "cluster": cluster["id"],
            "file": frame["file"],
            "location": location["name"],
            "location_type": location["location_type"],
            "utc": frame["utc"],
            "solar_elevation": round(elevation, 2) if elevation is not None else None,
            "solar_azimuth": round(azimuth, 1) if azimuth is not None else None,
            "band": band,
            "light_conditions": light_conditions,
            "needs_manual_band": frame["utc"] is None and not frame["flash_fired"],
            "gear": {
                "focal_mm": [int(focal), int(focal)] if focal else None,
                "aperture": f"f/{frame['f_number']:g}" if frame["f_number"] else None,
                "needs_reflector": False,  # human-set; default stays false
            },
        })

    path = write_step(args.shoot, "_derived.json", {"candidates": candidates})
    print(f"Derived light bands for {len(candidates)} candidates -> {path.name}")
    for c in candidates:
        elev = (f"{c['solar_elevation']:6.2f} deg" if c["solar_elevation"] is not None
                else "     --    ")
        print(f"  {c['cluster']} {c['file']}: elev {elev}  "
              f"band {c['band'] or '-'}  tags {c['light_conditions']}  "
              f"gear {c['gear']['focal_mm']} {c['gear']['aperture']}")
    if manual:
        print(f"  needs manual band (no timestamp): {', '.join(manual)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
