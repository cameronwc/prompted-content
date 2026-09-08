"""Hook -> reaction -> app-action clip selection for the UGC composer.

Rights gating mirrors tools/reels_gen/select.py: `load_action_clips` only
ever offers an app-action clip whose slug is present in the rights-filtered
dist/guides_data.json AND clears config/pinterest_exclusions.yaml (an
excluded slug is dropped from the pool silently, the same way
reels_gen.select.eligible_poses drops an excluded pose -- it is not a
candidate, not a reported skip-with-reason). `guard_action_clip` is the hard
refusal, mirroring reels_gen.select.guard_renderable: it is called again,
independently, immediately before any app-action clip is composited -- an
excluded clip must never reach the renderer even if a caller bypasses
`load_action_clips` entirely.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from pinterest.catalog import Pose, load_poses
from pinterest.rights import RightsGate

from .hooks import EMOTIONS, Hook

DEFAULT_GUIDES_PATH = Path("dist/guides_data.json")
DEFAULT_ACTIONS_DIR = Path("dist/actions")
DEFAULT_REACTIONS_DIR = Path("dist/reactions")


@dataclass(frozen=True)
class ReactionClip:
    path: Path
    emotion: str  # from filename, "neutral" if the word after the last dash is unrecognised


@dataclass(frozen=True)
class ActionClip:
    path: Path
    slug: str
    tone: str
    pose: Pose
    category: str  # pose.primary_category, used for hashtags


@dataclass(frozen=True)
class Selection:
    hook: Hook
    reaction: ReactionClip
    action: ActionClip
    prompt: str  # the pose's chosen-tone prompt text, verbatim


def guard_action_clip(pose: Pose, gate: RightsGate) -> None:
    """Hard refusal: raises RightsViolation if `pose` is excluded. Never
    trust dist/guides_data.json (or load_action_clips' own filtering) alone
    -- this re-checks against config/pinterest_exclusions.yaml every time,
    exactly like reels_gen.select.guard_renderable."""
    gate.check(pose)


def parse_reaction_emotion(path: Path) -> str:
    stem = path.stem
    word = stem.rsplit("-", 1)[-1].lower() if "-" in stem else ""
    return word if word in EMOTIONS else "neutral"


def load_reaction_clips(reactions_dir: Path = DEFAULT_REACTIONS_DIR) -> list[ReactionClip]:
    if not reactions_dir.is_dir():
        return []
    return [ReactionClip(path=p, emotion=parse_reaction_emotion(p))
            for p in sorted(reactions_dir.glob("*.mp4"))]


def load_action_clips(actions_dir: Path = DEFAULT_ACTIONS_DIR,
                      guides_path: Path = DEFAULT_GUIDES_PATH,
                      gate: RightsGate | None = None,
                      poses: list[Pose] | None = None,
                      ) -> tuple[list[ActionClip], list[tuple[Path, str]]]:
    """(clips, skipped): app-action clips whose slug is present in
    dist/guides_data.json AND whose pose clears `gate`. Everything else
    (bad filename, unknown slug, rights-excluded) is reported in `skipped`
    with a reason rather than raised -- `guard_action_clip` is the hard
    refusal, called again right before compositing."""
    if not guides_path.is_file():
        raise FileNotFoundError(f"missing {guides_path}")
    guides = json.loads(guides_path.read_text())
    guides_slugs = {p["slug"] for p in guides.get("poses", [])}
    poses = poses if poses is not None else load_poses()
    poses_by_slug = {p.slug: p for p in poses}

    clips: list[ActionClip] = []
    skipped: list[tuple[Path, str]] = []
    if not actions_dir.is_dir():
        return clips, skipped
    for path in sorted(actions_dir.glob("*.mp4")):
        stem = path.stem
        if "__" not in stem:
            skipped.append((path, "filename is not <slug>__<tone>.mp4"))
            continue
        slug, tone = stem.rsplit("__", 1)
        if slug not in guides_slugs:
            skipped.append((path, "slug not present in dist/guides_data.json (rights-filtered)"))
            continue
        pose = poses_by_slug.get(slug)
        if pose is None:
            skipped.append((path, "slug not found in the pose catalog"))
            continue
        if gate is not None and gate.is_excluded(pose):
            skipped.append((path, "rights-excluded (config/pinterest_exclusions.yaml)"))
            continue
        clips.append(ActionClip(path=path, slug=slug, tone=tone, pose=pose,
                                category=pose.primary_category))
    return clips, skipped


def choose_prompt_for_tone(pose: Pose, tone: str) -> str | None:
    """The pose's prompt text for `tone`, whitespace-normalised, verbatim
    otherwise -- None if the pose carries no prompt of that tone."""
    for p in pose.prompts:
        if p.get("tone") == tone and p.get("text"):
            return " ".join(p["text"].split())
    return None


def build_selections(hooks: list[Hook], reactions: list[ReactionClip],
                     actions: list[ActionClip], count: int, seed: int,
                     category: str | None = None) -> list[Selection]:
    """Seeded and reproducible: pick a hook, then a reaction whose emotion
    matches the hook's emotion (fallback: any reaction), then an app-action
    clip whose pose carries the hook's category if one is set (fallback:
    any app-action clip) -- never repeating a (hook, action) pair within the
    run. `category`, if given, restricts the hook pool to hooks tagged with
    exactly that category."""
    if not reactions:
        raise ValueError("no reaction clips available")
    if not actions:
        raise ValueError("no app-action clips available")

    pool_hooks = [h for h in hooks if category is None or h.category == category]
    if not pool_hooks:
        raise ValueError(f"no hooks in category {category!r}")

    rng = random.Random(seed)
    used_pairs: set[tuple[int, str]] = set()
    selections: list[Selection] = []
    max_pairs = len(pool_hooks) * len(actions)
    attempt_cap = max(count * 50, 500)
    attempts = 0
    while len(selections) < count and attempts < attempt_cap and len(used_pairs) < max_pairs:
        attempts += 1
        hook = rng.choice(pool_hooks)

        emotion_matches = [r for r in reactions if r.emotion == hook.emotion]
        reaction = rng.choice(emotion_matches or reactions)

        if hook.category:
            cat_matches = [a for a in actions if hook.category in a.pose.categories]
        else:
            cat_matches = []
        action = rng.choice(cat_matches or actions)

        key = (hook.index, action.path.name)
        if key in used_pairs:
            continue
        prompt = choose_prompt_for_tone(action.pose, action.tone)
        if prompt is None:
            continue  # this clip's tone has no matching prompt text on the pose; try again
        used_pairs.add(key)
        selections.append(Selection(hook=hook, reaction=reaction, action=action, prompt=prompt))

    if len(selections) < count:
        raise ValueError(f"could not find {count} distinct (hook, action) pairs "
                         f"(found {len(selections)} of at most {max_pairs} possible)")
    return selections
