# NAIP first-run plan

This file is the handoff for the first real imagery-model run. It is intentionally
self-contained so the experiment can survive branch changes without relying on chat history.

## Objective

Prove the end-to-end path on one 2022 NAIP tile before materializing all of San Francisco:

1. acquire raw four-band imagery;
2. align municipal inventory points;
3. materialize leakage-safe image chips and targets;
4. train a short GPU smoke run;
5. decode predictions and measure the spatial, DBH, and taxonomy tasks; and
6. decide whether the evidence justifies a citywide run.

The model consumes raw RGB–NIR pixels. It does not consume crown polygons, lidar features, or a
canopy-segmentation layer.

## Current state

Completed and verified:

- SF inventory export with all located trees retained for detection and independent attribute masks;
- deterministic 768 m spatial train/validation/test blocks with 64 m guards;
- training-only species vocabulary and channel-statistics policy;
- Planetary Computer NAIP STAC discovery;
- atomic, signed-at-runtime NAIP download and raster validation;
- GeoTIFF-to-chip materialization;
- a static registration-review UI with click-derived offset measurements;
- four-head ResNet/FPN model and multitask losses;
- resumable Lightning training and Lambda Cloud container scaffolding; and
- eight unit/smoke tests, including synthetic raster materialization and model/loss shapes.

The earlier clean-cohort export had 90,431 spatially eligible rows. Re-exporting under the new
schema retains every coordinate-valid tree for detection and reports separate detection, DBH,
genus, and species counts in `inventory/ussfo/summary.json`; those regenerated counts supersede
the old table.

The training-only taxonomy currently retains 33 species in 28 genera.

Generated audit artifacts are gitignored under `artifacts/`. They can be reproduced with:

```powershell
cd imagery_model
uv sync --all-groups
uv run urban-tree-ml inventory export --config configs/sf_naip_baseline.yaml
uv run urban-tree-ml imagery index --config configs/sf_naip_baseline.yaml
```

## First tile

Start with this NAIP item:

```text
ca_m_3712213_sw_10_060_20220518
```

Approximate bounds:

```text
west  -122.502707
south   37.747875
east  -122.434711
north   37.814586
```

It covers western/north-central San Francisco, including substantial lower-density canopy and
avoids making the first run primarily a downtown building-shadow test.

## Execution sequence

### 1. Fetch the selected imagery

The `urban-tree-ml imagery fetch` command:

- selects a STAC item by ID from `stac-items.json`;
- signs the Planetary Computer asset URL at runtime;
- streams the COG to a temporary sibling path;
- verifies its byte count, CRS, band order, resolution, and raster readability; and
- atomically renames it into `artifacts/imagery/ussfo/2022/`.

Do not persist signed URLs because their SAS tokens expire. Record the item ID and unsigned source
URL in a sidecar manifest.

```powershell
uv run urban-tree-ml imagery fetch `
  --config configs/sf_naip_baseline.yaml `
  --item-id ca_m_3712213_sw_10_060_20220518
```

### 2. Stage the one-tile inputs

Keep the selected raster and provenance manifest under `imagery/ussfo/2022/`. Do not materialize
the real chips locally or before registration feedback has been finalized. Copy the raster,
manifest, and finalized `qa/registration/<RASTER_STEM>/` directory to the attached Lambda
filesystem with the same relative paths.

The Lambda preparation stage will produce:

```text
artifacts/chips/sf-naip-rgbn-species-v2/
├── train/*.npz
├── validation/*.npz
├── test/*.npz
├── chips.parquet
├── normalization.json
└── summary.json
```

Stop if any split is empty, if a surprising fraction of windows is invalid, or if output-cell
collisions are common.

Locally, run only the bounded synthetic inventory/target/feedback/chip/model tests documented in
`README.md`.

### 3. Perform registration QA

Before training, render at least 100 deterministically sampled inventory points over their chips,
balanced across development split, spatial block, DBH, and species. Record:

- median and tail georegistration offset;
- points falling on obvious non-tree pixels;
- likely removals or trees planted after the May 2022 acquisition;
- merged neighboring crowns;
- building and terrain shadows; and
- source-coordinate clusters with systematic shifts.

Do not tune offsets against test labels. Estimate any correction on training blocks and verify it
on validation blocks.

```powershell
uv run urban-tree-ml qa registration `
  --config configs/sf_naip_baseline.yaml `
  --raster artifacts/imagery/ussfo/2022/ca_m_3712213_sw_10_060_20220518.tif `
  --samples 100
