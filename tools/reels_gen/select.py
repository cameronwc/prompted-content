"""Pose eligibility and prompt selection for the reels renderer.

Rights gating here is the same object tools/pins.py uses
(pinterest.rights.RightsGate, built from config/pinterest_exclusions.yaml).
`guard_renderable` is the hard refusal: it is called again, independently
of `eligible_poses`' own filtering, immediately before any pixel of a pose
is read -- an excluded pose must never reach the renderer even if a caller
skips `eligible_poses` entirely.
"""
from __future__ import annotations

from dataclasses import dataclass

from pinterest.catalog import Pose
from pinterest.rights import RightsGate

MAX_PROMPT_CHARS = 110
CATEGORIES = ("family", "couples", "engagement", "maternity", "senior")
TONES = ("nervous_client", "playful", "calm", "romantic")
RENDERABLE_SOURCES = ("photo", "ai")  # synthetic placeholder tiles are never reels


@dataclass
class Selection:
    pose: Pose
    category: str
    tone: str
    prompt: str


def guard_renderable(pose: Pose, gate: RightsGate) -> None:
    """Hard refusal: raises RightsViolation if `pose` is excluded. Never
    trust dist/guides_data.json (or any other pre-filtered extract) alone --
    this re-checks against config/pinterest_exclusions.yaml every time."""
    gate.check(pose)


def choose_prompt(pose: Pose, forced_tone: str | None) -> tuple[str, str] | None:
    """(text, tone) under MAX_PROMPT_CHARS, or None if nothing qualifies.

    Default (forced_tone is None): the nervous_client prompt if it is
    <=110 chars; otherwise the shortest other prompt that is <=110 chars.
    With forced_tone: only that tone's prompt is considered.
    """
    prompts = [(" ".join(p["text"].split()), p.get("tone", ""))
               for p in pose.prompts if p.get("text")]
    if forced_tone:
        for text, tone in prompts:
            if tone == forced_tone:
                return (text, tone) if len(text) <= MAX_PROMPT_CHARS else None
        return None
    nervous = next((text for text, tone in prompts if tone == "nervous_client"), None)
    if nervous is not None and len(nervous) <= MAX_PROMPT_CHARS:
        return nervous, "nervous_client"
    fits = [(text, tone) for text, tone in prompts if len(text) <= MAX_PROMPT_CHARS]
    if not fits:
        return None
    return min(fits, key=lambda tt: (len(tt[0]), tt[1]))


def category_for(pose: Pose, requested: str | None) -> str:
    """The category used for the filename/palette: the requested filter
    category when the pose carries it, otherwise the pose's primary
    (first-listed) category."""
    if requested and requested in pose.categories:
        return requested
    return pose.primary_category


def eligible_poses(poses: list[Pose], gate: RightsGate, *, category: str | None = None,
                   tone: str | None = None, slug: str | None = None,
                   ) -> tuple[list[Selection], list[tuple[Pose, str]]]:
    """(selections, skipped) where skipped is [(pose, reason)]. Rights-
    excluded poses are dropped silently here (they are not "skipped for a
    reason", they are simply never candidates) but every other filtered-out
    or unfit pose is reported so `reels generate` can print it."""
    selections: list[Selection] = []
    skipped: list[tuple[Pose, str]] = []
    for pose in poses:
        if gate.is_excluded(pose):
            continue
        if pose.image_source not in RENDERABLE_SOURCES:
            continue
        if slug and pose.slug != slug:
            continue
        if category and category not in pose.categories:
            continue
        chosen = choose_prompt(pose, tone)
        if chosen is None:
            reason = (f"no {tone} prompt <={MAX_PROMPT_CHARS} chars" if tone
                     else f"no prompt <={MAX_PROMPT_CHARS} chars")
            skipped.append((pose, reason))
            continue
        text, ptone = chosen
        selections.append(Selection(pose=pose, category=category_for(pose, category),
                                    tone=ptone, prompt=text))
    return selections, skipped
