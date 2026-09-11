# Tile prediction curation → public tree-data pipeline

Status: implementation handoff, 2026-09-11. This document describes verified
existing interfaces and a **proposed new contract**; the production adapter,
modification API, and municipal-tree override pipeline are not implemented yet.

## Goal and boundaries

Build a Vue UI in `sf_tree_reporting` that loads an imagery tile plus model
predictions, overlays them, lets an authenticated user inspect/correct/accept
them, and submits durable **user modifications** for the general data pipeline.

This is separate from the Python training/registration reviewer. Reuse ideas
and exported data, not its HTML templates, localhost persistence endpoints, or
training-label storage as the production backend. Do not restart or mutate the
active reviewer while implementing this. Do not launch GPU inference or change
the running experiment as part of the UI work without separate authorization.

The first useful vertical slice is: one exported tile → overlays → one durable
pending modification → approval → versioned export → public pipeline output.
Support fixtures before wiring live credentials. Rendering is not completion:
prove a modification survives refresh and a later upstream city refresh.

## Where things actually live

- Main app repository: `C:/Users/ethan/coding_projects/sf_tree_reporting`.
- Vue/Vite/TypeScript app: `src/`; use **pnpm**, never npm.
- Active modeling code is in another checkout:
  `C:/Users/ethan/AppData/Local/Temp/sf-tree-review-done/imagery_model`.
  Its parent branch is `model_training`; there are substantial uncommitted
  changes. Inspect rather than assuming main has the latest implementation.
- Model artifacts: `C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts`.
- Training curation canonical store: `C:/Users/ethan/coding_projects/training-data/annotations`.
  Preserve it. It is not the destination for production user modifications.
- Existing reviewer: `http://127.0.0.1:8765` (local development/reference only).

Paths below are relative to the main repository unless prefixed `modeling/`,
which means the active modeling checkout above. These local artifact locations
are discovery hints, not deployable URLs or dependencies for a fresh clone.

## Existing code to read

| Concern | Entry points |
| --- | --- |
| Auth, contributions, upload flow | `src/src/composables/useAuth.ts`, `useSubmissions.ts`, `src/src/lib/firebase.ts` |
| Existing contribution display | `src/src/views/ContributionsView.vue`, `src/src/components/SubmissionThumbnail.vue` |
| Client authorization | `terraform/bootstrap/rules/firestore.rules` |
| Existing approval and approved-tree export | `reviewer/server.ts` |
| Approved new trees entering ingest | `data/raw/community_tree_info.py`, `community_tree_info.preql`, `community_update_time.py` |
| Shared schema, identity, territory, species | `data/raw/_ingest_shared.py`, `tree_dedup.preql` |
| General pipeline rules | `AGENTS.md`, `EXTENDING.md`, header of `data/trilogy.toml` |
| Prediction decoder and georeferencing | `modeling/src/urban_tree_ml/evaluation.py` |
| Existing JSON/image adapter | `modeling/src/urban_tree_ml/model_debug.py`, `qa_server.py` |
| Interactive curation inspiration | `modeling/src/urban_tree_ml/quality.py`, `studio_shell.py` |
| Model-only curation semantics | `modeling/src/urban_tree_ml/feedback.py`, `targets.py` |
| Crown overlays | `modeling/src/urban_tree_ml/crown_overlay.py` |
| Imagery identity and dates | `modeling/src/urban_tree_ml/imagery.py`, `imagery_dates.py` |

## Current prediction data available for handoff

Use `sf-boston-naip-curation-v5-retrain` as an available starting example.
The new `sf-boston-naip-curation-v8-date-finetune` is in progress at the time of
writing; do not depend on its outputs until completion and download verification.

Under `artifacts/runs/<run>/evaluation/`:

- `validation/`: SF validation export for the existing joint runs.
- `validation-usbos/`: Boston validation export.
- `train-ussfo/` and `train-usbos/`: v5 saved citywide training inference.
- Each available cohort includes `predictions.parquet`, `ground-truth.parquet`,
  `evaluation-metadata.json`, `taxonomy.json`, and scoring artifacts. Inspect
  actual files before hard-coding a newer run's layout.
- The corresponding `artifacts/chips/<dataset>/chips.parquet` provides chip
  offsets, source split, and payload paths. Model config identifies the raster,
  resolution, and output stride.

Predictions have `chip_id`, `output_x`, `output_y`, `score` (center confidence),
`dbh_log1p`, `dbh_in`, `genus_id`, `genus_confidence`, `species_id`,
`species_confidence`, and `species_top_ids`. Georeferenced exports also carry
`pixel_col`, `pixel_row`, `longitude`, and `latitude`. Optional
`center_target`/`detection_mask_value` describe model supervision, not public
tree truth. Resolve taxon IDs with **that run's taxonomy**; class IDs are not
stable cross-run identifiers. DBH predictions are estimates in inches, not
ground measurements. A model's top species label is not a human confirmation.

