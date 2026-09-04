"""Pose records for pin generation, read through the repo's catalog loader."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from common import POSES_DIR, iter_pose_dirs, load_pose, load_taxonomy


@dataclass
class Pose:
    id: str
    slug: str
    dir: Path
    record: dict

    @property
    def categories(self) -> list[str]:
        return list(self.record.get("categories") or [])

    @property
    def primary_category(self) -> str:
        return self.categories[0] if self.categories else "default"

    @property
    def image_source(self) -> str:
        return self.record.get("image_source", "synthetic")

    @property
    def detail_path(self) -> Path:
        return self.dir / self.record["image"]["detail"]

    @property
    def image_filenames(self) -> list[str]:
        img = self.record["image"]
        return [img["thumb"], img["detail"]]

    @property
    def prompts(self) -> list[dict]:
        return list(self.record.get("prompts") or [])

    @property
    def primary_prompt(self) -> str:
        # The nervous_client line is the product invariant and reads best on
        # a pin; fall back to the first prompt.
        for p in self.prompts:
            if p.get("tone") == "nervous_client":
                return p["text"]
        return self.prompts[0]["text"] if self.prompts else ""

    @property
    def name(self) -> str:
        return self.slug.replace("-", " ").strip().title()


@dataclass
class PromptText:
    """One unique verbal prompt, with the poses it appears on."""
    text: str
    tone: str
    pose_ids: list[str] = field(default_factory=list)
    category: str = "default"

    @property
    def id(self) -> str:
        return "text:" + hashlib.sha1(self.text.strip().encode("utf-8")).hexdigest()[:12]


def load_poses(poses_dir: Path | None = None, include_retired: bool = False) -> list[Pose]:
    dirs = iter_pose_dirs() if poses_dir is None else sorted(
        p for p in poses_dir.iterdir() if p.is_dir())
    poses = []
    for d in dirs:
        rec = load_pose(d)
        if not include_retired and rec.get("status") != "active":
            continue
        poses.append(Pose(id=rec["id"], slug=rec["slug"], dir=d, record=rec))
    return poses


def unique_prompts(poses: list[Pose]) -> list[PromptText]:
    """One entry per distinct prompt text (whitespace-normalised), sorted by id."""
    seen: dict[str, PromptText] = {}
    for pose in poses:
        for p in pose.prompts:
            text = " ".join(p["text"].split())
            entry = seen.get(text)
            if entry is None:
                entry = PromptText(text=text, tone=p.get("tone", ""),
                                   category=pose.primary_category)
                seen[text] = entry
            entry.pose_ids.append(pose.id)
    return sorted(seen.values(), key=lambda e: e.id)


def taxonomy_display() -> dict[str, dict[str, str]]:
    """{taxonomy_name: {id: display}}"""
    return {name: {e["id"]: e["display"] for e in entries}
            for name, entries in load_taxonomy().items()}


__all__ = ["POSES_DIR", "Pose", "PromptText", "load_poses", "unique_prompts",
           "taxonomy_display"]
