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

import numpy as np
import pytest
from PIL import Image

from pinterest import config as pin_config
from pinterest.catalog import Pose, load_poses
from pinterest.provenance import load_provenance
from pinterest.rights import RightsGate, RightsViolation

from ugc_gen import commands, compose, csvs, video_io
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

# Real inputs (see task/README): a real reaction clip and the real app
# recordings under dist/actions/ -- never the (currently absent, and never
# to be synthesized) `_test*` stand-ins those files pretend to be.
# reaction-07*.mp4 is excluded on principle -- it may be deleted.
REAL_SAMPLE_REACTION = REAL_REACTIONS_DIR / "reaction-08-impressed.mp4"


def _first_real_action_clip() -> Path | None:
    """First dist/actions/<slug>__nervous_client.mp4 recording whose pose
    is on the real catalog, not rights-excluded, and carries a
    nervous_client prompt -- a real, rights-cleared app recording usable
    end to end, not just a filename fixture."""
    if not REAL_ACTIONS_DIR.is_dir():
        return None
    poses_by_slug = {p.slug: p for p in load_poses()}
    for path in sorted(REAL_ACTIONS_DIR.glob("*__nervous_client.mp4")):
        slug = path.stem.split("__", 1)[0]
        pose = poses_by_slug.get(slug)
        if pose is None or GATE.is_excluded(pose):
            continue
        if any(pp.get("tone") == "nervous_client" for pp in pose.prompts):
            return path
    return None


REAL_SAMPLE_ACTION = _first_real_action_clip()

pytestmark_real_sample = pytest.mark.skipif(
    not REAL_SAMPLE_REACTION.is_file() or REAL_SAMPLE_ACTION is None,
    reason="real reaction/action sample clips are not present in dist/")


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
               "first_comment", "ai_disclosure", "link", "layout"):
        assert col in row
    n_tags = len(row["hashtags"].split())
    assert 8 <= n_tags <= 12
    from reels_gen.csvs import FIRST_COMMENT
    assert row["first_comment"] == FIRST_COMMENT
    assert row["layout"] == "open-then-split"


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


# -- new open-then-split timeline: duration rule (pure, no I/O) --------------

def test_main_duration_floors_short_or_typical_app_clips():
    # 165+ real recordings run ~7.0s -- below the 7.5s floor.
    assert compose.main_duration_seconds(3.0) == compose.MIN_MAIN_DURATION
    assert compose.main_duration_seconds(7.0) == compose.MIN_MAIN_DURATION


def test_main_duration_matches_app_clip_within_bounds():
    assert compose.main_duration_seconds(8.0) == 8.0


def test_main_duration_caps_long_app_clips_below_endcard_budget():
    assert compose.main_duration_seconds(30.0) == \
        compose.MAX_TOTAL_DURATION - compose.ENDCARD_DURATION


def test_total_duration_matches_spec_formula():
    assert compose.total_duration_seconds(7.0) == pytest.approx(7.5 + 1.2)
    assert compose.total_duration_seconds(8.0) == pytest.approx(8.0 + 1.2)
    assert compose.total_duration_seconds(50.0) == compose.MAX_TOTAL_DURATION
    assert compose.total_duration_seconds(50.0) <= 10.0


# -- new open-then-split timeline: move/split geometry (pure, no I/O) --------

def test_move_progress_is_zero_before_open_end_and_one_from_move_end_on():
    assert compose.move_progress(0.0) == 0.0
    assert compose.move_progress(compose.OPEN_END) == 0.0
    assert compose.move_progress(compose.MOVE_END) == 1.0
    assert compose.move_progress(compose.MOVE_END + 1.0) == 1.0
    mid = compose.move_progress((compose.OPEN_END + compose.MOVE_END) / 2)
    assert 0.0 < mid < 1.0


def test_reaction_and_panel_meet_exactly_at_split_rest_position():
    assert compose.reaction_band_height(0.0) == compose.HEIGHT
    assert compose.reaction_band_height(compose.MOVE_END) == compose.REACTION_H
    # the panel is fully below the bottom edge (invisible) at t=0...
    assert compose.panel_top_y(0.0) >= compose.HEIGHT
    # ...and at its documented resting position once SPLIT settles.
    assert compose.panel_top_y(compose.MOVE_END) == compose.PANEL_TOP
    assert compose.PANEL_TOP == compose.REACTION_H + compose.RULE_H


def test_hook_pill_visible_window_matches_spec():
    assert compose.hook_pill_alpha(0.0) == 255
    assert compose.hook_pill_alpha(compose.OPEN_END - 0.05) == 255  # still full just before 2.0s
    assert compose.hook_pill_alpha(compose.OPEN_END) >= 254  # ~full right at 2.0s (float boundary)
    assert compose.hook_pill_alpha(compose.HOOK_PILL_END) == 0
    faded = compose.hook_pill_alpha(compose.HOOK_PILL_END - compose.HOOK_PILL_FADE / 2)
    assert 0 < faded < 255


# -- new open-then-split timeline: app-panel crop-window detector -----------

