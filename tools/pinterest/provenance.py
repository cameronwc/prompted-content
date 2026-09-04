"""Pose id -> (shoot, source filename), derived from the ingest pipeline's
draft records in inbox/<shoot>/_drafts/*.yaml (`_ingest.file`, `pose_dir`).

inbox/ is gitignored, so `pins scan-rights` also writes a checked-in
snapshot, state/pinterest_provenance.json, which is merged with whatever
live drafts exist. The rights gate and the shoot-diversity constraint both
read the merged map; the gate additionally relies on excluded_pose_ids.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from common import REPO_ROOT
from ingest_common import ARCHIVE_DIR, INBOX_DIR

SNAPSHOT_PATH = REPO_ROOT / "state" / "pinterest_provenance.json"


@dataclass(frozen=True)
class Provenance:
    shoot: str
    source_file: str


def load_snapshot(path: Path = SNAPSHOT_PATH) -> dict[str, Provenance]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    return {pid: Provenance(v["shoot"], v.get("source_file", ""))
            for pid, v in data.get("poses", {}).items()}


def save_snapshot(prov: dict[str, Provenance], path: Path = SNAPSHOT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "poses": {pid: {"shoot": p.shoot, "source_file": p.source_file}
                                       for pid, p in sorted(prov.items())}}
    path.write_text(json.dumps(payload, indent=2) + "\n")


def load_provenance(inbox_dir: Path = INBOX_DIR, snapshot: Path = SNAPSHOT_PATH,
                    ) -> dict[str, Provenance]:
    """Snapshot first, live drafts override (they are the source of truth)."""
    out = load_snapshot(snapshot)
    out.update(load_drafts(inbox_dir))
    return out


def load_drafts(inbox_dir: Path = INBOX_DIR) -> dict[str, Provenance]:
    out: dict[str, Provenance] = {}
    if not inbox_dir.is_dir():
        return out
    for drafts_dir in sorted(inbox_dir.glob("*/_drafts")):
        shoot = drafts_dir.parent.name
        for draft_path in sorted(drafts_dir.glob("*.yaml")):
            try:
                draft = yaml.safe_load(draft_path.read_text()) or {}
            except yaml.YAMLError:
                continue
            ing = draft.get("_ingest") or {}
            pose_dir = ing.get("pose_dir")
            if not pose_dir:
                continue
            pose_id = pose_dir.rstrip("/").split("/")[-1]
            out[pose_id] = Provenance(shoot=shoot, source_file=ing.get("file") or "")
    return out


def shoot_source_files(shoot: str) -> list[str]:
    """Every source filename known for a shoot (inbox + archive)."""
    names: set[str] = set()
    for base in (INBOX_DIR / shoot, ARCHIVE_DIR / shoot):
        if base.is_dir():
            names.update(p.name for p in base.iterdir()
                         if p.is_file() and not p.name.startswith("_"))
    return sorted(names)


def known_shoots() -> list[str]:
    shoots: set[str] = set()
    for base in (INBOX_DIR, ARCHIVE_DIR):
        if base.is_dir():
            shoots.update(p.name for p in base.iterdir() if p.is_dir())
    return sorted(shoots)
