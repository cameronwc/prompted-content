"""Subcommand implementations for tools/pins.py."""
from __future__ import annotations

import fnmatch
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from PIL import Image

from common import REPO_ROOT

from . import (catalog, config, csv_out, grade, manifest as manifest_mod, metadata, render,
               seasons, upload)
from .provenance import load_provenance, save_snapshot, shoot_source_files
from .rights import RightsGate, RightsViolation
from .schedule import DEFAULT_RAMP, Scheduler, assign_slots
from .selection import candidates, mapped_categories, select, select_per_cohort
from .text_fit import FitError

CSV_DIR = REPO_ROOT / "dist" / "pins_csv"


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


class Context:
    """Everything a command needs, built once."""

    def __init__(self, cfg: dict | None = None, poses=None, manifest_path=None,
                 pins_dir: Path | None = None, provenance=None, grade_cfg: dict | None = None,
                 csv_dir: Path | None = None):
        self.cfg = cfg or config.load_all()
        self.grade = grade_cfg if grade_cfg is not None else grade.load_grade()
        self.poses = poses if poses is not None else catalog.load_poses()
        self.poses_by_id = {p.id: p for p in self.poses}
        self.prompts = catalog.unique_prompts(self.poses)
        self.provenance = provenance if provenance is not None else load_provenance()
        self.gate = RightsGate.from_config(self.cfg, self.provenance)
        self.display = catalog.taxonomy_display()
        self.manifest_path = manifest_path or config.MANIFEST_PATH
        self.manifest = manifest_mod.load(self.manifest_path)
        self.pins_dir = pins_dir or config.PINS_DIR
        self.csv_dir = csv_dir or CSV_DIR
        self.link_fallbacks: set[str] = set()
        metadata.set_copy_config(self.cfg["copy"])
        self._middle_counter = 0
        self._season_cache: dict[str, seasons.SeasonTag] = {}

    # -- reporting -----------------------------------------------------------

    def print_exclusions(self) -> dict:
        report = self.gate.report(self.poses)
        withheld = sum(1 for pr in self.prompts
                       if self.gate.prompt_excluded(pr, self.poses_by_id))
        print(RightsGate.format_report(report, withheld))
        return report

    def print_link_fallbacks(self) -> None:
        if self.link_fallbacks:
            print("Link fallbacks (no guide URL mapped, using "
                  f"{self.cfg['links']['fallback']}): "
                  + ", ".join(sorted(self.link_fallbacks)))
        else:
            print("Link fallbacks: none (every used category has a guide URL)")

    def text_fits(self, prompt) -> bool:
        try:
            render.layout_text_pin(prompt, self.cfg["cohorts"])
            return True
        except FitError:
            return False

    def season_tag(self, pose) -> seasons.SeasonTag:
        if pose.id not in self._season_cache:
            prov = self.provenance.get(pose.id)
            self._season_cache[pose.id] = seasons.derive_season(
                pose, self.cfg["seasons"], prov.shoot if prov else None)
        return self._season_cache[pose.id]

    def season_report(self) -> list[tuple]:
        """[(pose, tag)] for every active, non-excluded pose tagged seasonal."""
        out = []
        for pose in self.poses:
            if self.gate.is_excluded(pose):
                continue
            tag = self.season_tag(pose)
            if tag.season != "none":
                out.append((pose, tag))
        return out

    def print_season_report(self) -> None:
        tagged = self.season_report()
        print(f"Seasonal tagging: {len(tagged)} of {len(self.poses)} poses tagged "
              f"(threshold {self.cfg['seasons'].get('threshold')}); windows: "
              + ", ".join(f"{k}={v['from']}..{v['to']}" if v else f"{k}=any"
                          for k, v in self.cfg["seasons"]["windows"].items()))
        for pose, tag in tagged:
            prov = self.provenance.get(pose.id)
            why = (f"override in pinterest_seasons.yaml" if tag.source == "override"
                   else f"score {tag.score}: " + ", ".join(tag.hits))
            shoot = f" [{prov.shoot}]" if prov else ""
            print(f"  {pose.id}  {pose.slug:<34} {pose.image_source:<5} -> {tag.season:<8} ({why}){shoot}")

    def pool(self):
        return candidates(self.poses, self.prompts, self.gate, self.provenance, self.text_fits,
                          season_of=lambda pose: self.season_tag(pose).season)

    def shoot_of(self, pin_or_cand) -> str | None:
        if isinstance(pin_or_cand, dict):
            return pin_or_cand.get("shoot")
        return pin_or_cand.shoot

    def image_url_for(self, cohort: str, pin_id: str, digest: str, pin_type: str) -> str:
        media = self.cfg["csv"]["media"]
        key = upload.object_key(media["prefix"], cohort, pin_id, digest, render.FORMAT[pin_type][0])
        return upload.public_url(media["public_base_url"], key)

    # -- pin building --------------------------------------------------------

    def build_pin(self, cand, disclosure: str | None) -> tuple[dict, bytes]:
        cfg = self.cfg
        cohort = cand.cohort
        if cand.pin_type == "text":
            prompt = cand.prompt
            self.gate.check_prompt(prompt, self.poses_by_id)
            label = metadata.category_label(cand.category, self.display)
            data = render.render_text_pin(prompt, label, cfg["cohorts"])
            src = next((self.poses_by_id[p] for p in prompt.pose_ids
                        if p in self.poses_by_id), None)
            record = {
                "pin_id": cand.pin_id, "pin_type": "text", "cohort": cohort,
                "source_id": prompt.text, "source_pose_ids": list(prompt.pose_ids),
                "category": cand.category, "shoot": None,
                "title": metadata.text_title(prompt, self.display),
                "description": metadata.text_description(prompt, self.poses_by_id,
                                                         middle_index=self._middle_counter),
                "keywords": metadata.keywords(src, cand.category, self.display,
                                              extra=["posing prompt", "what to say to clients"]),
                "link": metadata.link_for(cfg, cand.category, src, cohort, self.link_fallbacks),
                "board": metadata.board_for(cfg, cand.category, None, "text", cand.pin_id),
                "alt_text": metadata.text_alt(prompt, label),
            }
            self._middle_counter += 1
        else:
            pose = cand.pose
            data = render.render_photo_pin(pose, cfg["cohorts"], self.gate, self.grade)
            disc = disclosure if cohort == "photo_ai" else None
            record = {
                "pin_id": cand.pin_id, "pin_type": "photo", "cohort": cohort,
                "source_id": pose.id, "source_pose_ids": [pose.id],
                "category": cand.category, "shoot": cand.shoot, "season": cand.season,
                "title": metadata.photo_title(pose),
                "description": metadata.photo_description(pose, cand.pin_id, disc),
                "keywords": metadata.keywords(pose, cand.category, self.display),
                "link": metadata.link_for(cfg, cand.category, pose, cohort, self.link_fallbacks),
                "board": metadata.board_for(cfg, cand.category, pose, "photo"),
                "alt_text": metadata.photo_alt(pose, self.display),
                "image_source": pose.image_source,
            }
            if cohort == "photo_ai" and disclosure not in record["description"]:
                raise SystemExit(f"error: photo_ai pin {cand.pin_id} is missing the disclosure")
        assert len(record["title"]) <= metadata.TITLE_MAX
        assert len(record["description"]) <= metadata.DESC_MAX
        record["content_hash"] = upload.content_hash(data)
        # The public URL is content-addressed, so it is known before upload.
        record["image_url"] = self.image_url_for(cohort, cand.pin_id, record["content_hash"],
                                                 cand.pin_type)
        return record, data


