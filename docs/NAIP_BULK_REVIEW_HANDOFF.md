# NAIP bulk review: test tiles and asks for the training side

Branch `naip-point-predict`, 2026-09-27. We are manually testing the path from
a reviewed NAIP detection to a city parquet (reviewer `/satellite` → publish →
`data/raw/satellite_tree_info.py` → the city's `SATELLITE_{code}` partition →
`tree_dedup` merge). This note records the test tiles we picked, what they
showed about the model, and the prediction-side work we want the satellite
training agent to wire up. The reviewer and pipeline side stays with us.

The run used throughout is `sf-boston-naip-swin-30cm-v2-finetune` (newest
complete run, crown head on). Its operating threshold is **0.13**, the
cutoff with the best F2. The run metadata still says 0.35, so every export
here passes `--threshold 0.13` and the reviewer's slider starts there.

## Test tiles

Both sets live in the git-ignored `reviewer/tiles/`. Point the reviewer at one
set at a time: `SATELLITE_TILE_DIR` is read flat and does not recurse into
subdirectories.

### San Francisco: `reviewer/tiles/sf-gaps-30cm-v2/` (existing predictions)

These were exported from the run's existing `train-ussfo` and `validation`
predictions with `--min-score 0.1`. No model was run. We picked chips by
ranking all 3,399 non-test SF chips on above-threshold detections minus the
published `ussfo_tree_info_v2` rows inside the chip (every source), then
checked each by eye. `det` counts detections at or above 0.35, the stricter
cut the ranking used, and `inv` counts current published trees. At 0.13
every tile has more detections.

| Chip | Cohort | Where (from the imagery) | det / inv | What it tests |
|---|---|---|---|---|
| r000019_c000059 | train | Presidio Terrace (private circle), 37.7881, -122.4611 | 51 / 2 | Clean, isolated crowns with no city coverage: the easy accept path |
| r000035_c000099 | train | Mission Bay / Dogpatch big-box parking lot, 37.7656, -122.3915 | 60 / 1 | Rows of similar small lot trees: bulk-accept ergonomics and species consistency |
| r000010_c000064 | train | Presidio Main Post / Letterman, 37.8005, -122.4523 | 36 / 4 (OSM only) | Presidio Trust land, which has no municipal inventory |
| r000019_c000052 | train | Presidio (PHSH district), 37.7882, -122.4733 | 30 / 2 (OSM only) | Same, with mixed buildings and canopy |
| r000062_c000110 | train | Hunters Point new rowhouse development, 37.7281, -122.3727 | 20 / 2 | Young street trees too new for the inventory |
| r000034_c000055 | train | Golden Gate Park lawn and forest edge, 37.7674, -122.4683 | 26 / 1 (OSM only) | Park trees: detections come easily on open lawn, rarely in the forest |
| r000017_c000062 | train | Presidio forest edge and a planted grid, 37.7909, -122.4559 | 32 / 9 | Closed-canopy recall (poor) next to an easy regular grid |
| r000065_c000057 | train | Ingleside Terraces residential, 37.7245, -122.4651 | 25 / 1 | Street and backyard trees lost when the SF dataset shrank from 203k to 145k |
| r000077_c000064 | train | Outer Mission near the county line, 37.7078, -122.4530 | 32 / 4 | Dense rooftops, where some detections sit on roofs: rejection path |
| r000048_c000067 | validation | Diamond Heights / Glen Park, 37.7479, -122.4475 | 23 / 1 | Backyard trees next to a forest edge, validation cohort |
| r000088_c000086 | train | School south of the SF/San Mateo line, 37.6924, -122.4148 | 27 / 5 (OSM only) | Territory: SF's `CITY_TERRITORY` box runs to 37.60, so accepts here land in SF's parquet |
| r000065_c000064 | validation | Balboa Reservoir parking lot, 37.7244, -122.4529 | 12 / 0 | Mostly pavement: the reject and "tile reviewed" path |
| r000017_c000073 | train | Alta Plaza Park, 37.7908, -122.4367 | 61 / 44 | Control: dense municipal coverage, the "Duplicate of" path |

Extras in the same directory, all residential gaps: r000045_c000061,
r000064_c000055, r000065_c000052 and r000072_c000051.

### Central Park: `reviewer/tiles/nyc-centralpark-30cm-v2/` (six point tiles)

There are no existing NYC predictions. These six were made with the branch's
`point_predict` as it stands, taking 48 s in total including model load. No
code was changed. Inside Central Park the published USNYC parquet has about
2,500 trees: 1,456 `OSM_USNYC` and 1,046 `NYC_OPENDATA` (Forestry points,
mostly on the perimeter). The Conservancy counts about 18,000.

