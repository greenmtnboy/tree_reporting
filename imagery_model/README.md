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

## Fairfax curation and training admission

The Fairfax 2023 pilot has 80 training-area tiles and 40 validation-area tiles in
the `Fairfax train` and `Fairfax validation` curator assignments. Review the full
tile, use **Mark more done**, then publish the saved training feedback. Both
development splits require this explicit completion flag. Training preparation
rejects an empty split, unpublished changes, changed source imagery and split
drift. Human-confirmed trees and reviewed empty tiles pass through the standard
target builder. Prediction imports retain unavailable-label status until rebuilt.

Forty raw-imagery holdout tiles are sealed under
`artifacts/holdouts/fairfax-naip-2023-v1`, in 40 test blocks disjoint from all
120 development blocks. Their manifest binds the source imagery and pixel-file
hashes. They are excluded from the curator and development datasets.

Prepare a fresh local curated dataset after both development splits have eligible
published reviews. Run from the active imagery checkout with its `src` on
`PYTHONPATH` and an imagery-capable Python. Set `$artifactRoot` to the shared
artifact directory and choose a new freeze path and dataset name.

```powershell
python lambda/prepare_fairfax_pilot.py prepare-training `
  --artifacts $artifactRoot `
  --output "$artifactRoot/run-inputs/fairfax-curated-next" `
  --dataset fairfax-curated-next-usffx `
  --taxonomy "$artifactRoot/runs/sf-boston-nyc-naip-swin-30cm-v17-curated-finetune/vegetation-taxonomy.json"
```

The output includes the frozen publication, admission policy, targets, data hashes
and `training-config.json`. The normal standalone training entry and joint
manifest assembly verify the Fairfax curation receipt. A subsequent authorized
joint-run preparation must include this curated source and its frozen validation
policy alongside the existing cities. Remote asset packaging belongs to that
run's preparation. This local preparation command only builds inputs.

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

### Fixed two-view training augmentation

Opt in with `training.augmentation: fixed_pair_v1` and
`training.random_dihedral: false`. For every training window (including boundary
shifts), this exposes two distinct square-symmetry orientations selected by a
SHA-256 recipe using the project seed, dataset name and chip ID. Both views remain
fixed across epochs, worker counts and manifest ordering. The first retains its
original lighting; the second applies a shared RGB+NIR exposure gain of +/-0.1 to
0.3 stops, clipped to [0,1] before normalization. All spatial labels/masks receive
the same geometric transform. Neither view guarantees the identity orientation.

This doubles training presentations, not stored chips; validation/test are neither
expanded nor augmented. `augmentation-recipes.json` records every view in the run,
so changes in source curation flow through when chips are rebuilt. Existing configs
default to the legacy one-view policy. The trainer already seeds its legacy RNG;
the new policy additionally fixes the recipe for each chip independent of loading
order. Comparing runs must account for the doubled presentations per epoch.

### Training budget and retained decision (2026-10-05)

Future three-city preparations read `configs/fine_tune_loss_policy.json` and freeze
it as `loss-policy.json` alongside explicit warm-start weights. The October 14
policy sets genus=species=1.0 and single-tree extra peaks=1.0 (previously 0.1),
retaining center=1.0, DBH=0.5 and crown=1.0. These are experimental coefficients,
not gradient shares. Historical configs and the already-running v14 are unchanged.
The step-zero parent must be evaluated under the same new objective as candidates.
Extra peaks are penalized only in explicit single-tree regions, with one allowance
per retained labeled-center partition; ordinary point annotations do not activate
this supplemental term, and Copse/Shrub mass footprints are exempt.

Studio's **Model description** tab (`/model-profile`) loads a lightweight profile
from saved configs, training results, baseline receipts, evaluation counts and
TensorBoard scalars. It shows raw and weighted losses at the selected checkpoint,
not simply the final logged step. Missing historical values stay unknown. Profiles
are stored as invalidatable derived JSON caches under `artifacts/qa/model-profiles/`
and can be downloaded from the tab; source run artifacts and live curation are not
modified. The profile route never loads checkpoints, imagery or prediction tables.
Changing the selected model on this tab does not promote a model.

Use `training.budget_policy: source_passes_v1` (the new default). In this policy,
`training.epochs` is a budget in **unaugmented source-window-equivalent passes**,
not expanded dataset epochs. Let `B = ceil(source_windows / batch_size)` and
`S = ceil(B / accumulate_grad_batches)`. Validation runs every B training batches
across epoch boundaries; the cosine scheduler advances per optimizer step and
both its horizon and the training cap are `epochs * S`. Thus adding views no
longer multiplies the allowed optimization budget. Partial batches/accumulations
introduce small cadence rounding differences, not an exact presentation budget.

`training.early_stopping_patience_checks` defaults to 12 non-improving validation
checks, explicitly checked after validation, not again at epoch end. With paired
views this is approximately six expanded epochs, rather than twelve. For the
v9-sized corpus (9,045 source windows, batch 2, accumulation 16, 80 passes),
validation is every 4,523 training batches and the step cap/cosine horizon is
22,640 optimizer updates. The derived budget is saved in run metadata/checkpoints.
Checkpoint names include epoch and step for intra-epoch validation, and an explicit
final save ensures `last.ckpt` contains the actual final optimizer state.

**Parent-baseline guard (2026-10-14):** weights-only fine-tunes first validate the
starting weights on the complete current frozen validation split, before any
optimizer updates. That loss seeds early stopping; the baseline itself consumes
neither optimization budget nor a patience check. Only a strictly lower
`validation/loss` selects a newly trained checkpoint. Ties and worse results select
the local `checkpoints/parent-baseline.ckpt` for all subsequent inference, keeping
the run self-contained. `last.ckpt` remains the actual final resumable optimizer
state, not the selected winner; the best trained candidate is retained separately.

`parent-baseline.json`, `checkpoint-selection.json`, and `training-result.json`
record the losses, selected checkpoint/hash, parent provenance, validation-input
signature (including masks), and eligibility for next-parent advancement. Input
changes or nonfinite losses fail closed. Full-state resumes preserve the original
baseline and verify its inputs/hash; a step-zero baseline is weights-only and
cannot be used as a full-state resume. Legacy resumes without a baseline are
marked unverified and cannot automatically advance the lineage.

Advance the saved parent only after verified completion/results and
`checkpoint_selection.eligible_for_parent_advance == true`. New or expanded heads
are compared to their initialized adaptation and require explicit review before
lineage advancement. This guards aggregate validation loss, not every city's
F1/F2/species metric. Historical results and the currently chosen v13 parent are
unchanged; this does not launch training or switch the reviewer model.

`legacy_epochs` is an explicit historical-reproduction escape hatch. Resuming a
checkpoint without budget metadata requires that legacy policy; a changed saved
budget is rejected. Weights-only warm starts may use the new budget. This changes
future execution, not historical v9 configs/artifacts, and does not launch training.

User decision: retain **sf-boston-naip-swin-30cm-v9-fixed-pair-finetune**, selected
`001.ckpt` SHA256 `392fbe1f422defe785dd80739bb74234a7a4e49b41c51e5640fbfc88b9b79539`,
and fixed-pair augmentation as the starting point for the next joint SF/Boston/NYC
fine-tune. Keep v7 as rollback/reference. Rationale: mostly flat SF/Boston metrics,
modest NYC 4 m detection/top-5 benefits, input diversity; not a proven broad or
augmentation-only generalization improvement. NYC stricter matching remained flat.
This is a future training decision, not automatic publication or live-model promotion.

