"""Tests for tools/ugc.py (the ugc_gen package).

Selection logic is tested against small synthetic Hook/ReactionClip/Pose
fixtures (fast, deterministic, independent of catalog contents); rights
refusal and the dry-run frame size are tested against the real catalog,
config/pinterest_exclusions.yaml, dist/guides_data.json and the synthetic
stand-in clips under dist/actions/ and dist/reactions/ (see VERIFY in the
task/README) -- the same split test_reels.py uses. Kept fast: no full MP4
encode, only first-frame decodes.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

from pinterest import config as pin_config
from pinterest.catalog import Pose, load_poses
from pinterest.provenance import load_provenance
from pinterest.rights import RightsGate, RightsViolation

from ugc_gen import commands, compose, csvs
from ugc_gen.hooks import EMOTIONS, Hook, HookConfigError, load_hooks
from ugc_gen.select import (ActionClip, ReactionClip, Selection, build_selections,
                            guard_action_clip, load_action_clips, load_reaction_clips,
                            parse_reaction_emotion)

REPO = Path(__file__).resolve().parent.parent
CFG = pin_config.load_all()
PROVENANCE = load_provenance()
GATE = RightsGate.from_config(CFG, PROVENANCE)
EXCLUDED_ID = CFG["exclusions"]["excluded_pose_ids"][0]

REAL_ACTIONS_DIR = REPO / "dist" / "actions"
REAL_REACTIONS_DIR = REPO / "dist" / "reactions"
REAL_GUIDES_PATH = REPO / "dist" / "guides_data.json"


def make_pose(pid: str, slug: str, source: str = "ai", categories=("couples",),
             prompts=None) -> Pose:
    rec = {
        "id": pid, "slug": slug, "status": "active", "image_source": source,
        "image": {"thumb": "thumb.jpg", "detail": "detail.jpg", "blurhash": "L00000"},
        "placeholder": False, "categories": list(categories), "subject_count": 2,
        "subject_types": ["adult"], "light_conditions": ["golden"],
        "location_types": ["field"], "orientation": "vertical", "difficulty": "easy",
        "prompts": prompts if prompts is not None else [
            {"text": "Hold hands and walk slowly toward the light.", "tone": "nervous_client"},
        ],
        "instructions": ["Do the thing."],
        "version": 1,
    }
    return Pose(id=pid, slug=slug, dir=Path("/nonexistent"), record=rec)


def make_hook(index=0, text="A hook line.", emotion="relieved", category=None) -> Hook:
    return Hook(index=index, text=text, emotion=emotion, category=category)


def make_action(slug: str, tone: str = "nervous_client", categories=("couples",)) -> ActionClip:
    pose = make_pose(f"01TEST{slug[:10].upper()}", slug, categories=categories)
    return ActionClip(path=Path(f"dist/actions/{slug}__{tone}.mp4"), slug=slug, tone=tone,
                      pose=pose, category=pose.primary_category)


def make_reaction(name: str, emotion: str) -> ReactionClip:
    return ReactionClip(path=Path(f"dist/reactions/{name}.mp4"), emotion=emotion)


# -- hook bank ---------------------------------------------------------------

def test_hook_bank_loads_with_thirty_entries_and_valid_emotions():
    hooks = load_hooks()
    assert len(hooks) == 30
    for h in hooks:
        assert h.emotion in EMOTIONS
        assert h.category is None or h.category in {
            "family", "couples", "engagement", "maternity", "senior"}
        assert h.text.strip() == h.text
        assert h.text


def test_hook_bank_every_emotion_and_several_categories_represented():
    hooks = load_hooks()
    emotions_seen = {h.emotion for h in hooks}
    assert emotions_seen == set(EMOTIONS)
    categories_seen = {h.category for h in hooks if h.category}
    assert len(categories_seen) >= 4


def test_hook_bank_rejects_wrong_count(tmp_path):
    bad = tmp_path / "hooks.yaml"
    bad.write_text("hooks:\n  - text: only one\n    emotion: relieved\n")
    with pytest.raises(HookConfigError):
        load_hooks(bad)


def test_hook_bank_rejects_invalid_emotion(tmp_path):
    entries = "\n".join(f"  - text: 'hook {i}'\n    emotion: relieved" for i in range(29))
    bad = tmp_path / "hooks.yaml"
    bad.write_text(f"hooks:\n{entries}\n  - text: 'bad one'\n    emotion: furious\n")
    with pytest.raises(HookConfigError):
        load_hooks(bad)


def test_hook_slug_is_filename_safe():
    hook = make_hook(text="What I say when a client says ‘I hate photos.’")
    assert hook.slug
    assert all(c.islower() or c.isdigit() or c == "-" for c in hook.slug)
    assert not hook.slug.startswith("-") and not hook.slug.endswith("-")


# -- reaction emotion parsing -------------------------------------------------

def test_parse_reaction_emotion_reads_word_after_last_dash():
    assert parse_reaction_emotion(Path("dist/reactions/zeely-004-skeptical.mp4")) == "skeptical"


def test_parse_reaction_emotion_unknown_word_is_neutral():
    assert parse_reaction_emotion(Path("dist/reactions/zeely-004-ecstatic.mp4")) == "neutral"
    assert parse_reaction_emotion(Path("dist/reactions/nodash.mp4")) == "neutral"


# -- selection -----------------------------------------------------------

def test_selection_prefers_matching_emotion():
    hooks = [make_hook(0, "hook", "skeptical")]
    reactions = [make_reaction("r-skeptical", "skeptical"), make_reaction("r-relieved", "relieved")]
    actions = [make_action("pose-a"), make_action("pose-b")]
    sels = build_selections(hooks, reactions, actions, count=2, seed=1)
    assert all(s.reaction.emotion == "skeptical" for s in sels)


def test_selection_falls_back_to_any_reaction_when_no_emotion_match():
    hooks = [make_hook(0, "hook", "laughing")]
    reactions = [make_reaction("r-relieved", "relieved")]
    actions = [make_action("pose-a"), make_action("pose-b")]
    sels = build_selections(hooks, reactions, actions, count=2, seed=1)
    assert all(s.reaction.emotion == "relieved" for s in sels)


def test_selection_prefers_matching_pose_category():
    hooks = [make_hook(0, "hook", "relieved", category="senior")]
    reactions = [make_reaction("r", "relieved")]
    actions = [make_action("family-pose", categories=("family",)),
              make_action("senior-pose", categories=("senior",))]
    sels = build_selections(hooks, reactions, actions, count=1, seed=3)
    assert sels[0].action.slug == "senior-pose"


def test_selection_falls_back_to_any_action_when_no_category_match():
    hooks = [make_hook(0, "hook", "relieved", category="maternity")]
    reactions = [make_reaction("r", "relieved")]
    actions = [make_action("family-pose", categories=("family",))]
    sels = build_selections(hooks, reactions, actions, count=1, seed=1)
    assert sels[0].action.slug == "family-pose"


def test_selection_never_repeats_a_hook_action_pair():
    hooks = [make_hook(0, "hook one", "relieved"), make_hook(1, "hook two", "skeptical")]
    reactions = [make_reaction("r1", "relieved"), make_reaction("r2", "skeptical")]
    actions = [make_action("pose-a"), make_action("pose-b"), make_action("pose-c")]
    sels = build_selections(hooks, reactions, actions, count=6, seed=7)
    pairs = [(s.hook.index, s.action.slug) for s in sels]
    assert len(pairs) == len(set(pairs))


def test_selection_is_reproducible_for_a_given_seed():
    hooks = [make_hook(i, f"hook {i}", EMOTIONS[i % len(EMOTIONS)]) for i in range(6)]
    reactions = [make_reaction(f"r{i}", e) for i, e in enumerate(EMOTIONS)]
    actions = [make_action(f"pose-{i}") for i in range(6)]
    a = build_selections(hooks, reactions, actions, count=5, seed=42)
    b = build_selections(hooks, reactions, actions, count=5, seed=42)
    assert [(s.hook.index, s.reaction.path.name, s.action.slug) for s in a] == \
          [(s.hook.index, s.reaction.path.name, s.action.slug) for s in b]


def test_selection_raises_when_more_pairs_requested_than_exist():
    hooks = [make_hook(0, "only hook", "relieved")]
    reactions = [make_reaction("r", "relieved")]
    actions = [make_action("only-pose")]
    with pytest.raises(ValueError):
        build_selections(hooks, reactions, actions, count=2, seed=1)


# -- rights: load_action_clips filtering + guard_action_clip refusal --------

def test_excluded_pose_id_is_configured_and_active():
    poses_by_id = {p.id: p for p in load_poses()}
    assert EXCLUDED_ID in poses_by_id
    assert GATE.is_excluded(poses_by_id[EXCLUDED_ID])


def test_guard_action_clip_raises_for_excluded_pose():
    poses_by_id = {p.id: p for p in load_poses()}
    pose = poses_by_id[EXCLUDED_ID]
    with pytest.raises(RightsViolation):
        guard_action_clip(pose, GATE)


def test_load_action_clips_skips_slug_not_in_guides_data(tmp_path):
    actions_dir = tmp_path / "actions"
    actions_dir.mkdir()
    (actions_dir / "_test__nervous_client.mp4").write_bytes(b"")
    guides_path = tmp_path / "guides_data.json"
    guides_path.write_text('{"poses": []}')
    clips, skipped = load_action_clips(actions_dir, guides_path, gate=GATE, poses=[])
    assert clips == []
    assert skipped == [(actions_dir / "_test__nervous_client.mp4",
                        "slug not present in dist/guides_data.json (rights-filtered)")]


def test_load_action_clips_skips_rights_excluded_pose_even_when_in_guides_data(tmp_path):
    poses_by_id = {p.id: p for p in load_poses()}
    excluded_pose = poses_by_id[EXCLUDED_ID]
    actions_dir = tmp_path / "actions"
    actions_dir.mkdir()
    (actions_dir / f"{excluded_pose.slug}__nervous_client.mp4").write_bytes(b"")
    guides_path = tmp_path / "guides_data.json"
    import json
    guides_path.write_text(json.dumps({"poses": [{"slug": excluded_pose.slug}]}))
    clips, skipped = load_action_clips(actions_dir, guides_path, gate=GATE, poses=[excluded_pose])
    assert clips == []
    assert skipped[0][1] == "rights-excluded (config/pinterest_exclusions.yaml)"


def test_load_action_clips_accepts_a_clean_slug(tmp_path):
    pose = make_pose("01OK", "clean-pose")
    actions_dir = tmp_path / "actions"
    actions_dir.mkdir()
    (actions_dir / "clean-pose__nervous_client.mp4").write_bytes(b"")
    guides_path = tmp_path / "guides_data.json"
    import json
    guides_path.write_text(json.dumps({"poses": [{"slug": "clean-pose"}]}))
    gate = RightsGate(filename_patterns=[], excluded_shoots=set(), excluded_pose_ids=set())
    clips, skipped = load_action_clips(actions_dir, guides_path, gate=gate, poses=[pose])
    assert skipped == []
    assert len(clips) == 1
    assert clips[0].slug == "clean-pose" and clips[0].tone == "nervous_client"


# -- filename / caption / schedule shape -------------------------------------

def test_video_filename_shape():
    hook = make_hook(4, "This is a test hook", "relieved")
    action = make_action("some-pose")
    sel = Selection(hook=hook, reaction=make_reaction("r", "relieved"), action=action,
                    prompt="A prompt.")
    name = commands.video_filename(7, sel)
    assert name == f"ugc-007-{hook.slug}-some-pose.mp4"
    assert name.startswith("ugc-007-")
    assert name.endswith("-some-pose.mp4")


def _record(hook="A hook.", hook_slug="a-hook", pose_slug="a-pose", prompt="Hold still.",
           source="ai", category="couples"):
    return csvs.VideoRecord(file=f"ugc-001-{hook_slug}-{pose_slug}.mp4", hook=hook,
                            hook_slug=hook_slug, pose_slug=pose_slug, prompt=prompt,
                            reaction_file="r.mp4", image_source=source, category=category)


def test_caption_includes_hook_quoted_prompt_and_link_in_bio():
    rec = _record(hook="The couple froze, so I said this.", prompt="Hold still and breathe.")
    caption = csvs.caption_for(rec)
    assert caption.startswith("The couple froze, so I said this.")
    assert "“Hold still and breathe.”" in caption
    assert caption.endswith("Link in bio.")


def test_ai_disclosure_mentions_both_for_ai_pose_and_only_reaction_for_real_photo():
    ai_rec = _record(source="ai")
    photo_rec = _record(source="photo")
    assert csvs.ai_disclosure_for(ai_rec) == "Reaction is AI-generated; posing reference is AI-generated."
    assert csvs.ai_disclosure_for(photo_rec) == "Reaction is AI-generated."


def test_link_carries_ugc_utm_campaign():
    assert "utm_campaign=ugc" in csvs.LINK


def test_captions_csv_columns_and_row_shape(tmp_path):
    rec = _record()
    out = csvs.write_captions([rec], tmp_path / "captions.csv")
    import csv as csv_mod
    rows = list(csv_mod.DictReader(out.open()))
    assert len(rows) == 1
    row = rows[0]
    for col in ("file", "hook", "pose_slug", "prompt", "reaction_file", "caption", "hashtags",
               "first_comment", "ai_disclosure", "link"):
        assert col in row
    n_tags = len(row["hashtags"].split())
    assert 8 <= n_tags <= 12
    from reels_gen.csvs import FIRST_COMMENT
    assert row["first_comment"] == FIRST_COMMENT


def test_schedule_no_two_consecutive_days_share_hook_or_pose():
    records = []
    for i in range(8):
        records.append(_record(hook=f"hook {i % 3}", hook_slug=f"hook-{i % 3}",
                               pose_slug=f"pose-{i % 4}"))
    ordered = csvs.build_schedule_order(records)
    assert len(ordered) == len(records)
    for a, b in zip(ordered, ordered[1:]):
        assert a.hook_slug != b.hook_slug
        assert a.pose_slug != b.pose_slug


def test_write_schedule_dates_sequential_from_start(tmp_path):
    from datetime import timedelta
    records = [_record(hook=f"hook {i}", hook_slug=f"hook-{i}", pose_slug=f"pose-{i}")
              for i in range(4)]
    start = date(2026, 9, 10)
    out = csvs.write_schedule(records, tmp_path / "schedule.csv", start)
    import csv as csv_mod
    rows = list(csv_mod.DictReader(out.open()))
    assert len(rows) == 4
    dates = [date.fromisoformat(r["date"]) for r in rows]
    assert dates == [start + timedelta(days=i) for i in range(4)]


# -- dry-run frame: real decode, real stand-in clips -------------------------

pytestmark_realfiles = pytest.mark.skipif(
    not (REAL_ACTIONS_DIR / "_test__nervous_client.mp4").is_file()
    or not (REAL_REACTIONS_DIR / "_test-skeptical.mp4").is_file(),
    reason="synthetic stand-in clips are not present (see README/VERIFY)")


@pytestmark_realfiles
def test_render_first_frame_is_1080x1920():
    hook = make_hook(0, "A dry-run hook.", "skeptical")
    pose = make_pose("01DRY", "dry-run-pose")
    action = ActionClip(path=REAL_ACTIONS_DIR / "_test__nervous_client.mp4", slug="dry-run-pose",
                        tone="nervous_client", pose=pose, category="couples")
    reaction = ReactionClip(path=REAL_REACTIONS_DIR / "_test-skeptical.mp4", emotion="skeptical")
    sel = Selection(hook=hook, reaction=reaction, action=action, prompt="Hold still.")
    font_candidates = CFG["cohorts"]["render"]["fonts"]["label"]
    frame = compose.render_first_frame(sel, font_candidates)
    assert frame.size == (1080, 1920)
    assert frame.mode == "RGB"


def _self_contained_actions_dir(tmp_path) -> tuple[Path, Path]:
    """A rights-cleared actions dir + matching guides.json, built from the
    real catalog but otherwise independent of dist/actions/'s live contents
    (another tool is actively populating it -- see README) so these tests
    do not depend on that process's progress. Content is the synthetic
    _test__nervous_client.mp4 stand-in, copied under a real, non-excluded
    slug that carries a nervous_client prompt (a product invariant, so this
    always finds one)."""
    import json
    import shutil

    poses = load_poses()
    real_pose = next(p for p in poses if not GATE.is_excluded(p)
                     and any(pp.get("tone") == "nervous_client" for pp in p.prompts))

    actions_dir = tmp_path / "actions"
    actions_dir.mkdir()
    shutil.copy(REAL_ACTIONS_DIR / "_test__nervous_client.mp4",
               actions_dir / f"{real_pose.slug}__nervous_client.mp4")

    guides_path = tmp_path / "guides_data.json"
    guides_path.write_text(json.dumps({"poses": [{"slug": real_pose.slug}]}))
    return actions_dir, guides_path


@pytestmark_realfiles
def test_dry_run_end_to_end_writes_pngs_contact_sheet_and_csvs(tmp_path):
    """Full cmd_generate(dry_run=True): exercises rights filtering,
    selection, and Pillow frame rendering together, against real hooks/
    catalog/rights config but self-contained action-clip content (see
    _self_contained_actions_dir) so it does not depend on dist/actions/'s
    live, externally-growing contents."""
    actions_dir, guides_path = _self_contained_actions_dir(tmp_path)
    out = tmp_path / "out"
    args = argparse.Namespace(count=1, seed=1, category=None, out=out, fps=30,
                              start_date=None, icon=None, hooks=None,
                              reactions=REAL_REACTIONS_DIR, actions=actions_dir,
                              guides=guides_path, dry_run=True)
    rc = commands.cmd_generate(args)
    assert rc == 0
    pngs = list(out.glob("*-frame0.png"))
    assert pngs, "no dry-run frames were written"
    for p in pngs:
        with Image.open(p) as im:
            assert im.size == (1080, 1920)
    assert (out / "contact_sheet.png").is_file()
    assert (out / "captions.csv").is_file()
    assert (out / "schedule.csv").is_file()
    assert not list(out.glob("*.mp4"))


@pytestmark_realfiles
def test_dry_run_never_selects_the_ungated_test_slug(tmp_path):
    """The literal dist/actions/_test__nervous_client.mp4 stand-in's slug
    ("_test") is not in dist/guides_data.json, so it must never be chosen
    -- generate must draw only from real, rights-cleared catalog slugs. This
    exercises load_action_clips' filename-parsing/slug-presence path
    directly against the real, unfiltered dist/actions/ directory (which
    does contain that literal file), not the self-contained fixture above."""
    args = argparse.Namespace(count=1, seed=1, category=None, out=tmp_path, fps=30,
                              start_date=None, icon=None, hooks=None,
                              reactions=REAL_REACTIONS_DIR, actions=REAL_ACTIONS_DIR,
                              guides=REAL_GUIDES_PATH, dry_run=True)
    try:
        rc = commands.cmd_generate(args)
    except ValueError:
        pytest.skip("no rights-cleared app-action clips available yet")
    assert rc == 0
    rows = list(__import__("csv").DictReader((tmp_path / "captions.csv").open()))
    slugs = {r["pose_slug"] for r in rows}
    assert "_test" not in slugs
