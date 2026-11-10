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
| Trees on screen after navigation | 9.6 s | 9.6 s | 9.3 s | 9.7 s |
| z11 settles (tiles trickle) | 8.3 s (0.85 s) | 9.9 s (0.63 s) | 8.0 s (0.51 s) | 10.2 s (0.58 s) |
| pan at z11 | 0.66, 0.77 s | 0.90, 0.91 s | 0.58, 0.62 s | 1.54, 1.14 s |
| z12 | 3.5 s (0.71 s) | 6.1 s (2.0 s) | 3.6 s (0.77 s) | 6.0 s (1.6 s) |
| pan at z12 | 0.55 s | 1.05 s | 0.72 s | 0.91 s |
| z13 | 2.0 s (0.42 s) | 2.0 s (0.64 s) | 1.8 s (0.85 s) | 3.4 s (0.67 s) |
| pan at z13 | 1.04 s | 1.09 s | 0.94 s | 1.78 s |
| z14 | 1.5 s | 1.5 s | 1.9 s | 2.2 s |
| z15 | 1.0 s | 0.65 s | 0.54 s | 1.16 s |
| z16 to z18 (four steps) | 0.65, 0.26, 0.29, 0.17 s | 0.59, 0.52, 0.20, 0.21 s | 0.62, 0.32, 0.41, 0.22 s | 0.56, 0.61, 0.20, 0.21 s |
| Whole walk (14 steps) | 21.3 s | 26.3 s | 20.8 s | 30.5 s |
| Renderer peak | 1,885 MB | 1,907 MB | 1,843 MB | 1,897 MB |

The whole walk is 21 s against 26 to 30 s: a quarter to a half slower.
The worker's own lines inside those walks: the z11 blocks 6.4 to 6.6 s
against 7.7 to 8.6 s (two 4x4 blocks run back to back, each waiting on the
other, right after the 6.9 to 7.4 s feature-table build), the z12 blocks
2.3 to 2.5 s against 3.3 to 3.7 s, z13 0.28 to 0.58 s against 0.35 to 0.75 s.
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

1. **Done on this branch.** Drop `category` from the overview GROUP BY and emit one shade per
   species at the aggregated zooms (`ensureNeighborhoodBatchTiles`,
   `generatePointTileMvt`'s aggregate branch, `aggCacheSql`). Nothing reads
   it; `COALESCE(tf.tree_form, 'default')` can stay a constant in the
   feature struct. Keep the hash split for the point zooms (z15+), where
   it costs nothing and is the visible effect. Worker: about -30% at z11,
   -5% at z13; fewer features for MapLibre to bucket.
2. **List only the colours the city holds** for the heatmap layers: join
   the city's vocabulary (species, tree_count) to `__species_shade` when
   the scheme is set or the city switches. Zero cost, but only smaller
   cities gain (Boston uses all 17), so low priority. (A window must keep
   the full list, as today.)
3. **Done on this branch (the second half).** The z15 feature table stores
   species, form and id and resolves the colour at read time, so the
   picker no longer rebuilds a 7 s table. Materialising the shade as a
   one-byte ENUM column (`ADD COLUMN` + `UPDATE`, 461 ms on Boston) was
   measured and is not worth it on its own: a few percent per query.
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

## What was done (this branch)

Items 1 and 3 above, as `colorSql(..., { aggregated: true })`, colour-only
GROUP BYs in the two overview aggregates and `aggCacheSql`, and a z15
feature table that carries id, species and form as columns and resolves the
colour at read time (`struct_insert`), keyed without the colour signatures.

Measured the same way after the change (leaf shade, autumn), worker then
Chromium:

| | Before (leaf shade) | After (leaf shade) | Tree form, for reference |
|---|---:|---:|---:|
| z11 view, worker | 2.69 s, 163k features, 4.6 MB | 1.88 to 1.96 s, 136k, 3.9 MB | 1.33 to 1.44 s, 91k |
| z12 view, worker | 4.6 to 4.7 s, 321k | 3.9 to 4.0 s, 299k | 3.3 to 3.6 s, 242k |
| z13 view, worker | 5.1 to 5.4 s, 389k | 4.6 s, 378k | 4.3 to 4.7 s, 345k |
| z14 view, worker | 2.0 s, 148k | 1.8 to 1.9 s, 144k | 1.7 to 1.9 s, 136k |
| Prewarm after a scheme switch | 7.4 s (feature table rebuilt) | 0.33 s (table kept) | |
| z11 blocks inside the Chromium walk | 7.7 to 8.6 s | 6.5 to 7.1 s | 6.4 to 6.6 s |
| z12 blocks | 3.3 to 3.7 s | 2.5 to 2.8 s | 2.3 to 2.5 s |
| z12 step settles | 6.0 to 6.1 s | 4.0 to 4.1 s | 3.5 to 3.6 s |
| Whole walk (14 steps) | 26.3, 30.5 s | 23.2, 23.3 s | 20.8, 21.3 s |

About half of the regression is recovered in the worker. What remains is
mostly MapLibre's side of the 18 layers (the z12 tiles still trickle for
1.2 s against 0.7 s) and the cells the extra colours genuinely add.

