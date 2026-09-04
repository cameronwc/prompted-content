"""captions.csv and schedule.csv writers for a reels generate run."""
from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .textfx import quote

CATEGORY_HASHTAGS = {
    "family": ["#familyphotographer", "#familyphotos", "#familyposing", "#familyphotography"],
    "couples": ["#couplesposing", "#coupledphotography", "#couplegoals", "#couplesphotography"],
    "engagement": ["#engagementphotos", "#engagementphotography", "#engagementposing", "#engaged"],
    "maternity": ["#maternityphotography", "#maternityposing", "#babybump", "#momtobe"],
    "senior": ["#seniorphotography", "#seniorportraits", "#seniorposing", "#seniorpics"],
}
COMMON_HASHTAGS = ["#posingprompts", "#photographytips", "#photographerlife", "#photoideas",
                   "#photographyguide"]
GOLDEN_HASHTAG = "#goldenhour"
MIN_HASHTAGS, MAX_HASHTAGS = 8, 12

# No link in the caption (platforms strip them) -- it lives in its own
# column. Platform is fixed to "reels" per spec, regardless of which app the
# file is ultimately posted to.
LINK = ("https://cooperindustries.cc/prompted/marketing/"
       "?utm_source=reels&utm_medium=video&utm_campaign=prompts")


@dataclass
class VideoRecord:
    file: str
    slug: str
    category: str
    tone: str
    prompt: str
    title: str
    image_source: str  # "photo" | "ai"
    light_conditions: tuple[str, ...] = ()
    credit: str | None = None
    steps: tuple[str, ...] = ()  # up to MAX_STEPS instructions, verbatim, in order
    appshot: str = "missing"     # "yes" | "missing" -- whether an app-screen segment was rendered

    @property
    def ai(self) -> bool:
        return self.image_source == "ai"


def hashtags_for(category: str, light_conditions) -> list[str]:
    tags = list(CATEGORY_HASHTAGS.get(category, [])[:4]) + list(COMMON_HASHTAGS)
    if "golden" in (light_conditions or ()):
        tags.append(GOLDEN_HASHTAG)
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    assert MIN_HASHTAGS <= len(out) <= MAX_HASHTAGS, f"{len(out)} hashtags out of 8-12 range"
    return out


MAX_CAPTION_CHARS = 2200  # Instagram's caption limit

FIRST_COMMENT = ("Every pose in this clip is in Prompted, free on the App Store. "
                 "Search “Prompted” or use the link in our bio.")


def _setup_sentence(steps: tuple[str, ...]) -> str:
    """"Setup: 1. ... 2. ... 3. ..." -- a short numbered sentence list, or
    "" if the pose carries no instructions."""
    if not steps:
        return ""
    return "Setup: " + " ".join(f"{i}. {s}" for i, s in enumerate(steps, start=1))


def caption_for(rec: VideoRecord) -> str:
    """The quoted prompt, then the numbered setup steps, then the closing
    brand line, "Link in bio.", and (AI-sourced poses) the AI-disclosure
    sentence. Stays under MAX_CAPTION_CHARS: if it would not, the
    setup-steps sentence is shortened (never the prompt, never the closing
    line)."""
    quoted = quote(rec.prompt)
    tail = "From Prompted, the posing app that is only a posing app. Link in bio."
    if rec.ai:
        tail += " Reference image is AI-generated."
    setup = _setup_sentence(rec.steps)

    parts = [quoted] + ([setup] if setup else []) + [tail]
    body = " ".join(parts)
    if len(body) <= MAX_CAPTION_CHARS or not setup:
        return body

    fixed = " ".join([quoted, tail])
    budget = MAX_CAPTION_CHARS - len(fixed) - 1  # -1: the space joining the shortened setup in
    if budget <= len("Setup: …"):
        return fixed
    truncated = setup[:budget]
    cut = truncated.rfind(" ")
    if cut > len("Setup:"):
        truncated = truncated[:cut]
    truncated = truncated.rstrip(".,;: ") + "…"
    return " ".join([quoted, truncated, tail])


def write_captions(records: list[VideoRecord], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "slug", "category", "tone", "prompt", "steps", "appshot",
                   "caption", "first_comment", "hashtags", "image_source", "link"])
        for rec in records:
            tags = hashtags_for(rec.category, rec.light_conditions)
            w.writerow([rec.file, rec.slug, rec.category, rec.tone, rec.prompt,
                       " | ".join(rec.steps), rec.appshot, caption_for(rec), FIRST_COMMENT,
                       " ".join(tags), rec.image_source, LINK])
    return out


def build_schedule_order(items: list[VideoRecord]) -> list[VideoRecord]:
    """Order `items` so that:
      - no two consecutive entries share a category, and
      - a real photo ("photo") shows up at least once in every run of 4
        consecutive entries, for as long as real photos remain in the pool
        (once they run out the constraint lifts -- "while they last").

    The category rule is the hard constraint: it only yields when literally
    every remaining item shares the last category. Real-photo spacing is
    best-effort within whatever the category rule leaves available -- once
    3 non-real posts have gone by, a real photo is picked if one exists
    among the category-legal candidates, but a real pick is never forced at
    the cost of repeating a category (that would just trade one violation
    for the other; a slightly longer AI-only run is the lesser deviation,
    and it can only happen as real photos are running out anyway).
    Deterministic: ties broken by (category count desc, category, slug).
    """
    pool = list(items)
    order: list[VideoRecord] = []
    last_category: str | None = None
    since_real = 0  # posts since the last real photo (0 == last post was real)
    while pool:
        non_repeat = [it for it in pool if it.category != last_category]
        candidates = non_repeat or pool
        need_real = since_real >= 3
        if need_real:
            real_candidates = [it for it in candidates if it.image_source == "photo"]
            if real_candidates:
                candidates = real_candidates
        counts = Counter(it.category for it in pool)
        pick = min(candidates, key=lambda it: (-counts[it.category], it.category, it.slug))
        order.append(pick)
        pool.remove(pick)
        last_category = pick.category
        since_real = 0 if pick.image_source == "photo" else since_real + 1
    return order


def write_schedule(items: list[VideoRecord], out: Path, start: date) -> Path:
    ordered = build_schedule_order(items)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "file", "slug", "category", "tone", "image_source"])
        for i, rec in enumerate(ordered):
            post_date = start + timedelta(days=i)
            w.writerow([post_date.isoformat(), rec.file, rec.slug, rec.category, rec.tone,
                       rec.image_source])
    return out
