"""The hook bank (config/ugc_hooks.yaml): 30 opening lines for the UGC
reaction video composer, each carrying the emotion a matching reaction clip
should show and, optionally, the pose category a matching app-action clip
should carry."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from reels_gen.select import CATEGORIES

from .textutil import slugify

EMOTIONS = ("skeptical", "confused", "impressed", "laughing", "relieved", "neutral")
DEFAULT_PATH = Path("config/ugc_hooks.yaml")
EXPECTED_COUNT = 30


class HookConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Hook:
    index: int
    text: str
    emotion: str
    category: str | None = None

    @property
    def slug(self) -> str:
        return slugify(self.text)


def load_hooks(path: Path = DEFAULT_PATH) -> list[Hook]:
    if not path.is_file():
        raise HookConfigError(f"missing hook bank {path}")
    data = yaml.safe_load(path.read_text()) or {}
    entries = data.get("hooks")
    if not isinstance(entries, list):
        raise HookConfigError(f"{path}: 'hooks' must be a list")

    hooks: list[Hook] = []
    seen_text: set[str] = set()
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or not str(e.get("text") or "").strip():
            raise HookConfigError(f"{path}: hook {i} is missing text")
        text = " ".join(str(e["text"]).split())
        emotion = e.get("emotion")
        if emotion not in EMOTIONS:
            raise HookConfigError(
                f"{path}: hook {i} ({text!r}) has invalid emotion {emotion!r}; "
                f"must be one of {EMOTIONS}")
        category = e.get("category")
        if category is not None and category not in CATEGORIES:
            raise HookConfigError(
                f"{path}: hook {i} ({text!r}) has invalid category {category!r}; "
                f"must be one of {CATEGORIES} or omitted")
        if text in seen_text:
            raise HookConfigError(f"{path}: duplicate hook text {text!r}")
        seen_text.add(text)
        hooks.append(Hook(index=i, text=text, emotion=emotion, category=category))

    if len(hooks) != EXPECTED_COUNT:
        raise HookConfigError(f"{path}: expected {EXPECTED_COUNT} hooks, found {len(hooks)}")
    return hooks
