"""Deterministic pin metadata from catalog fields plus template variation.

No LLM: every string is a function of the pose record, the taxonomy display
names and a stable hash of the pin id, so re-runs are reproducible and free.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlencode

from .catalog import Pose, PromptText

TITLE_MAX = 90
DESC_MAX = 300
KEYWORDS_MIN, KEYWORDS_MAX = 5, 10

# Closing lines: soft save CTA, rotated by a stable hash so descriptions do
# not look templated in aggregate.
CTA_TEMPLATES = [
    "Save this for your next session.",
    "Pin it for the next time you're stuck on set.",
    "Keep this one handy for your shoot list.",
    "Save it and try it on your next shoot.",
    "Worth saving for the next time nerves show up.",
    "Add it to your posing board.",
    "Save this so it's there when you need it.",
    "Pin it and come back to it on shoot day.",
]

CATEGORY_NOUN = {
    "family": "family photo pose",
    "couples": "couples pose",
    "engagement": "engagement pose",
    "maternity": "maternity pose",
    "senior": "senior portrait pose",
}
CATEGORY_KEYWORD = {
    "family": "family photo poses",
    "couples": "couples posing",
    "engagement": "engagement poses",
    "maternity": "maternity poses",
    "senior": "senior portrait poses",
}
LIGHT_PHRASE = {
    "golden": "golden hour light", "blue": "blue hour light", "soft_low": "soft low sun",
    "mid": "midday sun", "harsh_overhead": "harsh overhead sun", "overcast": "overcast light",
    "open_shade": "open shade", "backlit": "backlight", "indoor_window": "window light",
    "night_flash": "night flash",
}
LOCATION_PHRASE = {
    "beach": "on the beach", "forest": "in the forest", "urban": "on a city street",
    "field": "in an open field", "studio": "in the studio", "home": "at home",
    "mountain": "in the mountains",
}
SUBJECT_WORD = {"adult": "adult", "teen": "teen", "child": "child", "toddler": "toddler",
                "pregnant": "expecting parent", "senior_adult": "older adult", "pet": "pet"}


def stable_index(key: str, n: int) -> int:
    return int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16) % n


_COPY_CFG: dict | None = None


def set_copy_config(copy_cfg: dict) -> None:
    """Install the humanizer rules (config/pinterest_copy.yaml)."""
    global _COPY_CFG
    _COPY_CFG = copy_cfg


def _copy_cfg() -> dict:
    if _COPY_CFG is None:
        from .config import load_all  # lazy: tests may build metadata standalone
        set_copy_config(load_all()["copy"])
    return _COPY_CFG  # type: ignore[return-value]


def _count_word(n: int, cfg: dict) -> str:
    return str((cfg.get("number_words") or {}).get(n, n))


def _with_clause(types: list[str], cfg: dict) -> str:
    core = set(cfg.get("family_core_types") or ["adult"])
    words = cfg.get("type_words") or {}
    parts = []
    for t in types:
        if t in core or t not in words:
            continue
        singular, plural = words[t]
        # subject_types is a set of kinds, not a count per kind; a group of
        # older adults reads naturally as plural, everything else singular.
        parts.append(plural if t == "senior_adult" else singular)
    if not parts:
        return ""
    if len(parts) == 1:
        return f" with {parts[0]}"
    return " with " + ", ".join(parts[:-1]) + f" and {parts[-1]}"


def _rule_matches(when: dict, count: int, types: list[str], category: str) -> bool:
    tset = set(types)
    if "count" in when and count != int(when["count"]):
        return False
    if "min_count" in when and count < int(when["min_count"]):
        return False
    cat = when.get("category")
    if cat is not None:
        cats = cat if isinstance(cat, list) else [cat]
        if category not in cats:
            return False
    if when.get("types_all") and not set(when["types_all"]) <= tset:
        return False
    if when.get("types_any") and not (set(when["types_any"]) & tset):
        return False
    if when.get("types_only") is not None and not tset <= set(when["types_only"]):
        return False
    return True


def humanize_subjects(count: int, types: list[str], category: str,
                      copy_cfg: dict | None = None) -> str:
    """'a family of four with a toddler and grandparents', 'a couple', ...
    Never a parenthesised enum list."""
    cfg = copy_cfg or _copy_cfg()
    for rule in cfg["rules"]:
        if _rule_matches(rule.get("when") or {}, count, types, category):
            phrase = rule["phrase"]
            break
    else:
        phrase = cfg.get("fallback", "a group of {count_word}")
    return phrase.format(**{"count_word": _count_word(count, cfg),
                            "with": _with_clause(types, cfg)})


def _people(pose: Pose) -> str:
    return humanize_subjects(int(pose.record.get("subject_count", 1)),
                             list(pose.record.get("subject_types") or []),
                             pose.primary_category)


def _light(pose: Pose) -> str | None:
    for lc in pose.record.get("light_conditions") or []:
        if lc in LIGHT_PHRASE:
            return LIGHT_PHRASE[lc]
    return None


def _location(pose: Pose) -> str | None:
    for loc in pose.record.get("location_types") or []:
        if loc in LOCATION_PHRASE:
            return LOCATION_PHRASE[loc]
    return None


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _clip_title(s: str) -> str:
    if len(s) <= TITLE_MAX:
        return s
    cut = s[:TITLE_MAX - 1]
    cut = cut[:cut.rfind(" ")] if " " in cut else cut
    return cut.rstrip(" ,:-") + "…"


# -- titles ------------------------------------------------------------------

def photo_title(pose: Pose) -> str:
    cat = pose.primary_category
    noun = _cap(CATEGORY_NOUN.get(cat, "photo pose"))
    return _clip_title(f"{noun}: {pose.name}")


def text_title(prompt: PromptText, display: dict) -> str:
    name = display["categories"].get(prompt.category, "Posing")
    return _clip_title(f"{name} posing prompt: “{prompt.text}”")


# -- descriptions ------------------------------------------------------------

def _first_sentence(pose: Pose, tier: int) -> str:
    noun = CATEGORY_NOUN.get(pose.primary_category, "photo pose")
    people = _people(pose)
    light = _light(pose)
    loc = _location(pose)
    parts = [f"A {noun} for {people}"]
    if tier <= 0 and loc:
        parts.append(loc)
    if tier <= 1 and light:
        parts.append(f"in {light}")
    return _cap(" ".join(parts)) + "."


def _second_sentence(pose: Pose, tier: int) -> str:
    diff = pose.record.get("difficulty", "easy")
    if tier <= 0:
        prompt = pose.primary_prompt.strip().rstrip(".!?")
        if prompt:
            return f"Say it out loud: “{prompt}.”"
    return {"easy": "Easy to direct, even with nervous clients.",
            "moderate": "A little direction goes a long way here.",
            "advanced": "Best once your clients have warmed up."}.get(diff, "Easy to direct.")


def photo_description(pose: Pose, pin_id: str, disclosure: str | None) -> str:
    cta = CTA_TEMPLATES[stable_index(pin_id, len(CTA_TEMPLATES))]
    suffix = f" {disclosure}" if disclosure else ""
    for tier in (0, 1, 2):
        desc = f"{_first_sentence(pose, tier)} {_second_sentence(pose, tier)} {cta}{suffix}"
        if len(desc) <= DESC_MAX:
            return desc
    # Last resort: shortest first sentence, shortest CTA, keep the disclosure.
    desc = f"{_first_sentence(pose, 2)} {_second_sentence(pose, 2)} " \
           f"{min(CTA_TEMPLATES, key=len)}{suffix}"
    return desc[:DESC_MAX] if not disclosure else desc  # disclosure is never cut


def text_description(prompt: PromptText, poses_by_id: dict[str, Pose],
                     middle_index: int | None = None) -> str:
    """`middle_index` selects the middle clause (callers rotate it across a
    batch); the CTA is chosen independently by a stable hash."""
    cta = CTA_TEMPLATES[stable_index(prompt.id, len(CTA_TEMPLATES))]
    middles = _copy_cfg()["text_middle_clauses"]
    if middle_index is None:
        middle_index = stable_index(prompt.id + ":middle", len(middles))
    middle = middles[middle_index % len(middles)]
    src = next((poses_by_id[p] for p in prompt.pose_ids if p in poses_by_id), None)
    noun = CATEGORY_NOUN.get(prompt.category, "photo pose").replace(" pose", "")
    tone = {"nervous_client": "for nervous clients", "playful": "to loosen everyone up",
            "romantic": "for a quiet, connected frame", "calm": "to settle the energy",
            "energetic": "to bring the energy up"}.get(prompt.tone, "to say out loud")
    first = f"A {noun} posing prompt {tone}."
    if src:
        who = _people(src)
        loc = _location(src)
        second = f"Works for {who}" + (f" {loc}" if loc else "") + f", {middle}."
    else:
        second = "Read it exactly as written and shoot while they react."
    for cand in (f"{first} {second} {cta}", f"{first} Read it as written and shoot. {cta}"):
        if len(cand) <= DESC_MAX:
            return cand
    return f"{first} {cta}"[:DESC_MAX]


# -- keywords ----------------------------------------------------------------

def keywords(pose: Pose | None, category: str, display: dict, extra: list[str] = ()) -> str:
    kws: list[str] = []

    def add(k: str) -> None:
        k = k.strip().lower()
        if k and k not in kws:
            kws.append(k)

    add(CATEGORY_KEYWORD.get(category, "posing prompts"))
    add(f"{display['categories'].get(category, category).lower()} photography")
    add("photography poses")
    add("posing prompts")
    if pose:
        for lc in pose.record.get("light_conditions") or []:
            add(f"{display['light_conditions'].get(lc, lc).lower()} photos")
        for loc in pose.record.get("location_types") or []:
            add(f"{display['location_types'].get(loc, loc).lower()} photoshoot")
        for st in pose.record.get("subject_types") or []:
            add(f"{display['subject_types'].get(st, st).lower()} photos")
        if pose.record.get("subject_count", 1) >= 5:
            add("large group poses")
        for c in pose.categories[1:]:
            add(CATEGORY_KEYWORD.get(c, c))
    for e in extra:
        add(e)
    add("photo ideas")
    add("photographer tips")
    return ", ".join(kws[:KEYWORDS_MAX])


# -- links / boards ----------------------------------------------------------

def pose_tags(pose: Pose | None) -> set[str]:
    if pose is None:
        return set()
    tags = set(pose.record.get("light_conditions") or [])
    tags |= set(pose.record.get("location_types") or [])
    tags |= set(pose.record.get("subject_types") or [])
    if pose.record.get("subject_count", 1) >= 5:
        tags.add("large_group")
    return tags


def match_rule(rules: list[dict], category: str, tags: set[str]) -> dict | None:
    """Category is the primary key: any rule for the pose's own category beats
    every '*' rule. Tags only break ties among rules of the same category
    (tagged beats untagged). '*' rules apply only when the category has no
    rule at all."""
    def best_of(cands: list[dict]) -> dict | None:
        tagged = [r for r in cands if r.get("tag") and r["tag"] in tags]
        untagged = [r for r in cands if not r.get("tag")]
        return (tagged or untagged or [None])[0]

    own = [r for r in rules if r.get("category", "*") == category]
    hit = best_of(own)
    if hit is not None:
        return hit
    return best_of([r for r in rules if r.get("category", "*") == "*"])


def board_for(cfg: dict, category: str, pose: Pose | None, pin_type: str,
              pin_id: str = "") -> str:
    """Photo pins: the category board (tags tiebreak within the category).
    Text pins: the category board too, except a configurable share
    (stable hash of the pin id) that goes to the secondary board."""
    boards = cfg["boards"]
    if pin_type == "text":
        tp = boards["text_pins"]
        share = float(tp.get("secondary_share", 0.25))
        if share > 0 and stable_index(pin_id + ":board", 1000) < share * 1000:
            return tp["secondary_board"]
        rule = match_rule([r for r in boards.get("rules") or [] if not r.get("tag")],
                          category, set())
        return (rule or {}).get("board") or tp["secondary_board"]
    rule = match_rule(boards.get("rules") or [], category, pose_tags(pose))
    return (rule or {}).get("board") or boards["default"]


def link_for(cfg: dict, category: str, pose: Pose | None, cohort: str,
             fallbacks: set[str] | None = None) -> str:
    links = cfg["links"]
    rule = match_rule(links.get("rules") or [], category, pose_tags(pose))
    if rule and rule.get("slug"):
        base = links.get("base", "https://cooperindustries.cc/prompted/guides").rstrip("/")
        url = f"{base}/{rule['slug']}"
    elif rule and rule.get("url"):
        url = rule["url"]
    else:
        url = links["fallback"]
        if fallbacks is not None:
            fallbacks.add(category)
    utm = cfg["cohorts"]["utm"]
    campaign = cfg["cohorts"]["cohorts"][cohort]["utm_campaign"]
    return f"{url}?{urlencode({'utm_source': utm['source'], 'utm_medium': utm['medium'], 'utm_campaign': campaign})}"


# -- alt text ----------------------------------------------------------------

def photo_alt(pose: Pose, display: dict) -> str:
    who = _people(pose)
    loc = _location(pose)
    light = _light(pose)
    instr = (pose.record.get("instructions") or [""])[0].strip().rstrip(".")
    bits = [f"Photo of {who}"]
    if loc:
        bits.append(loc)
    if light:
        bits.append(f"in {light}")
    s = " ".join(bits)
    if instr:
        s += f". {instr}"
    s += f". Caption reads “{pose.name}” and the prompt “{pose.primary_prompt}”."
    return _cap(re.sub(r"\s+", " ", s))


def text_alt(prompt: PromptText, label: str) -> str:
    return (f"Text graphic on a warm neutral background. Small label “{label}” "
            f"above the prompt “{prompt.text}”, with a small Prompted wordmark "
            f"at the bottom.")


def category_label(category: str, display: dict) -> str:
    name = display["categories"].get(category, category).upper()
    return f"{name} PROMPT"
