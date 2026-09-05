# Urban tree imagery model

This is an isolated Python training package for predicting tree stem locations, diameter at
breast height (DBH), genus, and species from raw overhead imagery. It does **not** consume a
canopy segmentation layer or crown polygons.

The first model is a stride-2 dense point detector with four heads:

1. tree-center heatmap;
2. `log1p(DBH)` regression;
3. genus classification; and
4. species classification.

The SF municipal inventory supplies point and attribute labels. Spatial blocks, not random
rows, are held out. Test blocks remain untouched until the final evaluation, and the species
vocabulary is selected from the training blocks only.

## Why the first baseline uses NAIP

The motivating [Miller et al. paper](https://doi.org/10.1038/s41597-026-08104-3) classifies
pre-existing crown objects using 3 m PlanetScope time series and lidar features. It does not
detect stems from pixels. At 3 m, adjacent SF street trees often occupy the same pixel, so a
raw-image individual-tree detector would have a resolution problem before it had a modeling
problem.

The checked-in baseline therefore uses public four-band NAIP aerial imagery (roughly sub-meter)
to establish whether raw overhead pixels contain enough signal for point localization, DBH, and
taxonomy. The pipeline accepts any aligned multiband GeoTIFF/COG, so licensed sub-meter
satellite imagery can replace NAIP without changing labels or model code. PlanetScope is better
introduced later as a co-registered temporal-context branch, not presented as individual-stem
ground sampling it cannot provide.

NAIP discovery uses Microsoft Planetary Computer's `naip` STAC collection. The official
[STAC quickstart](https://planetarycomputer.microsoft.com/docs/quickstarts/reading-stac/) explains
asset signing, and its [NAIP example](https://planetarycomputer.microsoft.com/docs/quickstarts/storage/)
confirms that the `image` asset is ordered red, green, blue, NIR.

## Data reality check

The repository's v2 SF parquet currently has 193,378 records, of which roughly 149,000 have
coordinates. Every coordinate-valid row now remains a detection label. DBH is supervised only
when it falls within the configured credible range; genus and species have independent masks
derived from the training-only taxonomy. The exact post-guard counts for all four label cohorts
are written to `inventory/ussfo/summary.json` on export. Neighboring trees still share scene,
street, maintenance, and neighborhood effects, so the blocked holdout remains mandatory.

The inventory is also not a complete tree map. In particular, private trees are usually absent.
The default target builder uses positive-unlabeled supervision:

- known inventory points are positives;
- low-NDVI pixels may be trusted as negatives; and
- unlabeled vegetation is ignored by the detection loss.

This prevents an unlisted backyard tree from becoming a false background label. Genus, species,
and DBH losses each use their own validity mask at known stem points. When two or more records
quantize to the same output cell, every record in that collision is excluded from all four tasks
and the surrounding neighborhood is ignored rather than treated as background. The chip build
records those rows in `collision-exclusions.parquet` and the retained rows in `labels.parquet`
for audit and evaluation.

## Local workflow

All commands use `uv`; the Vue application continues to use `pnpm`. There is no npm workflow.

```bash
cd imagery_model
uv sync --group dev --group imagery

uv run urban-tree-ml check-config --config configs/sf_naip_baseline.yaml
uv run urban-tree-ml inventory export --config configs/sf_naip_baseline.yaml
uv run urban-tree-ml imagery index --config configs/sf_naip_baseline.yaml
uv run urban-tree-ml imagery fetch \
  --config configs/sf_naip_baseline.yaml \
  --item-id ca_m_3712213_sw_10_060_20220518
```

`imagery index` records matching STAC item IDs and acquisition metadata without persisting
short-lived signed URLs. `imagery fetch` signs the selected URL only in memory, streams it to a
temporary file, validates the size and RGB-NIR raster metadata, and atomically publishes the TIFF
with an unsigned provenance manifest.

Before building chips, generate the registration review. It selects spatially and taxonomically
diverse image scenes and makes every eligible tree in each scene reviewable; test points are
excluded unless `--include-test` is explicit. `--samples` is an approximate minimum tree count
because the final scene is kept intact. Serve the returned UI so button presses and click-derived
offsets are saved atomically to `reviews.json`:

```bash
uv run urban-tree-ml qa registration \
  --config configs/sf_naip_baseline.yaml \
  --raster artifacts/imagery/ussfo/2022/ca_m_3712213_sw_10_060_20220518.tif \
  --samples 100

uv run urban-tree-ml qa heuristics \
  --config configs/sf_naip_baseline.yaml \
  --raster artifacts/imagery/ussfo/2022/ca_m_3712213_sw_10_060_20220518.tif

uv run urban-tree-ml qa serve \
  --config configs/sf_naip_baseline.yaml \
  --raster artifacts/imagery/ussfo/2022/ca_m_3712213_sw_10_060_20220518.tif
```

`qa heuristics` enriches an existing review manifest with a conservative RGB-NIR profile without
changing its scenes, sample IDs, or saved reviews. In the UI, **Check non-veg** only previews
low-NIR gray candidates. A second click explicitly marks those candidates `uncertain`, and the
same control can undo the batch. Exact-coordinate stacks are hidden because the configured
collision policy excludes unresolved stacks from supervision; **Show stacked** exposes them for
source-data inspection or manual splitting with explicit offsets. City-specific passes can set `--profile-id`,
`--ndvi-p90-max`, and `--gray-fraction-min` while retaining provenance on applied decisions.

Open `http://127.0.0.1:8765`; the Model Studio landing page links registration curation and any
available model-validation run. Registration samples default to aligned, so only exceptions need
attention. The server auto-detects validation artifacts for the configured experiment; use
`--evaluation-dir` to inspect a different run.
Every served save also refreshes a compact annotation bundle under
`$TREE_ML_ANNOTATIONS_ROOT/<city>/<review-id>/`. Set that variable to the `annotations` directory
of a local [`arborary-world/training-data`](https://github.com/arborary-world/training-data)
checkout. The bundle contains the normalized reviews, their exact sample manifest, and checksums,
but no NAIP pixels. Commit and push that data repository periodically during a long review. If the
variable is unset, bundles fall back to this package's Git-ignored `annotations/` directory. To
create the bundle without running the UI, use:

```bash
uv run urban-tree-ml qa snapshot \
  --config configs/sf_naip_citywide.yaml \
  --raster artifacts/imagery/ussfo/2022/ussfo-2022-mosaic.vrt
```

Each numbered ring selects an inventory tree in the shared image. Click its apparent tree center
to mark it offset, or use the buttons to mark it aligned, not-tree, uncertain, or duplicate.
Finalization turns each explicit offset into an exact correction for that reviewed tree, estimates the
tile-wide correction from training reviews only, reports validation residuals, and records
`not-tree`/`uncertain`/`duplicate` points as supervision exclusions. It never mutates the source
inventory or uses test reviews. Finalization also places `training-feedback.json` in the tracked
training-data bundle. Later edits invalidate and remove that derived file from the bundle until the
reviews are finalized again.
Use a scene's **Full screen** control for dense or visually ambiguous imagery. The Previous/Next
buttons and Left/Right Arrow keys move through the scenes allowed by the active filters while all
marker and feedback controls remain available. Arrow keys are left alone while editing a note or
using a form control. Shift-click markers or numbered selectors to build a multi-selection, or use
**Select all**; classification buttons then update the selection together, while image-click offset
marking is disabled. In full-screen mode, A/N/U/D apply aligned/not-tree/uncertain/duplicate.

Real-tile materialization is intended for Lambda, not a local smoke test. Chip building
automatically applies the finalized `training-feedback.json`; pass `--without-feedback` only for
a deliberate ablation. The smoke and baseline configurations share one `dataset` identifier, so
they reuse the same chips while writing checkpoints to separate experiment directories.

The bounded local check uses only synthetic 256-pixel rasters plus a 64-pixel model pass:

```bash
uv run --frozen --no-sync pytest \
  tests/test_inventory.py tests/test_targets.py tests/test_feedback.py \
  tests/test_chips.py tests/test_model.py -q
```

Do not use the full NAIP tile as a local smoke test. Chip building is CPU/RAM-heavy even though
training is GPU-heavy. Generated inventories, rasters, chips, and checkpoints are gitignored.

## Lambda Cloud

Create an on-demand instance and attach a Lambda filesystem **when the instance is launched**.
Lambda filesystems cannot be attached afterward and are mounted at
`/lambda/nfs/<FILESYSTEM_NAME>`. The root disk is ephemeral, so imagery, chips, normalization
statistics, and checkpoints should all use the attached filesystem. See Lambda's
[on-demand storage documentation](https://docs.lambda.ai/public-cloud/on-demand/) and
[filesystem guide](https://docs.lambda.ai/public-cloud/filesystems/).

Copy the selected raster, its provenance manifest, and the finalized QA directory into the
attached data root while preserving their paths under `imagery/` and `qa/`. Inventory export and
chip construction then happen on Lambda; the 400+ MB raster is never committed to Git.

On the instance:

```bash
git clone <this-repository>
cd sf_tree_reporting/imagery_model
export TREE_ML_DATA_ROOT=/lambda/nfs/<FILESYSTEM_NAME>/urban-tree-ml
export TREE_ML_RASTER="$TREE_ML_DATA_ROOT/imagery/ussfo/2022/ca_m_3712213_sw_10_060_20220518.tif"
export TREE_ML_PREPARE_DATA=1
bash lambda/run.sh
```

The launcher defaults to `configs/sf_naip_smoke.yaml`. Pass a different checked-in configuration
as its first argument, for example `bash lambda/run.sh configs/sf_naip_baseline.yaml`. With
`TREE_ML_PREPARE_DATA=1`, the script re-exports the inventory under the new label schema and builds
chips before training. Omit that flag on later resumptions. The script builds the pinned CUDA
container, exposes the GPU with `--gpus all`, mounts the persistent data root, and resumes
`last.ckpt` when present. GPU configurations fail fast if CUDA is unavailable.

`configs/sf_naip_augmented.yaml` is the controlled overfitting follow-up. It reuses the same v2
chips but writes to its own run directory and applies a random member of the eight exact square
symmetries (90-degree rotations and reflections) to every training image and all of its target
maps together. Validation imagery is never augmented.

After fetching every indexed tile for a year, build a zero-copy VRT and its per-item provenance
manifest. The citywide config points at this VRT and uses a new dataset identity so it cannot
overwrite one-tile chips:

```bash
uv run urban-tree-ml imagery mosaic --config configs/sf_naip_citywide.yaml --year 2022
uv run urban-tree-ml qa registration \
  --config configs/sf_naip_citywide.yaml \
  --raster "$TREE_ML_DATA_ROOT/imagery/ussfo/2022/ussfo-2022-mosaic.vrt" \
  --samples 160
```

To grow a review after labeling has started, use `--extend-existing` with the same raster,
window size, and development/test policy. The existing scene and sample IDs, rendered images,
and `reviews.json` are preserved; only previously unseen spatial scenes are appended. Increasing
`--samples` to 480 grows the balanced review to about 120 scenes. Re-finalize training feedback
after an extension because the prior feedback was pinned to the old manifest.

Finalize that citywide registration review before materializing chips. This separately checks
alignment across the tile footprint while keeping the test partition out of the review.
It deliberately does not contain or request a Lambda API key. Terminate the GPU instance when
training is done; the attached filesystem is billed separately until it is deleted.

Evaluate the selected checkpoint against validation before opening the test split:

```bash
uv run urban-tree-ml evaluate \
  --config configs/sf_naip_smoke.yaml \
  --checkpoint "$TREE_ML_DATA_ROOT/runs/sf-naip-rgbn-species-smoke-v2/checkpoints/002.ckpt" \
  --split validation
```

The command decodes local maxima from the center heatmap, performs one-to-one geographic
matching, and writes metrics, matched tree IDs, georeferenced predictions, the evaluated ground
truth, and a taxonomy snapshot under the run's `evaluation/validation/` directory. The Model
Studio visualizes those artifacts as training curves, metrics, and filterable NAIP overlays. Test
evaluation fails unless `--allow-test` is explicitly passed after preprocessing and threshold
decisions are frozen.

## What counts as success

Do not select the model on a single headline accuracy. The final report should include:

- center precision/recall and average precision at 2 m and 4 m matching radii;
- DBH MAE/RMSE and residuals by true DBH and taxon, on matched detections only;
- genus/species macro-F1, per-class precision/recall, top-k accuracy, and calibration;
- a joint metric requiring a spatial match, correct taxon, and DBH within tolerance;
- results for both the strict clear-coverage cohort and an expanded all-eligible cohort; and
- neighborhood maps of errors, including shadows, dense crowns, young trees, and private land.

See [DESIGN.md](DESIGN.md) for the experiment gates and leakage/label limitations.