NYC data rule (user update, 2026-10-05): future training uses **curated NYC chips
only**, and includes geographically separate NYC validation. No uncurated NYC
chips or raw-inventory fallback, including shifted/augmented variants, may enter
training. Verify curated eligibility in the frozen manifest before allocating
compute; if eligibility cannot be established, stop and ask. This supersedes the
earlier v8-specific authorization to train on unreviewed inventory. Test remains
sealed. This records a launch requirement, not a claim that historical recipes
already enforce the new eligibility filter.

`lambda/prepare_fixed_pair.py` freezes an SF/Boston-only continuation from v7.
It pins parent chip metadata, labels, normalization and checkpoint hashes, reuses
the immutable remote development pixels, and never reads live curation. The GPU
job records development-pixel hashes before training and leaves test pixels sealed.
Versus the parent checkpoint, results measure additional fine-tuning plus the new
augmentation; isolating an augmentation-only effect requires a matched continuation
control, which this recipe does not automatically launch.

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

For a single Studio that safely switches between the local San Francisco and Boston workspaces,
set the shared artifact and tracked-annotation roots and use the checked-in city registry:

```bash
TREE_ML_DATA_ROOT=/path/to/artifacts \
TREE_ML_ANNOTATIONS_ROOT=/path/to/training-data/annotations \
uv run --env-file .env urban-tree-ml qa studio \
  --studio-config configs/studio_sf_boston.yaml
```

The selected city is carried on every registration read, save, and finalize request. Review images
are served from a city-namespaced URL, and validation-to-curation resolves the city from the selected
evaluation run. This keeps identically named scenes and chip IDs from crossing annotation bundles.

The reviewer accelerates JSON encoding and review-container copies with
Rust. Build it using the server's Python interpreter with
`python native/build_review_native.py`; restart to activate. Rust is required by
default: missing modules and unsupported values fail explicitly, with no automatic
fallback. `TREE_ML_REVIEW_NATIVE=off` explicitly selects Python for rollback or
benchmarks. `/api/save-timings` reports the active backend and policy. See
[native/README.md](native/README.md) for build instructions, rollback, compatibility
tests and measured save/load hotspots.

`qa heuristics` enriches an existing review manifest with a conservative RGB-NIR profile without
changing its scenes, sample IDs, or saved reviews. In the UI, **Check non-veg** only previews
low-NIR gray candidates. A second click explicitly marks those candidates `uncertain`, and the
same control can undo the batch. Exact-coordinate stacks are hidden because the configured
collision policy excludes unresolved stacks from supervision; **Show stacked** exposes them for
source-data inspection or manual splitting with explicit offsets. City-specific passes can set `--profile-id`,
`--ndvi-p90-max`, and `--gray-fraction-min` while retaining provenance on applied decisions.

Open `http://127.0.0.1:8765`; the Model Studio landing page links registration curation, run
history, and model validation. The run-history view discovers every sibling
`runs/*/evaluation/validation` bundle, charts compatible datasets over time, and links each row to
its validation explorer. A chip ID can also be compared across every run. From either chip view,
**Curate this chip** adds previously unseen inventory points to the same registration manifest and
opens that scene directly in the fullscreen reviewer; saves and finalization continue through the
existing annotation bundle. Registration samples default to aligned, so only exceptions need
attention. `--evaluation-dir` chooses the initially selected run and the sibling run directory to
scan.

The scoped chip reviewer shows a 16-source-pixel context border on each side,
including corners, read from the neighboring tiles in the same raster. An amber
dashed boundary marks the original tile; the lightly tinted outer band belongs
to neighboring tiles and is context only. Annotation coordinates, ownership,
saved footprints, and training chips remain unchanged. Pixel close-up also uses
this context near an edge, with the same aerial display settings. Missing source
coverage stays blank; a failed context load is explicitly reported without
blocking the original tile. No neighboring labels are imported or published.

Validation calls predictions without an inventory match **unmatched**, since an incomplete inventory
cannot prove they are non-trees. Each prediction also carries its independent center-supervision
status: a red ring is a trusted-background penalty, cyan is ignored/no loss, and gold is a reduced
negative weight near a labeled center. Tooltips show the exact status and chip summaries split
unmatched predictions into penalized, ignored, and unavailable counts. New evaluations persist the
target and mask value at every decoded prediction; older evaluations reconstruct them from the saved
raster and chip metadata when all exclusions are locally available.
Full-screen registration review automatically opens an interactive Street View panorama beside the
overhead tile and retargets it whenever a numbered tree is selected. Enable the Google Maps Embed
API, restrict its browser API key to the review server's origin, and expose it only to the server
process as `GOOGLE_MAPS_EMBED_API_KEY` (for example,
`uv run --env-file .env urban-tree-ml qa serve ...`). Embed requests remain lazy-loaded until a
full-screen image is opened. If the variable is absent, full-screen review remains overhead-only.
The fullscreen overhead tile lightly labels every visible inventory marker with its species;
the active tree is emphasized and duplicate verdicts are purple-tinted for comparison.
The sidebar also renders a marker-free, nearest-neighbor 20-pixel crop around the selected
inventory location or clicked correction, with a crosshair at the exact point hidden by the marker.
Clicking a model-prediction marker moves the same close-up to that prediction without changing any
tree verdict.
When Street View Static API is also enabled for the browser key, the reviewer resolves the actual
nearest panorama location, aims its initial heading toward the selected tree, and draws that camera
and view direction over the overhead tile. The iframe is interaction-locked by default so the cone
remains truthful; **Unlock** permits free panorama navigation while leaving the cone labeled as the
initial view. Metadata-only requests do not consume Street View Static quota.
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
In both curation prediction overlays, a filled center dot means a one-to-one match to that
model's frozen evaluation labels; a hollow center is unmatched. Matching follows the selected
confidence and the chip-review matching radius (2 m if absent). Hover shows the matched tree
ID and distance, or whether an unmatched prediction is ignored by scoring. Dashed centers
mean match data is unavailable. Colors still identify models; crown outlines are unchanged.
Live edits do not rewrite historical matches, and comparison overlays still allow ordinary
curation clicks through them (Alt-click remains the explicit prediction-acceptance gesture).
Fullscreen image-scoped review supports three freehand polygon tools. **Confirmed woods** (`P`,
cyan) marks incompletely annotated woods: negative center supervision and unmatched predictions
there are ignored, **not credited as correct**. **Confirm background** (`B`, red) marks areas known
to contain no trees, restoring trusted negative supervision. **Single-tree area** (amber) penalizes
extra centers within an authoritative canopy, keeping each known labeled tree's allowance.
Trace a boundary and release to save; Escape cancels a partial drawing. Other areas stay visible
while tree markers are hidden. Select an outline, then drag or use WASD (Shift for 5 pixels) to
move it. Ctrl+Z (Cmd+Z on Mac) undoes movements and tree classification actions in reverse order,
including grouped classifications and classification-based removal of manually added trees.
Undo restores the prior classification and location without discarding notes or crown-size edits.
The last 50 actions are kept per scene while the page is open. Shift-click an area's outline to
remove it, or use **Undo last area**.
Existing circular background/protect annotations remain supported without conversion; new
background areas use polygons. Known labeled centers always stay positive. Explicit background
and single-tree assertions take precedence over broad woods ignore regions.