Existing local read interfaces, for prototyping only:

```text
GET /api/model/summary?run=<evaluation-run-id>
GET /api/model/chip/<chip-id>?run=<evaluation-run-id>
GET /api/model/image/<chip-id>.png?run=<evaluation-run-id>
```

`chip()` currently returns `{chip_id, predictions, ground_truth,
confidence_threshold, display:{chip_pixels, output_stride, resolution_m}}`.
Evaluation-run IDs can differ from training-run IDs for city/train cohorts;
discover them from the studio rather than inventing a suffix. Not every chip
has predictions. Show “Inference unavailable,” not an empty result implying
“no trees.” The current API is not a complete stable production contract:
it needs imagery identity, dates, georeferencing, immutable prediction IDs,
authorization, and a deployable image URL/export adapter.

Do not expose sealed test labels or test predictions through the public UI.
Keep split/cohort provenance internally even though the new UI is not an
evaluation product. Contributions on validation imagery change the future
evaluation-label version, not the meaning of historical reported scores.

## Imagery and coordinates: easy mistakes to avoid

- These are NAIP **aerial photographs**, not satellite captures. SF's selected
  eight sources are May 18–19, 2022; Boston's eight sources are July 7, 2023.
  The broader catalog has other dates that were not used.
- Current chips are 256 × 256 pixels at 0.6 m/pixel: 153.6 m square.
  Current output stride is 2; read these values from metadata, not constants.
- `r000027_c000101` identifies a mosaic grid row/column, **not** a universal
  slippy-map tile or a globally unique tile. Include city and immutable imagery
  version in identity. Run ID identifies a prediction layer, not the image.
- Tile-local image coordinates are `x = output_x * output_stride`,
  `y = output_y * output_stride`. Exported `pixel_col/pixel_row` include the
  mosaic's `column_offset/row_offset`; do not draw them as tile-local pixels.
- Existing `_add_geography` applies the raster affine transform to those pixel
  coordinates, then converts its projected CRS to EPSG:4326 with `always_xy`.
  Follow that convention exactly; do not invent an extra half-pixel shift.
  GeoJSON is `[longitude, latitude]`, not the opposite.
- Prefer an adapter that exports tile-local x/y **and** WGS84 coordinates plus
  the tile affine transform/CRS. Verify round trips on known points. A bounding
  box alone is inadequate for rotated/sheared or reprojected imagery.
- CSS resize, `object-fit` letterboxing, zoom, and device-pixel ratio all need
  explicit handling. SVG `viewBox="0 0 width height"` over the same image is a
  straightforward approach. Keep point glyphs circular and crown radii in
  image/metre units, not accidentally dependent on viewport stretching.
- Current detections are intended to represent **crown centers**. Tall trees
  can appear displaced from inventory trunk positions due to viewing angle.
  A crown-center adjustment must not overwrite a municipal trunk coordinate.
  Store both with explicit position roles if a user supplies both.
- Crown circles in the reviewer are **DBH-derived allometric estimates**, not
  image-segmented crowns. Tallo genus/fallback coefficients live in
  `data/raw/crown_width_coefficients.csv`; display that provenance. Radius is
  half the width in metres. Do not call these observed crown measurements.
- Model-input eligibility now excludes known plantings after the selected
  imagery cutoff; unknown dates remain eligible. Acquisition metadata is
  frozen per run in `imagery-dates.json`. This does not delete newer trees
  from the present-day municipal dataset.

## Proposed handoff contract (new, not an existing API)

Create a versioned export adapter on the modeling side, then consume its static
JSON/image assets or equivalent authenticated service in Vue. A single-tile
response should contain:

```typescript
interface TilePredictionBundleV1 {
  schemaVersion: 1
  tileId: string // city + imageryVersion + grid identity
  city: string
  imageryVersion: string // immutable content/manifest identity
  provider: string
  acquisitionStart: string // ISO calendar date
  acquisitionEnd: string
  sourceItemIds: string[]
  image: {
    url: string; sha256: string; width: number; height: number
    resolutionM: number; crs: string
    affine: [number, number, number, number, number, number]
    // Explicitly document coefficient order and tile-local pixel convention.
  }
  predictionLayer: {
    runId: string; checkpointSha256: string; taxonomyVersion: string
    status: 'available' | 'unavailable'
    predictions: Array<{
      predictionId: string // immutable within this layer, not a tree ID
      xPx: number; yPx: number; longitude: number; latitude: number
      positionRole: 'crown_center'
      centerConfidence: number
      species: string | null; speciesConfidence: number | null
      genus: string | null; dbhInches: number | null
      crownWidthM?: number | null
      crownWidthMethod?: string
      candidateTreeIds?: string[] // suggestions, never automatic identity merges
    }>
  }
  inventoryVersion: string
  inventoryTrees: Array<{
    treeId: string; longitude: number; latitude: number
    species: string | null; dbhInches: number | null
    source: string; positionRole: 'trunk' | 'unknown'
  }>
}
```

