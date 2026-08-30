#!/usr/bin/env python3
"""Collapse near-duplicate frames into clusters and pick a candidate each.

Perceptual-hashes (imagehash.phash) every frame that survived the quality
gates, clusters by Hamming distance (single-link: a frame joins a cluster
when it is within the threshold of any member), ranks within each cluster
by a composite quality score with sharpness weighted highest, and selects
the top frame as the cluster's candidate. Alternates are retained; nothing
is deleted.

--select <cluster-id> <filename> overrides a pick; operator overrides are
kept across re-runs as long as the chosen file still lands in the same
cluster.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import imagehash
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ingest_common import load_ingest_config, read_step, shoot_dir, write_step  # noqa: E402


def surviving_frames(quality: dict) -> list[dict]:
    return [f for f in quality["frames"] if f["status"] != "reject"]


def build_clusters(shoot_path: Path, frames: list[dict], max_distance: int) -> list[list[dict]]:
    hashes = {}
    for frame in frames:
        with Image.open(shoot_path / frame.get("use_file", frame["file"])) as im:
            hashes[frame["file"]] = imagehash.phash(im)

    clusters: list[list[dict]] = []
    for frame in frames:
        h = hashes[frame["file"]]
        home = None
        for cluster in clusters:
            if any(h - hashes[member["file"]] <= max_distance for member in cluster):
                home = cluster
                break
        if home is None:
            clusters.append([frame])
        else:
            home.append(frame)
    return clusters


def composite(frame: dict, ranges: dict, cfg: dict) -> float:
    def norm(value, key):
        lo, hi = ranges[key]
        return (value - lo) / (hi - lo) if hi > lo else 1.0

    exposure = 100 - frame["blown_pct"] - frame["crushed_pct"]
    return (cfg["weight_sharpness"] * norm(frame["sharpness"], "sharpness")
            + cfg["weight_exposure"] * norm(exposure, "exposure")
            + cfg["weight_resolution"] * norm(frame["short_edge"], "short_edge"))


def apply_select(shoot: str, cluster_id: str, filename: str) -> int:
    path = shoot_dir(shoot) / "_clusters.json"
    data = json.loads(path.read_text())
    for cluster in data["clusters"]:
        if cluster["id"] != cluster_id:
            continue
        members = [m["file"] for m in cluster["members"]]
        if filename not in members:
            sys.exit(f"error: {filename} is not in cluster {cluster_id} ({members})")
        cluster["candidate"] = filename
        cluster["operator_selected"] = True
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        print(f"{cluster_id}: candidate -> {filename} (operator override)")
        return 0
    sys.exit(f"error: no cluster '{cluster_id}' in _clusters.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shoot")
    parser.add_argument("--select", nargs=2, metavar=("CLUSTER", "FILENAME"),
                        help="Override a cluster's candidate frame")
    parser.add_argument("--threshold", type=int,
                        help="Max phash Hamming distance (default from ingest_config.yaml)")
    args = parser.parse_args()

    if args.select:
        return apply_select(args.shoot, *args.select)

    cfg = load_ingest_config()["cluster"]
    max_distance = args.threshold if args.threshold is not None else cfg["phash_hamming_max"]

    shoot_path = shoot_dir(args.shoot)
    quality = read_step(args.shoot, "_quality.json")
    frames = surviving_frames(quality)

    # Keep operator overrides from a previous run where possible.
    previous_overrides = {}
    clusters_path = shoot_path / "_clusters.json"
    if clusters_path.is_file():
        for cluster in json.loads(clusters_path.read_text()).get("clusters", []):
            if cluster.get("operator_selected"):
                previous_overrides[frozenset(m["file"] for m in cluster["members"])] = \
                    cluster["candidate"]

    grouped = build_clusters(shoot_path, frames, max_distance)
    ranges = {
        "sharpness": (min(f["sharpness"] for f in frames), max(f["sharpness"] for f in frames)),
        "exposure": (min(100 - f["blown_pct"] - f["crushed_pct"] for f in frames),
                     max(100 - f["blown_pct"] - f["crushed_pct"] for f in frames)),
        "short_edge": (min(f["short_edge"] for f in frames), max(f["short_edge"] for f in frames)),
    }

    clusters = []
    for i, members in enumerate(sorted(grouped, key=lambda c: c[0]["file"]), 1):
        ranked = sorted(members, key=lambda f: composite(f, ranges, cfg), reverse=True)
        cluster = {
            "id": f"c{i:02d}",
            "candidate": ranked[0]["file"],
            "members": [
                {"file": f["file"],
                 **({"use_file": f["use_file"]} if "use_file" in f else {}),
                 "quality": round(composite(f, ranges, cfg), 3),
                 "sharpness": f["sharpness"]}
                for f in ranked
            ],
        }
        override = previous_overrides.get(frozenset(m["file"] for m in members))
        if override:
            cluster["candidate"] = override
            cluster["operator_selected"] = True
        clusters.append(cluster)

    write_step(args.shoot, "_clusters.json", {
        "threshold": max_distance,
        "clusters": clusters,
    })

    print(f"{len(frames)} frames in -> {len(clusters)} clusters / candidates out "
          f"(threshold {max_distance})")
    for c in clusters:
        alts = [m["file"] for m in c["members"] if m["file"] != c["candidate"]]
        mark = " (operator)" if c.get("operator_selected") else ""
        print(f"  {c['id']}: {c['candidate']}{mark}"
              + (f"  alternates: {', '.join(alts)}" if alts else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
