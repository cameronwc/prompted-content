"""Pinterest pipeline tests (the ten from the brief, plus config guards).

Runs against the real catalog and configs where that is what the brief asks
for (longest prompt, exclusion ids), and against synthetic fixtures for
everything that must not depend on catalog contents.
"""
from __future__ import annotations

import copy
import csv
import io
import re
from collections import Counter
from itertools import combinations
import shutil
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from PIL import Image

from pinterest import catalog, commands, config, csv_out, grade, metadata, render, seasons, selection
from pinterest.color import hex_delta_e
from pinterest.provenance import Provenance
from pinterest.rights import RightsGate, RightsViolation
from pinterest.schedule import DEFAULT_RAMP, Scheduler, assign_slots, per_day_for, slot_allowed
from pinterest.text_fit import FitError, cap_height, fit_text, min_size_for_cap

REPO = Path(__file__).resolve().parent.parent
CFG = config.load_all()
FONTS = CFG["cohorts"]["render"]["fonts"]["prompt"]
T = CFG["cohorts"]["render"]["text"]


# -- fixtures ----------------------------------------------------------------

def make_pose(tmp: Path, pid: str, slug: str, source: str, category="family",
              image_names=("thumb.jpg", "detail.jpg"), subject_count=3,
              prompt_text: str | None = None, light: str = "golden",
              subject_types=("adult", "child")) -> catalog.Pose:
    d = tmp / pid
    d.mkdir(parents=True, exist_ok=True)
    for name in image_names:
        w = 400 if name.startswith("thumb") else 1200
        Image.new("RGB", (w, int(w * 1.25)), (120, 90, 70)).save(d / name, "JPEG")
    p1 = prompt_text or f"Look at each other and laugh ({pid[-3:]})."
    rec = {
        "id": pid, "slug": slug, "status": "active", "image_source": source,
        "image": {"thumb": image_names[0], "detail": image_names[1], "blurhash": "L00000"},
        "placeholder": False, "categories": [category], "subject_count": subject_count,
        "subject_types": list(subject_types), "light_conditions": [light],
        "location_types": ["field"], "orientation": "vertical", "difficulty": "easy",
        "instructions": ["Stand them shoulder to shoulder facing the light."],
        "prompts": [{"text": p1, "tone": "nervous_client"},
                    {"text": f"Squeeze in tight, tighter ({pid[-3:]}).", "tone": "playful"}],
        "gear": {"focal_mm": [35, 85], "aperture": "f/2.8", "needs_reflector": False},
        "accessibility": [], "version": 1,
    }
    (d / "pose.yaml").write_text(yaml.safe_dump(rec))
    return catalog.Pose(id=pid, slug=slug, dir=d, record=rec)


def fixture_poses(tmp: Path, n_ai=6, n_photo=4, n_bad=2) -> list[catalog.Pose]:
    poses = []
    cats = ["family", "couples", "maternity", "senior", "engagement"]
    lights = ["golden", "overcast", "mid", "open_shade", "blue"]
    types = [("adult", "child"), ("adult",), ("pregnant", "adult"), ("teen",),
             ("adult", "toddler", "senior_adult")]
    for i in range(n_ai):
        poses.append(make_pose(tmp, f"01AI{i:022d}", f"ai-pose-{i}", "ai", cats[i % 5],
                               ("thumb_ai.jpg", "detail_ai.jpg"), light=lights[i % 5],
                               subject_types=types[i % 5]))
    for i in range(n_photo):
        poses.append(make_pose(tmp, f"01PH{i:022d}", f"photo-pose-{i}", "photo", cats[i % 5],
                               light=lights[(i + 1) % 5], subject_types=types[i % 5]))
    for i in range(n_bad):
        poses.append(make_pose(tmp, f"01BAD{i:021d}", f"lavender-bad-{i}", "photo", "couples"))
    return poses


def make_gate(poses, provenance=None, extra_ids=(), shoots=("123farm-lavender-couples",)):
    ex = {"filename_patterns": ["ACTNATURALLY_PHOTOS-*"], "excluded_shoots": list(shoots),
          "excluded_pose_ids": list(extra_ids), "apply_to_text_pins": True}
    return RightsGate.from_config({"exclusions": ex}, provenance or {})


@pytest.fixture
def ctx(tmp_path):
    poses = fixture_poses(tmp_path / "poses")
    prov = {"01BAD000000000000000000000": Provenance("123farm-lavender-couples", "ACTNATURALLY_PHOTOS-3.jpg"),
            "01BAD000000000000000000001": Provenance("123farm-lavender-couples", "DSC_0001.jpg")}
    cfg = copy.deepcopy(CFG)
    c = commands.Context(cfg=cfg, poses=poses, manifest_path=tmp_path / "manifest.json",
                         pins_dir=tmp_path / "pins", provenance=prov, grade_cfg=grade.load_grade())
    return c