| Tile (chip) | Where | inv | Candidates ≥ 0.1 | Top score |
|---|---|---|---|---|
| p26918_e586445_n4513997 | Sheep Meadow (lawn, perimeter trees) | 8 | 512 (cap) | 0.22 |
| p26918_e586829_n4514765 | The Ramble (closed canopy) | 0 | 417 | 0.24 |
| p26918_e587904_n4516685 | North Woods (closed canopy) | 0 | 450 | 0.23 |
| p26918_e587136_n4515072 | Great Lawn ballfields | 4 | 405 | 0.23 |
| p26918_e588288_n4516531 | Conservatory Garden | 9 | 345 | 0.28 |
| p26918_e586598_n4515149 | CPW / Theodore Roosevelt Park (AMNH); Forestry-covered control | 168 | 190 | 0.32 |

**USNYC now has a `SATELLITE_USNYC` partition, wired on this branch but not
yet committed.** The pieces are the `SATELLITE_DATA_SOURCES` entry, its
freshness column, and the datasource in `nyc_tree_info.preql`. Scheduled
refreshes run from main, so until it merges a Central Park publish reaches the
export and is then skipped by the ingest with only a warning. The reviewer's
publish check accepts any `CITY_CODES` city, which does not guarantee the city
has a satellite partition.

## What the tiles showed about the model

1. **NYC scores are squeezed into a narrow band, so the 0.13 cutoff does not
   transfer.** On the six tiles almost every candidate scores between 0.13
   and 0.32. At 0.13 all 512 on Sheep Meadow's empty lawn pass, and only 9
   reach 0.2. On the same run, SF tiles reach 0.51–0.55.
   The imagery is `nj_m_4007309_sw_18_030_20230820`: native 30 cm, August,
   full leaf-on, an NJ-state item. By contrast, SF is 60 cm from May, resampled
   to 30 cm, and Boston is July. At 0.2 Sheep Meadow's lawn is almost empty,
   but candidates in the Ramble and the North Woods are still scattered
   through the canopy rather than sitting on crowns.
2. **Closed-canopy recall is poor in SF too.** The Presidio forest
   (r000017_c000062) and the Golden Gate Park woodland (r000034_c000055) get a
   handful of detections, while open-grown and street trees on the same tiles
   are found. This is expected from the positive-unlabeled target, which
   ignores unlabeled vegetation, and from labels that are almost all street
   trees. It also means "few detections" and "few trees" are not the same
   thing in parks.
3. **Some detections sit on rooftops** in dense row housing
   (r000077_c000064). Those are useful rejections to feed back.

## Asks for the training agent

In priority order. Items 1 and 2 are small fixes. Items 3–5 are the bulk
pipeline.