## Second round: the tile layout and the post-landing build

Two more changes, measured the same way (leaf shade, Boston, three Chromium
walks per build against the branch as it stood after the first round):

**One MVT source layer per colour** (`workers/mvtLayers.ts`). The first
round said the rest was MapLibre's 18 buckets and that no SQL change reaches
it; the tile's layout does. Every tile query now groups by colour, and each
group becomes its own source layer named by the colour (`ST_AsMVT` takes a
constant name, so the worker renames each layer and concatenates them: a
protobuf message's repeated field is the concatenation of its encodings).
Each heatmap layer and each circle layer reads its own source layer with no
filter, so MapLibre buckets every feature once per layer kind instead of
running 18 colour filters over every feature of every tile. The tiles also
lose what nothing reads: `display_color` (now the layer name), `category`,
`rotation`, the cell's constant `id` of -1 (a tap on a cell opened and
closed an empty tree card), `dbh` below z12 (no circle buckets those tiles)
and the per-cell float `dbh` above it (whole inches now, so the value table
holds a few dozen sizes, not one per cell).

| | Before | After |
|---|---:|---:|
| z11 view, MVT bytes (worker) | 3.9 MB | 2.3 MB |
| z12 view | 8.2 MB | 5.6 MB |
| z13 view | 10.3 MB | 7.0 MB |
| z11 / z12 / z13 view, worker time | 2.1 / 3.9 / 4.8 s | 2.2 / 3.9 / 4.8 s (unchanged) |
| z12 step settles (Chromium, 3 runs) | 4.30, 4.12, 4.63 s | 3.57, 4.02, 3.57 s |
| z12 tiles trickle (first to last) | 1.22, 1.23, 1.31 s | 0.82, 0.85, 0.87 s |
| z13 step | 2.52, 2.56, 2.17 s | 2.05, 2.10, 2.09 s |
| z14 tiles trickle | 0.44, 0.42, 0.51 s | 0.03, 0.04, 0.03 s |
| Whole walk | 25.0, 23.4, 23.1 s | 22.6, 22.5, 22.1 s |
| Renderer peak | 1,853 to 1,913 MB | 1,795 to 1,821 MB |

The worker's time does not move (the encode is per feature, and the
features are the same); what moves is the bytes and MapLibre's side. Frame
times under SwiftShader were the same within its resolution, so the 18
circle layers (one draw call per colour per tile, where there was one) do
not show as render cost here; that is the thing to watch on a real GPU.
Callers that named `trees-circle` or the `trees` source layer read the
per-colour lists (`treeCircleLayerIds`, `treeSourceFeatureCount`).