def rebuild(ctx):
    return commands.Context(cfg=ctx.cfg, poses=ctx.poses, manifest_path=ctx.manifest_path,
                            pins_dir=ctx.pins_dir, provenance=ctx.provenance, grade_cfg=ctx.grade)


def gen(ctx, **kw):
    args = dict(limit=None, cohort=None, dry_run=False, start_date=date(2026, 9, 8),
                pins_per_day=None, regenerate=[], no_upload=True)
    args.update(kw)
    return commands.cmd_generate(ctx, **args)


# -- 1/2/3: rights gate ------------------------------------------------------

def test_actnaturally_prefixed_asset_raises(tmp_path):
    pose = make_pose(tmp_path, "01X0000000000000000000000A", "x", "photo",
                     image_names=("ACTNATURALLY_PHOTOS-7.jpg", "ACTNATURALLY_PHOTOS-7.jpg"))
    gate = make_gate([pose])
    with pytest.raises(RightsViolation):
        render.render_photo_pin(pose, CFG["cohorts"], gate)


def test_provenance_filename_raises(tmp_path):
    pose = make_pose(tmp_path, "01X0000000000000000000000B", "x", "photo")
    gate = make_gate([pose], {pose.id: Provenance("some-other-shoot", "ACTNATURALLY_PHOTOS-12.jpg")},
                     shoots=())
    assert [r.rule for r in gate.reasons(pose)] == ["filename_pattern"]
    with pytest.raises(RightsViolation):
        render.render_photo_pin(pose, CFG["cohorts"], gate)


def test_excluded_shoot_raises(tmp_path):
    pose = make_pose(tmp_path, "01X0000000000000000000000C", "x", "photo")
    gate = make_gate([pose], {pose.id: Provenance("123farm-lavender-couples", "DSC_9.jpg")})
    assert [r.rule for r in gate.reasons(pose)] == ["excluded_shoot"]
    with pytest.raises(RightsViolation, match="excluded_shoot"):
        render.render_photo_pin(pose, CFG["cohorts"], gate)


def test_explicit_pose_id_raises(tmp_path):
    pose = make_pose(tmp_path, "01X0000000000000000000000D", "x", "ai", image_names=("thumb_ai.jpg", "detail_ai.jpg"))
    gate = make_gate([pose], extra_ids=[pose.id])
    with pytest.raises(RightsViolation, match="excluded_pose_id"):
        render.render_photo_pin(pose, CFG["cohorts"], gate)


def test_gate_identical_for_photo_ai_and_photo_real(tmp_path):
    prov = {}
    real = make_pose(tmp_path, "01X0000000000000000000000E", "r", "photo")
    ai = make_pose(tmp_path, "01X0000000000000000000000F", "a", "ai", image_names=("thumb_ai.jpg", "detail_ai.jpg"))
    for p in (real, ai):
        prov[p.id] = Provenance("123farm-lavender-couples", "ACTNATURALLY_PHOTOS-1.jpg")
    gate = make_gate([real, ai], prov)
    assert selection.photo_cohort(real) == "photo_real"
    assert selection.photo_cohort(ai) == "photo_ai"
    assert {r.rule for r in gate.reasons(real)} == {r.rule for r in gate.reasons(ai)}
    for p in (real, ai):
        with pytest.raises(RightsViolation):
            render.render_photo_pin(p, CFG["cohorts"], gate)
    pool, _, _ = selection.candidates([real, ai], catalog.unique_prompts([real, ai]), gate)
    assert "photo_real" not in pool and "photo_ai" not in pool


def test_real_catalog_exclusions_cover_123farm():
    """Every checked-in excluded id is a real, active, photo-sourced pose and
    the gate excludes exactly those from the live catalog."""
    poses = catalog.load_poses()
    by_id = {p.id: p for p in poses}
    ids = set(CFG["exclusions"]["excluded_pose_ids"])
    assert len(ids) == 12
    for pid in ids:
        assert pid in by_id and by_id[pid].image_source == "photo"
    gate = RightsGate.from_config(CFG, {})  # no provenance: id layer alone must hold
    assert {p.id for p in poses if gate.is_excluded(p)} == ids


def test_generate_excludes_and_reports(ctx, capsys):
    assert gen(ctx) == 0
    out = capsys.readouterr().out
    assert "Rights exclusions: 2 of 12 poses excluded" in out
    assert "by filename_pattern: 1" in out and "by excluded_shoot: 2" in out
    for pin in ctx.manifest["pins"].values():
        assert not any(pid.startswith("01BAD") for pid in pin["source_pose_ids"])


# -- 4: disclosure -----------------------------------------------------------

def test_photo_ai_descriptions_carry_disclosure(ctx):
    assert gen(ctx) == 0
    ai = [p for p in ctx.manifest["pins"].values() if p["cohort"] == "photo_ai"]
    assert ai
    for p in ai:
        assert "AI-generated posing reference." in p["description"]
        assert len(p["description"]) <= 300
    for p in ctx.manifest["pins"].values():
        if p["cohort"] != "photo_ai":
            assert "AI-generated" not in p["description"]


