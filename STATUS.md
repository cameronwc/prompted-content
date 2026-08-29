# STATUS

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
