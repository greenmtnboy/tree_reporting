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
records those rows in `collision-exclusions.parquet` for audit and evaluation.

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

Before building chips, generate the registration review. It samples spatially and taxonomically
diverse training/validation points; test points are excluded unless `--include-test` is explicit.
Serve the returned UI so button presses and click-derived offsets are saved atomically to
`reviews.json`:

```bash
uv run urban-tree-ml qa registration \
  --config configs/sf_naip_baseline.yaml \
  --raster artifacts/imagery/ussfo/2022/ca_m_3712213_sw_10_060_20220518.tif \
  --samples 100

uv run urban-tree-ml qa serve \
  --config configs/sf_naip_baseline.yaml \
  --raster artifacts/imagery/ussfo/2022/ca_m_3712213_sw_10_060_20220518.tif
```

Open `http://127.0.0.1:8765`; samples default to aligned, so only exceptions need attention.
Click the apparent tree center before choosing an offset verdict. Finalization turns each explicit
offset into an exact correction for that reviewed tree, estimates the tile-wide correction from
training reviews only, reports validation residuals, and records `not-tree`/`uncertain` points as
supervision exclusions. It never mutates the source inventory or uses test reviews.

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
It deliberately does not contain or request a Lambda API key. Terminate the GPU instance when
training is done; the attached filesystem is billed separately until it is deleted.

## What counts as success

Do not select the model on a single headline accuracy. The final report should include:

- center precision/recall and average precision at 2 m and 4 m matching radii;
- DBH MAE/RMSE and residuals by true DBH and taxon, on matched detections only;
- genus/species macro-F1, per-class precision/recall, top-k accuracy, and calibration;
- a joint metric requiring a spatial match, correct taxon, and DBH within tolerance;
- results for both the strict clear-coverage cohort and an expanded all-eligible cohort; and
- neighborhood maps of errors, including shadows, dense crowns, young trees, and private land.

See [DESIGN.md](DESIGN.md) for the experiment gates and leakage/label limitations.
