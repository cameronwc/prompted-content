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

## Adding a real pose

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
categories: [couples, engagement]
subject_count: 2
subject_types: [adult]        # never more types than subject_count
light_conditions: [golden, backlit]
location_types: [field]
orientation: vertical
difficulty: easy              # easy | moderate | advanced
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