1. **Exporter writes empty bundles for chips outside the cohort.** When
   `tile_bundle_export --cohort validation --chips <train chip>` is given a
   train chip, it writes that chip's bundle with 0 predictions. The tile id
   does not include the cohort, so this silently overwrites a good export
   made from `train-ussfo`. We hit this exporting a mixed chip list. Fix:
   skip, with a message, any chip that has no rows in the cohort's
   `predictions.parquet` (or whose split doesn't match the cohort), in the
   same way test chips are skipped.
2. **Write the manifest when building the SF 30 cm VRT.**
   `artifacts/imagery/ussfo/2022/ussfo-2022-mosaic-sf-boston-naip-swin-30cm-v2-finetune-30cm.manifest.json`
   did not exist locally, so the exporter refused to run. The Boston
   equivalent had been derived by hand. We derived SF's the same way: a copy
   of `ussfo-2022-mosaic.manifest.json` with the path, the size doubled,
   resolution 0.3 and `derived_from` set. Please make the resample step emit
   the manifest for both cities so this is not hand-made.
3. **Gap-ranked export mode.** Add a way to choose chips by "detections minus
   current inventory". This could be an exporter flag such as
   `--rank-by-gap N` or a small command next to it. The logic we used is in
   `imagery_model/artifacts/rank_sf_gaps.py` (git-ignored, about 50 lines;
   run it from `imagery_model` with `uv run --group imagery --with duckdb`):
   - chip bounds from `tile_affine` + `tile_bounds_lonlat` over `chips.parquet`;
   - above-threshold detections per chip from both non-test cohorts;
   - a join against the published city parquet by bounding box;
   - sort by the difference.

   Two caveats. Rank on counts at a threshold the run is calibrated for, and
   see finding 2 above: a low count in a park is recall, not an absence of
   trees.
4. **Area mode for `point_predict` (Central Park first).** It currently takes
   repeated `--point`. We want `--bbox` or `--polygon` to lay out tiles at a
   full-tile step on its half-tile grid, skip tiles already in
   `artifacts/point-tiles`, and write one bundle per tile. Central Park is
   about 3.5 km². It is tilted about 29° from the grid, so it takes 195
   tiles at 153.6 m (113 wholly inside, 82 straddling the edge). Run it
   resumably, in batches that save their own results: long background runs
   on this machine have been killed partway through.

   Interim, 2026-09-27: we ran the whole park from the outside, generating
   the 195 grid-centre points and feeding them to the existing `--point` in
   batches of 40. The tiles are in `reviewer/tiles/nyc-centralpark-30cm-v2/`,
   ranked by a canopy-cover proxy: vegetation (NDVI > 0.2) that is textured
   in NIR, with the local std over a 9-pixel window above 3.5. On this image
   lawn stays below 2 and forest sits above 5. The scripts
   `imagery_model/artifacts/cp_grid.py` and `cp_density.py` (git-ignored)
   are the logic to fold in.
5. **NYC before a Central Park bulk run is worth reviewing.** With scores
   capped near 0.3 and candidates not on crowns, the bulk output would be
   noise. Options, cheapest first:
   - a per-imagery threshold or recalibration (the bundle's
     `confidenceThreshold` is already the slider's start, so a calibrated
     value can ship in the bundle);
   - normalisation against the NJ 2023 item's band statistics;
   - adding USNYC street-tree labels (1.12M `NYC_OPENDATA` rows) to a
     fine-tune, evaluated on held-out NYC blocks.

   Central Park itself has almost no labels, so it is a test area, not
   training data, until reviewed accepts exist.

   **Reviewer finding (2026-09-27): predictions on dense-canopy tiles are
   wrong, so a training pass is needed.** In the Ramble and the North Woods,
   detections do not sit on crowns at any threshold. The plan is to review
   the sparse tiles first (open lawn with scattered trees, where detections
   are checkable). Those accepts become the first NYC labels. The training
   pass then needs to learn closed canopy. That requires positives inside the
   canopy: today's positive-unlabeled target ignores all unlabeled
   vegetation, so a forest tile gives almost no signal. Candidate sources are
   the NYC Parks Forestry points that fall in parks, the reviewed Central
   Park accepts, and SF's Presidio and Golden Gate Park woodland, where the
   same failure shows.
6. **Tag bulk accepts, and exclude them from ground truth.** This is
   standing agreement. Hand-reviewed accepts are field-verified and stay as
   labels. Anything a bulk or auto-accept path publishes must carry a
   distinct marker in the export. The inventory export used for training must
   drop those rows from training and evaluation labels. Please agree the field
   name with us before either side builds it; it has to be in
   `satellite/published_trees.ndjson` and read by `inventory export`.

## Regenerating the tiles

```bash
cd imagery_model
# SF: each chip must be exported from its own cohort (see ask 1)
uv run --group imagery python -m urban_tree_ml.tile_bundle_export \
  --run sf-boston-naip-swin-30cm-v2-finetune --cohort train-ussfo --min-score 0.1 \
  --out ../reviewer/tiles/sf-gaps-30cm-v2 --chips r000019_c000059 r000035_c000099 ...
uv run --group imagery python -m urban_tree_ml.tile_bundle_export \
  --run sf-boston-naip-swin-30cm-v2-finetune --cohort validation --min-score 0.1 \
  --out ../reviewer/tiles/sf-gaps-30cm-v2 --chips r000048_c000067 r000065_c000064 r000065_c000052

# Central Park
uv run --group imagery --group train python -m urban_tree_ml.point_predict \
  --run sf-boston-naip-swin-30cm-v2-finetune --min-score 0.1 \
  --out ../reviewer/tiles/nyc-centralpark-30cm-v2 \
  --point 40.7719,-73.9750 --point 40.7784,-73.9700 --point 40.7960,-73.9570 \
  --point 40.7813,-73.9665 --point 40.7824,-73.9727 --point 40.7940,-73.9525
```

Add `--threshold 0.13` to all three commands; the tiles currently in
`reviewer/tiles` were made with it. The Central Park tiles open at 0.13,
where lawns fill with false detections. Drag the slider to about 0.2 to see
what the model actually separates there. Set `SATELLITE_THRESHOLD=0.13` for
"Predict at" too. Ideally the training side records 0.13 as the run's
`confidence_threshold`, so the flag is no longer needed.
