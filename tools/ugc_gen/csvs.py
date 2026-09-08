"""captions.csv and schedule.csv writers for a `ugc generate` run.

`first_comment` and the per-category hashtag bank are reused verbatim from
tools/reels_gen/csvs.py (`FIRST_COMMENT`, `hashtags_for`) -- same app, same
pinned-comment pitch and category tags, just a different video shape.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from reels_gen.csvs import FIRST_COMMENT, hashtags_for
from reels_gen.textfx import quote

LINK = ("https://cooperindustries.cc/prompted/marketing/"
       "?utm_source=ugc&utm_medium=video&utm_campaign=ugc")


@dataclass
class VideoRecord:
    file: str
    hook: str
    hook_slug: str
    pose_slug: str
    prompt: str
    reaction_file: str
    image_source: str  # "photo" | "ai" -- the POSE's source (the reaction is always AI)
    category: str
    light_conditions: tuple[str, ...] = ()

    @property
    def ai(self) -> bool:
        return self.image_source == "ai"


def ai_disclosure_for(rec: VideoRecord) -> str:
    if rec.ai:
        return "Reaction is AI-generated; posing reference is AI-generated."
    return "Reaction is AI-generated."


def caption_for(rec: VideoRecord) -> str:
    """The hook line, one plain sentence quoting the prompt, then "Link in
    bio."."""
    sentence = f"The prompt: {quote(rec.prompt)}"
    if not sentence.endswith((".", "!", "?", "”")):
        sentence += "."
    return " ".join([rec.hook, sentence, "Link in bio."])


def write_captions(records: list[VideoRecord], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "hook", "pose_slug", "prompt", "reaction_file", "caption",
                   "hashtags", "first_comment", "ai_disclosure", "link"])
        for rec in records:
            tags = hashtags_for(rec.category, rec.light_conditions)
            w.writerow([rec.file, rec.hook, rec.pose_slug, rec.prompt, rec.reaction_file,
                       caption_for(rec), " ".join(tags), FIRST_COMMENT, ai_disclosure_for(rec), LINK])
    return out


def build_schedule_order(records: list[VideoRecord]) -> list[VideoRecord]:
    """Order `records` so no two consecutive entries share a hook or a pose.
    The category-alternation rule in reels_gen/csvs.py has a documented
    "while supply lasts" escape hatch for real photos; this constraint has
    no such escape -- it only yields (first on the hook match, then on the
    pose match) when literally every remaining item would violate it,
    which cannot happen for the (hook, action) pairs this composer
    produces without an enormous, all-identical run. Deterministic tie-break."""
    pool = list(records)
    order: list[VideoRecord] = []
    last_hook: str | None = None
    last_pose: str | None = None
    while pool:
        candidates = [r for r in pool if r.hook_slug != last_hook and r.pose_slug != last_pose]
        if not candidates:
            candidates = [r for r in pool if r.hook_slug != last_hook]
        if not candidates:
            candidates = [r for r in pool if r.pose_slug != last_pose]
        if not candidates:
            candidates = pool
        pick = min(candidates, key=lambda r: (r.hook_slug, r.pose_slug, r.file))
        order.append(pick)
        pool.remove(pick)
        last_hook, last_pose = pick.hook_slug, pick.pose_slug
    return order


def write_schedule(records: list[VideoRecord], out: Path, start: date) -> Path:
    ordered = build_schedule_order(records)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "file", "hook", "pose_slug"])
        for i, rec in enumerate(ordered):
            post_date = start + timedelta(days=i)
            w.writerow([post_date.isoformat(), rec.file, rec.hook, rec.pose_slug])
    return out
