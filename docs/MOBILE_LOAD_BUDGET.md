# The mobile load budget

Why NYC and Boston crash Safari tabs on phones, what was measured, what the
first stage changed, and the plan for the second. The measurements are from
2026-10-23 on the production build, real GCS data, headless Chromium with an
iPhone 13 profile; "peak" is the renderer process's private memory, which the
page and its dedicated workers (DuckDB included) share.

## The constraint

iOS Safari reloads a tab whose process outgrows its budget: about 1 GB on a
first visit, and 300 to 500 MB after reloads
([WebKit bug 269777](https://bugs.webkit.org/show_bug.cgi?id=269777)). The
map materialises the whole city in the DuckDB-wasm heap, that heap never
shrinks, and the heap's size is set by the *peak* during the city load, not by
what stays resident. So the number to manage is the peak, and every transient
(a hash join, an aggregate, a wide intermediate table) counts as much as the
tables that remain.

## Before and after stage 1

| Load | Rows | Peak before | Peak after | Trees on screen before | after |
|---|---:|---:|---:|---:|---:|
| SF, phone | 145k | 831 MB | 789 MB | 7.2 s | 6.0 s |
| Boston, phone | 404k | 1,033 MB | 879 MB | 24.4 s | 16.9 s |
| NYC, phone | 1.16M | 1,682 MB | 1,064 MB | 16.2 s | 10.1 s |
| NYC, desktop | 1.16M | 2,454 MB | 1,338 MB | 37.6 s | 30.1 s |

Entry JavaScript went from 8.8 MB (2.4 MB gzip) to 3.0 MB (0.8 MB gzip); the
dashboard, species and chat code now load when first shown.

Bytes from GCS did not change (48 MB for NYC, 34 MB of it the city parquet)
and are not the problem: the phone that crashes has finished downloading.

### What the first stage changed

All of it is in `src/`; no published file changed.

- **The city row is narrow** (`src/workers/cityTables.ts`). Species, display
  name, source name, cultivar, source and city are per-city `ENUM` columns,
  two bytes instead of a sixteen-byte string slot plus heap. One tile index
  pair is stored (z20) and coarser zooms shift it; the old table stored
  eight pairs. NYC's resident table went from 393 MB to 145 MB (native
  DuckDB, same storage format as wasm). The `trees` view casts the ENUMs back
  to VARCHAR, so chat SQL and the URL filters see the published types.
- **The vocabularies are read before the rows.** Each ENUM is built from a
  `SELECT DISTINCT` projection of the parquet, which DuckDB-wasm serves as
  column-chunk range requests at no extra bytes, so the one row scan casts
  straight into the ENUMs and the wide VARCHAR table never exists. Retyping
  columns in place after the load was tried first; it materialised the wide
  table and cost four seconds of `ALTER`.
- **The crown prediction is a column, not a table.** The per-city slice is
  joined at load without an aggregate (its grain is one row per tree); only
  the all-city fallback file is aggregated first.
- **No per-tree colour table.** The default colour is a `CASE` over
  `tree_form`; a chat override is a sparse table that exists only while it is
  active and is `COALESCE`d over the default. The city load no longer builds
  a 1.16M-row colour map, and no tile query joins one.
- **Overview tiles are viewport-bounded.** At zooms 11 to 14 a tile is served
  from an aggregate over the 4x4 block of tiles it sits in; nothing builds
  the whole city at once. The whole-city overview batch and the z14
  aggregate cache were the largest single transient after the load itself.
  (Stage 1 kept them for the desktop prewarm; the tile-latency work below
  removed them.)
- `SET preserve_insertion_order = false`: nothing depends on row order, and
  keeping it made every `CREATE TABLE AS` buffer its input.
- **Routing and layout load the dashboards lazily** (`src/router.ts`,
  `MobileLayout.vue`, `App.vue`).
- **The rankings lookup is city-scoped.** `loadTreeRanking` filters the
  312 MB rankings parquet by `ranking_city` as well as id; the file is
  ordered by city, so DuckDB prunes the other cities' row groups from the
  footer. An id-only lookup from the check-in dialog read most of the id
  column.

### What still sets the peak

The timeline for NYC on a phone: 400 MB before any city data (the 34 MB wasm
module compiled, MapLibre, the page), about 1,000 MB at the end of the city
load, 1,064 MB once the first tiles are up. The 600 MB of load growth against
185 MB of resident tables is the parquet read, the predictions join and the
ENUM casts; the wasm heap keeps whatever those grew it to.

How much of that growth the load's SQL shape owns was measured by replaying
the shipped statements in native DuckDB (single thread, NYC, each variant in
a fresh process, peak process memory sampled during the `CREATE TABLE`):

| Load | Peak growth | Time |
|---|---:|---:|
| As shipped: enrichment join, predictions join, vocabulary reads | +260 MB | 0.88 s |
| Same, without the predictions join | +178 MB | 0.70 s |
| One scan of a pre-projected 24 MB parquet, no joins | +173 MB | 0.48 s |

The resident table is 179 MB in all three, so below the predictions join
there is no transient left: the growth is the table. The join is a third of
the native growth, the enrichment join and vocabulary reads cost nothing
measurable, and the other 340 MB the browser shows is wasm's allocator and
range-request buffers, which no file shape changes.

### The browser projection (stage 1b)

The pipeline now publishes the worker's row shape per city
(`data/raw/tree_browser_city.preql`, `{code}_tree_browser_v2.parquet`): the
display name, the growth form and the crown prediction are columns, so the
load is one scan and a cast, with no enrichment join, no predictions join
and no probes. The worker falls back to assembling the shape itself for a
city whose projection is not out yet. `ecoregion_browser_v2.parquet` is the
same for the ecoregion dimension: three columns instead of 5.75 MB.

Measured on NYC, phone layout, the same production build serving both files
from localhost, twice each, with the bench's zoom walk (z13, 15, 16, 17, 18):

| Path | Trees on screen | Renderer at load | Peak over the zoom walk |
|---|---:|---:|---:|
| Assembled (the stage 1 load) | 13.2 s, 14.8 s | 973 MB, 981 MB | 1,866 MB, 2,020 MB |
| Projection | 9.0 s, 12.3 s | 988 MB, 970 MB | 2,061 MB, 1,754 MB |

So the projection is worth about three seconds of a thirteen-second load
and nothing at all in memory: wasm's heap after the load is the same with
or without the joins, which the native probe's 80 MB did not predict. The
pipeline job stays because the load is simpler, faster and probe-free, and
because the projection is the file stage 2 partitions.

### What sets the peak now: the tiles, not the load

The zoom walk is the finding. After the load the renderer sits near 970 MB,
and serving tiles at z15 to z18 then pushes the peak to 1.6 to 2.1 GB,
varying by several hundred MB between identical runs. The stage 1
measurement settled at one camera and never saw this; a phone that survives
the load is still reloaded by the first zoom in. The transient is somewhere
in the per-viewport work: the neighbourhood batch (a 4x4 block of z15 tiles
is about 17 km² of NYC), the crown query at z17+, and whatever MapLibre
holds for the tiles it was handed. None of it is isolated yet. This is the
next measurement to make, per step, with DuckDB's memory tags and the
worker's `[Perf]` lines, before any further lever is chosen.

Levers after that, in the order they are worth trying:

1. **Size the neighbourhood block by tree count, not by zoom.** The block
   is a constant per zoom band in `neighborhoodBlockSizeForZoom`; NYC at
   z15 puts tens of thousands of trees in one batch, Burlington a few
   hundred.
2. **Coarser overview grids at z11 and z12.** Done in the tile-latency work
   below: 128 m at z11, 64 m at z12, where the heatmap is the only layer.
3. **Replace the spatial extension.** `ST_AsMVT` is its only use and a
   point-only MVT encoder is small JavaScript, but there is no evidence it
   is faster: the engine runs in its own worker, so the encoder would run
   beside it, and Arrow rows are a few times the bytes of an MVT blob per
   tile. The case is the 6 MB extension download and its heap share, neither
   isolated yet. The native profile below puts the encoding at about 6 µs a
   feature on one thread, so it is the cost of an overview batch; gate on
   that share and the download, not on the argument.

## Tile latency

Stage 1 made the map slower to use while making it cheaper to load, and
the load bench could not see it: it settles at one camera. `pnpm bench:tiles`
covers interaction. It loads a city, waits for the desktop intro to land,
then jumps through z11 to z18 with pans and records, per step, the time until
the camera is still and every tile is loaded ("settled"), when the trees
source's first tile arrived and how long the rest trickled in, and the
renderer's memory. Boston, desktop 1400x900, each build served from
localhost against the same live data (1.57M rows, 62% of them predicted;
the stage 1 table above measured Boston at 404k):

| Step | Before stage 1 (f380c0c) | Stage 1 + 1b | Batching fixes | + intro fix |
|---|---:|---:|---:|---:|
| Trees on screen | 50 s | 19 to 29 s | 11 to 14 s | 7 to 10 s |
| Intro lands | 66 s | 51 to 75 s | after 120 s | 18 to 22 s |
| z11 settles | 13.1 s | 16.3 to 22.7 s | 2.6 s | 3.6 to 3.8 s |
| z12 | 4.8 s | 7.8 to 8.6 s | 2.9 s | 2.9 to 3.1 s |
| z13 | 1.6 s | 2.0 to 2.8 s | 2.4 s | 1.2 to 2.1 s |
| pan at z13 | 0.4 s | 1.5 to 1.7 s | 0.3 s | 0.3 s |
| z14 | 1.0 s | 1.7 to 2.2 s | 3.3 s | 2.8 to 3.4 s |
| z15 | 0.65 s | 2.0 to 2.5 s | 1.1 s | 0.65 to 2.4 s |
| Whole walk | 24.1 s | 36.5 to 40.0 s | 15.2 s | 16.8 to 17.0 s |
| Renderer peak | 2,029 MB | 2,072 to 2,082 MB | 1,772 MB | 1,659 to 1,663 MB |

The "batching fixes" column walked the map two minutes after the load,
once the prewarm had long finished; the last column walks it as soon as
the intro lands, so its first steps share the connection with the
prewarm's last few seconds.

What the worker's `[Perf]` timeline showed on the stage 1 build, in order
of cost:

- The z15 feature table (20 to 28 s of wasm) was built twice: the first
  build started under tile-query revision 0 just before the first
  `setTileQuery` bumped it to 1, so its guard discarded the result.
- Each overview zoom ran two overlapping neighbourhood aggregates, one for
  the 4x4 block and one for the reported visible range, 15 s apiece at z11.
  That is the popping at the far zoom: the tiles land when the second
  aggregate does.
- The 32 m grid at z11 is half a pixel (76 m per pixel there), so the far
  zoom aggregated half a million cells to draw a heatmap.
- The desktop prewarm then built whole-city z14 and z13 batches, 27 s on
  the single connection right after the load. Every tile request queued
  behind them, which is the "less snappy once it is up".
- Republishing an unchanged tile query or tree filter invalidated every
  cache.

A native profile of the same SQL (DuckDB, one thread, Boston projection)
put the z11 overview at 5.8 s on the 32 m grid through the `trees` view and
0.8 s on a 128 m grid; MVT encoding dominates at about 6 µs a feature, and
a z18 points batch costs 211 to 332 ms through the view against 55 ms
reading `trees_fast` directly.

What changed (`src/workers/duckdbPipeline.worker.ts`):

- Overview grids follow the pixel: 128 m at z11, 64 m at z12, 32 m at z13,
  24 m at z14. The circle layer starts at z12.8 and the heatmap weights by
  `grid_m`, so z11 and z12 look the same.
- The feature table is keyed by its content (base query, tree filter,
  zoom), not by the revision counter, so a republish of the same query
  reuses it; and `setTileQuery` and `setPublishedTreeIdFilterSql` return
  early when nothing changed.
- Overview batches cover the fixed 4x4 block only, and a request whose
  range an in-flight batch already covers waits for that batch.
- The default tile query reads `trees_fast` directly instead of joining
  the `trees` view back to it; a chat or URL query still goes through the
  view.
- The whole-city overview batch, the z14 aggregate cache and the prewarm's
  z13 and z14 calls are gone. The prewarm builds the z15 feature table and
  the z16 to z18 blocks around the camera.
- Every batch, feature-table build, prewarm and cache invalidation logs a
  `[Perf]` line with its milliseconds and reason, so the bench's timeline
  is the worker's.

What it costs: a z13 or z14 view that falls outside a built block costs
1.5 to 3 s with tiles trickling in, where the stage 1 desktop served them
instantly after its 27 s stall; a pan within the block is free. Dropping
the feature table saves another 360 MB of peak but makes a z15 view cost
12 s, so it stays.

### The intro

The batching fixes left the desktop intro held at its start pose for over
two minutes. The cause had been there since the intro was written and
grew with the city: Boston went from 404k rows to 1.57M when the predicted
partition went live.

- The intro gates its motion on a prefetch of the z15 and z16 tiles in
  view, and the z15 prefetch built the whole-city z15 feature table first.
- The prewarm started at the same moment, and the worker has one
  connection, so every query queued behind the other.
- Each "four-tile" points batch cost 8 to 15 s. `EXPLAIN ANALYZE` in the
  browser showed why: DuckDB-WASM (1.33) pushes the outer
  `WHERE feature.geom IS NOT NULL AND NOT ST_IsEmpty(feature.geom)` into
  the scan, ahead of the tile-range filter, and runs `ST_AsMVTGeom` on
  every tree in the city to keep 1,585. Native DuckDB 1.5 orders the same
  filter correctly (82 ms), so a native profile never shows it.

What changed:

- The points batch and the feature-table reader carry the geometry guard
  as the aggregate's `FILTER (WHERE ...)`, which cannot be pushed down:
  8.3 s to 32 ms for a four-tile z16 batch, same bytes. The feature table
  drops the guard (a point clipped to its own tile is never empty; 0 of
  1,573,245 were): 11.4 s to 3.6 s, where the filter ran the geometry
  twice. The aggregate modes filter above their GROUP BY, which already
  stops the pushdown.
- The intro's z15 prefetch batches the visible range only and no longer
  builds the feature table.
- The prewarm, which still builds it, starts once the intro has landed
  (or at once after a city switch, which has no intro).

On Boston the intro now starts moving one to two seconds after the
trees appear, lands 10 s later (the animation's length), and the prewarm is
done 7 to 10 s after that. The phone layout's walk settles in 10.5 s.

Worth re-measuring: the 12 s z15 view that kept the feature table (above)
was a points batch, and so paid the same pushdown. With it fixed the
table may no longer earn its 360 MB.

## Stage 2: cells

The premise is that what the map shows is arbitrary (linked-in filters, chat
publishes, colour overrides), so there is one rendering path, DuckDB over
whatever rows are resident, and the question is which rows are resident. The
city becomes a directory of cell files (z12 or z13 web-mercator tiles) plus a
manifest; the worker loads and evicts cells by viewport into the same
`trees_fast` shape. A filter that is row-local (a predicate on a row's own
columns, which is every URL filter and every `CASE` colour) is correct on a
partial load; one that is not (an aggregate, a window, a `LIMIT`) loads the
whole city first, which stage 1 made affordable. Zooming out to the city
overview is the other moment that loads everything, with a progress state.

The pipeline side is a native Trilogy feature, not a Python script:
`docs/TRILOGY_BUCKETED_OUTPUT_SPEC.md`.

## Measuring

```powershell
cd src
pnpm build
pnpm preview --port 6180            # second terminal
$env:MAP_BENCH_URL = 'http://localhost:6180/'; $env:MAP_BENCH_CITY = 'USNYC'
$env:MAP_BENCH_LAT = '40.7128'; $env:MAP_BENCH_LNG = '-74.0060'
pnpm bench:memory                   # MAP_BENCH_LAYOUT=desktop for the other layout
```

`scripts/bench-map-memory.mjs` prints the renderer's private memory at each
step and the bytes each remote file cost, counted from the worker's own
requests. It reads process memory from the OS on Windows and Linux. Compare
peaks, not final figures, run one scenario at a time (a second browser on
the machine skews both), and run each scenario at least twice: the zoom
walk's peak moves by several hundred MB between identical runs.

`pnpm bench:tiles` (`scripts/bench-tile-latency.mjs`) takes the same
`MAP_BENCH_*` variables, defaults to the desktop layout, and prints one row
per step of its walk (`MAP_BENCH_STEPS` to change it, e.g.
`z11,pan,z13,pan,z15`) followed by the page's `[Perf]` lines with seconds
since navigation. Run it before and after any change to the tile queries,
the batching or the prewarm, on the same data, with a baseline build served
from a second preview port. Settle times move by about half a second between
identical runs; the trickle span and the `[Perf]` batch milliseconds are
the steadier signal.

To measure against a file that is not published yet, build with
`VITE_TREES_BASE_URL=http://localhost:6180/local-trees` and put the files
the worker will ask for under `src/public/local-trees/` (gitignored): the
city parquet, `tree_enrichment_v2.parquet`, the city's predictions slice
and the candidate file. The preview then serves them with range requests
like the bucket does, and removing the candidate from `dist/local-trees/`
between runs gives the fallback path on the same build.

The worker logs `[Perf] duckdb-worker:city-load` with per-statement
milliseconds and DuckDB's own memory tags after every city load, so a
regression in the load shape shows in the console before it shows on a
phone.