Use a stable prediction ID assigned during export (e.g. scoped to checkpoint,
tile, and output cell); do not persist row numbers that change with sorting or
confidence filtering. Asset URLs may expire; identity must not depend on the
URL. Serve CORS-compatible thumbnails/full tiles with appropriate caching and
provider attribution/licensing. Keep image assets separate from annotations.
Do not ship checkpoints, raw NIR arrays, or entire city parquets to render one
tile. Treat names/notes as untrusted text; never interpolate unsanitized HTML.

## Production writeback is not training-label writeback

The existing training reviewer has aligned/offset/not-tree/uncertain/duplicate/
occluded states, region masks, and explicit image “done” state. These describe
what can be supervised **in that image**. They are useful UI inspiration but
must not become municipal CRUD operations without translation:

| Image-review action | Safe production interpretation |
| --- | --- |
| Accept prediction | A human image observation, initially pending; not automatically a new tree |
| Move crown center | Image-specific crown-position correction; retain trunk position |
| Not a tree prediction | Reject that prediction, not delete a nearby inventory tree |
| Uncertain / occluded | Insufficient visual evidence; not proof of absence |
| Duplicate | Link/reject a duplicate detection; merge real tree identities only with explicit confirmation |
| Add unknown tree | New image observation with species unknown; dedup/approval needed before public addition |
| Edit species or measured DBH | Explicit field proposal with evidence/method; never promote predicted DBH to measured |
| Mark tile done | Review coverage for this image/version, independent of point labels and publication |

Proposed storage: append-only modification events with a materialized current
view. Each event needs an idempotency key, authenticated actor (server-derived),
server timestamp, operation type, tile/imagery/run/prediction provenance,
optional canonical target tree ID, observation date, baseline entity revision,
field patch, evidence/notes, and approval state. Keep raw predictions immutable.
Use optimistic concurrency and an explicit stale-edit conflict path. “Undo”
is a compensating/reverting event, not loss of audit history. Multi-select
changes should share a batch ID with clear partial-failure semantics.

Separate image observations from accepted persistent tree-field corrections.
An observation from 2022 cannot alone establish present-day absence, nor
override a newer measured species/DBH/location value. Define authority and
time precedence per field. Pending suggestions can appear for their author,
but unapproved modifications must not leak into the public canonical rollup.

### Existing approved-new-tree pipeline

`useSubmissions.ts` currently submits photo-based pending records to Firestore.
`reviewer/server.ts` uses an Admin SDK approval transaction to create
`publishedTrees` entries with IDs such as `community-<submissionId>`, then
rewrites `community/published_trees.ndjson` and its freshness manifest in the
published GCS bucket. `community_tree_info.py` reads that public export, not
Firestore REST, and normalizes approved additions into city pipelines.

This is an **addition** path, not an existing municipal patch system. Its photo
requirements do not necessarily fit image-tile observations. Reuse auth,
approval, export, and freshness patterns; do not fake a photo submission or
create a second community tree just to edit an existing municipal one.

### New accepted-modification path to implement

1. Authenticated client submits a validated proposal; only trusted approval
   code can change publication state. Existing Firestore rules do not grant a
   new arbitrary modification collection permission—add and test scoped rules.
2. Publish a minimal versioned approved-modification export. Exclude actor PII,
   private notes, and secrets from public artifacts. Private proposal history
   and public materialized facts are different datasets.
3. Apply accepted field overrides deterministically at a documented point in
   city ingest/dedup/publication. Choose it after inspecting source IDs and
   dedup behavior; this handoff does not pretend an override hook exists.
4. Preserve upstream/source values and provenance; a city refresh must not
   erase approved overrides. Reconcile obsolete baseline revisions rather
   than applying every old edit blindly. A duplicate-identity merge needs a
   stable alias mapping so pending edits do not become orphaned.
5. Wire export freshness into the relevant city refresh graph. A successful
   submission is not yet public; show pending/approved/published states and
   publication version distinctly. Make publication retryable/idempotent and
   protect concurrent export writers from dropping each other's changes.
6. Verify published rows and counts. Do not silently drop other sources while
   applying a patch; keep city ownership and dedup invariants intact.