**The z15 feature table is built in row slices.** The desktop prewarm
builds it right after the intro lands, and DuckDB answers one statement at a
time: as one 6.5 s statement, the first zoom or pan after landing waited
behind all of it (the z11 step's 8 s was 6.5 s of queue and 2 s of its own
aggregate). Built as `rowid` slices of 250k rows (7 on Boston, the longest
1.0 s, 6.8 s in all against 6.5 s), a tile query waits for one slice. A
generation counter stops a build whose rows changed under it between slices
(a city load, a window move), which the old single statement could not
need and the old key could not tell: every city's default query has the
same key.

| | Before | After |
|---|---:|---:|
| z11 view asked 0.3 s into the prewarm (worker) | about 8 s (derived: the build's remaining 6.2 s, then its own 1.9 s) | 1.9 s (measured) |
| z11 step (Chromium, 3 runs) | 8.06, 8.18, 7.83 s | 4.55, 4.84, 4.86 s |
| The two z11 pans after it | 0.7 to 1.1 s | 0.7 to 1.6 s (the remaining slices run under them) |
| Whole walk | 22.6, 22.5, 22.1 s | 19.3, 19.6, 20.2 s |

Together: the walk is 19 to 20 s against 23 to 25 s for the branch's first
round, and below the 21 s tree-form reference measured before either round.

What is left on the list: item 2 (only the colours a city holds) still
gains only the smaller cities; item 4's single blurred circle layer is
still the way to drop the per-colour heatmap passes on the GPU; item 5, the
z12/z13 cell budget, is still the biggest number, and the stack section's
Arrow-fed layer is still the architectural answer. The per-colour source
layers are a step towards it: the colour already travels as the layer, not
as a property.

## Third round: the desktop draws its trees from memory

The stack section's option 2, done for the desktop. A desktop city is never
windowed, so every tree is already in DuckDB; the map now takes them once as
typed arrays and draws them in one custom WebGL layer
(`composables/treeDotsLayer.ts`). The vector-tile source, its 18 heatmap and
18 circle layers, the overview batches and the z15 feature-table prewarm are
gone from the desktop. A phone keeps the tiles (its window is the reason they
exist; docs/MOBILE_LOAD_BUDGET.md), as does a desktop without WebGL2 and a
renderable half-float target, which `TreeDotsLayer.supported` checks.

How it is built:

(The fourth round below replaced the whole-city read with chunks; the
renderer is unchanged.)

- **The data.** `getCityPoints` (`workers/cityPoints.ts`) reads every drawn
  tree's rowid, position (float32 metres from the city's corner), trunk size
  and colour as a one-byte index into the scheme's palette; 0.8 to 1.2 s on
  Boston's 1.7M rows, 17 bytes a tree. A colour change reads only
  `getCityPointColors`, rowid and colour (0.2 to 0.4 s), and scatters it by
  rowid; a query or filter change re-reads the points.