@pytest.mark.parametrize("value", ["", "   ", None])
def test_empty_disclosure_fails_run(ctx, value):
    ctx.cfg["cohorts"]["ai_disclosure"] = value
    with pytest.raises(SystemExit) as exc:
        gen(ctx, cohort="photo_ai")
    assert exc.value.code == 2
    assert not ctx.manifest_path.exists()


def test_missing_disclosure_key_rejected_by_config():
    cfg = copy.deepcopy(CFG)
    del cfg["cohorts"]["ai_disclosure"]
    with pytest.raises(SystemExit):
        config.validate(cfg)


# -- 5: UTM ------------------------------------------------------------------

def test_links_carry_cohort_utm(ctx):
    assert gen(ctx) == 0
    for p in ctx.manifest["pins"].values():
        q = parse_qs(urlparse(p["link"]).query)
        assert q["utm_source"] == ["pinterest"] and q["utm_medium"] == ["pin"]
        assert q["utm_campaign"] == [CFG["cohorts"]["cohorts"][p["cohort"]]["utm_campaign"]]


# -- 6: text auto-fit --------------------------------------------------------

def _assert_fits(text):
    w, h, _ = render.text_safe_area(CFG["cohorts"])
    fit = fit_text(text, FONTS, w, h, T["max_point_size"], T["min_cap_height"], T["step"],
                   T["max_lines"], T["leading"])
    assert fit.height <= h and len(fit.lines) <= T["max_lines"]
    for line in fit.lines:
        assert fit.font.getbbox(line)[2] <= w
    assert "".join(fit.lines).replace(" ", "") == "".join(text.split())  # nothing truncated
    floor = min_size_for_cap(FONTS, T["min_cap_height"], T["max_point_size"])
    if fit.size > floor:  # an orphan is tolerated only at the floor, never a smaller size
        assert not (len(fit.lines) > 1 and len(fit.lines[-1].split()) == 1)
    assert fit.cap_height >= T["min_cap_height"]
    return fit


def test_fit_longest_fittable_catalog_prompt():
    """The longest prompt that fits the floor never overflows; longer ones are
    refused (skipped by the pipeline), never shrunk below the floor."""
    prompts = sorted(catalog.unique_prompts(catalog.load_poses()), key=lambda p: -len(p.text))
    fitted = refused = 0
    for pr in prompts:
        try:
            _assert_fits(pr.text)
            fitted += 1
        except FitError:
            refused += 1
    assert fitted > len(prompts) * 0.9 and refused >= 1


def test_fit_synthetic_300_chars_is_refused_not_shrunk():
    words = "posing prompt for nervous clients who freeze when the camera comes up ".split()
    text = ""
    i = 0
    while len(text) < 300:
        text += words[i % len(words)] + " "
        i += 1
    text = text.strip()[:300]
    w, h, _ = render.text_safe_area(CFG["cohorts"])
    with pytest.raises(FitError):
        fit_text(text, FONTS, w, h, T["max_point_size"], T["min_cap_height"], T["step"],
                 T["max_lines"], T["leading"])


def test_fit_refuses_impossible():
    with pytest.raises(FitError):
        fit_text("word " * 400, FONTS, 200, 200, 40, 20, 4, 3, 1.3)


def test_rendered_cap_height_never_below_minimum():
    prompts = [pr for pr in catalog.unique_prompts(catalog.load_poses())]
    checked = 0
    for pr in prompts:
        try:
            fit = render.layout_text_pin(pr, CFG["cohorts"])
        except FitError:
            continue
        assert cap_height(fit.font) >= T["min_cap_height"], pr.text
        assert fit.cap_height >= 90
        checked += 1
    assert checked > 200


def test_text_block_fills_canvas():
    prompt = catalog.PromptText("Nobody has to look at the camera. Look at whoever you love "
                                "most in this group.", "calm", ["x"], "family")
    fit = render.layout_text_pin(prompt, CFG["cohorts"])
    assert 0.55 <= fit.height / CFG["cohorts"]["render"]["height"] <= 0.78


def test_hyphen_and_em_dash_break():
    prompt = catalog.PromptText("Give me main-character-walking-to-the-bus energy.", "playful", ["x"], "senior")
    fit = render.layout_text_pin(prompt, CFG["cohorts"])
    assert "".join(fit.lines).replace(" ", "") == prompt.text.replace(" ", "")


def test_text_pin_renders_under_limit_and_size():
    prompt = catalog.PromptText("Look at each other like you have a secret.", "romantic", ["x"], "couples")
    data = render.render_text_pin(prompt, "COUPLES PROMPT", CFG["cohorts"])
    assert len(data) <= CFG["cohorts"]["render"]["max_bytes"]
    im = Image.open(io.BytesIO(data))
    assert im.size == (1000, 1500) and im.format == "PNG"


