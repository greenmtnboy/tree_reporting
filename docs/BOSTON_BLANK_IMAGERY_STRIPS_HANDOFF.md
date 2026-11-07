# Boston blank imagery strips: investigation handoff

Observed 23 October 2026 (America/New_York), while rendering Boston aerial
detections against all other tree sources. This is an investigation handoff,
not a completed imagery-pipeline fix.

## Finding

Two conspicuous straight gaps in the tree map correspond to **blank pixels
already present in the cached NAIP inputs**. One example has a 56.1 m vertical
strip; another has a 32.4 m horizontal strip. All four image bands are zero
throughout each strip, but both cache manifests report `valid_fraction: 1.0`.
Both prediction records have `status: "ok"`.

The gaps are visible in both the local staged detections and the published
city parquet. Changing the poster's projection, water shading, or point styling
does not repair them. Missing input pixels are confirmed; the exact mechanism
that produced the incorrect validity metadata still needs a controlled repro.

## Workspace and artifacts

Inspected checkout: `C:/Users/ethan/coding_projects/sf_tree_reporting`.
HEAD at handoff: `85e6f59a93b2fee67152480cf32ebe63e3356f2b`.

Read the root and `imagery_model/AGENTS.md` before implementation. The imagery
instructions mention a separate active checkout under
`C:/Users/ethan/AppData/Local/Temp/sf-tree-review-done/imagery_model`; the specific
source path checked there was absent in this session. Resolve the current
working checkout before deciding where a fix belongs. Do not assume this
main checkout's code is necessarily the version that originally created the cache.

Relevant local paths, relative to the inspected checkout:

- `scripts/inspect_boston_imagery_seams.py`: read-only reproduction, created
  in this conversation and currently untracked.
- `scripts/usbos_sources_seam_diagnostic.png` and `.json`: the two examples,
  exact blank pixel indices, projected bounds, source items, and measurements.
- `scripts/render_boston_sources_map.py`: poster renderer, also currently
  untracked. It is not the cause of these strips.
- `scripts/usbos_sources_overlay_water.png`: poster showing the gaps.
- `.tmp/boston-gap-raw-v-published.png`: matched crop of staged versus published
  detections, longitude -71.21 to -71.145, latitude 42.28 to 42.36.
- `imagery_model/artifacts/point-tiles/naip2023/`: cached image inputs.
- `imagery_model/artifacts/bulk/usbos-v13/plan.json`, `tiles/`,
  `usbos_predicted_staging.parquet`, `predict-all.log`, and `overview.json`.

Images, JSON render outputs, `.tmp`, and model artifacts are gitignored; a
different worktree or machine will need access to them separately. The new
scripts and this handoff have not been committed or pushed by this work.

## Confirmed examples

Both inputs are 512 x 512 pixels, four bands, 0.3 m/pixel, EPSG:26919.
Pixel indices below are zero-based and inclusive.

| | Vertical strip | Horizontal strip |
|---|---|---|
| Cache basename | `p26919_e320102_n4688794_30cm` | `p26919_e318566_n4687258_30cm` |
| Bulk tile record | `2084_30525.json` | `2074_30515.json` |
| Completely zero region | Columns 325–511, all rows | Rows 0–107, all columns |
| Strip width/height | 187 px = **56.1 m** | 108 px = **32.4 m** |
| Blank fraction of tile | **36.5234375%** | **21.09375%** |
| Manifest validity | **100%** | **100%** |
| Prediction record | `ok`, 289 detections | `ok`, 193 detections |
| Manifest's sole source item | `ma_m_4207147_nw_19_030_20230707_20231116` | `ma_m_4207147_sw_19_030_20230901_20231116` |
| Source acquisition | 2023-07-07 | 2023-09-01 |

Append `.tif` or `.manifest.json` to each cache basename in the cache directory.
Prediction counts above are from the saved tile bundles, before staged-file
thresholding and deduplication.

Projected bounds `(west, south, east, north)`:

- Vertical: `(320102.4, 4688640.0, 320256.0, 4688793.6)`.
- Horizontal: `(318566.4, 4687104.0, 318720.0, 4687257.6)`.

For the vertical example, immediate west/east cached neighbors
`p26919_e319949_n4688794_30cm.tif` and
`p26919_e320256_n4688794_30cm.tif` had no all-zero RGB pixels in the inspected
images. The eastern neighbor's manifest switches to
`ma_m_4207147_ne_19_030_20230707_20231116`.
For the horizontal example, the tile immediately north,
`p26919_e318566_n4687411_30cm`, switches to the NW July source item.
These transitions point to source-image edge handling rather than a missing
entire prediction job. Whether adjacent source coverage fills every missing
pixel must still be checked against the original rasters.

SHA-256 fingerprints of the inspected inputs:

```text
p26919_e320102_n4688794_30cm.tif
b8536cab8fb6b7f018031e5decb5c70ea507dae8901d4681df91ca4ff2188083
p26919_e320102_n4688794_30cm.manifest.json
8b2306389a4438f979ae6c4a6fed55c3048e450f7f23fede07d64544ec00dee4
p26919_e318566_n4687258_30cm.tif
90ad1970865c6522a77fc90dfb3d675721593e89bff3d7e77e3415bfaa545b0e
p26919_e318566_n4687258_30cm.manifest.json
037eed9be71c05296d0f88c2a4cc78004e53bcbf6ada9cc96586b8f73127249f
```

