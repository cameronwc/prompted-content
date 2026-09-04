# prompted-content

Content pipeline for **Prompted**, the iOS posing reference app. This repo
owns the pose catalog: taxonomy, per-pose records, images, validation, and
publishing. The app consumes `catalog.json` (served from R2) and never
generates content itself.

```
taxonomy/        stable-ID vocabularies (categories, light, locations, …)
schema/          JSON Schema for a pose record
poses/<ulid>/    one directory per pose: pose.yaml + thumb.jpg + detail.jpg
tools/           validate / seed / placeholders / build / publish
infra/           Terraform (Cloudflare R2 + rules) — see infra/README.md
dist/            built catalog.json (committed; carries catalog_version)
```

Quick reference:

| Command | Does |
|---|---|
| `make seed` | regenerate the 240-pose dev catalog + placeholder images |
| `make validate` | validate every pose; non-zero exit on any failure |
| `make build` | validate, then write `dist/catalog.json` |
| `make publish` | dry-run upload plan; `CONFIRM=1` to really upload |

Python 3.11+; `make` creates `.venv` automatically.

## Photo ingest pipeline (the normal path for real photography)

Turns a folder of raw session exports into validated, tagged, published
pose records. The camera records no GPS: location and timezone are
supplied once per shoot and drive the solar light-band derivation, so get
the timezone right — a wrong zone shifts every band by hours, silently.

### 1. Export the session from Lightroom

One export of the whole session (not per-pose):