# -- generate ----------------------------------------------------------------

def cmd_generate(ctx: Context, *, limit: int | None, cohort: str | None, dry_run: bool,
                 start_date: date | None, pins_per_day: int | None,
                 regenerate: list[str], no_upload: bool = False, per_cohort: int | None = None,
                 preview_scale: int | None = None) -> int:
    cfg = ctx.cfg
    shares = {k: float(v["share"]) for k, v in cfg["cohorts"]["cohorts"].items()}
    if cohort and cohort not in shares:
        sys.exit(f"error: unknown cohort {cohort}; choose from {', '.join(shares)}")

    print(f"Catalog: {len(ctx.poses)} active poses, {len(ctx.prompts)} unique prompts")
    ctx.print_exclusions()
    pool, _, unfit = ctx.pool()
    print("Available per cohort: " + ", ".join(f"{c}={len(pool.get(c, []))}" for c in shares))
    if unfit:
        t = cfg["cohorts"]["render"]["text"]
        print(f"Skipped {len(unfit)} prompts that cannot meet the {t['min_cap_height']}px "
              f"cap-height floor within {t['max_lines']} lines:")
        for pr in unfit:
            print(f"  - ({len(pr.text)} chars) {pr.text}")

    already = set(ctx.manifest["pins"])
    regen = set(regenerate)
    missing = regen - {c.pin_id for cs in pool.values() for c in cs}
    if missing:
        sys.exit(f"error: --regenerate ids not found among candidates: {', '.join(sorted(missing))}")

    weighted = mapped_categories(cfg, {c.category for cs in pool.values() for c in cs})
    ctx.print_season_report()

    # Seasonal pins whose window does not open within the lookahead are left
    # for a later run (logged), so they never burn a slot they cannot use.
    run_start = start_date or date.today()
    se = cfg["seasons"]
    deferred_season: list = []
    if True:  # applies to dry runs too, so the contact sheet reflects the gate
        for cohort_name, cands in list(pool.items()):
            keep = []
            for c in cands:
                if seasons.window_opens_within(se["windows"].get(c.season), run_start,
                                               int(se["lookahead_days"])):
                    keep.append(c)
                elif c.pin_id not in already:
                    deferred_season.append(c)
            pool[cohort_name] = keep
        if deferred_season:
            print(f"Deferred (out of season, window not open within {se['lookahead_days']} days "
                  f"of {run_start}): {len(deferred_season)} pins — left for a later run")
            for c in deferred_season:
                print(f"  - {c.pin_id} ({c.season}, {c.pose.slug})")
    if dry_run:
        picks = select_per_cohort(pool, per_cohort or 4, set(), set(), weighted)
        print(f"\nDRY RUN: rendering a contact sheet of {len(picks)} pins "
              f"({per_cohort or 4} per cohort); nothing uploaded, manifest untouched.")
    elif per_cohort:
        picks = select_per_cohort(pool, per_cohort, already, regen, weighted)
        print(f"\nSelected {len(picks)} new pins ({per_cohort} per cohort); "
              f"{len(already)} already in manifest.")
    else:
        picks = select(pool, shares, limit, already, regen, weighted, cohort)
        print(f"\nSelected {len(picks)} new pins"
              + (f" (cohort {cohort})" if cohort else "") + f"; {len(already)} already in manifest.")
    if not picks:
        print("Nothing to generate.")
        return 0

    needs_disclosure = any(p.cohort == "photo_ai" for p in picks)
    disclosure = config.require_disclosure(cfg) if needs_disclosure else None

    # Schedule first (diversity rules may drop picks), then render only what
    # actually gets a slot.
    slots: dict[str, datetime] = {}
    if not dry_run:
        start = start_date or date.today()
        scheduler = Scheduler.resume(start, manifest_mod.scheduled_times(ctx.manifest),
                                     ramp=cfg["cohorts"].get("ramp", DEFAULT_RAMP),
                                     pins_per_day=pins_per_day)
        existing: dict[str, list[datetime]] = defaultdict(list)
        for p in ctx.manifest["pins"].values():
            if p.get("shoot") and p.get("scheduled_at") and p["pin_id"] not in regen:
                existing[p["shoot"]].append(datetime.fromisoformat(p["scheduled_at"]))
        new_items = [c for c in picks if not (c.pin_id in ctx.manifest["pins"]
                                              and ctx.manifest["pins"][c.pin_id].get("scheduled_at"))]
        allow = lambda c, t: seasons.season_allows(c.season, t.date(), se)  # noqa: E731
        div = cfg["cohorts"].get("diversity", {})
        assigned, dropped = assign_slots(
            new_items, scheduler, ctx.shoot_of, existing, div, allow=allow,
            horizon_days=max(int(div.get("window_days", 30)), int(se["lookahead_days"])))
        slots = {c.pin_id: t for c, t in assigned}
        season_dropped = [c for c, _ in dropped if not seasons.season_allows(
            c.season, scheduler.peek().date(), se) and c.season != "none"]
        for c in season_dropped:
            print(f"Deferred (seasonal, no slot inside the {c.season} window this run): "
                  f"{c.pin_id} ({c.pose.slug}) — left for a later run")
        dropped = [(c, sh) for c, sh in dropped if c not in season_dropped]
        picks = [c for c in picks if c not in season_dropped]
        if dropped:
            by_shoot = Counter(s for _, s in dropped)
            for shoot, n in sorted(by_shoot.items()):
                print(f"WARNING: shoot diversity — dropped {n} pin(s) from shoot '{shoot}': "
                      f"no compliant slot within this run "
                      f"(min {cfg['cohorts']['diversity'].get('min_days_apart', 7)} days apart, "
                      f"max {cfg['cohorts']['diversity'].get('max_per_window', 2)} per "
                      f"{cfg['cohorts']['diversity'].get('window_days', 30)} days). "
                      f"Output reduced rather than violating the rule.")
            dropped_ids = {c.pin_id for c, _ in dropped}
            picks = [c for c in picks if c.pin_id not in dropped_ids]

    groups: dict[str, list[tuple[str, bytes]]] = defaultdict(list)
    grade_pairs: list[tuple[str, Image.Image, Image.Image]] = []
    generated = Counter()
    for cand in picks:
        try:
            record, data = ctx.build_pin(cand, disclosure)
            if dry_run and cand.pin_type == "photo":
                before = render.crop_photo(cand.pose, cfg["cohorts"], ctx.gate)
                grade_pairs.append((cand.pin_id, before, grade.apply_grade(before, ctx.grade)))
        except RightsViolation as exc:
            print(f"\n{exc}", file=sys.stderr)
            return 3
        groups[cand.cohort].append((cand.pin_id, data))
        generated[cand.cohort] += 1
        if dry_run:
            continue
        ext = render.FORMAT[cand.pin_type][0]
        out = ctx.pins_dir / cand.cohort / f"{cand.pin_id.replace(':', '-')}.{ext}"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        prev = ctx.manifest["pins"].get(cand.pin_id)
        same = prev is not None and prev.get("content_hash") == record["content_hash"]
        record.update({
            "local_path": rel(out),
            "scheduled_at": prev["scheduled_at"] if prev and prev.get("scheduled_at")
            else slots[cand.pin_id].isoformat(timespec="minutes"),
            "uploaded_at": prev.get("uploaded_at") if same else None,
            "r2_key": prev.get("r2_key") if same else None,
            "batch_file": prev.get("batch_file") if prev else None,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        })
        ctx.manifest["pins"][cand.pin_id] = record

    ctx.print_link_fallbacks()
    print("Generated per cohort: " + ", ".join(f"{c}={n}" for c, n in sorted(generated.items())))
    if any(c.pin_type == "photo" for c in picks):
        print(f"Colour grade: {'ON' if ctx.grade.get('enabled', True) else 'OFF'} "
              f"(strength {ctx.grade.get('strength')}), applied to photo_real and photo_ai alike")

    if dry_run:
        sheet = render.contact_sheet(groups, ctx.pins_dir / "contact_sheet.png")
        print(f"Contact sheet (full): {rel(sheet)}")
        if preview_scale:
            sheet = render.contact_sheet(groups, ctx.pins_dir / f"contact_sheet_{preview_scale}px.png",
                                         thumb_w=preview_scale)
            print(f"Contact sheet ({preview_scale}px feed preview): {rel(sheet)}")
        if grade_pairs:
            strip = render.grade_strip(grade_pairs, ctx.pins_dir / "grade_before_after.png")
            print(f"Grade before/after strip: {rel(strip)}")
        return 0

    manifest_mod.save(ctx.manifest, ctx.manifest_path)
    print(f"Manifest: {rel(ctx.manifest_path)} ({len(ctx.manifest['pins'])} pins total)")
    print("Next: `pins upload` then `pins csv`" if not no_upload else "Next: `pins csv --no-verify`")
    return 0


