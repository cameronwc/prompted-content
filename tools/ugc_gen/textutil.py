"""Filename-safe slugs for hook text (config/ugc_hooks.yaml has no slug
field of its own -- filenames and schedule keys derive it from `text`)."""
from __future__ import annotations

import re

_NON_WORD = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 40) -> str:
    s = text.lower().replace("’", "").replace("‘", "").replace("'", "").replace('"', "")
    s = _NON_WORD.sub("-", s).strip("-")
    if len(s) > max_len:
        head = s[:max_len]
        s = head.rsplit("-", 1)[0] if "-" in head else head
    return s or "hook"