Regions are saved immediately in the image/orthoprojection's geographic annotation bundle and
reproject into every intersecting regular or shifted chip. Finalize and rebuild chips/evaluations
to apply them to training and curation metrics. Historical frozen-label scores are unchanged;
best-confidence model selection still maximizes F2. Ignored predictions neither add true positives
nor count as false positives; unmatched predictions in confirmed background remain false positives.
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
cd tree_reporting/imagery_model
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

After completing the grouped registration pass, use `configs/sf_naip_citywide_curated.yaml` for
the controlled uplift run. It keeps every citywide training and evaluation setting fixed while
writing checkpoints to a distinct experiment directory; rebuild the shared citywide chips from
the newly finalized feedback before training.

## External-city validation: Boston

Boston is the first cross-city transfer cohort. It is deliberately **not** merged into the SF
validation split or used for early stopping. `configs/boston_naip_external.yaml` gives Boston its
own inventory, spatial splits, imagery, chips, reviews, and dataset identity while pinning the SF
training taxonomy and normalization statistics. That lets the same checkpoint answer three
separate questions:

- can it detect Boston inventory trees at all;
- does DBH transfer for trees with credible measurements; and
- how well does the fixed SF vocabulary transfer where Boston labels overlap it?

Inventory export reports genus and species out-of-vocabulary counts in
`inventory/usbos/summary.json`; OOV Boston taxa remain detection/DBH examples but are masked out of
the corresponding classification metric. Never rebuild a Boston-derived taxonomy for this
cohort, because that would change the checkpoint's output classes.

Start with a bounded 2023 footprint instead of downloading the entire city. The index command
reports item counts by year and the twelve tiles containing the most inventory points. Choose
roughly 6–12 adjacent, tree-dense tiles from that list. The checked-in config pins the initial
eight-tile footprint so acquisition and mosaicking remain reproducible:

```bash
cd imagery_model
uv run urban-tree-ml inventory export --config configs/boston_naip_external.yaml
uv run urban-tree-ml imagery index --config configs/boston_naip_external.yaml
uv run urban-tree-ml imagery fetch-selected --config configs/boston_naip_external.yaml
uv run urban-tree-ml imagery mosaic \
  --config configs/boston_naip_external.yaml \
  --year 2023 \
  --output "$TREE_ML_DATA_ROOT/imagery/usbos/2023/usbos-2023-external.vrt"
```

Massachusetts 2023 NAIP is native 0.3 m imagery. The Boston config records that acquisition
resolution separately and the VRT averages it onto the SF model's 0.6 m grid. This keeps the
checkpoint's physical field of view and meter-based decoder geometry unchanged.

Then run the same registration, heuristic, serving, finalization, and chip-build workflow with the
Boston config and VRT. Use a Boston-specific annotation checkout path under
`$TREE_ML_ANNOTATIONS_ROOT/usbos/`; the training-data repository stores only manifests and review
decisions, never NAIP pixels.

Evaluation writes the Boston cohort into the existing SF training run rather than creating a
second model run:

```bash
uv run urban-tree-ml chips build \
  --config configs/boston_naip_external.yaml \
  --raster "$TREE_ML_DATA_ROOT/imagery/usbos/2023/usbos-2023-external.vrt"

uv run urban-tree-ml evaluate \
  --config configs/boston_naip_external.yaml \
  --checkpoint "$TREE_ML_DATA_ROOT/runs/<SF_RUN>/checkpoints/<BEST>.ckpt" \
  --split validation \
  --cohort external-usbos
```

The Model Studio discovers every `runs/*/evaluation/*` cohort and labels it by city and cohort.
Chip comparisons are restricted to the same city and dataset because identical grid-style chip
IDs from two rasters do not identify the same geography. A cohort opened from another city's
server remains viewable, but its curation action is disabled; serve Boston's own registration
review and raster before writing Boston feedback.

On Lambda, the two-stage wrapper keeps acquisition separate from the post-curation evaluation and
never launches a new training run:

```bash
export TREE_ML_DATA_ROOT=/lambda/nfs/<FILESYSTEM_NAME>/urban-tree-ml
bash lambda/external-city.sh configs/boston_naip_external.yaml acquire

# After syncing/finalizing the Boston review bundle back onto the filesystem:
export TREE_ML_CHECKPOINT="$TREE_ML_DATA_ROOT/runs/<SF_RUN>/checkpoints/<BEST>.ckpt"
bash lambda/external-city.sh configs/boston_naip_external.yaml evaluate
```

The acquire stage does not request GPU access inside Docker. The evaluate stage requires the GPU,
writes `evaluation/external-usbos` beneath the referenced SF run, and exits when evaluation is
complete. Instance termination remains the responsibility of the outer Lambda launcher so a shell
failure cannot masquerade as a successful teardown.

To grow a review after labeling has started, use `--extend-existing` with the same raster,
window size, and development/test policy. The existing scene and sample IDs, rendered images,
and `reviews.json` are preserved; only previously unseen spatial scenes are appended. Increasing
`--samples` to 480 grows the balanced review to about 120 scenes. Re-finalize training feedback
after an extension because the prior feedback was pinned to the old manifest.

Finalize that citywide registration review before materializing chips. This separately checks
alignment across the tile footprint while keeping the test partition out of the review.
It deliberately does not contain or request a Lambda API key. For future training runs,
run batch inference with the selected best checkpoint over **all training chips in
both SF and Boston**, as well as the normal validation evaluation, while the GPU
is still up. Save run/city/split-specific predictions for reviewer overlays and
curation assignments; record processed chip IDs, including chips with zero
detections, so missing inference is distinguishable from an empty prediction.
Keep test chips sealed. Verify inference coverage and collect/verify the artifacts
before normal teardown. Training-chip predictions are curation diagnostics, not
held-out performance. The joint launcher pipeline now runs this automatically
before writing `COMPLETE`; each cohort includes `inference-coverage.json` with
processed chip IDs and prediction counts. The collector already packages the
whole run, including these cohorts. Retain the safety shutdown deadline on failures.
The attached filesystem is billed separately until it is deleted.

Evaluate the selected checkpoint against validation before opening the test split:

New inference resolves equal-score local maxima deterministically before the
per-chip detection cap (`equal_score_greedy_row_column_v1`). Within the configured
square NMS neighborhood, the first retained peak in score-descending, row/column
order suppresses tied neighbors; separated peaks remain eligible. This prevents
BF16 plateaus from consuming several detections without widening the NMS radius
or changing confidence values. Metrics and evaluation metadata record the policy.
Existing prediction parquets and reviewer overlays are unchanged until inference
is explicitly rerun. Compare models using the same decoding policy; historical
results without this field used the previous all-tied-maxima behavior.

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

### Review assignments and vocabulary preparation

The validation explorer's **Assignment** filter discovers the newest completed,
checksum-verified benchmark queue for the selected training run and city. It offers
all assigned, representative audit, and diagnostic chips, with card tags, reasons,
and reviewed progress. It combines with Unreviewed only and persists across navigation.
Assignment tags do not alter labels or imply that a chip is reviewed.

