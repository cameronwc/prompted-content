# STATUS

## Live infrastructure & first publish (2026-08-29, third work session)

- **All 50 AI images generated** (46 in this session, 0 failures, ≈$3.08).
  Catalog v5: `ai=50, synthetic=190`, all 240 records valid.
- **Infrastructure is live** on account `9e255bb…`: bootstrap state bucket
  (`prompted-terraform-state`, local bootstrap state kept out of git),
  `prompted-content-dev` and `prompted-content` created via the dev/prod
  Terraform envs with real S3-backend state in R2. r2.dev public access
  explicitly disabled on both; no custom domain yet (no zone owned).
  One-time manual step performed by the operator: enabling R2 on the
  account (subscription activation, not infrastructure).
- **Dev is published and verified**: catalog/v5.json + latest.json +
  every referenced image uploaded; `make verify-published` confirms all
  480 referenced keys exist. Bug found and fixed during this: publish
  uploaded hardcoded image names instead of the names each record
  references, so AI poses' `_ai` files were initially missing. The first
  publish also left 100 unreferenced plain-named files for AI poses in
  the dev bucket; retained (nothing is ever deleted), harmless.
- **Prod bucket exists but is unpublished** — when wanted:
  `make publish CONFIRM=1 ENV=prod`.
- **Credentials** live in a gitignored operator env-file at the repo root,
  loaded by the Makefile so no command echoes them. ROTATE when
  convenient: the Gemini key and the Cloudflare API token both passed
  through session transcripts.
- Remaining gaps: CI still never executed (no GitHub remote); custom
  domain + cache/allowlist rules dormant until a zone exists; the app
  reads via S3 credentials or a future public domain — there is currently
  no anonymous public URL for the content (by design).


## AI image generation (added 2026-08-29, second work session)

### Complete and verified

- **`image_source` schema field** — optional enum `synthetic|ai|photo`,
  default `synthetic`. All 240 pre-existing records pass unchanged;
  `schema_version` untouched. Validator reports per-source counts.
- **Stratified selection** (`tools/select_ai_subset.py`, `make ai-select`) —
  50 poses (family 15 / couples 15 / senior 10 / maternity 10), pure greedy
  with no RNG; two runs are byte-identical. Verified against current
  records: harsh_overhead=10 (≥6), blue/night_flash=12 (≥6),
  mobility-tagged=13 (≥3), horizontal=17, family subject counts {3,4,5,6},
  ≤3 poses per location type within a category. Re-selected after the
  operator's `soft_low`/`mid` taxonomy commits (2205ba4, f36b87f) changed
  pose light metadata mid-task.
- **Generator** (`tools/generate_ai_images.py`) — `gemini-3.1-flash-image`
  via the Interactions API (endpoint, request shape, and pricing checked
  against current docs). Prompts built solely from record metadata; the
  record is never derived from the image. Hero-first per category with the
  hero passed as a style reference; fixed style suffix. All images 1K by
  operator decision (2026-08-29; original plan had 2K heroes). Dry-run,
  `--limit`, `--ids`, cost confirmation, exponential backoff, per-image
  logging, skip-and-continue on failure — all exercised. Resumability
  verified: a rerun reports "4 of 50 already generated" and does nothing.
- **4 of 50 images generated live** — family hero, a harsh_overhead
  5-subject family, a horizontal night_flash family, a golden 5-subject
  family. All exactly 400×500 / 1200×1500, EXIF `Software` = "AI-generated
  placeholder (gemini-3.1-flash-image) -- Prompted UI test fixture, not for
  release", real blurhashes in pose.yaml, `image_source: ai`,
  `placeholder: true` retained. Visual spot-check confirmed photographic
  content with hard shadows.
- **Integration** — `make ai-select` / `ai-dry-run` / `ai-generate`
  (CONFIRM=1-gated, matching publish/tf-apply). `build_catalog.py` emits
  `image_source` on every pose and prints the per-source breakdown
  (`ai=4, synthetic=236`). Full `make validate` + `make build` pass with
  the 4 AI images in place (catalog_version continued 3 → 4).
- **`STRICT_NO_PLACEHOLDER` gate** — **new in this session, not
  pre-existing.** The brief described it as an existing mechanism; a
  full-tree grep confirmed no such gate existed, so it was added (disclosed)
  to `build_catalog.py`: `STRICT_NO_PLACEHOLDER=1 make build` refuses while
  any record has `placeholder: true`. Verified: exits 1 listing the 240
  placeholder poses.

### Incomplete / known costs