```

Serve the review with `urban-tree-ml qa serve`, click the apparent center of offset trees, and
assign each sample a verdict. Reviews auto-save to disk while retaining browser-local and portable
export copies. Select **Finalize training feedback** after at least 20 training examples are
reviewed. The resulting `training-feedback.json` contains a training-only robust tile correction,
exact per-tree corrections for explicit offset verdicts, validation residuals, and explicit
`not-tree`/`uncertain` exclusions. Test labels are omitted by default and never contribute.

`chips build` auto-detects this finalized manifest for the selected raster. Explicit point
corrections replace the global correction for those trees, avoiding double shifts. Excluded points
are removed from attribute/center targets while their neighborhoods remain ignored—not converted
into background negatives. The original source inventory is never modified.

### 4. Run a short GPU smoke test

The checked-in `sf_naip_smoke.yaml` reuses the baseline chip dataset but writes to an isolated run
directory. It uses:

```yaml
training:
  epochs: 3
  batch_size: 8
  workers: 4
  accumulate_grad_batches: 1
```

Run it on one Lambda GPU:

```bash
export TREE_ML_DATA_ROOT=/lambda/nfs/<FILESYSTEM_NAME>/urban-tree-ml
export TREE_ML_RASTER="$TREE_ML_DATA_ROOT/imagery/ussfo/2022/ca_m_3712213_sw_10_060_20220518.tif"
export TREE_ML_PREPARE_DATA=1
bash lambda/run.sh
```

This re-exports the full detection cohort, builds real chips with finalized feedback, and starts
the GPU smoke run. Set `TREE_ML_PREPARE_DATA=0` for subsequent checkpoint resumptions.

The run is a plumbing and learning-signal test, not a benchmark. Confirm:

- CUDA is active;
- losses are finite;
- checkpoints resume;
- center loss decreases;
- DBH and taxonomy losses beat frozen/random behavior; and
- validation loss is not obviously diverging.

### 5. Add holdout decoding and metrics

Before a full run, implement a separate `evaluate` command. It must not be invoked automatically
by training. Decode local maxima from the center heatmap and match predictions to inventory points
with one-to-one assignment.

Report:

- center precision, recall, F1, and AP at 2 m and 4 m;
- DBH MAE/RMSE and residuals by DBH and taxon on matched points;
- genus/species macro-F1, per-class precision/recall, top-k accuracy, and calibration;
- a joint spatial + DBH + taxonomy metric; and
- strict clear-coverage and expanded-cohort results separately.

Use validation for confidence thresholds and early decisions. Open the test partition once the
pipeline and thresholds are frozen.

### 6. Scale to the 2022 city mosaic

Only after the one-tile gates pass:

- fetch all eight 2022 SF NAIP tiles;
- materialize directly from tiled COGs or build a local VRT rather than duplicating a large mosaic;
- preserve item provenance per chip;
- rerun registration and acquisition-date QA across tile seams; and
- launch the configured 80-epoch run with resumable checkpoints.

## Model structure

```text
RGB + NIR chip: 4 × 256 × 256 at 0.6 m/pixel
                    │
                    ▼
       pretrained ResNet-34 encoder
           strides 2, 4, 8, 16, 32
                    │
                    ▼
       128-channel top-down feature pyramid
                    │
                    ▼
       stride-2 grid: 128 × 128 at 1.2 m/cell
          ├── center heatmap: 1 channel
          ├── log1p(DBH): 1 channel
          ├── genus logits: 28 channels
          └── species logits: 33 channels
```

The pretrained RGB convolution is expanded to four channels; NIR is initialized from the mean RGB
weights. The heads use CenterNet-style focal loss, smooth-L1 DBH loss, and cross-entropy genus and
species losses. Current weights are 1.0 / 0.5 / 0.25 / 1.0 respectively.

Because the municipal inventory omits private trees, detection uses positive-unlabeled
supervision: known points and their local neighborhoods are supervised, low-NDVI pixels are
trusted negatives, and other unlabeled vegetation is ignored.

## Go/no-go gate for the full run

Proceed to all SF tiles only if:

- imagery/inventory alignment is credible at the chosen matching radii;
- the center heatmap learns localized peaks instead of broad canopy activation;
- validation detection beats a simple vegetation/local-density baseline;
- DBH beats median and species-median baselines;
- taxonomy beats frequency priors at genus level and shows useful supported-species signal; and
- the expanded cohort does not collapse in a way hidden by the clean cohort.

If known-point taxonomy works but detection does not, retain the encoder and reformulate the first
product as attribute prediction at supplied candidate points. If PlanetScope is added later, use it
as a co-registered temporal/phenology branch rather than pretending 3.7 m pixels provide reliable
individual-stem localization.