# -- upload ------------------------------------------------------------------

def cmd_upload(ctx: Context, *, env: str, tf_dir: str | None, confirm: bool) -> int:
    media = ctx.cfg["csv"]["media"]
    pending = [p for p in ctx.manifest["pins"].values() if not p.get("uploaded_at")]
    if not pending:
        print("Nothing to upload: every pin in the manifest is already uploaded.")
        return 0
    plan = []
    for pin in pending:
        path = REPO_ROOT / pin["local_path"]
        if not path.is_file():
            sys.exit(f"error: {pin['pin_id']} rendered file missing: {pin['local_path']} "
                     f"(re-run `pins generate --regenerate {pin['pin_id']}`)")
        digest = upload.content_hash(path.read_bytes())
        if digest != pin["content_hash"]:
            sys.exit(f"error: {pin['pin_id']} on-disk hash differs from the manifest; regenerate it")
        ext, ctype = render.FORMAT[pin["pin_type"]]
        key = upload.object_key(media["prefix"], pin["cohort"], pin["pin_id"], digest, ext)
        plan.append((pin, path, key, ctype))
    print(f"Upload plan ({env}): {len(plan)} pins -> {media['public_base_url']}/{media['prefix']}/")
    if not confirm:
        for pin, _, key, ctype in plan[:15]:
            print(f"  PUT {key}  ({ctype}; cache-control: {upload.IMMUTABLE_CACHE})")
        if len(plan) > 15:
            print(f"  ... {len(plan) - 15} more")
        print("DRY RUN — re-run with --confirm to upload.")
        return 0
    up = upload.Uploader(env, tf_dir)
    done = skipped = 0
    for pin, path, key, ctype in plan:
        if up.put(path, key, ctype):
            done += 1
        else:
            skipped += 1
        pin["image_url"] = upload.public_url(media["public_base_url"], key)
        pin["r2_key"] = key
        pin["uploaded_at"] = datetime.now().isoformat(timespec="seconds")
    manifest_mod.save(ctx.manifest, ctx.manifest_path)
    print(f"Uploaded {done}, skipped {skipped} unchanged. Manifest updated.")
    return 0