Each validation card has a direct **Curate** button. It opens the existing fullscreen
curator with a session-local snapshot of the entire filtered, sorted validation queue,
not just the displayed card limit. Next/Previous and left/right arrows follow that
queue, saving before navigation; endpoints do not wrap. Back to validation restores
the originating filters. Mark Done remains explicit. A stale-save conflict blocks
navigation rather than discarding changes; reload only after resolving any unsaved work.
The queue survives refresh in the same tab, but is not shared across browser sessions.

`python -m urban_tree_ml.expand_taxonomy --root ARTIFACTS --source-run RUN
--output NEW_DIRECTORY` stages an append-only cross-city taxonomy candidate from
retained **training** targets only (100 examples by default). It retains existing
IDs and adds genus-only supervision for supported long-tail genera. Single-token
genus labels are not promoted into new species classes. The support cutoff is a
candidate-generation heuristic, not proof of enough independent imagery.

The staged candidate still needs synonym/canonical-name review. It does not change
live inventories, chips, UI labels, or model checkpoints. Before training with it,
re-encode each frozen inventory's species/genus IDs and eligibility flags against
the SAME new taxonomy, rebuild chips, and use a new experiment/checkpoint. Merely
replacing taxonomy.json is unsafe: build_chips consumes the encoded inventory IDs.
The old checkpoints must keep their original taxonomy. Detection supervision is
independent of species eligibility; vocabulary growth does not itself resolve
localization errors or guarantee better large-tree recall.

### Frozen two-city comparison

Rescore the saved SF-only and joint SF/Boston predictions without GPU inference:

```bash
uv run python -m urban_tree_ml.frozen_comparison \
  --root "$TREE_ML_DATA_ROOT" \
  --output "$TREE_ML_DATA_ROOT/benchmarks/my-new-snapshot"
```

The local Studio must be running (override its address with `--studio`). The tool
copies and hashes both prediction exports and the joint run's validation labels,
checks taxonomy/grid compatibility, and scores only shared validation chips. It
does not apply newer unpublished curation, alter annotations, or access test data.
It refuses to overwrite an existing snapshot. Only a directory with `COMPLETE`
is a finished report; `manifest.json` records provenance and output checksums.

`REPORT.md` links to 50 review targets per city: 25 density-stratified random audit
chips and 25 diagnostic chips. CSVs contain all chip scores, confidence sweeps,
and DBH-bin recall. UI links open live/original evaluation views, not the frozen
rescore. Reviewed status is captured separately and does not establish that all
visible trees are labeled. Unmatched predictions remain inventory-relative errors,
not certified false trees or evidence of a training penalty.

Do not select the model on a single headline accuracy. The final report should include:

- center precision/recall and average precision at 2 m and 4 m matching radii;
- DBH MAE/RMSE and residuals by true DBH and taxon, on matched detections only;
- genus/species macro-F1, per-class precision/recall, top-k accuracy, and calibration;
- a joint metric requiring a spatial match, correct taxon, and DBH within tolerance;
- results for both the strict clear-coverage cohort and an expanded all-eligible cohort; and
- neighborhood maps of errors, including shadows, dense crowns, young trees, and private land.

See [DESIGN.md](DESIGN.md) for the experiment gates and leakage/label limitations.

### Reviewer model-cache budget

Navigation uses a metadata-only catalog, not the full history metrics. Metadata
is refreshed when run/evaluation files are added, changed or removed, including
when local imagery directories change after a VRT restore. The history response
is separately cached against metadata, prediction/match/truth file versions and
confidence-sweep exports/provenance. Only file metadata is inspected on a cache
hit; new evaluations remain visible without a restart. Concurrent cold history
requests share one calculation, without blocking metadata-only navigation.
Neither cache is keyed by live annotations or changes saved curation. Cold
history still computes missing in-memory goal metrics from frozen predictions.

The model catalog retains loaded evaluation bundles under a shared **1024 MiB**
estimated-memory budget. Set `TREE_ML_MODEL_CACHE_MB` before starting the reviewer
to change it (`0` disables retention). Least-recently-used bundles are evicted;
later access reloads their immutable artifacts from disk. A bundle larger than
the budget can serve its current request but is not retained.

Accounting includes prediction/truth tables, chip indexes, prepared ranking
arrays, ranking results, images, target masks, and collision data. Lazy cache
growth triggers another budget check. Concurrent loads are serialized to avoid
duplicate cold loads. Eviction drops cache ownership, never clears a bundle being
used by an active request and never touches saved curation or files.

This bounds estimated retained model-cache payload, **not total process RSS**:
active requests, temporary calculations, allocator overhead, and unrelated caches
can consume additional memory. Ranking results retain their additional limit of
eight threshold/radius combinations per bundle; images and masks each retain at
most 128 chips per bundle, all charged against the shared budget.

### Curation backups

New snapshots use `curation-archive/revisions-v2/<revision>.json` manifests and
SHA-256-addressed, compressed `chunks/` objects. Shared `file-index/` manifests
also avoid repeating the chunk lists for unchanged files. Content-defined boundaries let
small edits reuse unchanged annotation bytes. Each revision restores independently:
there is no predecessor/delta chain. Capture checks a byte-for-byte round trip;
restore verifies chunk, file, revision, annotation-state and image checksums.
Cloud transfers group new chunks into roughly 4 MiB immutable, SHA-256-verified
`packs/` archives, avoiding thousands of tiny uploads. Revision manifests name
their pack dependencies; restoring does not depend on the local pack index.

Manual backup publishes both cities to the annotation Git repository and private
cloud archive. It uploads chunks before a frozen set of manifests, so concurrent
curation cannot publish a manifest with missing dependencies. The status API omits
image-hash inventories and reports the active stage. Restoring downloads only the
chunks and images referenced by the chosen revision.

Normal backups never delete legacy `revisions/*.zip`, locally or remotely, and
no longer bulk-sync them. Old local-only ZIP history is not newly uploaded by a
normal backup; current state and all new compact snapshots are backed up.
Existing cloud ZIP history is not automatically pruned. Cleanup/migration is the
separate verified operation below; never delete legacy history just because a
new-format current backup exists. New compact history is deduplicated, not
time-retained or size-bounded.

Historical ZIPs can be migrated explicitly with `python -m
urban_tree_ml.curation_history --directory <artifacts>/curation-archive`.
The resumable migration groups 256 revisions into independent SQLite packs under
`history-v1/packs/`, sharing compressed chunks without a revision delta chain.
Every ZIP member is preserved byte-for-byte (the ZIP envelope itself is not).
Original revision IDs remain usable: `history-v1/catalog.json` maps each to its
SHA-256-addressed pack. Restore tries current v2, compact historical, then legacy
ZIP storage. These artifacts are Git-ignored; compaction does not shrink a sparse
checkout or rewrite Git history.

`--publish` uploads packs before the catalog, then independently downloads and
SHA-256-verifies every pack and the catalog. `--verify-restores` compares the first
and last revision of every downloaded pack against the original ZIPs and performs
a full historical restore for each city into a new scratch directory.
Original ZIP replacement is a
separate `--retire` action, gated on that verification and full restore smoke-test
evidence (`RESTORE_SAMPLES_VERIFIED.json`, tied to the exact catalog hash).
Retirement checks local ZIP SHA-256, remote ZIP MD5/size, and uses exact-name,
generation-conditional cloud deletes. It retains an audit trail and never deletes
images, source imagery, live annotations, or newer snapshots. Cloud soft-delete
retention remains in force, so billing reduction is delayed by the bucket's policy.

### Chip tags and curation insights

