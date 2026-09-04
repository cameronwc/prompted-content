"""state/pinterest_manifest.json — every generated pin, ever."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import MANIFEST_PATH

VERSION = 1


def load(path: Path = MANIFEST_PATH) -> dict:
    if path.is_file():
        data = json.loads(path.read_text())
        if data.get("version") != VERSION:
            raise SystemExit(f"error: manifest {path} has version {data.get('version')}, "
                             f"expected {VERSION}")
        return data
    return {"version": VERSION, "pins": {}}


def save(manifest: dict, path: Path = MANIFEST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {"version": VERSION,
               "pins": dict(sorted(manifest["pins"].items()))}
    path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n")


def scheduled_times(manifest: dict) -> list[datetime]:
    return [datetime.fromisoformat(p["scheduled_at"]) for p in manifest["pins"].values()
            if p.get("scheduled_at")]