# -- csv ---------------------------------------------------------------------

def cmd_csv(ctx: Context, *, batch_size: int | None, out_dir: Path | None, verify: bool,
            print_rows: bool = False) -> int:
    cfg_csv = ctx.cfg["csv"]
    batch_size = batch_size or int(cfg_csv["batch_size"])
    out_dir = out_dir or ctx.csv_dir
    pending = [p for p in ctx.manifest["pins"].values()
               if not p.get("batch_file") and p.get("scheduled_at")]
    if not pending:
        print("Nothing to write: every scheduled pin is already in a batch file.")
        return 0
    # Schedule order interleaves cohorts by construction (selection alternates
    # cohorts and slots are handed out in selection order).
    pending.sort(key=lambda p: (p["scheduled_at"], p["pin_id"]))
    errors = []
    for pin in pending:
        try:
            if not pin.get("image_url"):
                raise csv_out.RowError(f"{pin['pin_id']}: no image_url")
            csv_out.check_image_url(pin["image_url"], verify=verify)
            if verify and not pin.get("uploaded_at"):
                raise csv_out.RowError(f"{pin['pin_id']}: not uploaded yet — run `pins upload` "
                                       f"first (or --no-verify for local testing)")
            csv_out.validate_row(pin, cfg_csv)
        except csv_out.RowError as exc:
            errors.append(str(exc))
    if errors:
        print(f"error: {len(errors)} rows failed validation; no CSV written:", file=sys.stderr)
        for e in errors[:20]:
            print(f"  - {e}", file=sys.stderr)
        return 1
    existing = {p["batch_file"] for p in ctx.manifest["pins"].values() if p.get("batch_file")}
    start_index = len(existing) + 1
    written = csv_out.write_batches(pending, out_dir, cfg_csv, batch_size, start_index)
    for name, ids in written:
        for pid in ids:
            ctx.manifest["pins"][pid]["batch_file"] = name
        print(f"  wrote {rel(out_dir / name)} ({len(ids)} rows)")
    manifest_mod.save(ctx.manifest, ctx.manifest_path)
    print(f"{sum(len(i) for _, i in written)} rows in {len(written)} batch file(s); columns: "
          + ", ".join(c["name"] for c in cfg_csv["columns"]))
    if print_rows:
        for name, _ in written:
            print(f"\n----- {name} -----")
            sys.stdout.write((out_dir / name).read_text(encoding="utf-8"))
    return 0


