"""Seasonal tagging and publish windows (config/pinterest_seasons.yaml).

A pose's season is derived from its own text (slug, instructions, prompts),
its location type and its source shoot name by weighted keyword scoring,
unless an explicit override is configured. Windows are month-day ranges
that may wrap the year end.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .catalog import Pose


@dataclass
class SeasonTag:
    season: str
    score: int = 0
    hits: list[str] = field(default_factory=list)
    source: str = "derived"       # derived | override


def _text_of(pose: Pose, shoot: str | None) -> str:
    rec = pose.record
    bits = [pose.slug.replace("-", " "), shoot or ""]
    bits += rec.get("instructions") or []
    bits += [p.get("text", "") for p in rec.get("prompts") or []]
    bits += rec.get("location_types") or []
    bits += rec.get("light_conditions") or []
    return " ".join(bits).lower()


def derive_season(pose: Pose, cfg: dict, shoot: str | None = None) -> SeasonTag:
    override = (cfg.get("overrides") or {}).get(pose.id)
    if override:
        return SeasonTag(override, source="override")
    text = _text_of(pose, shoot)
    best = SeasonTag("none")
    for season, words in (cfg.get("keywords") or {}).items():
        score, hits = 0, []
        for word, weight in words.items():
            n = len(re.findall(r"\b" + re.escape(word.lower()) + r"\b", text))
            if n:
                score += int(weight) * n
                hits.append(f"{word}x{n}" if n > 1 else word)
        if score >= int(cfg.get("threshold", 3)) and score > best.score:
            best = SeasonTag(season, score, hits)
    return best


def _md(s: str) -> tuple[int, int]:
    m, d = s.split("-")
    return int(m), int(d)


def in_window(day: date, window: dict | None) -> bool:
    if not window:
        return True
    start, end = _md(window["from"]), _md(window["to"])
    md = (day.month, day.day)
    if start <= end:
        return start <= md <= end
    return md >= start or md <= end  # wraps the year


def window_opens_within(window: dict | None, start: date, days: int) -> bool:
    """Does the window contain any day in [start, start + days]?"""
    if not window:
        return True
    return any(in_window(start + timedelta(days=i), window) for i in range(days + 1))


def season_allows(season: str, day: date, cfg: dict) -> bool:
    return in_window(day, (cfg.get("windows") or {}).get(season))