- **The heat** is two passes for any palette: trees add colour and density
  into a quarter-resolution pair of half-float targets, and a resolve draws
  the colour-weighted mean with an alpha from log2 of the density. Grey ("no
  recorded shade", "unknown trunk") counts fully as density and a sixth as
  colour, so the 83% of Boston with no shade no longer washes out the rest.
  A pyramid of 16 m to 8 km cells (`lib/treeDots.ts`) keeps each zoom to a
  cell per third of a kernel, built in a dedicated worker
  (`workers/treeDotsIndex.worker.ts`) because it costs most of a second.
- **The circles** are one point draw from z12.8 to z15.5 (z14 since the fifth
  round), coloured from a palette texture. Points and pyramid cells are stored in 512 m chunk order,
  so a frame draws only the chunk rows in view.
- **Picking** projects the candidates of a grid index through the last
  frame's matrix: a click selects exactly the circle drawn under it, and the
  card is read by rowid.

Boston, Chromium (SwiftShader), the same builds side by side:

| | Tiles (before) | Resident (after) |
|---|---:|---:|
| "Color by" change: trees on screen during it | 0 for 3.8 s | all 1,695,264 throughout |
| layers removed and re-added | 36 | 0 |
| new colours on screen | ~4.4 s, after a refetch of every tile | 1.3 s (DBH 2.2 s, it builds a per-tree table) |
| ms/frame under forced repaint, z10 / z11 / z12 | 375 / 300 / 273 | 250 / 200 / 158 |
| z13 / z14 / z15 | 333 / 300 / 150 | 273 / 214 / 136 |
| `pnpm bench:tiles` walk, 14 steps | 16.7 s | 8.8 s |
| z11 / z12 / z13 steps | 3.7 / 3.1 / 2.3 s | 1.1 / 0.9 / 1.2 s (the basemap's tiles) |
| renderer peak | 1,906 MB | 1,702 MB |
| first trees drawn after the city's tables load | 1.5 to 1.75 s | 2.6 to 2.8 s |
| main-thread work to index the points | | 80 ms |

The one number that moves the wrong way is the first draw: about 1.1 s
later, the points query, where the tile path drew its first z18 tiles
cheaply. Everything after it is cheaper, and the prewarm the tile path ran
after landing (7 s of the worker on Boston) is not needed at all.
SwiftShader runs vertex work on the CPU, so these frame times understate a
real GPU's gain: the tile path's per-frame cost was 36 offscreen passes,
the resident layer's is two plus one point draw.

Measured with `scripts/color-switch.bench.mjs` (new),
`scripts/frame-time.bench.mjs` and `pnpm bench:tiles`, all of which read the
resident layer where it is drawn.

## Fourth round: chunks of one global grid

The third round read a city whole. Toward seamless cross-city navigation,
where a city is a bookmark to a place rather than a unit of loading, the
desktop now reads its trees in chunks of one global grid and holds no notion
of a city in the renderer.

**The grid** (`workers/cityPoints.ts`): Web Mercator tiles at z12, keyed
`x * 4096 + y` from each row's stored z20 tile index. One grid for every
latitude and longitude. A chunk is the same size on screen anywhere, which is
what bounds the chunks a view draws; its ground size follows cos(latitude)
(7.2 km across in Boston, 9.8 km at the equator, 4.9 km at 60 degrees). An
equal-ground grid (degrees, H3) would not line up with the camera or with
the phone's window. A chunk is exactly 4 x 4 of the phone's z14 window cells
(`workers/cityWindow.ts`), so both layouts share one grid, and a
Hilbert-sorted projection keeps a chunk's rows in few row groups. Positions
are float32 metres from the chunk's south-west corner; the heat pyramid's
cells are the chunk halved nine times (19 m) up to the chunk, so no cell
straddles two chunks; each chunk's rows are stored in a 512 m draw grid's
order (sorted in the DuckDB worker, `gridOrderChunks`).

**The reads** (the worker): `getPointKeys` (which rows and colours a read
would answer with, no query), `getPointChunkDirectory` (the non-empty chunks
of the current rows with their counts, one GROUP BY; Boston has 33, 125 ms),
`getPointChunks(keys)` and `getPointChunkColors(keys)` (rowid and colour
only). The chunk filter carries the chunks' bounding box, so the table's zone
maps prune row groups.

**The order the layer loads in** (`composables/treeDotsLayer.ts`): new rows'
directory, then the chunks in view (padded a quarter), swapped in together
with the old rows dropped; then any chunk a move brings into view; then
colours, chunks in view first; then the nearest other chunks in 400k-tree
background reads, up to 6M trees held, farthest dropped past that. A sync
(query, filter or colour change) resolves when the view is current.

Boston, Chromium (SwiftShader), the whole-city build of the third round
against the chunked one:

| | Whole city | Chunks |
|---|---:|---:|
| First trees drawn after the city's tables load | 2.6 to 2.8 s | 2.1 to 2.2 s (directory, then the 2 chunks in view) |
| Main-thread work per read | 80 ms (once) | 3 to 14 ms (per read of 2 to 19 chunks) |
| Whole city resident | at first draw | 3 to 4 s after it, in 6 reads, before the intro lands |
| `pnpm bench:tiles` walk, 14 steps | 9.4 s | 8.1 s |
| ms/frame z10 / z11 / z12 / z13 (two runs) | 272 to 300 / 231 to 250 / 188 to 200 / 300 | 300 to 333 / 273 / 167 to 177 / 273 to 300 |
| settle after a jump, 10 jumps z10 to z14 | 0.83 to 1.13 s | 0.57 to 1.33 s |
| "Color by": new colours in view | 0.84 / 1.50 / 1.28 s | 0.82 / 1.56 / 1.03 s (tree form / DBH / leaf shade) |
| A jump to an unloaded chunk three chunks away | | 0.23 to 0.57 s to trees in view |

Frame times under SwiftShader move by a frame either way between runs; the
two builds draw the same work. Two settle outliers of 4 to 5 s in single
frame-bench runs of the chunked build did not recur in 20 timed jumps on
either build (all 0.6 to 1.3 s, the basemap's tiles).

What cross-city still needs, none of it in the renderer: the rows of more
than one city in the worker at once (the phone's window loader, by cell,
with the desktop's larger budget), a row key that survives that (the rowid
is the current table's; a colour refresh and the tree card use it), and a
published overview for the zooms where the view spans more than the budget
(z6 to z9 across a region).

## Fifth round: one handoff from dots to sprites

The resident dots made the desktop fast but left three looks on screen at
once: a blended heat, dots, and sprites that changed size on the way in.
This round leaves dots and sprites, with one handoff between them, and makes
the sprites keep up with the camera.

**The look** (`lib/treeCrowns.ts`, `lib/treeDots.ts`):

- The heat is off (`HEAT_ENABLED = false`; its pyramid worker still runs and
  can go if it stays off). Dots carry every zoom below the sprites.
- One constant places the handoff: `SPRITE_OPAQUE_ZOOM` (z14). The dots fade
  out and the sprites fade in over `SPRITE_HANDOFF_ZOOMS` (0.6) below it, and
  the MapLibre fallback reads the same pair. Above it dots show only where no
  sprite stands: past a capped batch's reach, or outside what was fetched.
- Sprites are always drawn at metre scale, never under `MIN_SPRITE_PX` (4 px);
  a tree with no crown width gets `DEFAULT_CROWN_WIDTH` (6 m). There is no
  marker-size transition left to cross.
- Dots are 30% smaller (`CIRCLE_RADIUS_SCALE`), and both tree layers draw
  under the basemap's labels.
- A dot is as wide as its tree's crown renders at that depth (`dotRadius`:
  the crown's disc at metre scale, measured else predicted else 6 m, as the
  sprite takes it), with the trunk-sized circle as its floor. A big tree past
  the sprites' reach hands over to a dot its own size rather than a trunk dot;
  only big crowns from about z12 up clear the floor. The crown travels to the
  dots as one byte a tree (quarter metres, 0 unknown) and four bytes of GPU
  attribute where it was two.

**The sprite query** (`workers/crownQuery.ts`):

- The default view (no filter, no chat scope) skips the filter; a filter is an
  `EXISTS` semi join. The `DISTINCT` join it replaced built the whole city's id
  set every query, ~170 of ~190 ms on San Francisco.
- It fetches the view's ground footprint (`lib/viewFootprint.ts`): the screen
  corners on the ground, stopped under the horizon as MapLibre's `getBounds`
  is, padded a quarter of the view's depth (at most 500 m) out to each side and
  behind the camera itself. Nearest first, up to the budget. The axis-aligned
  lng/lat box it replaced had large off-screen corners at any bearing but the
  four cardinal ones; they spent the budget, and after a spin the old view's
  direction stayed loaded further than the new one, an uneven horizon until
  the next batch.
- A capped batch is the footprint within `reach` of the camera it was queried
  from. Both layers measure reach from that camera, not the camera now; from
  the moving camera the disc slid ahead of the trees loaded and the dots hid an
  empty band in front of a pan.

**The batch** (`lib/crownBatch.ts`): the worker sorts it back to front for
the query's bearing and fills the vertex buffer, and transfers it; the main
thread only uploads it. Results cross the worker boundary as typed columns,
not row objects (32k trees: 5.3 s to ~0.26 s round trip before the move). A
batch is stamped with the bearing it was sorted for, so a spin that ends while
a query is in flight still fetches again.

**The connection.** Every query shares one DuckDB connection, so a sprite
query waited behind background chunk reads (10.6 s behind a 393k-row read in
one trace). Background reads now go in 100k-tree batches and stand aside while
the camera moves or a sprite query is due, for at most a second at a time;
longer starved them and left an empty band where neither dots nor sprites had
loaded.

**The budget** (`lib/spriteBudget.ts`) adjusts itself after every batch, by
rendering cost alone. The desktop has no distance cull of its own: the query
reads the view nearest first up to the cap, so the cap is where the sprites
end, and a cap aimed at a distance breaks wherever density changes. It used to
aim at one, where a 6 m crown is 4 px wide (1.6 km on a 1280x800 window), on
the claim that past that a sprite and a dot are interchangeable. They are not:
a sprite never draws under 4 px, bigger crowns are wider still, and thousands
of small sprites read as canopy where the dots read as points. On San Francisco
a fixed 131k cap against the ~21k it settled at looked clearly better at 50+
fps. Now a capped batch within cost grows the limit a step (x1.25) and
refetches, and only cost stops or shrinks it. A batch over cost holds its
limit as the ceiling at once but steps down a notch only later: stepping down at
once took away the band of sprites it had just painted. The step waits for the
next fetch after the view turns 45 degrees (a reverse, a look left or right) or
travels the ~11% of the reach a limit 1.25x smaller gives up, after which the
shorter reach still covers everything the old one painted ahead. An optional ground-distance cull
(`TreeRenderView.spriteDistance`, the debug panel's *Sprite distance*,
`localStorage.sf_trees_sprite_distance`) ends the sprites where we judge they
blend into the dots; it is off by default. Its limits are cost: the frame after an upload may run 12 ms long,
and the worker's build may take 150 ms; a batch over either sets a ceiling
that lifts again after 20 good batches. Between 8k and 131k sprites; a software
renderer starts at 8k and stops at 16k. `localStorage.sf_trees_sprite_limit`
fixes it, for sweeps.

Boston, RX 6800 XT (Direct3D), 1280x800, dense Back Bay and Jamaica Plain, a
4 s pan per view, fixed caps before the batch moved to the worker:

| Cap | Main-thread install | Query | Worst frame | Reach when capped |
|---:|---:|---:|---:|---:|
| 16k | 10-18 ms | 45-235 ms | 16.8 ms | 2.4-4.1 km |
| 32k | 22-65 ms | 50-260 ms | 33-50 ms | 3.6-4.8 km |
| 64k | 51-89 ms | 140-345 ms | 83-100 ms | 4.3-6.6 km |
| 128k | 115-147 ms | 385-490 ms | 200-233 ms | 7.4-9.7 km |

The GPU was never the limit (p95 16.7 ms at every cap); every hitch was the
install. After the move, at 32k: install 0 ms, worst frame 16.8 ms in every
view; the build costs 22-27 ms per 32k batch in the worker. With the
self-adjusting budget the same views settle at 8k to 13k sprites, reaching
2.0 to 3.9 km against the 1.6 km target. A z16 view holds 2k to 29k trees and
rarely needs the cap; z14 always does.

**Debugging.** The ⚙ beside the map's refresh button (lower left) shows a
readout: frame rate, resident dots, sprites against the budget, reach against
its target, query time split into SQL and build, upload, and a heading-up radar
of the view (white), the fetched area (green), a capped batch's reach (orange),
the target (dotted), the query in flight (blue) and a sample of the trees. Off
by default, remembered per browser.

Still open: wide pans show an occasional 200 ms frame that is neither the
sprites (upload and build are small then) nor the GPU; the dots' chunk reads
or the basemap are the next suspects. A frame stretched by something else can
lower the budget's ceiling for 20 batches. The sprite atlas, picking and the
phone's crowns are unchanged.

## Is this the right stack?

The data layer is right and is the product: DuckDB-WASM over published
parquets with no portal, Trilogy building the files, the chat and the URL
filters compiling to SQL over the same rows the map draws. Nothing in the
measurements argues for a server or a different engine.

The mismatch is one seam: the map uses MapLibre's vector-tile pipeline, and
one heatmap layer per colour, as the renderer for data that is already in
the page's memory. Every overview view takes the rows through four
representations: DuckDB aggregates them, `ST_AsMVT` encodes them to
protobuf in wasm (the spatial extension's only job here, a 6 MB download),
the bytes cross to MapLibre's worker, which decodes the protobuf and builds
one bucket per layer by filtering every feature once per colour, and then
each colour is its own offscreen pass per frame. Both halves scale with the
colour count, which is why a colour scheme change showed up as a
performance change. The sprite layer already does the other thing:
`getMapTrees` hands `TreeCrownLayer` typed arrays from Arrow and one
program draws them, with colour as a vertex attribute.

How much of the overview cost is the MVT round trip, measured on the
worker's own z11 to z13 block SQL with the final `ST_AsMVT` stage replaced
by returning the aggregate's columns:

| Block | MVT, as shipped | Columns only | MVT encoding's share |
|---|---:|---:|---:|
| z11 (136k cells) | 2.10 s | 1.03 s | 51% |
| z12 (299k cells) | 3.9 s | 1.68 s | 57% |
| z13 (378k cells) | 5.54 s | 1.96 s | 65% |

Half to two thirds of every overview batch is turning rows the browser
already holds into protobuf, before MapLibre spends its own time turning
them back.

Three ways forward, in rising scope:

1. **Precompute the default overview in the pipeline.** The default view
   (no filter, no chat scope) is the same aggregate for everyone: a
   per-city overview parquet of cells at the four overview grids with one
   colour column per scheme and phase, built by Trilogy beside the browser
   projection. The worker then serves default overview tiles from a table
   of a few hundred thousand cells instead of grouping 1.7M rows per view;
   filtered views keep the dynamic path, and they are subsets. This is the
   cheapest change and lives where the project already does its heavy
   lifting. It still pays the MVT encode and the per-colour buckets.

2. **Feed the overview and mid zooms from Arrow into one custom layer**, the
   way the sprites already work: the worker returns (x, y, colour, weight)
   as typed arrays for the view, and one WebGL program draws soft discs
   with additive blending at z6 to z13 and hard discs at z13 to z15. This
   removes `ST_AsMVT` and the spatial extension, the protobuf decode, the
   per-colour buckets and the per-colour passes in one move; colour becomes
   a per-point attribute and the palette size stops mattering. The costs
   are the ones `TreeCrownLayer` has already paid once: viewport-driven
   fetch and coverage, a pick path, and a visual redesign of the heat look
   (a multi-colour splat in one pass is accumulated premultiplied colour
   normalised by weight, or simply blurred discs). deck.gl's
   `MapboxOverlay` with a `ScatterplotLayer` on binary attributes is the
   off-the-shelf version of this and brings picking and culling, at the
   price of a second render framework beside MapLibre and about half a
   megabyte more script; the in-house layer is the smaller change given
   what exists.

3. **Prebuilt static tiles per city (PMTiles on GCS)** for the default view,
   with the DuckDB path kept for filtered views only. Zero browser compute
   for the common case and MapLibre's pipeline used as designed; still one
   tileset per colour scheme and phase, and still N heatmap layers unless
   combined with 2.

Recommended: 1 now (it is pipeline work in Trilogy and reuses the dedup
and projection machinery), 2 as the architectural simplification for the
big cities, starting with the heatmap zooms where the per-colour design
costs most and the look is a splat anyway. Not recommended: a server, a
different engine, or deck.gl adopted only for this.

## Not a problem

- The per-species join and the `hash(tree_id)` pick: a few percent at the
  overview zooms, noise at z15 and above and in the sprite query.
- `setSpeciesShadeSql` itself: 128 to 139 ms including the palette snap.
- The sprite layer: colour is a per-vertex attribute; no per-colour work.
- Chat overrides still layer over the species table per tree as before.