# -- status ------------------------------------------------------------------

def cmd_status(ctx: Context) -> int:
    pins = list(ctx.manifest["pins"].values())
    shares = {k: float(v["share"]) for k, v in ctx.cfg["cohorts"]["cohorts"].items()}
    pool, withheld, unfit = ctx.pool()
    print(f"Catalog: {len(ctx.poses)} active poses, {len(ctx.prompts)} unique prompts")
    ctx.print_exclusions()
    print(f"Prompts below the type floor (skipped): {len(unfit)}")
    print("\nAvailable candidates per cohort (after rights gate):")
    for c in shares:
        used = sum(1 for p in pins if p["cohort"] == c)
        print(f"  {c:<11} available={len(pool.get(c, [])):>4}  generated={used:>4}  "
              f"target share={shares[c]:.0%}")
    if not pins:
        print("\nManifest is empty; nothing generated yet.")
        return 0
    total = len(pins)
    print(f"\nGenerated pins: {total}")
    print("By cohort (actual share vs target):")
    for c in shares:
        n = sum(1 for p in pins if p["cohort"] == c)
        print(f"  {c:<11} {n:>4}  {n / total:>6.1%}  target {shares[c]:.0%}")
    print("By category:")
    for cat, n in sorted(Counter(p["category"] for p in pins).items()):
        print(f"  {cat:<11} {n:>4}")
    print("By board:")
    for board, n in sorted(Counter(p["board"] for p in pins).items()):
        print(f"  {board:<24} {n:>4}")
    print("By season (photo pins):")
    for season, n in sorted(Counter(p.get("season") or "none" for p in pins
                                    if p["pin_type"] == "photo").items()):
        print(f"  {season:<11} {n:>4}")
    print("By shoot (photo pins):")
    for shoot, n in sorted(Counter(p.get("shoot") or "(none)" for p in pins
                                   if p["pin_type"] == "photo").items()):
        print(f"  {shoot:<32} {n:>4}")
    print("Pipeline state:")
    uploaded = sum(1 for p in pins if p.get("uploaded_at"))
    batched = sum(1 for p in pins if p.get("batch_file"))
    print(f"  rendered {total}, uploaded {uploaded}, in CSV batches {batched}, "
          f"pending upload {total - uploaded}, pending CSV {total - batched}")
    print("Schedule per cohort:")
    for c in shares:
        times = sorted(p["scheduled_at"] for p in pins if p["cohort"] == c and p.get("scheduled_at"))
        if times:
            print(f"  {c:<11} {len(times):>4} pins  {times[0][:10]} -> {times[-1][:10]}")
    by_day = Counter(p["scheduled_at"][:10] for p in pins if p.get("scheduled_at"))
    if by_day:
        days = sorted(by_day)
        print(f"Scheduled days: {len(days)} ({days[0]} -> {days[-1]}), "
              f"max {max(by_day.values())}/day")
    return 0


