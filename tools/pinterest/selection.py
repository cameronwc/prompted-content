"""Pick which pins to generate this run.

Round-robins across categories inside each cohort (categories with a mapped
guide URL appear twice per cycle, so they get roughly double weight), and
picks the cohort with the largest deficit against its configured share at
every step. Everything already in the manifest is skipped unless listed in
`regenerate`.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .catalog import Pose, PromptText
from .metadata import match_rule
from .rights import RightsGate


@dataclass
class Candidate:
    pin_id: str
    cohort: str
    pin_type: str          # text | photo
    category: str
    pose: Pose | None = None
    prompt: PromptText | None = None
    shoot: str | None = None
    season: str = "none"


def photo_cohort(pose: Pose) -> str | None:
    if pose.image_source == "photo":
        return "photo_real"
    if pose.image_source == "ai":
        return "photo_ai"
    return None  # synthetic placeholder tiles are never pinned


def candidates(poses: list[Pose], prompts: list[PromptText], gate: RightsGate,
               provenance: dict | None = None, text_fits=None, season_of=None,
               ) -> tuple[dict[str, list[Candidate]], int, list[PromptText]]:
    """{cohort: [candidates]} after the rights gate, the withheld prompt
    count, and the prompts skipped because they cannot meet the type floor
    (`text_fits(prompt) -> bool`, optional)."""
    by_id = {p.id: p for p in poses}
    provenance = provenance or {}
    out: dict[str, list[Candidate]] = defaultdict(list)
    for pose in poses:
        cohort = photo_cohort(pose)
        if cohort is None or gate.is_excluded(pose):
            continue
        prov = provenance.get(pose.id)
        out[cohort].append(Candidate(f"photo:{pose.id}", cohort, "photo",
                                     pose.primary_category, pose=pose,
                                     shoot=prov.shoot if prov else None,
                                     season=season_of(pose) if season_of else "none"))
    withheld = 0
    unfit: list[PromptText] = []
    for prompt in prompts:
        if gate.prompt_excluded(prompt, by_id):
            withheld += 1
            continue
        if text_fits is not None and not text_fits(prompt):
            unfit.append(prompt)
            continue
        out["text"].append(Candidate(prompt.id, "text", "text", prompt.category, prompt=prompt))
    return dict(out), withheld, unfit


def mapped_categories(cfg: dict, categories: set[str]) -> set[str]:
    rules = cfg["links"].get("rules") or []
    return {c for c in categories if match_rule(rules, c, set())}


def _category_cycle(cats: list[str], weighted: set[str]) -> list[str]:
    cycle = []
    for c in sorted(cats):
        cycle.append(c)
        if c in weighted:
            cycle.append(c)
    return cycle


def _interleave_shoots(items: list[Candidate]) -> list[Candidate]:
    by_shoot: dict[str | None, list[Candidate]] = defaultdict(list)
    for c in items:
        by_shoot[c.shoot].append(c)
    order = sorted(by_shoot, key=lambda s: (s is None, str(s)))
    out: list[Candidate] = []
    while any(by_shoot.values()):
        for shoot in order:
            if by_shoot[shoot]:
                out.append(by_shoot[shoot].pop(0))
    return out


def select_per_cohort(pool: dict[str, list[Candidate]], per_cohort: int, already: set[str],
                      regenerate: set[str], weighted: set[str]) -> list[Candidate]:
    """Equal picks per cohort, interleaved (used by --dry-run / --per-cohort)."""
    per = {c: select({c: pool.get(c, [])}, {c: 1.0}, per_cohort, already, regenerate, weighted)
           for c in pool}
    out: list[Candidate] = []
    for i in range(per_cohort):
        for c in sorted(per):
            if i < len(per[c]):
                out.append(per[c][i])
    return out


def select(pool: dict[str, list[Candidate]], shares: dict[str, float], limit: int | None,
           already: set[str], regenerate: set[str], weighted_categories: set[str],
           only_cohort: str | None = None) -> list[Candidate]:
    # Per cohort: per category queues, deterministic order.
    queues: dict[str, dict[str, list[Candidate]]] = {}
    cycles: dict[str, list[str]] = {}
    for cohort, cands in pool.items():
        if only_cohort and cohort != only_cohort:
            continue
        fresh = [c for c in cands if c.pin_id not in already or c.pin_id in regenerate]
        per_cat: dict[str, list[Candidate]] = defaultdict(list)
        for c in sorted(fresh, key=lambda c: c.pin_id):
            per_cat[c.category].append(c)
        # Inside a category, alternate shoots so consecutive picks are not
        # near-duplicates from one session.
        for cat, items in per_cat.items():
            per_cat[cat] = _interleave_shoots(items)
        queues[cohort] = per_cat
        cycles[cohort] = _category_cycle(list(per_cat), weighted_categories)

    picked: list[Candidate] = []
    counts = {c: 0 for c in queues}
    positions = {c: 0 for c in queues}
    active_shares = {c: shares.get(c, 0.0) for c in queues}
    total_share = sum(active_shares.values()) or 1.0

    def has_more(cohort: str) -> bool:
        return any(queues[cohort].values())

    while (limit is None or len(picked) < limit) and any(has_more(c) for c in queues):
        n = len(picked) + 1
        # Largest deficit first: target share * n minus what it has.
        cohort = max((c for c in queues if has_more(c)),
                     key=lambda c: (active_shares[c] / total_share * n - counts[c], c))
        cycle = cycles[cohort]
        for _ in range(len(cycle)):
            cat = cycle[positions[cohort] % len(cycle)]
            positions[cohort] += 1
            if queues[cohort][cat]:
                picked.append(queues[cohort][cat].pop(0))
                counts[cohort] += 1
                break
    return picked