- **46 of 50 images not yet generated** (operator's call on spend). Exact
  command, resumable and skipping the 4 done:

      export GEMINI_API_KEY=...
      make ai-generate CONFIRM=1        # ≈ 46 × $0.067 ≈ $3.08 at 1K

- **Wasted spend during verification:** the first live attempt burned ~24
  billed generations (~$1.60–1.70) because the response parser did not know
  the Interactions API's `steps[]` shape; images were generated and paid
  for but discarded. Fixed, then verified on a single image before
  continuing. Total incurred this session ≈ $2.00 including the 4 kept
  images and one diagnostic call.
- The API key was pasted into the session transcript by the operator to
  unblock generation; it lives in no file and no commit, but **rotate it**.
- `soft_low`/`mid` prompt phrasing and the multi-light "primary light only"
  rule are my judgment calls, unreviewed.

---

# Original pipeline STATUS (first session)

Honest state of the repo as of 2026-08-29. Every "verified" claim below was
run in this working tree, not assumed.

## Complete and verified

- **Scaffold & Makefile** — `make` prints the target list; all targets
  (`seed`, `validate`, `build`, `publish`, `clean`, `tf-plan-*`,
  `tf-apply-*`) exist. Apply targets refuse to run without `CONFIRM=1`.
- **Taxonomy** — five YAML files with stable `id` / `display` / optional
  `parent`. `engagement` has parent `couples`. Displays are renameable
  without touching pose records.
- **Schema** — `schema/pose.schema.json` (draft 2020-12) covering exactly
  the specified fields. Verified to accept a valid pose and reject one
  missing a required field.
- **Validator** — schema conformance, referential integrity, the
  `nervous_client` prompt invariant, ULID and slug uniqueness (plus
  directory-name/id match), image presence/size/aspect (4:5 within 1%,
  400w/1200w), and subject-count coherence. Four deliberate breakages each
  produced a distinct, path-specific error; exit codes verified.
- **Seed catalog** — 240 poses: 60 per category, 20 of the 60 couples poses
  also `engagement`. Distributions verified: family `subject_count` 3–7 with
  mixed kid types, senior = 1×teen, maternity 16/60 with partner and 25/60
  `late_term`, difficulty 120 easy / 100 moderate / 20 advanced, golden-hour-
  heavy light mix, category-weighted locations, minority accessibility tags.
  Prompts: 3 per pose, distinct tones, `nervous_client` always present;
  218 unique hand-written lines, max reuse of any line is 5 across 240
  poses; subject-dependent lines (toddler/pet) only appear on poses that
  contain that subject type. Generator is deterministic (fixed RNG seed and
  ULID timestamp base) and refuses to delete poses with `placeholder: false`.
- **Placeholder images** — 480 JPEGs, all exactly 400×500 / 1200×1500,
  muted category tints, pose id/slug/category/subject-count/light rendered
  on-tile, diagonal "PLACEHOLDER / NOT A PHOTOGRAPH" banner. Real blurhash
  computed per image and written back into each `pose.yaml`.
- **Catalog builder** — refuses to build on validation failure (validation
  runs in-process first), embeds taxonomy + 240 poses, recomputes blurhash
  from the images at build time, rewrites image paths as bucket keys,
  increments `catalog_version` (verified 1 → 2 across two builds), prints
  the summary. All 240 embedded poses re-validate against the pose schema.
- **Terraform** — provider pinned 5.24.0 (resource names checked against the
  v5.24.0 provider docs, not assumed). `terraform fmt`, `init`, `validate`
  pass in bootstrap and both envs. Bootstrap `plan` runs clean (1 bucket).
  A dev-env `plan` (see caveats) shows 5 resources: content bucket,
  custom-domain binding, explicitly disabled r2.dev domain, path-allowlist
  ruleset, cache ruleset (300s `latest.json`, 1y immutable paths). Both
  buckets carry `lifecycle { prevent_destroy = true }`. No credentials in
  any tracked file (swept).
- **Publish** — dry-run prints every object key with content-type and
  cache-control. Config resolution from `terraform output -json` verified
  against a real (scratch) state; env-var fallback path verified with a
  clear one-line warning. Upload requires `--confirm`; nothing is ever
  deleted; versioned `catalog/vN.json` + `latest.json` pointer.

## Incomplete or unverified

- **No live Cloudflare account is connected.** Consequences:
  - `terraform apply` has never run anywhere (per instructions).
  - `envs/*` S3-backend `terraform init` has never run against real R2; it
    was verified with `-backend=false`, and the dev plan was produced in a
    throwaway copy using a `backend_override.tf` (local backend) with
    placeholder account/zone IDs. First real init happens after bootstrap
    apply.
  - `publish.py --confirm` (the actual upload path, boto3 head/put) has
    never executed against a real bucket. Dry-run only.
- **CI workflows have never executed** — the repo has no GitHub remote yet.
  Both workflows are written but unexercised.
- **Custom domain / cache rules are dormant** until `zone_id` and
  `custom_domain` are set in an env's `terraform.tfvars`. Registering the
  domain and adding the zone to the account is a documented manual step.
- `requirements.txt` uses minimum-version bounds, not exact pins. Exact
  installed versions from the verified run: Pillow 12.3.0, PyYAML 6.0.3,
  blurhash-python 1.2.2, jsonschema 4.26.0, boto3 1.43.83, python-ulid 4.0.1.

## Deviations from the prompt, with reasons

1. **Phase 3 was validated with `validate.py --no-images`** (a flag added
   for exactly this), because the image checks in `make validate` require
   Phase 4's placeholder output. The fully clean `make validate` over all
   240 poses ran immediately after Phase 4 and on every build since. `make
   seed` now chains generator + placeholders, so a fresh seed passes full
   validation in one step.
2. **"Public read access for the catalog path"** — R2 has no per-path
   bucket ACL. Implemented as the closest explicit equivalent: the r2.dev
   whole-bucket toggle is declared and *disabled*, public access exists only
   through the custom-domain binding, and a zone firewall ruleset blocks
   every path on that host except `/catalog/*`, `/poses/*`, and
   `/latest.json`.
3. **Prompt copy reuse** — with 720 prompt slots and hand-written banks,
   some lines repeat across poses (max 5 of 240). Lines are dealt
   shuffle-without-replacement per bank so reuse is spread evenly; no two
   sampled poses share a full prompt set. Judged acceptable for a dev
   catalog; noted so nobody mistakes the banks for unlimited.
4. **Bootstrap/env plans used placeholder credentials** (`CLOUDFLARE_API_TOKEN`
   set to a non-secret dummy) since plan makes no API calls for these
   resources. Plans against a live account may differ in computed values,
   not in resource shape.