# -- scan-rights -------------------------------------------------------------

def cmd_scan_rights(ctx: Context, write_snapshot: bool = True) -> int:
    """Report what the exclusion rules catch, refresh the provenance
    snapshot, and flag drift between excluded_shoots (resolved locally) and
    the checked-in excluded_pose_ids."""
    ex = ctx.cfg["exclusions"]
    print("Exclusion rules:")
    print(f"  filename_patterns: {ex['filename_patterns']}")
    print(f"  excluded_shoots:   {ex['excluded_shoots']}")
    print(f"  excluded_pose_ids: {len(ex['excluded_pose_ids'])} ids")
    print(f"\nProvenance available for {len(ctx.provenance)} poses "
          f"(state/pinterest_provenance.json + inbox/*/_drafts).")
    by_shoot: dict[str, list[str]] = defaultdict(list)
    for pid, prov in ctx.provenance.items():
        if pid in ctx.poses_by_id:
            by_shoot[prov.shoot].append(pid)
    print("Shoots with finalized poses in the catalog:")
    for shoot, ids in sorted(by_shoot.items()):
        files = shoot_source_files(shoot)
        hits = [f for f in files if any(fnmatch.fnmatchcase(f, pat) for pat in ex["filename_patterns"])]
        flag = "  <-- matches filename_patterns" if hits else ""
        print(f"  {shoot}: {len(ids)} poses, {len(files)} source files, "
              f"{len(hits)} pattern hits{flag}")
    if write_snapshot:
        save_snapshot({pid: p for pid, p in ctx.provenance.items() if pid in ctx.poses_by_id})
        print("Provenance snapshot written to state/pinterest_provenance.json")
    resolved = {pid for shoot, ids in by_shoot.items() if shoot in ex["excluded_shoots"]
                for pid in ids}
    listed = set(ex["excluded_pose_ids"])
    print()
    ctx.print_exclusions()
    drift = resolved - listed
    if drift:
        print(f"\nDRIFT: {len(drift)} poses from excluded shoots are NOT in excluded_pose_ids "
              f"(add them so the exclusion survives without inbox/):")
        for pid in sorted(drift):
            print(f"  - {pid}   # {ctx.provenance[pid].source_file}")
        return 1
    print("\nexcluded_pose_ids covers every pose resolved from excluded_shoots.")
    return 0