The **Curation insights** tab (`/insights`) groups frozen development predictions
by city, overlapping chip tags and species. Pick a completed model, train or
validation split, confidence and crown-aware/fixed-2m/fixed-4m matching. Prediction-only
cohorts expose prediction head disagreements, never invented accuracy. Test is
excluded. Scores cover available inference footprints, not every possible city tile.

Use **Chip tags** on chip-review cards, individual curation scenes or the insights
chip table. Select several categories and/or add comma-separated custom names.
Tags are stored separately from training annotations in
`annotations/<city>/<acquisition-review-id>/chip-tags.json`, keyed by physical
footprint. Switching model or equivalent raster resolution preserves tags;
shifted footprints and different acquisitions remain distinct. Per-chip revision
checks reject stale edits. Tags are included in backup revision identities,
cloud restore verification and Git annotation snapshots. They do not change
training targets or publish feedback.

Counts are pooled, not averages of chip percentages. Overlapping tag groups must
not be added together. Shifted variants are included by default (explicit
`shifts=0` links retain base-only views); base/shifted counts remain separate.
v10/v11 validation has no shifted chips. Species top-1/top-5 use matched labeled trees; end-to-end species recall
also counts missed trees. Fewer than 20 matched labels is flagged as low support.
Copse/shrub categories are separate from scientific-species totals. Missing masks
suppress precision/F scores. Sparse inventory truth is not exhaustive truth.
The genus/species conflict table compares output heads using checkpoint taxonomy;
it flags inconsistency without claiming which head is correct. Chip drilldowns
rank by the selected species' accuracy and show its matched support. Conflict
tables show both raw counts and rates over comparable species/genus predictions.
Species table headers sort in either direction, with missing scores last.
Search, minimum labeled/matched samples, minimum chips, maximum top-1 accuracy
and scientific/vegetation filters apply instantly to the loaded species table,
without rescoring; table filter/sort settings persist in the URL. Minimums are
inclusive and do not change other report sections.

### Species debugger

Open **Species debugger** in Studio (`/species-debugger?city=usbos`). Choose two
saved models, a training/validation split and a scientific species or genus.
The shared City selector includes **All cities**. Model families resolve to each
city's cohort; only cities/tiles supported by both selections are compared, with
excluded cities reported explicitly. Counts are pooled across included cities.
Each card carries its owner city for imagery, full-tile links and corrections;
an individual label cannot be saved with `city=all`.
The view loads automatically. Without explicit URL filters it selects the species
with the most new classification errors (A correct → B wrong), falling back to
the most B mismatches. The clickable ranking shows wrong/classified support for
both models and new-error counts; missed detections, masked samples, unknown
labels and vegetation categories are excluded from this species ranking.
Explicit URL filters and deliberately clearing the species are preserved.
The paginated card grid shows each labeled tree or unmatched prediction with
24–60 m of surrounding imagery, both classifications, and detection markers.
A dashed tile boundary distinguishes adjacent imagery. Filter by labeled or
predicted species, regressions, improvements, unknown labels, or unmatched peaks.
One background comparison is cached; crops load lazily. Test data is excluded.

Both models match against **B's frozen labels** on their shared tiles, using the
same confidence and matching policy. The completed v16 CPU diagnostic sample
also exposes v15 parent, rejected step848, and rejected final1132 predictions.
Its labeled-center mode separates taxonomy changes from detection matching;
masked/unsampled centers are unavailable, never counted as wrong. These sampled
checkpoints are clearly labeled and are not full-run evaluations or promotions.

**Edit current label** prepares the editable scene inline if needed. Save an
explicit scientific species/genus correction, clear it to unknown, or restore
the inventory label. Concurrent edits fail with a revision conflict; reload
the saved label before applying your proposed correction. Existing positions,
review status, crowns, notes, and other trees are preserved. Added trees update
their canonical region. Unsaved edits block view changes and warn on navigation.

Corrections stay separate from frozen scores. **Publish All** exports inventory
`taxonomy_corrections`; the next chip build maps them through that run's taxonomy
to species/genus IDs and masks. Unknown labels retain detection supervision.
Names absent from the taxonomy do not silently add model classes; a known genus
can remain supervised while species is masked. Existing runs and sealed test
labels remain unchanged. Browser verification uses the disposable
`tests/species_debugger_fixture.py` server on port8768 and
`tests/browser_species_debugger.cjs`; neither opens live annotations.

A prominent loading panel shows elapsed time and actual cohort/chip progress.
Previous results are hidden while updating and failures offer a retry. Counts
describe work completed rather than an estimated time remaining.

Scoring uses one on-demand CPU background worker and one cached result, with a
fresh evaluation bundle per cohort. It does not take the annotation save lock.
Live annotation snapshots are captured briefly under that lock; coverage/tag
aggregation happens outside it. No GPU inference or promotion occurs.

### Diagnosing slow saves

`GET /api/save-timings` exposes the last 30 save timings since startup, containing
only city, route, outcome and timings (no tree IDs or annotation contents).
Successful save responses also include `timing_ms`; saves taking at least one
second log the same diagnostic. Phases distinguish request preparation, lock
wait, state loading, validation/merge, serialization, revision hashing, durable
write and cache update. `lock_held` is a containing total, not an extra phase to
add. These are server timings, excluding browser debounce/queue and network
transit; a slow snapshot is not the same as an unsaved canonical annotation.

For an isolated baseline, `python lambda/profile_review_save.py <review-dir>`
copies manifest/reviews into a disposable directory and profiles two no-op saves.
It refuses a copy changed during capture and never writes to the live workspace.
An October 9, 2026 sample measured SF/Boston warm no-op saves at 0.35–0.66 seconds
and NYC at 0.03–0.04 seconds, plus uncached state loading of 1–1.4 seconds and
0.09 seconds respectively. This demonstrates city-size overhead, not the cause
of a particular long wait; use live timings to identify contention.

### Queue transition prefetch

The scoped curation editor prepares only the **next member of its active queue**
(training, validation, or a mixed-city chip queue), after the current chip has
loaded and pending edits have saved. No gallery-wide or speculative queue search
is performed. `/api/review-next` is read-only: it checks city, imagery, model
cohort and chip split without recalculating coverage or creating a review scene.
Unopened chips use read-only source-pixel previews; their scene is materialized
only on actual navigation. Test windows are not admitted.

One entry in tab-local session storage holds imagery, the 16 px border, and the
selected prediction pair (or single model). It expires after five minutes and
is capped at 2 MiB of serialized UTF-16 storage. The entry is consumed on arrival;
model/queue changes invalidate it. Slow, failed, oversized or unavailable
prefetches fall back to ordinary loading and never block navigation.

Tab still waits for a successful durable save. Existing chips route directly to
their editor; current annotations and their conflict revision are always fetched
again on entry, including shared-tree changes from neighboring chips. Prediction
matching remains against frozen model labels, not the editable annotation state.
Existing-chip routes skip coverage and scene regeneration, model ownership uses
metadata rather than full prediction bundles, and comparison selectors no longer
score all historical runs. First-time server scene creation does not regenerate
the unused full static gallery HTML; the served gallery remains manifest-backed.

### Tracking validation-curation returns without retraining

Keep the model, saved predictions, evaluation windows, confidence threshold, and
matching policies fixed across successive curation snapshots. Rebuild **labels
and scoring masks**, not just tree coordinates. The sealed test is never used.
From this directory with `PYTHONPATH=src;lambda` on Windows:

```powershell
python lambda/rescore_current_validation.py --root <artifacts> --output <new-benchmark> --runs sf-boston-nyc-naip-swin-30cm-v12-curated-finetune --count-baseline <previous-completed-benchmark>
python lambda/track_validation_curation.py --root <artifacts> --baseline <previous-completed-benchmark> --current <new-benchmark>
```

Each output is immutable and captures saved curation before counting and scoring.
Stale publications are derived only inside the isolated snapshot. Original
evaluations and live annotations are not overwritten. The count is unique **net
tree-record changes within an interval** (including newly added trees, excluding
timestamp-only changes), not clicks or lifetime unique-tree totals. Repeated edits
to a tree in later intervals may count again; regions are tracked separately.

`curation-counts.json` retains the exact IDs/categories behind the denominator.
`curation-progress.json` records per-city fixed-.13 crown-aware, 2 m and 4 m F2,
precision/recall, matched-species top-1/top-5, best-threshold reference results,
and percentage-point change per 100 reviewed trees. Model outputs and chip sets
must be identical between endpoints. Pooled metrics are explicitly micro-pooled,
not an unlabelled average. `benchmarks/validation-curation-progress/*.json` indexes
the immutable intervals by path and SHA256; register each completed round once.

This is a descriptive curation-effort proxy, not a causal per-tree gain estimate:
registration, mask regions, target counts and matched-species support change too.
Correcting missed-tree labels can legitimately lower scores. Use several
comparable intervals plus residual-error audits to assess diminishing returns;
flat or decreasing score alone is not proof that validation is clean. Switching
models starts a separate series. No automatic training or promotion is implied.

### Manual recombinant components (draft lab)

The Studio **Recombinant tiles** tab (`/recombinant`) uses one pinned Boston
orthophoto, `ma_m_4207148_nw_19_030_20230707_20231116`, acquired July 7, 2023.
The initial view `r000062_c000063` contains large crowns and open ground.
Only 771 v12 training windows whose **entire source footprint plus spatial guard**
is training-eligible are selectable/rendered. Dark overview areas are unavailable;
the overview does not read held-out windows. The photo checksum is verified at
preparation, and extraction rejects changes to its file size/modification time.
Windows are cut directly from the original 30 cm RGB+NIR photo, not a multi-photo
mosaic. Photo-grid rounding is recorded through actual source-pixel coordinates.

Set independent **width and height from 16 to 512 px in 16px increments**, or drag
the corner handles in Position box mode. Each axis snaps independently; the
opposite corner stays anchored inside the viewport. Square presets remain as
shortcuts. Resizing a custom cut asks before replacing its outline; names/tags/edges
are retained. Existing square tiles load unchanged through their legacy `size` field.
Position box stays
active across repeated clicks; deliberately choose Draw cut outline to click vertices
around a tree/cluster and its ground/shadow margin. **Close outline** joins the
last vertex to the first. The **whole rectangle is selected by default**; cutting is
optional. Undo/clear are available. The right sidebar is the single save/edit
form: give the tile a name, characteristic tags, and four edge materials, then
save to the photo's library. Select a saved tile to edit its name/tags and four edge
materials: grass, soil, pavement, water, low vegetation, unknown or blocked.
There is no separate lighting classification: tiles share their parent photo.

The source view can pan off the original 512 px grid: focus the image and use
arrow keys (32 px / 9.6 m), Shift+arrow (128 px / 38.4 m), or the on-screen arrows.
Text/select fields retain their ordinary arrow-key behavior. Whole-rectangle selections
stay at their screen position; custom outlines stay anchored to their source pixels
while visible, with a confirmation before an out-of-view cut is replaced. Failed
pans retain the current image and draft. Every requested viewport is authorized
against the exact union of the pinned, guarded TRAIN rectangles before raster IO:
no uncovered gaps, validation/test/guard pixels, or out-of-photo bounds are read.
Saved `pan_x_y` views and absolute source-pixel provenance let these cuts reopen
and remain independent of the original grid. This does not expand train coverage.
Saving automatically includes the tile in generation and records the user's
clear-border confirmation; there are no separate enable/confirmation checkboxes.
Check that **no relevant object or its shadow crosses any cut edge** before saving.
This is a human check, not an automatic detector. Changing a saved tile's geometry
creates a new tile; metadata edits update the selected tile.
Mixed/unknown edge materials cannot join internally; recut clipped objects or
shadows rather than relying on a blocked edge facing the chip boundary.

The generator replaces the manual composition preview. It saves a 512 px RGB+NIR
chip from one source photograph, using saved library tiles only. Unsaved drawings
and tile metadata edits remain in the editor without blocking generation and are
not included until saved. Unsaved seam review still asks before replacement.
The default **Mixed rectangles** mode packs
different footprints on a 16px grid (`rect-pack-v2`); uniform-square modes remain
available. Seeded, bounded exact-cover backtracking varies the starting corner
and row/column scan direction (without rotating image pixels). Candidate weight
is `sqrt(area) / (1 + prior placements of that tile)`: larger pieces remain
favored, but repeated use is discouraged. It checks every touching edge (including partial edges at T junctions),
and permits repetition. No cropping, rotation, stretching, gaps or overlaps are
allowed. A compatible 16×16 background filler can fill leftover grid cells; it
still must match neighboring materials, and repeated texture can be noticeable.
The search can stop at its budget without proving that no solution exists.
Irregular cuts are overlaid on seeded-random full rectangles explicitly tagged **background**
(case-insensitive comma/semicolon-separated tag; a sidebar button adds it).
Backgrounds must come from the same photograph. A nested pass of the same packer
fills each cut's bounding rectangle using mixed background sizes/textures. Only
whole background tiles enter this pass, so nesting stops after one level. Up to
three independently seeded backing layouts per foreground are offered to the
outer packer; extra backing choices do not multiply a foreground's selection weight.
Individual backgrounds need not divide the cut dimensions if their combination
fills it exactly. Compatible **16×16px** tiles can fill remaining gaps. The size
dropdown includes 16px and 32px presets. All internal seams require compatible,
unblocked materials and use the selected blending width.
Without one, generation fails explicitly. The original keep-mask selects foreground
pixels; background pixels fill the remainder in all four bands. Composite boundary
materials come from the visible layer; mixed conflicting materials are blocked.
Neighbors must have matching known,
non-blocked edge materials. Seeded, bounded backtracking fails explicitly if no
arrangement is found or its search budget is exhausted; it never relaxes guards.
Tile repetition is allowed and reported. No rotation, scaling, inpainting or label
transfer is performed during generation. The same seed and eligible tile set with unchanged edge
markings, packing mode and blend settings reproduce the same pixels within an
algorithm version. Uniform generation is now `edge-grid-v3-background`. Recipes record each placement's
x/y/width/height, foreground/background IDs, frozen inputs and overlay count.
New recipes also record every backing repeat's local offset/dimensions and source
tile ID (`background_layout: mixed-rect-v2`). Diagnostic labels and training
export resolve each repeat separately, including occlusion, masks and unique
tree/group identities. Existing same-size and repeat-grid recipes remain readable and unchanged.