## Reproduce without inference or network access

From the repository root (uv may install Python dependencies on first use):

```powershell
uv run scripts/inspect_boston_imagery_seams.py
```

The script reads the two cached TIFFs and manifests, tests
`np.all(data == 0, axis=0)` across **all four bands**, and writes the diagnostic
PNG/JSON. It neither fetches imagery nor edits caches, runs the model, or
publishes data. `--cache` and `--output` allow alternate paths.

Expected output: 56.1 m / 0.365234375 blank / 1.0 reported valid, and
32.4 m / 0.2109375 blank / 1.0 reported valid.

## Prediction-run context

Run: `sf-boston-nyc-naip-swin-30cm-v13-curated-finetune`.
Plan: EPSG:26919, `tileM: 153.6`, 16,011 planned tiles, filled metro-circle
boundary. A full scan of the 16,011 local tile JSONs found **all `status: ok`**
and no missing record files. This does not prove complete imagery or useful
detections within every tile.

An earlier log/overview reports 15,991/16,011; do not use that older summary as
the current tile-file status. Recheck the live artifacts when investigating.

The local staged parquet contained 1,556,695 rows, one run, minimum score
approximately 0.15. The plan's `runThreshold` is 0.35; do not confuse this with
the observed staged-file cutoff. The public Boston parquet contained
1,420,068 surviving `PREDICTED_USBOS` rows and 32 `SATELLITE_USBOS` rows at the
time of inspection. Cross-source merges mean those counts need not match.
Published data watermark: `2026-10-23 18:52:16 UTC`.

Public parquet used for the comparison:
`https://storage.googleapis.com/trilogy_public_models/duckdb/trees/usbos_tree_info_v2.parquet`.

## Leading hypothesis and code to inspect

In the inspected `imagery_model/src/urban_tree_ml/point_predict.py`:

- `tile_items` (around line 167) searches all same-year items intersecting the
  window, with the point's own items first.
- `read_tile` (around line 187) constructs a `WarpedVRT`, reads band masks,
  accepts `np.all(warped.read_masks(bands) > 0, axis=0)`, and composites only
  pixels not already marked valid. It exits early when `valid.all()` is true.
- `write_cached_tile` (around line 225) writes the image without an explicit
  validity mask or nodata value in this implementation.
- The cached branch (around lines 378–384) reads the TIFF and trusts the saved
  `valid_fraction`, rather than revalidating the pixels.
- The newly fetched branch computes that fraction from the accumulated mask;
  the minimum-valid threshold is 0.9.

**Hypothesis, not yet established by a controlled source-raster repro:**
outside-source pixels in the warped window may be zero-filled while treated
as valid. This could both prevent later neighboring images from filling those
pixels and cause the premature `valid.all()` exit, yielding the incorrect
100% validity. Check VRT coverage, source nodata/alpha/mask semantics, and
candidate-item coverage together before choosing a fix. Also investigate
four-band NAIP interpretation and saved color interpretation; do not assume
the fourth band is an alpha channel.

The exact historical implementation used to produce these caches is not
confirmed. The current source is a starting point, not proof of provenance.

## Suggested investigation and repair sequence

1. Preserve the two examples and their manifests; reproduce them against the
   relevant original NAIP assets in a separate scratch cache. Record Rasterio/
   GDAL versions, candidate items, source bounds, nodata, mask flags, band/color
   interpretation, and warped validity. Compare the first item alone with
   the intended multi-item composite.
2. Add a small offline test using adjacent georeferenced rasters. Verify that
   outside-source pixels remain invalid, the second raster fills the seam,
   real nodata stays invalid, and the final fraction reflects actual coverage.
   Include an edge in both directions and a four-band case. All-zero bands are
   useful diagnostic evidence here; they should not become a blanket rule
   that every dark pixel is invalid.
3. Scan cached tiles to measure the scope of the problem. Report suspect
   stripes and manifest discrepancies separately from genuinely uncovered
   imagery and legitimate dark areas. Identify which existing prediction
   bundles depend on those cache entries.
4. Fix validity/compositing and make affected cache entries refreshable.
   Versioned cache provenance or targeted invalidation is needed: simply
   fixing `read_tile` leaves existing TIFFs/manifests in use.
5. Rebuild and verify the two examples in isolation. Confirm the missing region
   receives real neighboring imagery where available, with honest validity
   for any remaining gaps. Inspect the result before rerunning detections.
6. A prediction rerun also needs targeted bundle invalidation/replacement.
   `bulk_predict.py`'s `pending()` skips an existing `ok` record, and
   `--retry-errors` only retries records marked `error`. These examples are
   already `ok`; rerunning the normal command alone will skip them.
7. After any separately authorized repair/rerun, reassemble locally with the
   intended threshold and seam deduplication, compare affected/unaffected
   regions, then follow the existing publication workflow. Preserve reviewed
   predictions and stable provenance; do not bypass the freeze-confirmed
   publication safeguard.

No cached inputs, prediction bundles, training/model settings, or published
parquets were changed during this investigation. No inference or GPU run was
started. This handoff asks the next agent to investigate; it does not itself
authorize compute allocation, model promotion, or publication.

Success means both examples are explained and repaired at the input stage,
regression tests cover the failure, affected-cache scope is known, and an
explicit rerun path handles existing successful bundles. Do not paint over
the missing tree strips in the visualization.