# -- grade-profile -----------------------------------------------------------

def cmd_grade_profile(ctx: Context) -> int:
    """Measure the real and AI photo sets so the grade targets can be tuned."""
    import statistics
    sets: dict[str, list[dict]] = defaultdict(list)
    for pose in ctx.poses:
        if pose.image_source in ("photo", "ai") and not ctx.gate.is_excluded(pose):
            with Image.open(pose.detail_path) as im:
                sets[pose.image_source].append(grade.measure(im))
    keys = ("rb_ratio", "mean_luminance", "luminance_std", "saturation")
    print(f"{'set':<8}{'n':>5}  " + "  ".join(f"{k:>15}" for k in keys) + "   (median)")
    for name, ms in sorted(sets.items()):
        print(f"{name:<8}{len(ms):>5}  " + "  ".join(
            f"{statistics.median(m[k] for m in ms):>15.3f}" for k in keys))
    print("\nCurrent targets: " + ", ".join(f"{k}={ctx.grade['target'][k]}" for k in keys))
    return 0


# -- seasons -----------------------------------------------------------------

def cmd_seasons(ctx: Context) -> int:
    """Print the seasonal tagging report (what was tagged, and why)."""
    ctx.print_season_report()
    untagged = [p for p in ctx.poses if not ctx.gate.is_excluded(p)
                and ctx.season_tag(p).season == "none"]
    print(f"Evergreen (none): {len(untagged)} poses schedule any time.")
    return 0
