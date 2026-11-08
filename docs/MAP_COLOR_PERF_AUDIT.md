# Map performance after the leaf-shade colour scheme (desktop)

An audit of the desktop map after #182 and #185 made "Leaf shade (seasonal)"
the default colour scheme, measured 2026-11-08 on Boston (USBOS, 1,695,264
rows, the real published projection, enrichment and vocabulary) with the
tree-form scheme as the baseline. The tools are
`src/src/workers/shadeBench.wasm.test.ts` (the real tile worker on
DuckDB-WASM under Node), `pnpm bench:tiles` with `MAP_BENCH_QUERY` and
`src/scripts/frame-time.bench.mjs` (Chromium). Headless Chromium draws WebGL
with SwiftShader, so its frame times are relative, not a desktop GPU's.

## What changed on the hot path

`colorSql` (workers/cityTables.ts) is read by every tile batch, the z15
feature table and the sprite query. With the scheme on it adds a `LEFT JOIN`
to `__species_shade` on `CAST(tf.species AS VARCHAR)` and picks one of the
species' shades by `hash(tf.tree_id)`. The map then draws one heatmap layer
per distinct colour (useMapLayers.ts) and the overview aggregates group
cells by `(gx, gy, category, display_color)`.

The data decides most of the cost:

| | Tree form | Leaf shade (autumn) |
|---|---:|---:|
| Distinct colours in Boston's tiles | 8 (worker lists 9) | 18 (17 palette + grey) |
| Trees painted grey | 0 | 1,403,328 of 1,695,264 (83%) |
| Species with a shade | | 1,765 of 2,148 in the city; 3,812 of 4,811 enriched |
| Trees of a two-shade species | | 57,545 |

## Where the time goes

### 1. More overview cells, so slower overview batches and bigger tiles

The colour is now independent of `tree_form`, so a grid cell splits into one
feature per (form, colour) pair instead of one per form. Worker, 7x5-tile
desktop view at the city centre, the intro's whole-footprint prefetch:

| Zoom (cell) | Tree form | Leaf shade | Delta |
|---|---:|---:|---:|
| z11 (128 m) | 1.35 to 1.76 s, 91k features, 2.8 MB | 2.69 s, 163k features, 4.6 MB | +1.0 to 1.3 s, +79% features |
| z12 (64 m) | 3.2 to 3.6 s, 242k, 6.8 MB | 4.6 to 4.7 s, 321k, 8.8 MB | +1.1 to 1.4 s, +33% |
| z13 (32 m) | 4.3 to 4.7 s, 345k, 9.4 MB | 5.1 to 5.4 s, 389k, 10.6 MB | +0.7 to 1.0 s, +13% |
| z14 (24 m) | 1.7 to 2.0 s, 136k, 3.8 MB | 2.0 s, 148k, 4.1 MB | +0.3 s, +8% |
| z15 to z18 (points) | 0.30 / 0.10 / 0.06 / 0.04 s | 0.33 / 0.11 / 0.06 / 0.04 s | within noise |
| z15 feature table | 6.6 to 7.3 s | 7.1 to 7.3 s | within noise |
| Sprite query, z16 camera | 0.46 to 0.61 s | 0.49 to 0.53 s | within noise |

The join itself is cheap. Re-running the worker's own z11/z13/z14 block SQL
with the colour read swapped (same output) moved it by a few percent at
most: an ENUM-keyed shade table 3.10 s against 3.21 s at z11; a materialised
per-tree shade column (one `ALTER TABLE ADD COLUMN` + `UPDATE` over 1.7M
rows, 467 ms once) 3.04 s. What costs is the cells: MVT encoding runs at
about 6 µs a feature (docs/MOBILE_LOAD_BUDGET.md), and the view at z11 went
from 91k to 163k of them.

Two things fragment the cells for nothing:

- **`category` is still in the overview GROUP BY** (`tf.tree_form AS category`
  in both aggregate batches and `aggCacheSql`). No layer reads `category`
  from a tile: the heatmap filters on `display_color`, the circle layer
  reads `display_color` and `dbh`, and the sprites come from the crown
  query. It was free when colour was a function of form; now it multiplies.
  Dropping it: z11 163k to 144k features, 3.21 to 2.77 s; z13 389k to 383k.