def test_unfittable_prompt_is_skipped_and_logged(ctx, capsys):
    long_text = ("Take a slow breath in together, and as you exhale, let your shoulders drop "
                 "and lean all the way back into him while I count.")
    poses = list(ctx.poses) + [make_pose(ctx.pins_dir.parent / "poses", "01AI0000000000000000000099",
                                         "long-prompt", "ai", "family",
                                         ("thumb_ai.jpg", "detail_ai.jpg"), prompt_text=long_text)]
    c = commands.Context(cfg=ctx.cfg, poses=poses, manifest_path=ctx.manifest_path,
                         pins_dir=ctx.pins_dir, provenance=ctx.provenance, grade_cfg=ctx.grade)
    assert gen(c, cohort="text") == 0
    out = capsys.readouterr().out
    assert "cap-height floor" in out and long_text in out
    assert not any(p["source_id"] == long_text for p in c.manifest["pins"].values())


# -- palette -----------------------------------------------------------------

def test_palette_min_delta_e():
    pal = T["palette"]
    cats = [c for c in pal if c != "default"]
    assert len(cats) == 5
    for a, b in combinations(cats, 2):
        assert hex_delta_e(pal[a], pal[b]) >= T["palette_min_delta_e"], (a, b)


# -- 7: manifest dedupe ------------------------------------------------------

def test_two_runs_produce_no_duplicates(ctx, capsys):
    assert gen(ctx, limit=5) == 0
    first = copy.deepcopy(ctx.manifest["pins"])
    assert len(first) == 5
    ctx2 = rebuild(ctx)
    assert gen(ctx2, limit=5) == 0
    assert len(ctx2.manifest["pins"]) == 10
    for pid, rec in first.items():
        assert ctx2.manifest["pins"][pid]["scheduled_at"] == rec["scheduled_at"]
    times = [p["scheduled_at"] for p in ctx2.manifest["pins"].values()]
    assert len(set(times)) == len(times)
    ctx3 = rebuild(ctx)
    assert gen(ctx3) == 0
    ctx4 = rebuild(ctx)
    n = len(ctx4.manifest["pins"])
    assert gen(ctx4) == 0
    assert len(ctx4.manifest["pins"]) == n
    assert "Nothing to generate" in capsys.readouterr().out


def test_regenerate_rebuilds_but_keeps_schedule(ctx):
    assert gen(ctx, limit=3) == 0
    pid = next(iter(ctx.manifest["pins"]))
    before = ctx.manifest["pins"][pid]
    ctx2 = rebuild(ctx)
    assert gen(ctx2, regenerate=[pid], limit=1) == 0
    after = ctx2.manifest["pins"][pid]
    assert after["scheduled_at"] == before["scheduled_at"]
    assert after["generated_at"] >= before["generated_at"]


# -- 8: CSV ------------------------------------------------------------------