Read `AGENTS.md`, `data/trilogy.toml`'s header, and `EXTENDING.md` before editing
`data/`. Core jobs must not reach city portals; consumers use published source
views. Do not fold the modification work into enrichment in a way that changes
frontend datasource scope. The common species normalization/alias machinery
is preferable to a new conflicting vocabulary in the UI.

## UI inspiration and performance lessons

### Queued fix: preserve actual inventory species in reviewer labels

User reports that some trees still display “Unknown species” when their actual
species did not make the model's top-class vocabulary. This is a reported UI
issue to investigate, not evidence the source species is unknown. Show the
original/normalized **inventory species** prominently for assignment and
duplicate investigation; show the training target separately (species, genus
fallback, or detection-only/out-of-vocabulary). Only say “unknown” if the
inventory identification truly is missing. Do not display an inventory name as
though it were the model's prediction.

There is already a best-effort `inventory_species` enrichment in
`modeling/src/urban_tree_ml/model_debug.py::_add_ground_truth_names`; trace its
fallback source and propagation through chip JSON, registration sample data,
marker labels, and tooltips. A known source species with `species_id=-1`/null
must remain named. Add a regression fixture for a rare species mapped only to
genus and another mapped to detection-only. This task is queued; no existing
reviewer behavior was changed while writing this handoff.

- Separate inventory and prediction marker styles, confidence thresholds,
  species labels/tooltips, and optional thin crown circles with a clear legend.
- Full-screen review; next/previous respect the exact filtered queue and restore
  filters/scroll when returning. A visible exit must always reveal page controls.
- Show a separate pixel close-up without the selected dot obscuring the image.
- Shift-click and box-select; select-all scoped to the tile; group movement;
  disable ambiguous per-tree operations for multi-selection. Current reviewer
  uses WASD for movement, Q uncertain, E duplicate, F not-tree, O occluded.
  Do not capture these while typing in a form; provide touch/button equivalents.
- Hide excluded observations without changing the data. “Aligned by default”
  is not reviewed; require explicit done state keyed by imagery version.
- Update only the active tile during a nudge, cache crown estimates, debounce
  durable saves briefly, and flush/await acknowledgement before navigation.
  Local storage is a recovery cache, not the authoritative record. Avoid full
  city DOM rebuilds or whole-city write payloads on every movement.
- Show “Opening…”/“Saving…” and errors; disable duplicate submissions while
  pending, with retry after failure. Render only visible cards and load tiles
  progressively; do not block the whole gallery on every image request.
- Street View is optional corroboration, often from another date. Do not store
  Google imagery in the training/export corpus or confuse its date/position
  with NAIP. Browser keys require appropriate API/referrer restrictions.

## Acceptance checks

- Fixture tile renders known points at correct positions at multiple zooms,
  desktop/mobile sizes, and non-square viewport aspect ratios.
- Units, stride, affine transform, CRS order, crown radius, and image dates have
  explicit tests; changing providers/resolution does not silently reuse offsets.
- Threshold changes affect display, not persisted identities or proposals.
- Missing inference differs visibly from an available zero-detection layer.
- Pending/approved/rejected modifications survive reload, conflicts, retries,
  navigation, and network failure without overwriting other users' edits.
- Image-specific rejection/occlusion cannot delete a canonical tree. Crown
  correction cannot silently replace trunk coordinates. Unknown stays unknown.
- Approved new observation deduplicates correctly; existing-tree patches retain
  stable identity and survive upstream refresh. Versioned exports include all
  concurrently approved changes and exclude private data.
- Reviewer annotations, frozen training artifacts, and sealed tests untouched.
- From `src/`: `pnpm test`, relevant Playwright coverage via `pnpm test:e2e`,
  `pnpm lint`. Add Firebase-rule/backend tests for new writes. For data changes,
  run relevant ingest tests; job-table changes require the prescribed full raw
  test suite. Live resolver query tests are a separate, rate-limited concern.

## Suggested implementation order

1. Build the export adapter + one SF and one Boston fixture, documenting actual
   schema and immutable IDs. Agree on who publishes real image assets.
2. Implement the isolated Vue tile-overlay/curation component and mock proposal
   persistence. No production writes in this step.
3. Add authenticated proposal storage, conflict handling, and approval workflow.
4. Implement/version the approved modifications export and canonical pipeline
   integration, with an end-to-end fixture proving persistence across refresh.
5. Connect production assets and add monitoring for failed/stale publication.

Do not block the first UI prototype on inference-on-demand, a new detection
architecture, automatic identity merging, or importing the training reviewer.
Those are separate extensions. Record unresolved approval-role, storage/asset
hosting, and field-precedence decisions explicitly before production rollout.