- **Two-shade species split by `hash(tree_id)`** put two colours in the same
  cell. One shade per species at the overview zooms: z11 163k to 154k.

Both together with the materialised column: z11 2.23 s against 3.21 s as
shipped.

### 2. Twice the heatmap layers, so twice the bucket parsing and twice the render passes

MapLibre (4.7.1) builds one bucket per layer whose filter differs, and
`CircleBucket.populate` (the heatmap's bucket) evaluates that layer's filter
against every feature of the tile. With 18 heatmap layers each overview tile
is filtered 18 times in MapLibre's worker, against 8 to 9 before; at the
z13 view that is 389k features x 19 buckets (one circle) against 345k x 10.
Each heatmap layer is also its own offscreen pass (a quarter-resolution
framebuffer and a full-screen colour-ramp blend) on every frame between z6
and z15.

Chromium, 1400x900, Boston served from localhost (`pnpm bench:tiles`,
`MAP_BENCH_QUERY=colorBy=tree_form` against the default):

| Step | Tree form (run 1) | Leaf shade (run 1) | Tree form (run 2) | Leaf shade (run 2) |
|---|---:|---:|---:|---:|
| Trees on screen after navigation | 9.6 s | 9.6 s | TF2_LOAD | LS2_LOAD |
| z11 settles (tiles trickle) | 8.3 s (0.85 s) | 9.9 s (0.63 s) | TF2_Z11 | LS2_Z11 |
| pan at z11 | 0.66, 0.77 s | 0.90, 0.91 s | TF2_PAN11 | LS2_PAN11 |
| z12 | 3.5 s (0.71 s) | 6.1 s (2.0 s) | TF2_Z12 | LS2_Z12 |
| pan at z12 | 0.55 s | 1.05 s | TF2_PAN12 | LS2_PAN12 |
| z13 | 2.0 s (0.42 s) | 2.0 s (0.64 s) | TF2_Z13 | LS2_Z13 |
| pan at z13 | 1.04 s | 1.09 s | TF2_PAN13 | LS2_PAN13 |
| z14 | 1.5 s | 1.5 s | TF2_Z14 | LS2_Z14 |
| z15 | 1.0 s | 0.65 s | TF2_Z15 | LS2_Z15 |
| z16 to z18 (four steps) | 0.65, 0.26, 0.29, 0.17 s | 0.59, 0.52, 0.20, 0.21 s | TF2_Z16 | LS2_Z16 |
| Whole walk (14 steps) | 21.3 s | 26.3 s | TF2_TOTAL | LS2_TOTAL |
| Renderer peak | 1,885 MB | 1,907 MB | TF2_PEAK | LS2_PEAK |

The worker's own lines inside those walks: the z11 blocks 6.5 to 6.6 s
against 7.7 to 7.8 s (two 4x4 blocks run back to back, each waiting on the
other, right after the 6.9 to 7.4 s feature-table build), the z12 blocks
2.3 to 2.4 s against 3.3 to 3.6 s, z13 0.27 to 0.58 s against 0.35 to 0.75 s.
So of the 2.7 s the z12 step lost, about 1.1 s is the worker and the rest
is MapLibre parsing 18 buckets a tile instead of 9: the span from the first
tile to the last went from 0.7 s to 2.0 s, and the pans at z11 and z12,
which the worker answers from cache, slowed by 0.2 to 0.5 s.

Per-zoom render cost and main-thread time (`frame-time.bench.mjs`):

Features MapLibre holds for the view (`querySourceFeatures`), and frames per
second under forced repaint. SwiftShader draws at 2 to 9 frames a second
in either scheme, so only the direction is meaningful:

| Zoom | Features, tree form | Features, leaf shade | ms/frame, tree form | ms/frame, leaf shade |
|---|---:|---:|---:|---:|
| z11 | 88,876 | 151,505 (+70%) | 300 | 429 |
| z12 | 144,024 | 204,267 (+42%) | 300 | 375 |
| z13 | 146,245 | 162,383 (+11%) | 375 | 375 |
| z14 | 76,701 | 81,128 (+6%) | 375 | 375 |
| z15 | 23,436 | 23,436 | 188 | 176 |
| z16 | sprites, no tiles | sprites, no tiles | 111 | 107 |

Main-thread script time over a step was 10 to 80 ms in both schemes: the
cost is in MapLibre's workers (bucket parsing) and the GPU passes, not the
page's own JavaScript.

### 3. Every colour in the enrichment table gets a layer, whether the city has it or not

`baseDistinctColors` lists every snapped shade in `__species_shade`, which
is the whole enrichment table, so every city draws the maximum 18 layers. A
city whose trees use 10 shades still parses and renders 18. Boston happens
to use all 17, but the vocabulary file already says which species a city
has, so the list could be the join of the vocabulary and the shade table
(a few thousand rows) rather than the table alone.

### 4. Switching the scheme rebuilds everything

`treeFilterSignature` and `featureTableBuildKey` include the shade
signature, so the "Color by" picker invalidates every tile and batch and
rebuilds the z15 feature table (7 s on Boston) because `display_color` is
baked into it. The DBH override always did this; now the default scheme
does too, and the prewarm's work is thrown away on the first switch.

### 5. Not a regression, but the biggest number on the page

The z12 and z13 desktop views are 242k to 389k features and 7 to 10 MB of
MVT in either scheme: at z13 a 7x5 view is 34 by 24 km, the whole metro,
on a 32 m grid, and the circle layer (from z12.8) draws every cell. The
colour change added 13 to 33% on top; the base is the cell size against a
desktop viewport. docs/MOBILE_LOAD_BUDGET.md lever 3 ("size the block by
tree count, not by zoom") is this.

## What to do, in order

1. **Drop `category` from the overview GROUP BY and emit one shade per
   species at the aggregated zooms** (`ensureNeighborhoodBatchTiles`,
   `generatePointTileMvt`'s aggregate branch, `aggCacheSql`). Nothing reads
   it; `COALESCE(tf.tree_form, 'default')` can stay a constant in the
   feature struct. Keep the hash split for the point zooms (z15+), where
   it costs nothing and is the visible effect. Worker: about -30% at z11,
   -5% at z13; fewer features for MapLibre to bucket.
2. **List only the colours the city holds** for the heatmap layers: join
   the city's vocabulary (species, tree_count) to `__species_shade` when
   the scheme is set or the city switches. Zero cost; smaller cities draw
   10 layers instead of 18. (A window must keep the full list, as today.)
3. **Materialise the shade as a column at scheme change**, a one-byte ENUM
   over the palette plus grey, `ADD COLUMN` + `UPDATE` (467 ms on Boston,
   1.7 MB), and read it in `colorSql` with no join and no hash. Small per
   query (a few percent), but it also lets the z15 feature table and the
   tile caches stop depending on the colour: store the species or shade
   code in the feature and resolve the colour at read time, so the picker
   no longer rebuilds a 7 s table.
4. **Reduce the heatmap layer count rather than the per-layer cost.** The
   per-colour-layer design is O(colours) in both parse and render. Options
   in rising effort: cap the palette the overview snaps to (the autumn
   palette's six warm shades and three barks could be three and one at
   z<13 while the point zooms keep seventeen); or replace the per-colour
   heatmaps with a single circle layer with blur at z11 to z13 (one bucket,
   one pass, `circle-color` data-driven), which also removes the
   `point_count`/`grid_m` weighting indirection.
5. **The cell budget at z12/z13 on desktop** is the pre-existing cost that
   dwarfs the rest: a tree-count-aware grid (coarser when the view holds
   more than ~100k cells) would cut the 7 to 10 MB views in either scheme.

## Not a problem

- The per-species join and the `hash(tree_id)` pick: a few percent at the
  overview zooms, noise at z15 and above and in the sprite query.
- `setSpeciesShadeSql` itself: 128 to 139 ms including the palette snap.
- The sprite layer: colour is a per-vertex attribute; no per-colour work.
- Chat overrides still layer over the species table per tree as before.