def test_detect_chip_window_finds_a_synthetic_amber_band():
    from PIL import ImageDraw
    w, h = 1320, 2868
    im = Image.new("RGB", (w, h), compose.hex_rgb(compose.PAPER_HEX))
    band_top, band_bottom = int(h * 0.70), int(h * 0.73)
    ImageDraw.Draw(im).rectangle((0, band_top, w, band_bottom), fill=compose.hex_rgb(compose.AMBER_HEX))
    window = compose.detect_chip_window(im)
    assert window is not None
    y0, y1 = window
    assert y0 == max(0, band_top - compose.CHIP_WINDOW_ABOVE)
    assert y1 == min(h, band_bottom + compose.CHIP_WINDOW_BELOW)
    assert y0 <= band_top and band_bottom <= y1


def test_detect_chip_window_none_without_amber_falls_back():
    im = Image.new("RGB", (1320, 2868), compose.hex_rgb(compose.PAPER_HEX))
    assert compose.detect_chip_window(im) is None


def test_scaled_app_size_fits_panel_width_margin_for_a_typical_window():
    video_w, video_h = compose.scaled_app_size(1320, 974)
    assert video_w == compose.WIDTH - 2 * compose.APP_PANEL_MARGIN
    assert video_h <= compose.PANEL_H


def test_scaled_app_size_clamps_to_panel_height_for_an_oversized_window():
    video_w, video_h = compose.scaled_app_size(1320, 2868)  # full frame, would overflow the panel
    assert video_h == compose.PANEL_H
    assert video_w <= compose.WIDTH - 2 * compose.APP_PANEL_MARGIN


@pytestmark_real_sample
def test_app_crop_window_contains_the_chip_row_on_a_real_action_clip():
    """The crop-window detector, run against a real dist/actions/ recording
    (skipped above if none is available): the fixed window it returns must
    contain whatever amber tone-chip row is actually on screen."""
    orig = video_io.probe_video(REAL_SAMPLE_ACTION)
    y0, y1 = compose.app_crop_window(REAL_SAMPLE_ACTION, orig["width"], orig["height"])
    assert 0 <= y0 < y1 <= orig["height"]

    frame = video_io.extract_last_frame(REAL_SAMPLE_ACTION, orig["width"], orig["height"])
    assert frame is not None
    arr = np.asarray(frame.convert("RGB"), dtype=np.int32)
    target = np.array(compose.hex_rgb(compose.AMBER_HEX), dtype=np.int32)
    dist = np.sqrt(((arr - target) ** 2).sum(axis=2))
    row_frac = (dist < compose.CHIP_MATCH_DIST).sum(axis=1) / arr.shape[1]
    lo = int(arr.shape[0] * compose.CHIP_SEARCH_TOP_RATIO)
    hi = int(arr.shape[0] * compose.CHIP_SEARCH_BOTTOM_RATIO)
    chip_rows = [y for y in range(lo, hi) if row_frac[y] > compose.CHIP_ROW_MIN_FRACTION]
    assert chip_rows, "expected a detectable amber tone-chip row on a real action clip"
    assert y0 <= min(chip_rows) and max(chip_rows) <= y1


# -- new open-then-split timeline: real-clip dry-run frame --------------------

@pytestmark_real_sample
def test_render_first_frame_open_phase_is_full_bleed_reaction():
    """t=0 is pure OPEN: the reaction fills the whole 1080x1920 frame, so
    no paper-background panel should be visible near the bottom edge."""
    slug = REAL_SAMPLE_ACTION.stem.split("__", 1)[0]
    pose = next(p for p in load_poses() if p.slug == slug)
    hook = make_hook(0, "A dry-run hook.", "impressed")
    action = ActionClip(path=REAL_SAMPLE_ACTION, slug=slug, tone="nervous_client", pose=pose,
                        category=pose.primary_category)
    reaction = ReactionClip(path=REAL_SAMPLE_REACTION, emotion="impressed")
    sel = Selection(hook=hook, reaction=reaction, action=action, prompt="Hold still.")
    font_candidates = CFG["cohorts"]["render"]["fonts"]["label"]
    frame = compose.render_first_frame(sel, font_candidates)
    assert frame.size == (1080, 1920)
    assert frame.mode == "RGB"
    paper = compose.hex_rgb(compose.PAPER_HEX)
    bottom_centre = frame.getpixel((compose.WIDTH // 2, compose.HEIGHT - 5))
    assert bottom_centre != paper


def _self_contained_actions_dir_real(tmp_path) -> tuple[Path, Path]:
    """Like _self_contained_actions_dir, but sourced from a real recording
    already present in dist/actions/ (REAL_SAMPLE_ACTION) instead of the
    synthetic `_test` stand-in, which this task explicitly must not
    create."""
    import json
    import shutil

    actions_dir = tmp_path / "actions"
    actions_dir.mkdir()
    shutil.copy(REAL_SAMPLE_ACTION, actions_dir / REAL_SAMPLE_ACTION.name)

    slug = REAL_SAMPLE_ACTION.stem.split("__", 1)[0]
    guides_path = tmp_path / "guides_data.json"
    guides_path.write_text(json.dumps({"poses": [{"slug": slug}]}))
    return actions_dir, guides_path


@pytestmark_real_sample
def test_dry_run_end_to_end_with_real_clips_writes_layout_column(tmp_path):
    actions_dir, guides_path = _self_contained_actions_dir_real(tmp_path)
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
    rows = list(__import__("csv").DictReader((out / "captions.csv").open()))
    assert rows and rows[0]["layout"] == "open-then-split"
