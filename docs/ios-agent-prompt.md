# Task: Implement remote pose catalog consumption in the Prompted iOS app

Prompted is a posing reference app for portrait photographers. Its content
(the pose catalog) is produced by a separate content-pipeline repo and
served from Cloudflare R2. Your job is to make the app load, cache, and
render that catalog. The app **never generates or mutates content** — it is
a pure consumer.

## Base URL

```
https://pub-38ee305366a94cfeb5daf8e4f4c51dc9.r2.dev
```

This is a rate-limited r2.dev development URL. Put it behind a single
configuration point (e.g. `ContentEnvironment.baseURL`) — it WILL change to
a custom domain later, and nothing but that one constant should need to
change. No auth, no SDK; plain HTTPS GETs.

## Read protocol

1. `GET {base}/latest.json` → `{"path": "catalog/v5.json", "catalog_version": 5}`.
   Small and cheap (5-minute cache headers). If `catalog_version` equals the
   locally cached catalog's version, use the cache and stop.
2. `GET {base}/{path}` (e.g. `catalog/v5.json`) → the full catalog
   (~330 KB JSON). Versioned catalog files are **immutable**: cache to disk,
   never revalidate a version you already have.
3. Images: prefix each pose's `image.thumb` / `image.detail` with the base
   URL — they are relative keys like `poses/<ulid>/thumb_ai.jpg`. Immutable:
   cache aggressively, never revalidate (the server sends
   `cache-control: public, max-age=31536000, immutable`).
4. Offline: last successfully cached catalog + images must keep working
   with no network. First launch with no network shows an empty/error state.

## Catalog shape

Top level:

```json
{
  "schema_version": 1,
  "catalog_version": 5,
  "generated_at": "2026-08-29T…+00:00",
  "taxonomy": { "categories": [...], "light_conditions": [...],
                "location_types": [...], "subject_types": [...],
                "accessibility": [...] },
  "poses": [ ... 240 records ... ]
}
```

- **`schema_version`**: hard-gate on it. If it is greater than the version
  the app understands, keep the last understood cached catalog and surface
  a "content update requires app update" state. Never attempt to parse
  forward-incompatible content.
- **Taxonomy entries** are `{id, display, parent?}`. Build every filter
  chip, section header, and label from taxonomy `display` strings, keyed by
  `id`. **Never hardcode taxonomy IDs or display strings in the app.** IDs
  are stable forever; display strings can change between catalogs.
  `engagement` has `parent: couples` — child categories render nested/
  indented under their parent wherever categories are listed.

A pose record:

```json
{
  "id": "01M15D1T4FE4R9T3F1HNPWJ063",
  "slug": "heads-together-huddle",
  "image": {
    "thumb": "poses/01M15D1T4FE4R9T3F1HNPWJ063/thumb_ai.jpg",
    "detail": "poses/01M15D1T4FE4R9T3F1HNPWJ063/detail_ai.jpg",
    "blurhash": "d66a#JIo0K?HTKoenNR-…"
  },
  "placeholder": true,
  "image_source": "ai",
  "categories": ["family"],
  "subject_count": 4,
  "subject_types": ["adult", "child", "toddler"],
  "light_conditions": ["blue", "harsh_overhead", "mid"],
  "location_types": ["forest"],
  "orientation": "vertical",
  "difficulty": "moderate",
  "prompts": [
    {"text": "…", "tone": "nervous_client"},
    {"text": "…", "tone": "calm"},
    {"text": "…", "tone": "playful"}
  ],
  "gear": {"focal_mm": [50, 85], "aperture": "f/2.8", "needs_reflector": false},
  "accessibility": [],
  "version": 2,
  "status": "active"
}
```

Field semantics the app must honor:

- `status`: render only `"active"` poses. `"retired"` records stay in the
  catalog for ID stability; hide them everywhere.
- `image.thumb` is 400×500, `image.detail` 1200×1500 — both 4:5. The grid
  is 2 columns of 4:5 cells; `orientation: horizontal` poses still ship as
  4:5 images (laterally composed) and get no special cell treatment.
- `blurhash`: decode and show as the placeholder in grid cells and the
  detail view while the JPEG loads. This is a first-class feature, not a
  nicety — placeholder quality during grid scroll is one of the things the
  current image set exists to evaluate.
- `prompts`: 2+ entries, tones from `playful | calm | romantic |
  nervous_client`; every pose has at least one `nervous_client` prompt.
  Shoot Mode shows one prompt at a time as large overlaid text on the
  detail image with a scrim; tone is switchable. Prompt copy is verbatim
  photographer direction — display it exactly, no truncation without
  expansion.
- `image_source` (`synthetic | ai | photo`; treat a missing field as
  `synthetic`): the current catalog is 50 `ai` / 190 `synthetic`. The mixed
  set is intentional — the UI must look right against both.
- `placeholder: true` means the imagery is a stand-in (synthetic tile or AI
  generation). Show a small unobtrusive "placeholder" indicator in the
  detail view only — never on the grid, and never anything that obscures
  the image (the AI set specifically exists to test text-over-image
  contrast unobstructed).
- `subject_count`, `light_conditions`, `location_types`, `difficulty`,
  `accessibility`, `gear`: filter/browse dimensions and detail-view
  metadata. Multi-select filters within a dimension OR together; across
  dimensions AND together.

## What NOT to do

- No hardcoded pose data, taxonomy entries, or counts anywhere.
- Do not mutate, re-derive, or "fix" catalog data client-side.
- Do not fetch `catalog/vN.json` speculatively; only what `latest.json`
  names.
- Do not treat `latest.json`'s absence/failure as fatal when a cached
  catalog exists — fall back silently.
- Do not ship any of the current imagery in the app bundle.

## Acceptance criteria

1. Cold launch on network: catalog v5 loads, grid shows 240 → filtered
   active poses with blurhash-first cells.
2. Airplane mode after one successful load: everything cached still browses.
3. `latest.json` bumped to a new version → next foreground refresh picks it
   up; unchanged version → zero catalog/image re-downloads.
4. Filters are built from the embedded taxonomy (renaming a `display` in a
   future catalog changes the UI with no app change).
5. Shoot Mode renders every prompt tone legibly over the darkest
   (`night_flash`, `blue`) and brightest (`harsh_overhead`) AI images —
   these exist in the catalog specifically as the extremes to test against.
6. A `schema_version: 2` catalog is refused gracefully.
