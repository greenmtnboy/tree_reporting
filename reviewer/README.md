# Local submission reviewer

This localhost-only service lists pending Firestore submissions and uses the
Firebase Admin SDK to approve or reject them.

## Run

Authenticate Application Default Credentials once:

```powershell
gcloud auth application-default login
gcloud auth application-default set-quota-project sf-tree-reporting-prod
```

Then:

```powershell
cd reviewer
pnpm install
pnpm dev
```

Open <http://127.0.0.1:4174>. The server binds only to localhost. Set
`GOOGLE_CLOUD_PROJECT`, `FIREBASE_STORAGE_BUCKET`, `PUBLISHED_BUCKET`, or
`REVIEWER_PORT` to override the defaults.

The reviewer requires a Firestore composite index on `status` ascending and
`submittedAt` ascending, and write access to the public
`sf-tree-reporting-published` bucket. Terraform manages both.

## What approval does

Approving is the single gate between a private upload and public data. It:

1. **Re-encodes each photo** through `sharp` and writes it to the public
   `sf-tree-reporting-published` bucket under `community/photos/`. The client
   already strips EXIF via a canvas re-encode, but the storage rules only check
   `contentType`, so anything speaking the Storage API can upload a JPEG with
   intact GPS tags. Re-encoding here means a published photo carries no EXIF,
   IPTC, or XMP regardless of how it was uploaded.
2. **Creates the `publishedTrees` record** and marks the submission `published`,
   in one transaction.
3. **Rewrites the public export** — `community/published_trees.ndjson` (the rows
   the data pipeline reads) and `community/manifest.json` (the freshness
   timestamp).

Rejected and pending submissions never leave the private `submissions` bucket,
which has `public_access_prevention = "enforced"`.

Submissions whose `city` is not one of the supported city codes are rejected at
approval rather than silently dropped later by the ingest.

`POST /api/republish` rebuilds the export from Firestore without approving
anything — use it for the first run, or if an approval committed but the export
write failed.

## Satellite review (`/satellite`)

The second page reviews the imagery model's detections on NAIP aerial tiles
and publishes the accepted ones through a path of their own, shaped like the
photo one. It reads **tile bundles** from `SATELLITE_TILE_DIR` (default:
`reviewer/fixtures/tiles`, which holds one real SF tile and one Boston tile so
the page works on a fresh clone). Bundles are written by the exporter in the
modeling package:

```powershell
cd imagery_model
uv run --group imagery python -m urban_tree_ml.tile_bundle_export `
  --run sf-boston-naip-curation-v5-retrain --cohort validation `
  --chips r000020_c000066 --out ../reviewer/tiles
$env:SATELLITE_TILE_DIR = "$PWD/../reviewer/tiles"; cd ../reviewer; pnpm dev
```

A bundle is the `TilePredictionBundleV1` contract from
`PREDICTION_CURATION_HANDOFF.md`: the chip as a PNG, the tile's affine and
CRS, the run's above-threshold detections with stable ids
(`{chip}:{output_x}:{output_y}`), and the published inventory trees the tile
covers, each with the crown width `tree_predictions_v2.parquet` gives it.
The exporter never emits sealed test-split chips or ground truth.

What the page shows, and what each decision means:

- **Detections** are pink dots at the model's crown centre with a dashed ring
  for its DBH-derived crown estimate (the Tallo genus fit in
  `data/raw/crown_width_coefficients.csv`, applied as `tree_predictions.preql`
  applies it). **Inventory trees** are blue diamonds at the trunk with a solid
  ring for their published crown prediction. **Reviewed detections from other
  tiles** are orange triangles, so a tree accepted on an overlapping tile is
  not accepted twice. Crown rings are allometric estimates, not measured
  canopies, and the legend says so.
- **Accept** records a new tree at the crown centre (nudge it with the arrow
  keys; the trunk position of an inventory tree is never moved). The species
  is whatever the reviewer left in the box -- the model's label counts as
  confirmed only because a person kept it, and `speciesSource` records which.
  A measured DBH can be typed; the model's estimate is stored separately and
  the ingest never publishes it as a measurement.
- **Duplicate of** links the detection to an inventory tree on the tile. It
  publishes *at that tree's coordinates*, so the shared cluster merge in
  `data/raw/tree_dedup.preql` is guaranteed to absorb it: the municipal values
  win, the satellite species or DBH fills only what the inventory left empty,
  and the satellite id survives in `merged_tree_ids`.
- **Not a tree** and **Uncertain** are statements about this image. They are
  stored, shown, and never published; nothing a reviewer does here can delete
  or edit a canonical tree.
- **Tile reviewed** is coverage for this image and prediction layer, keyed by
  imagery version and run id, independent of the per-detection decisions.

Decisions are private Firestore documents (`satelliteObservations`, keyed by
tile, run and prediction, with a revision for stale-edit conflicts).
**Publish** -- per tile from the tile view, or per observation from the queue
-- creates the `satelliteTrees` record in a transaction and rewrites the public
export `satellite/published_trees.ndjson` plus `satellite/manifest.json`,
whose `latestPublishedAtByCity` is what `data/raw/satellite_update_time.py`
reads so that a publish in one city rebuilds that city's Parquet alone. The
export carries no notes and no actor. `POST /api/satellite/republish` rebuilds
it from Firestore.

`pnpm test` runs the pure logic (bundle contract, observation shaping, export
shape) against the committed fixtures; `pnpm typecheck` covers both pages.

## Data refresh

Approval does not trigger a rebuild. The normal scheduled `trilogy refresh raw`
run reads the public export through `data/raw/community_tree_info.py`. Each city
model declares a `complete where city = 'X' and {code}_source = 'COMMUNITY_X'`
partition over that shared ingest, which Trilogy unions with the municipal
source(s) into the city's usual versioned Parquet.

`community_update_time.py` reads `manifest.json` as a freshness input, so a new
approval makes the affected materialization stale on the next scheduled run.
The manifest carries `latestPublishedAtByCity`, and each city's model probes
only its own entry, so approving a tree in Boston rebuilds Boston's Parquet
alone rather than all fourteen.

Trees without an identified species are emitted as `Unknown` because `species`
is a non-null key in the canonical model.

The pipeline reads a **public GCS object rather than Firestore** on purpose:
`firestore.googleapis.com` enforces IAM, not security rules, so a public
`allow read` rule still returns 403 to the unauthenticated pipeline. See
`EXTENDING.md` for the full rationale.
