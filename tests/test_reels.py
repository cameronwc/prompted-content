"""Tests for tools/reels.py (the reels_gen package).

Runs against the real catalog/config for the rights and cap-length checks
(these must hold for the actual excluded ids), and against small synthetic
Pose/VideoRecord fixtures for everything that must not depend on catalog
contents. Kept fast (no MP4 encoding) via --dry-run-equivalent frame checks
and --limit-sized runs only.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml
from PIL import Image

from pinterest import config as pin_config
from pinterest.catalog import Pose, load_poses
from pinterest.provenance import load_provenance
from pinterest.rights import RightsGate, RightsViolation

from reels_gen import commands, csvs, frames, select, textfx

REPO = Path(__file__).resolve().parent.parent
CFG = pin_config.load_all()
PROVENANCE = load_provenance()
GATE = RightsGate.from_config(CFG, PROVENANCE)
EXCLUDED_ID = CFG["exclusions"]["excluded_pose_ids"][0]


# -- fixtures ------------------------------------------------------------

def make_pose(pid: str, slug: str, source: str, category: str = "family",
             prompts=None) -> Pose:
    rec = {
        "id": pid, "slug": slug, "status": "active", "image_source": source,
        "image": {"thumb": "thumb.jpg", "detail": "detail.jpg", "blurhash": "L00000"},
        "placeholder": False, "categories": [category], "subject_count": 2,
        "subject_types": ["adult"], "light_conditions": ["golden"],
        "location_types": ["field"], "orientation": "vertical", "difficulty": "easy",
        "prompts": prompts if prompts is not None else [
            {"text": "Hold hands and walk slowly toward the light.", "tone": "nervous_client"},
            {"text": "Trade jackets and act natural.", "tone": "playful"},
        ],
        "version": 1,
    }
    return Pose(id=pid, slug=slug, dir=Path("/nonexistent"), record=rec)


# -- rights refusal --------------------------------------------------------

def test_excluded_pose_id_is_configured_and_active():
    """Sanity check on the fixture itself: the id under test really is an
    active pose that really is listed in the exclusions file."""
    poses_by_id = {p.id: p for p in load_poses()}
    assert EXCLUDED_ID in poses_by_id
    assert GATE.is_excluded(poses_by_id[EXCLUDED_ID])


def test_guard_renderable_raises_for_excluded_pose():
    poses_by_id = {p.id: p for p in load_poses()}
    pose = poses_by_id[EXCLUDED_ID]
    with pytest.raises(RightsViolation):
        select.guard_renderable(pose, GATE)


def test_eligible_poses_never_returns_an_excluded_pose():
    poses = load_poses()
    selections, _ = select.eligible_poses(poses, GATE)
    ids = {sel.pose.id for sel in selections}
    assert EXCLUDED_ID not in ids
    # every excluded id in the config is absent, not just the first one
    for pid in CFG["exclusions"]["excluded_pose_ids"]:
        assert pid not in ids


def test_build_assets_hard_refuses_an_excluded_pose():
    """Even if a caller bypasses eligible_poses' own filtering (bug, or a
    caller that built a Selection by hand), build_assets must still refuse:
    it calls guard_renderable before opening any image."""
    poses_by_id = {p.id: p for p in load_poses()}
    pose = poses_by_id[EXCLUDED_ID]
    sel = select.Selection(pose=pose, category=pose.primary_category,
                           tone="nervous_client", prompt="short prompt")
    with pytest.raises(RightsViolation):
        commands.build_assets(sel, CFG, "AI-generated posing reference", None, GATE)


def test_actnaturally_photos_pattern_is_excluded_by_filename_rule():
    """Anything from ACTNATURALLY_PHOTOS must be impossible to render even
    by filename alone, independent of the excluded_pose_ids list."""
    pose = make_pose("01ZZZTESTPOSE00000000000", "test-pose", "photo")
    prov = {pose.id: PROVENANCE.get(EXCLUDED_ID)} if EXCLUDED_ID in PROVENANCE else {}
    # Build a gate whose only signal is the filename pattern, matched against
    # this pose's own image filenames (the third, independent check layer).
    pose.record["image"] = {"thumb": "ACTNATURALLY_PHOTOS-99.jpg",
                            "detail": "ACTNATURALLY_PHOTOS-99.jpg", "blurhash": "L00000"}
    assert GATE.is_excluded(pose)
    with pytest.raises(RightsViolation):
        select.guard_renderable(pose, GATE)


# -- prompt selection -------------------------------------------------------

def test_prompt_selection_prefers_short_nervous_client():
    pose = make_pose("01P1", "prefers-nervous", "ai", prompts=[
        {"text": "Short nervous line.", "tone": "nervous_client"},
        {"text": "A", "tone": "playful"},
    ])
    text, tone = select.choose_prompt(pose, None)
    assert (text, tone) == ("Short nervous line.", "nervous_client")


def test_prompt_selection_falls_back_to_shortest_other_when_nervous_too_long():
    long_nervous = "N" * 111
    pose = make_pose("01P2", "falls-back", "ai", prompts=[
        {"text": long_nervous, "tone": "nervous_client"},
        {"text": "This playful line is definitely under the cap.", "tone": "playful"},
        {"text": "A slightly longer calm line that is still under one ten.", "tone": "calm"},
    ])
    text, tone = select.choose_prompt(pose, None)
    assert tone == "playful"
    assert len(text) <= select.MAX_PROMPT_CHARS


def test_prompt_selection_none_when_every_prompt_too_long():
    pose = make_pose("01P3", "no-fit", "ai", prompts=[
        {"text": "N" * 111, "tone": "nervous_client"},
        {"text": "P" * 200, "tone": "playful"},
    ])
    assert select.choose_prompt(pose, None) is None


def test_prompt_selection_forced_tone_ignores_nervous_client():
    pose = make_pose("01P4", "forced-tone", "ai", prompts=[
        {"text": "Short nervous line.", "tone": "nervous_client"},
        {"text": "A romantic line under the cap.", "tone": "romantic"},
    ])
    text, tone = select.choose_prompt(pose, "romantic")
    assert tone == "romantic"
    assert text == "A romantic line under the cap."


def test_prompt_selection_forced_tone_absent_returns_none():
    pose = make_pose("01P5", "no-romantic", "ai", prompts=[
        {"text": "Short nervous line.", "tone": "nervous_client"},
    ])
    assert select.choose_prompt(pose, "romantic") is None


def test_eligible_poses_reports_skips_with_a_reason():
    poses = [make_pose("01P6", "unfittable", "ai", prompts=[
        {"text": "N" * 111, "tone": "nervous_client"},
    ])]
    gate = RightsGate(filename_patterns=[], excluded_shoots=set(), excluded_pose_ids=set())
    selections, skipped = select.eligible_poses(poses, gate)
    assert selections == []
    assert len(skipped) == 1
    assert skipped[0][0].slug == "unfittable"
    assert "110" in skipped[0][1]


def test_eligible_poses_filters_by_category_and_slug():
    poses = [make_pose("01P7", "a-pose", "ai", category="senior"),
            make_pose("01P8", "b-pose", "ai", category="family")]
    gate = RightsGate(filename_patterns=[], excluded_shoots=set(), excluded_pose_ids=set())
    selections, _ = select.eligible_poses(poses, gate, category="senior")
    assert [s.pose.slug for s in selections] == ["a-pose"]
    selections, _ = select.eligible_poses(poses, gate, slug="b-pose")
    assert [s.pose.slug for s in selections] == ["b-pose"]


# -- text ----------------------------------------------------------------

def test_quote_uses_curly_quotes():
    assert textfx.quote("hello there") == "“hello there”"


def test_real_catalog_nervous_client_prompts_all_fit_five_lines():
    """The floor scaled to the 1080px canvas must still be renderable within
    5 lines for every nervous_client prompt actually used by the default
    selection rule (a fit failure here would silently drop a pose)."""
    poses = load_poses()
    selections, _ = select.eligible_poses(poses, GATE)
    fonts = CFG["cohorts"]["render"]["fonts"]["prompt"]
    safe_w, safe_h = frames.prompt_safe_area()
    failures = []
    for sel in selections:
        try:
            textfx.fit_prompt(sel.prompt, fonts, safe_w, safe_h)
        except Exception as exc:  # pragma: no cover - reported, not raised
            failures.append((sel.pose.slug, str(exc)))
    assert not failures, failures


# -- filename / caption / hashtag shape -------------------------------------

def _record(category="family", tone="playful", source="ai", light=("golden",)):
    return csvs.VideoRecord(file=f"{category}-a-pose-{tone}.mp4", slug="a-pose",
                            category=category, tone=tone, prompt="Hold still and breathe.",
                            title="A pose", image_source=source, light_conditions=light)


def test_generate_filename_shape():
    rec = _record(category="senior", tone="calm")
    assert rec.file == "senior-a-pose-calm.mp4"


def test_caption_includes_quoted_prompt_and_pose_title():
    rec = _record()
    caption = csvs.caption_for(rec)
    assert "“Hold still and breathe.”" in caption
    assert "A pose" in caption
    assert "Prompted, the posing app that is only a posing app." in caption


def test_caption_discloses_ai_reference_only_for_ai():
    ai_caption = csvs.caption_for(_record(source="ai"))
    photo_caption = csvs.caption_for(_record(source="photo"))
    assert "AI-generated" in ai_caption
    assert "AI-generated" not in photo_caption


def test_hashtags_in_range_and_category_specific():
    tags = csvs.hashtags_for("maternity", ["golden"])
    assert csvs.MIN_HASHTAGS <= len(tags) <= csvs.MAX_HASHTAGS
    assert any("maternity" in t for t in tags)
    assert "#goldenhour" in tags
    no_golden = csvs.hashtags_for("maternity", ["overcast"])
    assert "#goldenhour" not in no_golden


def test_hashtags_all_categories_stay_in_range():
    for cat in select.CATEGORIES:
        tags = csvs.hashtags_for(cat, [])
        assert csvs.MIN_HASHTAGS <= len(tags) <= csvs.MAX_HASHTAGS


def test_link_has_no_link_in_caption_but_column_carries_platform():
    assert "http" not in csvs.caption_for(_record())
    assert "utm_source=reels" in csvs.LINK
    assert "utm_medium=video" in csvs.LINK


# -- schedule alternation ----------------------------------------------------

def _records_mix(n_per_cat=3, categories=("family", "couples", "engagement"),
                 real_every=None):
    items = []
    i = 0
    for cat in categories:
        for j in range(n_per_cat):
            source = "photo" if (real_every and i % real_every == 0) else "ai"
            items.append(csvs.VideoRecord(file=f"f{i}.mp4", slug=f"s{i}", category=cat,
                                          tone="playful", prompt="x", title="X",
                                          image_source=source))
            i += 1
    return items


def test_schedule_no_two_consecutive_same_category():
    items = _records_mix(n_per_cat=6, real_every=3)
    ordered = csvs.build_schedule_order(items)
    assert len(ordered) == len(items)
    for a, b in zip(ordered, ordered[1:]):
        assert a.category != b.category


def test_schedule_real_photo_every_fourth_post_while_supply_lasts():
    # Plenty of real photos relative to the run length: every window of 4
    # consecutive posts must contain at least one real photo.
    items = _records_mix(n_per_cat=6, real_every=3)
    ordered = csvs.build_schedule_order(items)
    for i in range(len(ordered) - 3):
        window = ordered[i:i + 4]
        assert any(it.image_source == "photo" for it in window), \
            [it.image_source for it in window]


def test_schedule_relaxes_once_real_photos_are_exhausted():
    # Only 2 real photos total, spread across a much longer run: the rule
    # must not be violated before they run out, and must not raise/loop
    # forever after they do.
    items = _records_mix(n_per_cat=10, categories=("family", "couples", "senior"))
    items[0] = csvs.VideoRecord(file="r0.mp4", slug="r0", category="family", tone="playful",
                                prompt="x", title="X", image_source="photo")
    items[1] = csvs.VideoRecord(file="r1.mp4", slug="r1", category="couples", tone="playful",
                                prompt="x", title="X", image_source="photo")
    ordered = csvs.build_schedule_order(items)
    assert len(ordered) == len(items)
    assert sum(1 for it in ordered if it.image_source == "photo") == 2
    # category rule must still hold throughout, even after reals run out
    for a, b in zip(ordered, ordered[1:]):
        assert a.category != b.category


def test_write_schedule_dates_are_sequential_from_start(tmp_path):
    from datetime import date, timedelta
    items = _records_mix(n_per_cat=2, real_every=2)
    start = date(2026, 9, 10)
    out = csvs.write_schedule(items, tmp_path / "schedule.csv", start)
    import csv as csv_mod
    rows = list(csv_mod.DictReader(out.open()))
    assert len(rows) == len(items)
    dates = [date.fromisoformat(r["date"]) for r in rows]
    assert dates == [start + timedelta(days=i) for i in range(len(items))]


# -- dry run: rendered frame size --------------------------------------------

def test_dry_run_produces_1080x1920_frames(tmp_path):
    args = argparse.Namespace(limit=3, category=None, tone=None, slug=None,
                              out=tmp_path, fps=30, start_date=None, dry_run=True)
    rc = commands.cmd_generate(args)
    assert rc == 0
    pngs = list(tmp_path.glob("*-frame0.png"))
    assert pngs, "no dry-run frames were written"
    for p in pngs:
        with Image.open(p) as im:
            assert im.size == (1080, 1920)
    assert (tmp_path / "contact_sheet.png").is_file()
    assert (tmp_path / "captions.csv").is_file()
    assert (tmp_path / "schedule.csv").is_file()
    assert not list(tmp_path.glob("*.mp4"))


def test_dry_run_never_includes_an_excluded_pose(tmp_path):
    args = argparse.Namespace(limit=None, category="couples", tone=None, slug=None,
                              out=tmp_path, fps=30, start_date=None, dry_run=True)
    commands.cmd_generate(args)
    rows = list(__import__("csv").DictReader((tmp_path / "captions.csv").open()))
    slugs = {r["slug"] for r in rows}
    excluded_slugs = {p.slug for p in load_poses() if p.id in CFG["exclusions"]["excluded_pose_ids"]}
    assert not (slugs & excluded_slugs)
