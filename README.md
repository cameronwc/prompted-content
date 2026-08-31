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