- Crop: 4×5 where you can; non-4:5 frames are flagged, never auto-cropped
- File: JPEG, sRGB, quality ~90, **full resolution** (short edge ≥ 1200 px)
- Metadata: **keep all EXIF** — capture time, focal, aperture, ISO and the
  flash flag feed the pipeline (there's no GPS to strip)
- No watermark, no sharpening beyond your normal export

### 2. Create the shoot manifest

```sh
make ingest-init            # interactive: name, location, timezone
```

Saved locations come from `locations.yaml` (typed once, reused by name);
the timezone is auto-suggested from the coordinates and must be confirmed.
Then drop the exports into `inbox/<shoot-name>/` (gitignored).

### 3. Run the pipeline

```sh
make ingest-scan     SHOOT=<name>   # EXIF -> _scan.json (UTC per frame)
make ingest-quality  SHOOT=<name>   # blur/exposure/resolution gates -> _rejects.json
make ingest-cluster  SHOOT=<name>   # near-duplicates -> one candidate per pose
make ingest-derive   SHOOT=<name>   # solar elevation -> light band, gear from EXIF
make ingest-prompts  SHOOT=<name> CONFIRM=1   # Gemini prompt copy (needs GEMINI_API_KEY)
make ingest-draft    SHOOT=<name>   # drafts + _review.md
```

Rejects are flags, never deletions (`--keep` via
`INGEST_ARGS="--keep <file>"` force-keeps). Wrong cluster pick:
`INGEST_ARGS="--select c03 DSCF1234.jpg" make ingest-cluster ...`.
Prompt copy off-key: `INGEST_ARGS="--regenerate c03 --note 'less cheesy'"
make ingest-prompts ... CONFIRM=1`.

### 4. Review — the human part

Open `inbox/<shoot>/_review.md`. For each candidate: fill every `TODO:`
in its `_drafts/<ulid>.yaml` (slug, categories, subject count/types,
difficulty, accessibility, plus `backlit`/`open_shade` if true — those are
never inferred), and **read the posing instructions and the three
generated prompts**. Instructions are photographer-facing setup steps
(how to arrange the subjects — every photo pose ships with them);
prompts are the lines said aloud. Approve by setting
`prompts_approved: true`, editing the text (an edit counts as review), or
`make approve-prompts SHOOT=<name>` after reading everything.

### 5. Finalize and publish

```sh
make ingest-finalize SHOOT=<name>   # refuses incomplete/unapproved drafts
make validate
git add poses/ && git commit
make publish-dev CONFIRM=1          # build once, upload to dev
make verify-dev                     # fetch + validate what's actually published
make promote-prod CONFIRM=1         # copy the EXACT dev artifact to prod (prints diff)
```

Finalize writes `poses/<ulid>/` (1200w detail, 400w thumb, blurhash) and
moves the source frames to `archive/<shoot>/` — nothing is ever deleted.
Prod is never rebuilt: `promote-prod` copies the verified dev catalog
byte-for-byte, refuses placeholders, retains all previous versions, and
`make rollback-prod TO=<version> CONFIRM=1` is a pointer flip.

## Adding a real pose by hand (fallback)

### 1. Export from Lightroom

Two exports per pose, both **4:5 crop** (portrait). Validation enforces the
aspect within 1% and the exact widths.

- Crop: 4×5 in the Crop tool (this is the aspect the app's grid uses)
- File: JPEG, sRGB, quality ~80, no watermark
- Detail export: resize to **width 1200 px** (→ 1200×1500), sharpen for screen
- Thumb export: resize to **width 400 px** (→ 400×500)
- Strip location metadata (these ship publicly)

### 2. Create the pose directory

Mint a ULID (never reuse one, even from a deleted pose):

```sh
.venv/bin/python -c "from ulid import ULID; print(ULID())"
mkdir poses/<ULID>
```

Copy the exports in as `poses/<ULID>/thumb.jpg` and `poses/<ULID>/detail.jpg`.

### 3. Write `pose.yaml`

The directory name and `id` must match. All list values must be IDs from
`taxonomy/*.yaml` — IDs are permanent; only `display` strings are renameable.

```yaml
id: <ULID>                    # same as the directory name
slug: forehead-lean-golden    # kebab-case, unique across the repo
image:
  thumb: thumb.jpg
  detail: detail.jpg
  blurhash: "L00000"          # any string; build recomputes the real one
placeholder: false            # real photography
image_source: photo
categories: [couples, engagement]
subject_count: 2
subject_types: [adult]        # never more types than subject_count
light_conditions: [golden, backlit]
location_types: [field]
orientation: vertical
difficulty: easy              # easy | moderate | advanced
instructions:                 # photographer-facing setup steps — required
  - Stand them face to face, foreheads a hand-width apart, hands clasped low.
  - Feet staggered so their leading shoulders overlap toward the camera.
prompts:                      # ≥2; at least one nervous_client — enforced
  - text: Press your foreheads together and close your eyes for three seconds.
    tone: romantic
  - text: You don't have to look at the camera. Just look at each other.
    tone: nervous_client
gear:
  focal_mm: [50, 85]          # [min, max]
  aperture: f/1.8
  needs_reflector: false
accessibility: []             # seated_variant, plus_size_flattering, …
version: 1
status: active                # retire poses; never delete their IDs
```

Prompt copy is read verbatim in the app while shooting: write it the way
you'd actually talk a nervous client through the pose.

### 4. Validate → build → publish

```sh
make validate         # per-pose report; fix anything it names
make build            # writes dist/catalog.json, bumps catalog_version
make publish          # dry run: prints every key it would write
make publish CONFIRM=1 ENV=dev    # real upload to the dev bucket
```

Publishing reads the bucket/account from `terraform output` in
`infra/envs/<env>` (falling back to `PROMPTED_BUCKET` /
`PROMPTED_ACCOUNT_ID` with a warning) and needs an R2 S3 token pair in
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`. It writes an immutable
`catalog/v<N>.json`, uploads any new `poses/<ulid>/` images, updates the
`latest.json` pointer, and never deletes anything.

Commit the pose directory and the rebuilt `dist/catalog.json` together.

## AI-generated images

**Policy (operator decision 2026-08-31):** the catalog ships a mix of AI
imagery and real photography in both dev and prod until enough real
photoshoots exist to replace the AI set. AI poses carry
`image_source: ai` and `placeholder: false`; replace them shoot by shoot
via the ingest pipeline and retire nothing — a real pose simply supersedes
an AI one when its record is updated with photo imagery.

### History: original dev-fixture role

A stratified subset of 50 poses carries AI-generated images
(`image_source: ai`, files `thumb_ai.jpg` / `detail_ai.jpg`) instead of the
synthetic tiles, so the iOS UI can be evaluated against photographic
content: Shoot Mode text contrast, blurhash quality during grid scroll,
grid cohesion, and contact-sheet legibility.

**These are UI test fixtures scheduled for replacement by real
photography. Nothing AI-generated ever ships.** They keep
`placeholder: true`, carry an EXIF `Software` tag naming them AI
placeholder content, and are refused by the `STRICT_NO_PLACEHOLDER=1`
release gate on `make build`. There is deliberately no visible watermark —
a mark would obscure the contrast behaviour the images exist to test.

```sh
make ai-select                    # deterministic 50-pose subset -> dist/ai_subset.json
make ai-dry-run                   # print every constructed image prompt; free
export GEMINI_API_KEY=...         # env var only; never in a file or in git
make ai-generate CONFIRM=1        # generate (resumable; skips what exists)
make ai-generate CONFIRM=1 AI_ARGS="--limit 5"   # capped test run
```

Generation uses `gemini-3.1-flash-image` at 1K (~$0.067/image, ~$3.40 for
the full 50). Image prompts are built only from each pose record's own
metadata — the record is never edited to match the generated image. The
first pose per category is generated first and passed as a style reference
to the rest of its category.

## Pinterest pins

`tools/pins.py` turns the catalog into Pinterest-ready pins, uploads them to
R2 under `pins/`, and writes bulk-upload CSVs on a ramped schedule. Pins are
split into three tracked cohorts (`text`, `photo_real`, `photo_ai`) so
performance can be compared by content type; cohorts share boards and are
separated only by `utm_campaign`.

```sh
make pins-dry-run                                   # dist/pins/contact_sheet.png, 4 pins per cohort
.venv/bin/python tools/pins.py generate --dry-run --preview-scale   # + contact_sheet_236px.png (feed size)
                                                    #   + grade_before_after.png (colour grade review)
make pins-generate PINS_ARGS="--limit 100 --start-date 2026-09-08"
make pins-generate PINS_ARGS="--cohort photo_ai --limit 20"
make pins-upload                                    # dry run; CONFIRM=1 ENV=dev|prod to upload
make pins-csv PINS_ARGS="--batch-size 100"          # dist/pins_csv/pins_batch_001.csv, ...
make pins-status                                    # counts by cohort/category/board, schedule
make pins-scan-rights                               # exclusion report + drift check
```

Config lives in `config/pinterest_*.yaml` (boards, links, CSV columns,
exclusions, cohorts + rendering + shoot diversity, colour grade, copy
humanizer, seasons). State is `state/pinterest_manifest.json`:
every pin ever generated with its cohort, content hash, board, scheduled
time and batch file. Re-running `generate` only produces pins not yet in the
manifest; `--regenerate <pin-id>` forces a rebuild (schedule slot kept).
Image URLs are content-addressed and known at generate time; `pins csv`
verifies each one is reachable unless `--no-verify` is passed. `--workdir DIR`
runs everything against a scratch manifest, and `--per-cohort N` picks equal
counts per cohort; `pins csv --print` echoes the rows to stdout:

```sh
tools/pins.py --workdir /tmp/pins-check generate --per-cohort 4 --start-date 2026-09-08
tools/pins.py --workdir /tmp/pins-check csv --batch-size 12 --no-verify --print
```

**Text pins are sized for the feed.** Pinterest shows pins at ~236px wide,
so the prompt has a 90px cap-height floor on the 1500px canvas and the label
is 42px with tracking. Auto-fit steps down from 200pt; a prompt that cannot
meet the floor within 7 lines is skipped and listed in the run output rather
than shrunk. Category backgrounds are five warm neutrals that differ by at
least ΔE 12 (CIEDE2000), so category reads at thumbnail size.

**Photo pins are graded to one look.** `config/pinterest_grade.yaml` holds a
reference profile measured from the real photograph set (`pins grade-profile`
re-measures); every photo pin — real and AI alike — is normalised toward it
before the scrim, so the cohort test measures real-vs-AI, not warm-vs-moody.

**Boards and guide links are keyed by category.** A tag (light, location,
subject type, `large_group`) only picks between rules for the same category
and never sends a pose to another category's board or guide. Text pins go
to their category board, with a configurable share (default 25%) routed to
the secondary Posing Prompts board.

**Copy is humanized and varied.** `config/pinterest_copy.yaml` maps subject
count + types + category to phrases ("a family of four with a toddler and
grandparents", "an expecting couple") and holds the rotating middle clause
for text-pin descriptions; the closing CTA rotates independently. No
description ever prints a raw enum list.

**Seasonal gate.** `config/pinterest_seasons.yaml` derives a `season`
(none, spring, summer, fall, holiday) from each pose's own text, location and
source shoot name by weighted keywords, with per-pose overrides. Holiday
content schedules only Nov 1–Dec 20, fall Sep 1–Nov 15, evergreen any time.
Pins whose window does not open within `lookahead_days` of the run start are
left for a later run and listed in the output. `pins seasons` prints the
tagging report with the matched keywords.

**Shoot diversity.** Photo pins from one shoot are never scheduled within 7
days of each other and at most 2 land in any rolling 30 days
(`diversity:` in `pinterest_cohorts.yaml`). Pins that cannot get a compliant
slot are dropped with a warning naming the shoot. Shoot provenance comes
from `inbox/*/_drafts` and the checked-in snapshot
`state/pinterest_provenance.json` that `pins scan-rights` refreshes.

**Rights exclusion is absolute.** Images from Act Naturally Photos (the
123 Farm lavender shoot) are not licensed for this use. `config/pinterest_exclusions.yaml`
carries three layers — `filename_patterns` (default `ACTNATURALLY_PHOTOS-*`),
`excluded_shoots`, and `excluded_pose_ids` — and the photo renderer checks
all three before reading a pixel; an excluded asset reaching it is a
non-zero exit. Pose records don't store their shoot (that lives in the
gitignored `inbox/*/_drafts`), so shoot exclusions are also materialised as
explicit ids; `make pins-scan-rights` reports drift. Every run prints how
many poses were excluded and by which rule.

**AI imagery is included but disclosed.** `photo_ai` descriptions end with
`ai_disclosure` from `pinterest_cohorts.yaml`; an empty value fails the run.
EXIF is not manipulated to evade detection — pins are re-rendered (text as
PNG, photo as JPEG, both under 800 KB) and disclosed in the copy.

Schedule: week 1 5/day, week 2 8/day, week 3 12/day, then 25/week, between
06:00 and 20:00 with jitter and never the same minute twice; override with
`--pins-per-day` / `--start-date`. Metadata (title < 90, description < 300
with ~8 rotating closers, 5–10 keywords, alt text, board, link + UTM) is
derived deterministically from catalog fields — no LLM calls.

Tests: `make test` (`tests/test_pinterest.py`).

## Rules that keep the catalog sane

- Taxonomy IDs and pose ULIDs are permanent. Retire (`status: retired`),
  never delete or reuse.
- Every pose has a `nervous_client` prompt. This is a product invariant,
  not a style preference.
- `dist/catalog.json` stays committed — `catalog_version` continuity
  depends on it.
- Placeholder assets (everything with `placeholder: true`) are dev-only and
  are regenerated wholesale by `make seed`; don't hand-edit them.

## Infrastructure and CI

Terraform for the R2 buckets, public access, and cache rules lives in
`infra/` — layout, credential environment variables, minimum-scope API
token, and bring-up order are documented in [infra/README.md](infra/README.md).
CI validates the catalog on every push and runs `terraform fmt`/`validate`/
`plan` on PRs touching `infra/`; **apply is never automated**.

Current completeness and known gaps: [STATUS.md](STATUS.md).
