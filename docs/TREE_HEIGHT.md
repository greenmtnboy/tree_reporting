# Tree height

An optional `height_m` on every tree row, filled where a city publishes a
LIDAR canopy survey. Boston's metro is the pilot, from two surveys:

| Survey | Layer | Crowns | Shape | Units |
|---|---|---|---|---|
| `BOSTON_LIDAR_2024` | [BPRD Tree_Canopy_Data_2024 / 12, Tree Centroids 2024](https://gisportal.boston.gov/arcgis/rest/services/BPRD/Tree_Canopy_Data_2024/FeatureServer/12) | 496,683 | point + `Radius` | feet (`Height` is a string column) |
| `CAMBRIDGE_LIDAR_2024` | [ENVIRONMENTAL_TreeCanopy2024](https://www.cambridgema.gov/gis/gisdatadictionary/environmental/environmental_treecanopy2024) ([FeatureServer](https://services1.arcgis.com/WnzC35krSYGuYov4/arcgis/rest/services/Tree_Canopy_2024/FeatureServer/0)) | 60,347 | crown polygon + `Radius` | feet (`Height_ft`, 98th percentile of the nDSM) |

The model is `data/raw/tree_height.preql`, the builder is
`data/raw/shared/canopy.py`, the staging job is
`data/canopy_staging/usbos_canopy_staging.preql` (`canopy-usbos`), and the
tests are `data/raw/tests/test_tree_height.py`.

## Why it is a lookup and not a source

A LIDAR crown is not a tree record. It has no species and no id that will
survive the next survey, and the watershed segmentation merges touching
crowns: 46% of Cambridge's inventory trees share their crown with another
published tree. If crowns went into the cluster merge as a sixth source
class they would add ~260k "trees" no person or model ever confirmed, and
they would compete with the inventory for position. So a crown is an
**attribute** that every tree row looks up by where it stands, the way
`tree_position.preql` looks up "is this point under a roof".

## The flow

```
portal crowns ──► shared/canopy.py ──► staging/usbos_canopy_height.parquet
(centroid, ft)    rasterise onto the     one row per ~2.2 x 2.5 m cell under a
                  position grid          crown: height_m, crown id, survey
                                                     │
raw tree rows (every partition) ─► canopy_cell ──────┘ LEFT JOIN
   municipal, OSM, satellite, predicted, community
                                         │
                          @by_survey over the cluster
                                         │
                       usbos_tree_info: height_m, height_source, height_crown_id
```

1. **Stage** (`canopy-usbos`, by hand). `fetch_city_crowns` pages both
   layers (centroids only; Cambridge's polygons come back as
   `returnCentroid`), converts feet to metres, and `height_sql` draws each
   crown as a circle of its own radius (clamped to 1.5–15 m) on the fixed
   position grid. A cell two circles cover goes to the crown it is
   *relatively* nearer (distance over radius), so a big crown is not carved
   up by a small neighbour. Heights outside 2–60 m are dropped.
2. **Key**. `tree_height.preql` derives `canopy_cell` from each raw row's
   own coordinates with the position grid's constants (pinned against
   `shared/ingest.py` by the test). The partitions need no new column.
3. **Merge**. `@by_survey` is `@by_source` with one change: an absorbed
   **prediction's** value is admitted last whatever else is in the cluster.
   `@by_source` keeps predictions out because their species and position are
   a model's guesses; this value is the survey's, and a prediction's point
   is a crown centre read off NAIP, where an inventory point is a kerbside
   geocode. When an inventory trunk misses every crown but the prediction it
   absorbed sits on one, the tree gets that crown's height
   (`test_an_absorbed_prediction_lends_its_crown`).
4. **Publish**. Boston's target stores `?height_m`, `?height_source` and
   `?height_crown_id`. Nothing else reads them yet: not the rollup, the
   browser projection, or the app.

## What it matches (measured 2026-10-29)

The published `usbos_tree_info_v2.parquet` keyed into the real raster
(6,445,941 cells from 554,141 crowns; 58 MB; 5 s to build):

| Source | Trees | Matched, all | Matched, inside survey coverage | Median height |
|---|---:|---:|---:|---:|
| CITY_OF_BOSTON | 52,033 | 75.9% | 75.9% | 10.5 m |
| CAMBRIDGE | 33,175 | 74.0% | 74.0% | 12.9 m |
| ARNOLD_ARBORETUM | 16,397 | 70.2% | 70.2% | 20.3 m |
| OSM_USBOS | 20,470 | 24.0% | 84.2% | 11.5 m |
| PREDICTED_USBOS | 1,527,072 | 19.7% | 71.4% | 16.4 m |
| Brookline, Somerville, Belmont, Watertown | 45,916 | <1% | (outside both surveys) | |

"Inside coverage" means within a 100 m bin holding at least one crown. The
predicted ring runs out to the whole metro circle, which is why its overall
rate is low.

Sanity checks:

- **Height tracks DBH** for matched Boston and Cambridge inventory trees:
  median 6.8 m under 6", 9.8 m at 6–12", 13.4 m at 12–24", 18.3 m at 24"+.
- **Units**: the medians in feet would be 34–45 m. The values are feet.
- **Unmatched inventory trees are not near misses.** The median distance to
  the nearest crown centroid is 8.5 m (quartiles 4.3 / 14.6 m). These look
  like trees the survey did not segment: young street trees under the
  canopy mask's height floor, trees planted since 2024, or trees removed
  since. A bigger radius would hand them a neighbour's height. Leave them
  null.

## Known limits

- **Merged crowns.** A segment can cover several trees, and its height is
  the 98th percentile of the whole segment, so a short tree under a tall
  neighbour's merged crown gets the tall one's height.
  `height_crown_id` exposes the sharing. A downstream "trees per crown" of
  more than 1 is the flag.
- **Field consistency.** `height_m`, `height_source` and `height_crown_id`
  are three separate `max()`s by class. They can disagree only when two
  rows of the *same* class in one cluster hit different crowns (two OSM
  nodes, two satellite rows), which is rare. A packed key like the species
  merge's would close it.
- **Survey vintage.** 2024. A tree planted since then has no crown, and a
  tree felled since then still lends its crown's height to anything standing
  where it was.
- **Edges of the surveys.** Boston's layer stops at its city limit and
  Cambridge's at its own. They overlap in a thin seam along the Charles
  (12 of the 100 m bins hold crowns from both). Where they do, the
  relatively nearer crown wins.

## What it opens up

- **Missing-tree candidates for the reviewer.** 262,448 crowns in the
  raster have no published tree under them. Many are on private land the
  imagery model also missed, and they would make a ready-made
  satellite-review queue with a height already attached.
- **Prediction quality.** A predicted tree with no LIDAR crown under it,
  inside survey coverage (~29% of in-coverage predictions), is either a
  post-2024 planting or a false positive. That gives a free precision probe
  for the NAIP model, and possibly a training-label source (crown centroids
  as positives).
- **Crown width.** The staged radius is an observed crown, so
  `2 * canopy_crown_radius_m` could fill `crown_width_m` where nothing else
  does. Not wired. It would change an existing published column.
- **Allometry.** Height against DBH by species, fitted on the matched
  inventory rows, the same way `crown_width_coefficients.csv` was, would
  give a predicted height everywhere else.

## Rolling it out

1. `canopy-usbos` publishes `staging/usbos_canopy_height.parquet` (or, from
   a workstation, `uv run python -c "from shared.canopy import
   extract_city_height; extract_city_height('USBOS')"` from `data/raw`).
   **The city model reads the object by URL, so it must exist before the
   first `city-usbos` refresh after merge.**
2. Force-build `city-usbos` (a new staged input reads as up_to_date on the
   cloud; see the new-source note in memory), then check:
   `select data_source, count(height_m) * 1.0 / count(*) from usbos_tree_info_v2.parquet group by 1`
   should be close to the table above.
3. Adding another city means adding its layers to `CANOPY_LAYERS`, a
   `CANOPY_STAGED_THROUGH` entry, a `canopy_staging/{code}_canopy_staging.preql`,
   a `canopy-{code}` job, and the import, lookup, probe and three target
   columns in the city model. `test_tree_height.py` names whichever is
   missing.

Generalising beyond LIDAR (a municipal inventory's own height field) would
add `raw_height_m` to `tree_partition` and coalesce it ahead of the survey.
That touches every city's ingest and the schema of every published parquet,
so it was left out of the pilot.