def test_csv_rows_validate_against_schema(ctx, tmp_path):
    assert gen(ctx) == 0
    out = tmp_path / "csv"
    assert commands.cmd_csv(ctx, batch_size=4, out_dir=out, verify=False) == 0
    files = sorted(out.glob("pins_batch_*.csv"))
    assert [f.name for f in files][:2] == ["pins_batch_001.csv", "pins_batch_002.csv"]
    cols = [c["name"] for c in CFG["csv"]["columns"]]
    seen = 0
    for f in files:
        with open(f, encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows and list(rows[0].keys()) == cols
        assert len(rows) <= 4
        cohorts_in_batch = set()
        for row in rows:
            assert len(row["title"]) <= 90 and len(row["description"]) <= 300
            assert all(row[c] for c in cols)
            u = urlparse(row["image_url"])
            assert u.scheme == "https" and u.netloc == "content.cooperindustries.cc"
            assert "r2.dev" not in row["image_url"]
            q = parse_qs(urlparse(row["link"]).query)
            assert q["utm_source"] == ["pinterest"] and q["utm_medium"] == ["pin"]
            pin = next(p for p in ctx.manifest["pins"].values() if p["title"] == row["title"]
                       and p["description"] == row["description"])
            cohorts_in_batch.add(pin["cohort"])
            assert pin["batch_file"] == f.name
            assert q["utm_campaign"] == [CFG["cohorts"]["cohorts"][pin["cohort"]]["utm_campaign"]]
            if pin["cohort"] == "photo_ai":
                assert "AI-generated posing reference" in row["description"]
            else:
                assert "AI-generated" not in row["description"]
            seen += 1
        if len(rows) == 4:
            assert len(cohorts_in_batch) >= 2  # interleaved, not grouped
    assert seen == len(ctx.manifest["pins"])


def test_csv_refuses_relative_or_missing_urls():
    with pytest.raises(csv_out.RowError):
        csv_out.check_image_url("pins/x.png", verify=False)
    with pytest.raises(csv_out.RowError):
        csv_out.validate_row({"pin_id": "x", "title": "t" * 91, "description": "d",
                              "link": "l", "image_url": "u", "published_at": "p",
                              "board": "b"}, CFG["csv"])


def test_csv_is_schema_driven(ctx, tmp_path):
    assert gen(ctx, limit=3) == 0
    ctx.cfg["csv"]["columns"] = [{"name": "Board", "field": "board"}, {"name": "Title", "field": "title"},
                                 {"name": "Media URL", "field": "image_url"},
                                 {"name": "Publish date", "field": "published_at"},
                                 {"name": "Description", "field": "description"}, {"name": "Link", "field": "link"}]
    out = tmp_path / "csv2"
    assert commands.cmd_csv(ctx, batch_size=100, out_dir=out, verify=False) == 0
    header = (out / "pins_batch_001.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header == "Board,Title,Media URL,Publish date,Description,Link"


# -- 9: ramp schedule --------------------------------------------------------

def test_ramp_per_day_counts_and_unique_timestamps():
    s = Scheduler(start=date(2026, 9, 8))
    times = [s.next() for _ in range(5 * 7 + 8 * 7 + 12 * 7 + 25 * 2)]
    assert len(set(times)) == len(times)
    by_day = {}
    for t in times:
        by_day[t.date()] = by_day.get(t.date(), 0) + 1
        assert 6 <= t.hour < 20 or (t.hour == 20 and t.minute == 0)
        assert t.second == 0
    days = sorted(by_day)
    assert [by_day[d] for d in days[:7]] == [5] * 7
    assert [by_day[d] for d in days[7:14]] == [8] * 7
    assert [by_day[d] for d in days[14:21]] == [12] * 7
    assert sum(by_day[d] for d in days[21:28]) == 25
    assert sum(by_day[d] for d in days[28:35]) == 25
    assert per_day_for(0, DEFAULT_RAMP, None) == 5 and per_day_for(30, DEFAULT_RAMP, 9) == 9


def test_pins_per_day_override_and_resume():
    s = Scheduler(start=date(2026, 9, 8), pins_per_day=3)
    first = [s.next() for _ in range(7)]
    assert [t.date() for t in first] == [date(2026, 9, 8)] * 3 + [date(2026, 9, 9)] * 3 + [date(2026, 9, 10)]
    r = Scheduler.resume(date(2026, 9, 8), first, pins_per_day=3)
    nxt = r.next()
    assert nxt.date() == date(2026, 9, 10) and nxt not in first


# -- 10: cohort shares -------------------------------------------------------

def test_cohort_shares_within_tolerance(tmp_path):
    poses = fixture_poses(tmp_path / "poses", n_ai=40, n_photo=20, n_bad=0)
    gate = make_gate(poses)
    pool, _, _ = selection.candidates(poses, catalog.unique_prompts(poses), gate)
    shares = {k: float(v["share"]) for k, v in CFG["cohorts"]["cohorts"].items()}
    picks = selection.select(pool, shares, 50, set(), set(), {"family"})
    assert len(picks) == 50
    for cohort, share in shares.items():
        actual = sum(1 for p in picks if p.cohort == cohort) / 50
        assert abs(actual - share) <= CFG["cohorts"]["share_tolerance"], (cohort, actual)
    # round-robin: no category dominates the photo_ai picks
    from collections import Counter
    cats = Counter(p.category for p in picks if p.cohort == "photo_ai")
    assert max(cats.values()) <= 2 * min(cats.values())


def test_cohort_restriction(ctx):
    assert gen(ctx, cohort="text", limit=4) == 0
    assert {p["cohort"] for p in ctx.manifest["pins"].values()} == {"text"}


# -- metadata / config guards ------------------------------------------------

def test_metadata_bounds_on_real_catalog():
    poses = catalog.load_poses()
    display = catalog.taxonomy_display()
    gate = RightsGate.from_config(CFG, {})
    for pose in poses:
        if gate.is_excluded(pose):
            continue
        assert len(metadata.photo_title(pose)) <= 90
        d = metadata.photo_description(pose, f"photo:{pose.id}", "AI-generated posing reference.")
        assert len(d) <= 300 and d.endswith("AI-generated posing reference.")
        kws = metadata.keywords(pose, pose.primary_category, display).split(", ")
        assert 5 <= len(kws) <= 10
        assert metadata.board_for(CFG, pose.primary_category, pose, "photo") in {
            "Family Photo Poses", "Couples Posing", "Engagement Poses", "Maternity Poses",
            "Senior Portrait Poses", "Golden Hour Posing", "Large Group Poses"}
    for prompt in catalog.unique_prompts(poses):
        assert len(metadata.text_title(prompt, display)) <= 90
        assert len(metadata.text_description(prompt, {p.id: p for p in poses})) <= 300


def test_cta_variation_across_pins(ctx):
    assert gen(ctx) == 0
    closings = {p["description"].replace(" AI-generated posing reference.", "").rsplit(". ", 1)[-1]
                for p in ctx.manifest["pins"].values()}
    assert len(closings) >= 3


def test_link_fallback_reported(ctx, capsys):
    ctx.cfg["links"]["rules"] = [r for r in ctx.cfg["links"]["rules"]
                                 if r.get("category") != "senior" and r.get("tag") != "golden"]
    assert gen(ctx) == 0
    out = capsys.readouterr().out
    assert "Link fallbacks" in out and "senior" in out
    senior = [p for p in ctx.manifest["pins"].values() if p["category"] == "senior"]
    assert senior and all(p["link"].startswith("https://cooperindustries.cc/prompted/marketing/?") for p in senior)


def test_shares_must_sum_to_one():
    cfg = copy.deepcopy(CFG)
    cfg["cohorts"]["cohorts"]["text"]["share"] = 0.9
    with pytest.raises(SystemExit):
        config.validate(cfg)


def test_dry_run_leaves_no_manifest(ctx, capsys):
    assert gen(ctx, dry_run=True) == 0
    assert not ctx.manifest_path.exists()
    assert (ctx.pins_dir / "contact_sheet.png").is_file()
    assert "contact sheet of 12 pins" in capsys.readouterr().out


# -- shoot diversity ---------------------------------------------------------

def test_slot_allowed_rules():
    from datetime import datetime, timedelta
    t0 = datetime(2026, 9, 8, 9, 0)
    assert slot_allowed(t0, [], 7, 2, 30)
    assert not slot_allowed(t0 + timedelta(days=6), [t0], 7, 2, 30)
    assert slot_allowed(t0 + timedelta(days=7), [t0], 7, 2, 30)
    assert not slot_allowed(t0 + timedelta(days=20), [t0, t0 + timedelta(days=10)], 7, 2, 30)
    assert slot_allowed(t0 + timedelta(days=31), [t0, t0 + timedelta(days=10)], 7, 2, 30)


def _check_diversity(pins, rules):
    from datetime import datetime, timedelta
    by_shoot = {}
    for p in pins:
        if p.get("shoot"):
            by_shoot.setdefault(p["shoot"], []).append(datetime.fromisoformat(p["scheduled_at"]))
    for shoot, times in by_shoot.items():
        times.sort()
        for a, b in zip(times, times[1:]):
            assert (b.date() - a.date()).days >= rules["min_days_apart"], shoot
        for i, a in enumerate(times):
            assert sum(1 for b in times[i:] if b - a < timedelta(days=rules["window_days"])) \
                <= rules["max_per_window"], shoot


def test_shoot_diversity_with_dominant_shoot(tmp_path, capsys):
    poses = []
    prov = {}
    for i in range(12):
        p = make_pose(tmp_path / "poses", f"01PH{i:022d}", f"a-{i}", "photo", "maternity")
        poses.append(p)
        prov[p.id] = Provenance("dominant-shoot", f"DSC{i}.jpg")
    for i in range(2):
        p = make_pose(tmp_path / "poses", f"01PX{i:022d}", f"b-{i}", "photo", "family")
        poses.append(p)
        prov[p.id] = Provenance("other-shoot", f"IMG{i}.jpg")
    cfg = copy.deepcopy(CFG)
    ctx = commands.Context(cfg=cfg, poses=poses, manifest_path=tmp_path / "m.json",
                           pins_dir=tmp_path / "pins", provenance=prov, grade_cfg=grade.load_grade())
    assert gen(ctx, cohort="photo_real", pins_per_day=10) == 0
    out = capsys.readouterr().out
    pins = list(ctx.manifest["pins"].values())
    rules = cfg["cohorts"]["diversity"]
    _check_diversity(pins, rules)
    assert len(pins) < 14, "output must be reduced, not the constraint violated"
    assert "WARNING: shoot diversity" in out and "dominant-shoot" in out
    # A second run must still respect the schedule already in the manifest.
    ctx2 = rebuild(ctx)
    assert gen(ctx2, cohort="photo_real", pins_per_day=10) == 0
    _check_diversity(list(ctx2.manifest["pins"].values()), rules)


def test_diversity_thresholds_configurable(tmp_path):
    poses, prov = [], {}
    for i in range(6):
        p = make_pose(tmp_path / "poses", f"01PH{i:022d}", f"a-{i}", "photo", "maternity")
        poses.append(p)
        prov[p.id] = Provenance("one-shoot", f"DSC{i}.jpg")
    cfg = copy.deepcopy(CFG)
    cfg["cohorts"]["diversity"] = {"min_days_apart": 1, "max_per_window": 6, "window_days": 30}
    ctx = commands.Context(cfg=cfg, poses=poses, manifest_path=tmp_path / "m.json",
                           pins_dir=tmp_path / "pins", provenance=prov, grade_cfg=grade.load_grade())
    assert gen(ctx, cohort="photo_real", pins_per_day=1) == 0
    assert len(ctx.manifest["pins"]) == 6


# -- grade -------------------------------------------------------------------

def test_grade_moves_toward_targets_and_is_cohort_blind():
    g = grade.load_grade()
    cool = Image.new("RGB", (300, 450), (70, 80, 110))
    graded = grade.apply_grade(cool, g)
    before, after = grade.measure(cool), grade.measure(graded)
    assert after["rb_ratio"] > before["rb_ratio"]
    assert after["mean_luminance"] > before["mean_luminance"]
    # identical input -> identical output regardless of which cohort it came from
    assert grade.apply_grade(cool, g).tobytes() == graded.tobytes()


def test_grade_leaves_monochrome_neutral():
    g = grade.load_grade()
    mono = Image.new("RGB", (300, 450), (90, 90, 90))
    m = grade.measure(grade.apply_grade(mono, g))
    assert abs(m["r"] - m["b"]) < 0.01


# -- 1: guide URL precedence -------------------------------------------------

def test_family_golden_pose_links_to_family_guide(tmp_path):
    pose = make_pose(tmp_path, "01X0000000000000000000000G", "golden-family", "ai", "family",
                     ("thumb_ai.jpg", "detail_ai.jpg"), light="golden")
    cfg = copy.deepcopy(CFG)
    # Even with an explicit '*'+golden rule present, category must win.
    cfg["links"]["rules"].append({"category": "*", "tag": "golden", "slug": "golden-hour-posing"})
    link = metadata.link_for(cfg, "family", pose, "photo_ai")
    assert link.startswith("https://cooperindustries.cc/prompted/guides/golden-hour-prompts?")
    # A tag only tiebreaks inside the same category.
    cfg["links"]["rules"].append({"category": "family", "tag": "golden", "slug": "family-golden"})
    assert metadata.link_for(cfg, "family", pose, "photo_ai").startswith(
        "https://cooperindustries.cc/prompted/guides/family-golden?")
    # '*' rules only serve categories with no rule of their own.
    assert metadata.match_rule(cfg["links"]["rules"], "newcat", {"golden"})["slug"] == "golden-hour-posing"


def test_boards_category_primary(tmp_path):
    pose = make_pose(tmp_path, "01X0000000000000000000000H", "x", "ai", "maternity",
                     ("thumb_ai.jpg", "detail_ai.jpg"), light="golden", subject_types=("pregnant", "adult"))
    assert metadata.board_for(CFG, "maternity", pose, "photo") == "Golden Hour Posing"  # within-category tiebreak
    pose.record["light_conditions"] = ["overcast"]
    assert metadata.board_for(CFG, "maternity", pose, "photo") == "Maternity Poses"


# -- 2: text pin board routing -----------------------------------------------

def test_text_pins_route_to_category_boards_with_secondary_share():
    boards = Counter(metadata.board_for(CFG, "couples", None, "text", f"text:{i:012x}")
                     for i in range(400))
    assert set(boards) == {"Couples Posing", "Posing Prompts"}
    share = boards["Posing Prompts"] / 400
    assert abs(share - CFG["boards"]["text_pins"]["secondary_share"]) < 0.08
    assert metadata.board_for(CFG, "family", None, "text", "text:zzz") in {"Family Photo Poses", "Posing Prompts"}


def test_no_board_exceeds_30_percent_of_batch(tmp_path):
    poses = fixture_poses(tmp_path / "poses", n_ai=40, n_photo=20, n_bad=0)
    ctx = commands.Context(cfg=copy.deepcopy(CFG), poses=poses, manifest_path=tmp_path / "m.json",
                           pins_dir=tmp_path / "pins", provenance={}, grade_cfg=grade.load_grade())
    assert gen(ctx, limit=60, pins_per_day=20) == 0
    pins = list(ctx.manifest["pins"].values())
    assert len(pins) >= 50
    boards = Counter(p["board"] for p in pins)
    for board, n in boards.items():
        assert n / len(pins) <= 0.30, (board, n, len(pins))


# -- 3: middle clause variation ----------------------------------------------

def test_middle_clause_varies_across_batch(ctx):
    poses = fixture_poses(ctx.pins_dir.parent / "poses2", n_ai=30, n_photo=0, n_bad=0)
    c = commands.Context(cfg=ctx.cfg, poses=poses, manifest_path=ctx.manifest_path,
                         pins_dir=ctx.pins_dir, provenance={}, grade_cfg=ctx.grade)
    assert gen(c, cohort="text", limit=20, pins_per_day=20) == 0
    descs = [p["description"] for p in c.manifest["pins"].values()]
    assert len(descs) == 20
    middles = CFG["copy"]["text_middle_clauses"]
    counts = Counter(next(m for m in middles if m in d) for d in descs)
    assert max(counts.values()) <= 3
    ctas = Counter(d.rsplit(". ", 1)[-1] for d in descs)
    assert len(ctas) >= 3  # CTA still varies independently


# -- 4: humanized composition ------------------------------------------------

ENUM_LIST = re.compile(r"\([^)]*,[^)]*\)")


def test_humanizer_phrases():
    h = metadata.humanize_subjects
    assert h(4, ["adult", "toddler", "senior_adult"], "family") == \
        "a family of four with a toddler and grandparents"
    assert h(2, ["adult"], "couples") == "a couple"
    assert h(2, ["adult", "toddler"], "family") == "a parent and their toddler"
    assert h(1, ["teen"], "senior") == "a high school senior"
    assert h(2, ["pregnant", "adult"], "maternity") == "an expecting couple"


def test_no_description_contains_enum_list(ctx):
    assert gen(ctx, pins_per_day=20) == 0
    for p in ctx.manifest["pins"].values():
        assert not ENUM_LIST.search(p["description"]), p["description"]
        assert not ENUM_LIST.search(p["alt_text"]), p["alt_text"]
    # and across the whole real catalog
    display = catalog.taxonomy_display()
    gate = RightsGate.from_config(CFG, {})
    for pose in catalog.load_poses():
        if gate.is_excluded(pose):
            continue
        d = metadata.photo_description(pose, f"photo:{pose.id}", None)
        assert not ENUM_LIST.search(d), d
        assert not ENUM_LIST.search(metadata.photo_alt(pose, display))


# -- 5: seasonal gate --------------------------------------------------------

def test_season_windows():
    from datetime import date as D
    w = CFG["seasons"]["windows"]
    assert seasons.in_window(D(2026, 11, 15), w["holiday"])
    assert not seasons.in_window(D(2026, 9, 8), w["holiday"])
    assert seasons.in_window(D(2026, 9, 8), w["fall"])
    assert seasons.in_window(D(2026, 12, 25), {"from": "12-01", "to": "01-15"})  # wraps
    assert seasons.window_opens_within(w["holiday"], D(2026, 10, 1), 45)
    assert not seasons.window_opens_within(w["holiday"], D(2026, 9, 3), 45)


def test_season_derivation_and_override(tmp_path):
    pose = make_pose(tmp_path, "01X0000000000000000000000S", "rug-gift-watch", "photo", "family")
    pose.record["instructions"] = ["Sit them by the Christmas tree with the stockings in frame."]
    tag = seasons.derive_season(pose, CFG["seasons"])
    assert tag.season == "holiday" and "christmas" in tag.hits
    plain = make_pose(tmp_path, "01X0000000000000000000000T", "bench-row", "photo", "family")
    assert seasons.derive_season(plain, CFG["seasons"]).season == "none"
    cfg = copy.deepcopy(CFG["seasons"])
    cfg["overrides"] = {plain.id: "fall"}
    assert seasons.derive_season(plain, cfg).season == "fall"


def test_real_catalog_holiday_poses_tagged():
    ctx = commands.Context()
    tagged = {p.slug: t.season for p, t in ctx.season_report()}
    for slug in ("rug-gift-watch", "hearth-tummy-time", "mantel-overhead-lift", "hip-cradle-gaze"):
        assert tagged.get(slug) == "holiday"


def test_holiday_pins_deferred_in_september_and_scheduled_in_window(tmp_path, capsys):
    poses = []
    for i in range(3):
        p = make_pose(tmp_path / "poses", f"01PH{i:022d}", f"xmas-{i}", "photo", "family")
        p.record["instructions"] = ["Christmas pajamas by the stockings."]
        poses.append(p)
    for i in range(3):
        poses.append(make_pose(tmp_path / "poses", f"01PX{i:022d}", f"beach-{i}", "photo", "maternity",
                               subject_types=("pregnant", "adult")))
    ctx = commands.Context(cfg=copy.deepcopy(CFG), poses=poses, manifest_path=tmp_path / "m.json",
                           pins_dir=tmp_path / "pins", provenance={}, grade_cfg=grade.load_grade())
    assert gen(ctx, cohort="photo_real", start_date=date(2026, 9, 8), pins_per_day=5) == 0
    out = capsys.readouterr().out
    assert "Deferred (out of season" in out and "xmas-0" in out
    pins = list(ctx.manifest["pins"].values())
    assert {p["season"] for p in pins} == {"none"} and len(pins) == 3
    # Late October: window opens within the lookahead, slots land inside Nov 1..Dec 20.
    ctx2 = rebuild(ctx)
    assert gen(ctx2, cohort="photo_real", start_date=date(2026, 10, 25), pins_per_day=5) == 0
    holiday = [p for p in ctx2.manifest["pins"].values() if p["season"] == "holiday"]
    assert len(holiday) == 3
    for p in holiday:
        assert seasons.in_window(date.fromisoformat(p["scheduled_at"][:10]), CFG["seasons"]["windows"]["holiday"])