**Fullscreen + current annotations** opens a viewport-filling generated-chip
viewer with annotated crown circles, species names, optional excluded centers,
outer/nested seams and unblended imagery. It resolves the latest saved source
reviews (including unpublished edits) in a disposable workspace; it does not
publish feedback, mutate annotations, bind libraries, or save labels into recipes.
Refresh annotations reloads saved state. Unsaved browser edits are not included.
Only training-source labels are mapped; amber boundary flags are diagnostic,
not training approval. Annotation failures leave the image usable with a retry
button, and stale requests cannot replace a newer overlay.
The loading banner is prominent and animated (respects reduced-motion settings).
Crown/name overlays reuse the registration reviewer's DOM primitive and crown
styles, separate from the image pixels so labels stay crisp when enlarged.
Click any generated pixel, including an unlabeled patch, to locate its original
foreground or backing source. **Open source for curation** opens the geographic
source scene in a new tab; save corrections there and refresh annotations here.
Lookup is read-only. Only following that link can create a missing source
viewport, using the verified guarded photo footprint and curation raster affine
(never interpreting a source-photo chip ID as a curation chip ID). Existing
scenes are reused. Background repeats and irregular-cut masks resolve separately;
at feathered seams the original discrete mask decides which source is opened.
Source drilldowns select the newest completed, imagery-compatible training
evaluation and include its run ID and `.13` prediction threshold in the editor
link. A panned cut opens the evaluated regular chip covering the clicked point.
The preview names the selected model. Missing coverage/files are explicit and
do not silently substitute an older model; source curation remains available.

**Edge-blending first pass:** choose Off, 2, 4 (UI default), or 8 native pixels
(0, 0.6, 1.2, or 2.4 m). `boundary-feather-v1` feathers inward from cutout borders
using smoothstep pixel-grid distance weights, then symmetrically mixes opposite
pixels in narrow vertical and horizontal grid-seam bands, in that order. No
global color correction or whole-chip blur: interiors beyond these bands remain
exactly unchanged. RGB and NIR share the same weights; results are rounded/clamped
to uint8. Legacy API requests without `blend_width` retain hard cuts.
Mixed packing uses `rect-boundary-feather-v1`: only actual shared rectangle borders
are blended, including partial joins, not fictitious full-width grid lines.
Keep objects and shadows outside the **entire blend band**, not merely off the
cut line. The lab cannot detect violations; wide blending may ghost/soften detail,
and cannot fix incompatible materials or a broad color mismatch. Start narrow,
use the **Compare: show unblended chip** toggle, and inspect seams/corners before
accepting. Material compatibility guards are unchanged. Future overlap-aware
texture blending or background-only color matching should be evaluated separately,
not applied globally to species-bearing canopy pixels.
Both `image` and `unblended_image` are saved in the generated NPZ, with separate
RGB previews, pixel hashes, and versioned blending settings. Existing source tiles
and historical generated chips are never rewritten; changing width requires a new
generation (reuse the seed for the same layout). These remain unlabeled drafts.

Generated chips have saved original four-band NPZs, RGB previews, pixel hashes,
seed, source hash, and frozen candidate/placement recipes. Later tile edits do
not change historical generated chips. Review each chip's seams, with boundaries
shown/hidden, and save unreviewed/seams-accepted/rejected status plus notes.
**Seam acceptance is not tree annotation or training approval**: these chips
remain unlabeled and `training_ready=false`, outside the geographic chip-review
queue and training datasets. Names/tags (including background) are not
labels. A synthetic placement gets no invented geographic transform.

The [source curation linkage design](reports/recombinant-annotation-linkage-design.md)
now has a tested backend diagnostic path in `recombinant_labels.py`. An explicit
`curation-reference.json` per photo library links the curation workspace, imagery
identity and inventory; it covers legacy cuts without copying labels into them.
`lambda/check_recombinant_labels.py --help` describes explicit binding and read-only
resolution. It uses current published feedback and the referenced encoded inventory,
reuses canonical chip-builder corrections, verifies saved assets, and maps visible
foreground/background centers into repeated synthetic placements. Unsaved or
unpublished edits are not read. Bindings are not automatically installed in existing
libraries, and there is no new reviewer control yet.
The diagnostic command remains `training_ready=false`. A separate explicit exporter
now rebuilds the loss/ignore/group masks and freezes an admitted dataset:

```powershell
python lambda/export_recombinant.py --root <artifacts> --config <local-pinned-config> --generated-ids <saved-chip-id> --destination <artifacts>/recombinant-exports/<new-name>
```

Bind the photo library first using `check_recombinant_labels.py --bind` and explicit
workspace/raster/inventory paths. The export config must preserve the photo's pinned
split and seed (legacy sources resolve these from their parent run), 512px native
30cm RGB+NIR, the intended taxonomy, and inherited four-band normalization. Resolve
any remote paths in that config to their verified local counterparts before use.
The destination must be new. This command **does not launch training or add chips to
an existing run**, and there is no reviewer export button yet.

Export requires accepted generated seams, current published curation, and completed
train-scene coverage for every visible source pixel in both foreground and background.
A completed small crop only covers that crop, not its surrounding chip. It rechecks
train/validation/test guards, verifies source/cut pixels and reconstructs the generated
image against its checksum. Unknown crown extents, clipped or partially occluded
crowns/groups, objects in blend bands, and species/genus target conflicts block export.
Group containment uses conservative bounds; an otherwise valid tight irregular cut
may need more margin. Human clear-border checks still cover shadows.

Targets reuse the canonical label and mask builders: exclusions, protect/woods,
confirmed-background, single-tree regions and vegetation-group allowances transfer
through each visible layer. Fully uncertain output cells at cut/blend boundaries
receive no loss. Each repeated single-tree region gets new group IDs, and collisions
retain the existing discard policy. Only supervised cells are exported; fully
unsupervised chips are rejected. Packing tags never imply negative tree labels.

The result contains train-only `chips.parquet`, original-band scaled NPZ tensors,
synthetic-coordinate `labels.parquet`, inherited normalization/taxonomy, recipes,
resolved-input snapshots, checksums and a `COMPLETE` marker. It has no invented
geographic transform and no validation/test samples. Files are staged and verified
before publishing the new directory; existing exports are never overwritten.
`recombinant_export.verify_export(path)` checks completeness and file integrity before
a future job consumes it. Cuts still store references, not copied annotations;
source edits affect the next export, never an old dataset. Live library review flags,
annotation files, models and training policy remain unchanged.

Everything is stored separately in
`artifacts/recombinant/boston-manual-v1/`: immutable original-band component NPZs
with keep masks, transparent previews, source/checksum/pixel provenance, and an
atomic revision-checked `library.json`. It persists across model selections and
server restarts, but **is not yet covered by the annotation backup job**. Back up
this whole folder to retain the library. Additional prepared photographs have
independent libraries under `artifacts/recombinant/sources/<photo-id>/` and appear
in the Photo library selector. Cross-source mixing is rejected. Legacy components
and manual layout recipes remain preserved; existing cuts default to disabled
until explicitly reviewed and marked. Multi-tab conflicts preserve current
drawings/markings/reviews and require Reload library before retrying; unsaved work warns on exit.
No curation annotations, published inputs, historical evaluations or training
chips are modified. These are geometry drafts, not reviewed training examples:
transparent gaps are unknown pixels, names/tags are not species labels, and even
fully filled chips need label/mask/seam validation before a future export.

