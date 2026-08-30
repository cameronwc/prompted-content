# Task: Render posing instructions in the Prompted iOS app

Follow-up to `ios-agent-prompt.md` (the catalog-consumption task — its
protocol, caching, and taxonomy rules all still hold). The catalog now
carries a new per-pose field, `instructions`, and this task is to decode
and render it. The app remains a pure consumer: never generate, mutate,
or "fix" catalog content client-side.

## The new field

```json
{
  "id": "01M1A2T18EK6CDF5SBQH7NXPQ2",
  "image_source": "photo",
  "instructions": [
    "Place the subject alone at the waterline, body angled 45 degrees toward the setting sun.",
    "Weight on the back foot, front knee soft, hands loose at the sides.",
    "Have them look out past the surf, chin level, not at the camera."
  ],
  "prompts": [ ... ]
}
```

- **`instructions`: optional array of 1–6 non-empty strings**, in order.
  Each string is one setup step.
- Present on every `image_source: "photo"` pose (the pipeline enforces
  this) and **absent from all 240 current `synthetic`/`ai` records** — so
  the decoder MUST treat it as optional, and the UI MUST look complete
  without it. Do not gate on `image_source`; gate purely on the field's
  presence. Treat an empty array like an absent field.

## What instructions are — and are not

Two kinds of text now live on a pose, with different jobs:

| | `instructions` | `prompts` |
|---|---|---|
| Audience | the photographer, read silently | the client, read **aloud** |
| Register | technical setup direction ("weight on the back foot") | warm spoken lines |
| Order | sequential steps — order matters | independent alternatives by tone |
| Home | detail view | Shoot Mode overlay |

Do not mix them: instructions never appear in the Shoot Mode prompt
rotation, and prompts never render inside the instructions list.

## Rendering

1. **Detail view**: when `instructions` is present, add a "Set it up"
   section between the image and the prompts section — a numbered list
   (1., 2., …), one step per row, full text with no truncation. Body
   text size; this is working reference material, not decoration. When
   the field is absent, the section does not exist (no empty state, no
   placeholder row).
2. **Shoot Mode**: add a way to glance at the steps without leaving the
   overlay — e.g. a compact "Setup" affordance that presents the numbered
   list over a scrim and dismisses back to the prompt. Prompts remain the
   primary surface; instructions must never replace or interleave with
   the prompt text. Omit the affordance entirely when the pose has no
   instructions.
3. **Grid**: nothing changes. No badges, no indicators.

## Decoding / compatibility notes

- `schema_version` is still `1`; `instructions` is additive. Your decoder
  must also tolerate the other additive fields that have landed since the
  v5 catalog was published: taxonomy `light_conditions` entries now carry
  `group` (and some carry `excludes` / `excludes_groups`), and the catalog
  top level gains `"light_bands"` (solar-elevation thresholds — consume it
  for the sun-band logic instead of hardcoding thresholds in Swift, per
  its own future task; for THIS task just don't let unknown keys break
  decoding).
- The current published catalog (v5) predates all of this. Build against
  the shape above; the next `publish-dev` delivers it. Until a `photo`
  pose is actually published, verify with a locally-injected fixture
  catalog containing at least one pose with `instructions` and one
  without.

## Acceptance criteria

1. A catalog where no pose has `instructions` (i.e. today's v5) renders
   exactly as before — no new UI anywhere.
2. A fixture pose with 3 instructions shows a numbered "Set it up"
   section in the detail view, steps in catalog order, untruncated.
3. Shoot Mode on that pose can surface the steps and return to the
   prompt; on a pose without instructions the affordance is absent.
4. Decoding succeeds against a catalog containing `instructions`,
   taxonomy `group`/`excludes` fields, and top-level `light_bands`.
5. No taxonomy IDs, thresholds, or instruction text hardcoded anywhere.