Source preparation is intentionally one-time and refuses to replace a pinned
source in place: `python lambda/prepare_recombinant_source.py --root <artifacts>
--run sf-boston-nyc-naip-swin-30cm-v12-curated-finetune
--photo ma_m_4207148_nw_19_030_20230707_20231116`.
`tests/manual_recombinant_fixture.py --source-root <artifacts> --port 8767`
serves a disposable UI test library and reads the pinned photo without changing
the live reviewer. `tests/browser_recombinant.cjs <playwright-test-module-path>
<screenshot.jpg>` exercises polygon cutting, edge marking, seeded generation,
seam review, reload and save conflicts
against that fixture only.

The registration editor’s 16-pixel neighboring-imagery border accepts tree placement, region drawing, dragging and nudging. The dashed amber line still marks the current tile. Border annotations retain geographic coordinates and appear in neighboring tiles; completion applies only to the current tile. Border input is disabled if its imagery fails to load.
# Curated validation policy (2026-10-26)

Future three-city fine-tunes use `more_done_validation_v1`: only full validation
tiles explicitly marked **Mark more done** (`scene_reviews[id].more_done is True`).
Ordinary Done, tree-by-tree status, and completed small crops do not qualify.
`prepare_three_city_curated.py` freezes review IDs and source hashes in
`validation-policy.json`. `joint.py` intersects them with the materialized
validation split, writes `curated-validation.json`, and removes other validation
rows from city manifests and labels before combining the training dataset.
Training rows, sealed test membership, pixel arrays, and masks are unchanged.
Scene sample split lists can contain both train and validation; chip manifest
membership determines admission.

The full step-zero parent baseline, early stopping, checkpoint selection, selected
model inference, fresh paired-parent inference, and score/confidence reports all
use that same frozen subset. Each city must have at least one eligible chip;
missing policies, changed source hashes, or mismatched cohorts fail closed when
the run declares this policy. The parent baseline records the admission-policy
hash alongside its exact input signature. Historical runs without the policy
retain their original semantics; do not overwrite their completed benchmarks.
New reporting uses only the explicit second-pass subset and identifies the
snapshot/cohort; never compare raw losses or scores across different subsets.

For curation planning, >=50% of SF/Boston validation tiles and all19 existing NYC
validation tiles are practical initial targets, not statistical guarantees or
hard launch thresholds. Distribute review over geographic blocks, density, crown
size, parkland, streets, shadows, and background-only scenes. Repeated selection
against the same validation set can overfit it; keep sealed spatial test data out
of routine tuning. See [Roberts et al. on structured validation](https://www.wsl.ch/lud/biodiversity_events/papers/Roberts_et_al-2017-Ecography.pdf)
and [Cawley and Talbot on model-selection overfitting](https://www.jmlr.org/papers/v11/cawley10a.html).

### Starting the local curator

From this imagery_model checkout, using the Python environment with the imagery
dependencies and required Rust extension installed:

```powershell
python curator.py start --background
python curator.py status
```

Omit `--background` to run in the current terminal (Ctrl+C stops it). The launcher
finds the main checkout via Git, loads its `.env` without printing credentials,
requires the Maps embed key, uses main-checkout artifacts and this checkout's
annotations, and sets the established model/GDAL cache defaults. Existing process
environment values take precedence. `--repository PATH` overrides main-checkout
discovery; `--port` defaults to 8765, bound only to 127.0.0.1.

Background startup checks HTTP readiness and writes logs under
`artifacts/qa/curator-logs/`. A healthy existing curator is reused; an occupied or
unresponsive port is reported without replacing its process. A kernel lock also
prevents duplicate launches before the server binds. After a startup timeout,
check `status` and the reported log; the process may still be initializing.
The launcher does not install dependencies or change review/model defaults.

### Imagery ownership checks

After adding imagery, collecting a model, or changing review routing, run the
artifact audit with `TREE_ML_DATA_ROOT` pointing at the actual artifacts directory
and `PYTHONPATH=src;lambda` on Windows:

```powershell
python lambda/audit_imagery_assignments.py --family <training-run-id> --output <audit.json> --live http://127.0.0.1:8765
```

The command exits nonzero for ambiguous or missing current-model imagery owners,
changed acquisition/split manifests, missing review images, duplicate chip keys,
sealed-test leakage, or incorrect live cohort navigation. It reads artifacts and
routes without editing annotations. Use it as a registration/release gate; unit
tests alone cannot detect a missing receipt in an actual downloaded run.

The focused regression suite is:

```powershell
python -m pytest tests/test_imagery_ownership.py tests/test_temporal_studio.py tests/test_temporal_training.py tests/test_collected_raster_paths.py tests/test_fairfax_curation.py tests/test_curation_admission.py
```

These tests exercise real small rasters: path spelling and portable nested VRTs
may differ, but source pixels, band mappings, acquisition identity, geographic
footprints and split membership must agree. Partial curation supervises only its
trusted regions; it does not admit unreviewed pixels as background.


## Prepared image cache in GCS

`build-chips` and training preparation can share immutable prepared image tiles
through a private GCS bucket and a local read-through cache. Install the `imagery`
dependency group and authenticate with Application Default Credentials. The
runtime identity needs object read and create access on the cache bucket.

Set `TREE_ML_PREPARED_IMAGE_CACHE_URI=gs://YOUR_PRIVATE_BUCKET/prepared`, or write
`prepared-image-cache.json` under `TREE_ML_DATA_ROOT` with this content.

```json
{"uri": "gs://YOUR_PRIVATE_BUCKET/prepared"}
```

The environment variable takes precedence, including an empty value to disable
caching. `TREE_ML_PREPARED_IMAGE_CACHE_DIR` optionally overrides the local default
`<data-root>/cache/prepared-images`. Local hits avoid network requests. A fresh
machine downloads an existing tile once. A miss prepares and uploads the tile.
Each build's `summary.json` records hits, misses, bytes transferred, source-hash
time and remote time for subsequent cost and performance comparisons.

The cache stores float32 imagery, the source validity mask and the image's
compressed NPZ member. Output chips keep the usual NPZ schema. Every build
regenerates curation targets, exclusions, scoring/training masks, admission and
normalization through the ordinary pipeline. Appending fresh targets preserves
the cached compressed image bytes.

Keys include source content and VRT dependencies/sidecars, ordered bands, input
scale, exact window, georeferencing, mask recipe, decoder versions and platform.
Source hashing reads the full dependency graph on each build. Changed imagery or
pixel preparation creates a new key. Curation edits reuse pixels while rebuilding
supervision. Linux and Windows deliberately have separate identities because
historical raster boundary reproduction differs. Frozen validation guards still
apply. Local corruption is repaired from GCS. Corrupt remote objects, source
mutation, authentication failures and conflicting concurrent writers fail the
build with an error. Uploads use create-only generation preconditions and local
publication uses complete temporary files.

For a containerized job, provision ADC separately on its host and set
`GOOGLE_APPLICATION_CREDENTIALS` to that host file in the job's environment.
`lambda/joint.sh` mounts it read-only at `/run/gcp-adc.json` and forwards the cache
and GCP project environment variables. Configure the URI in that same job
environment or in its persistent data root. Keep credentials outside Git, frozen
input/source archives and collected outputs. The launch tool does not copy local
ADC credentials to a remote instance. A remote host needs its own credential
provisioning before enabling GCS caching.

The first build fills the cache and includes upload overhead. Warm-cache timing
and source hashing should be measured on representative acquisitions before
estimating full-run savings. Cache storage and GCS operations/egress are billable.
