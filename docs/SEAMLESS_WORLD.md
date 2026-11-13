# The seamless world

The map draws the trees under the camera, whichever inventory published
them. A city is a bookmark: a name, a centre, and the scope of what is said
about it (the URL, the selector, the landmarks, the chat, the dashboards).
Open the map on Boston and fly to Burlington, Vermont, or to San Francisco:
the trees there load and draw, and the bookmark follows the camera.

Before this, a city was the unit of everything. The worker loaded one city's
projection file into one table, and going anywhere else dropped it and loaded
another behind an overlay. #187 had already made the desktop's renderer
city-free (chunks of one global grid); this makes the data and the loader
match it.

## What is published

`publish-world-blocks` (`data/world/publish_blocks.py`, run by
`data/world_tree_blocks.preql`) cuts every city's browser projection into
blocks, once a day, after the projections:

```
gs://trilogy_public_models/duckdb/trees/world/v1/
  manifest.json                    the list of blocks; the commit point
  blocks/{key}-{hash}.parquet      one Web Mercator z12 tile of trees
  vocab-{hash}.parquet             every value of the string columns
```

- **A block** is the z12 tile `key = x * 4096 + y`: the grid the desktop's
  point chunks already use (`CHUNK_ZOOM` in `src/src/workers/cityPoints.ts`),
  7.2 km across in Boston. It holds every tree in it whichever city
  published it, in the projection's columns less its watermark. Rows are in
  Morton order of their z20 tile, so a phone's z14 cell is one run of rows.
- **A tree two inventories both publish** (an OpenStreetMap node inside two
  cities' territories) is written once, to the first city by code. The
  projections carry 15,459 such rows today; drawn from city files they were
  two trees on one spot.
- **The vocabulary** is one file for the world, `(kind, value)` rows for the
  six columns the worker stores as ENUMs. 186 KB today.
- **The manifest** lists each block as `[key, rows, hash, [city index, rows,
  ...]]`, and each city with its tree count. 61 KB today.

Measured on 2026-11-09 from the 44 published projections: 14,479,144 trees in
1,301 blocks, 353 MB (the projections are 459 MB; the blocks are zstd). The
median block holds 893 trees, the 99th percentile 130,000, the largest
321,837 (Bogotá). The build takes two to three minutes under a 2.5 GB DuckDB
memory limit.

### Content-addressed, manifest last

A block's name carries a hash of its bytes, and so does the vocabulary's.
That buys four things:

- Everything one manifest names is from one build. A reader never sees a
  block from yesterday beside a vocabulary from today, which matters because
  a block's strings are cast into ENUMs built from the vocabulary.
- Blocks are immutable, served `max-age=31536000, immutable`. The bucket's
  default hour of caching, which makes a rebuilt city file look stale, does
  not apply; a revisit reads from the browser's cache.
- A block whose trees did not change keeps its name and is not uploaded
  again. The build is deterministic: two runs over the same projections name
  all 1,301 files the same (`test_a_rebuild_of_the_same_trees_names_every_file_the_same`).
- The manifest is the only mutable object, written last with
  `x-goog-if-generation-match`, `max-age=300`.

Files no manifest names are deleted once they are three days old
(`RETAIN_DAYS`), and never the files of the manifest being replaced. A tab
keeps the manifest it opened with; if a block it names is gone it reads the
manifest again and starts over (`resetWorld` in the worker).

### Guards

A candidate with fewer than 75% of the live manifest's trees, or missing a
city the live manifest has, is refused and the live one keeps serving: the
same ratio `@kept` holds each projection to, never a row floor. `--force`
publishes a deliberate shrink. A city with no projection yet is skipped with
a log line and joins once it has one.

### Why a script and not a model

Trilogy writes a target as one file. A target written as a set of files
keyed by an expression, with a manifest, is the proposal in
`docs/TRILOGY_BUCKETED_OUTPUT_SPEC.md`; pytrilogy 0.3.475 has `partition by`
for table engines and for reading hive-style files, not for writing them.
Until it does this is a `run` job in the shape of the homepage feed's: the
script builds, validates and uploads, and the spec says what would replace
it with a declaration. The per-city projections stay: they are this job's
input, and the bookmark's city-wide reads.

### Running it

```bash
cd data
# Build and validate against the bucket, upload nothing:
uv run world/publish_blocks.py --dry-run --output-dir /tmp/world
# From projections already on disk:
uv run world/publish_blocks.py --dry-run --inputs-dir /path/to/projections --output-dir /tmp/world
# Publish (GOOGLE_HMAC_KEY / GOOGLE_HMAC_SECRET):
envx run -f ../.env -- uv run world/publish_blocks.py
```

To run the map against a local build, serve the output directory with CORS
and set `VITE_WORLD_BASE_URL` to it.

## How the map reads it

### The worker holds cells, not a city

`src/src/workers/duckdbPipeline.worker.ts`. At start it fetches the manifest
and the vocabulary, creates the ENUM types and an empty `trees_fast`. No tree
is loaded. From then on the rows are one table per held **cell**, and
`trees_fast` is a view over them:

| | cell | budget | loads when |
|---|---|---|---|
| desktop | a whole block (z12) | 3,000,000 rows | the resident layer or the sprites ask for a block |
| phone | a sixteenth of one (z14) | 300,000 rows (150,000 on the low tier) | a tile asks for a cell |

Past its budget the worker drops the cells farthest from the camera, never
one within its policy's radius and never one a read is using. This is the
phone's window from `docs/MOBILE_LOAD_BUDGET.md` with a block file as its
source instead of a range read of a city file, and the desktop now runs the
same rules with a larger cell. There is no whole-city mode.

How a cell's rows arrive depends on how much of its block it is:

- **A desktop wants the whole block**, so the worker fetches the file with
  `fetch`, six at a time, and hands DuckDB the bytes. DuckDB-wasm reads a
  remote file through synchronous range requests, one at a time, and a metro
  is a hundred and fifty blocks.
- **A phone wants a sixteenth of it**, so DuckDB range-reads the row groups
  its cells fall in from the published file, as it read a window out of a
  city's projection. San Francisco's first phone view is 24 cells of four
  blocks of 3.6 MB each; fetched whole that was 14.6 MB for a third of the
  rows.
- **A phone on one street wants a cell or two of one block**, and there the
  range reads lose: DuckDB-wasm's read-ahead doubles to a megabyte past every
  seek, so two cells of Boston's 3.6 MB South End block were 22 requests in
  a row and 4.6 MB (6.5 s at 9 Mbps). A load that touches two blocks or fewer
  fetches them whole (`WHOLE_BLOCK_LOADS`), keeps the bytes for the block's
  next cell, and Near Me asks for its block as soon as it has a fix, beside
  the engine's download (`prefetchWindowAt`).

The phone's crowns load only the cells on screen (`TreeRenderView.view`). They
reach 1.8 km, nine z14 cells, and asking for all of them made a street's first
view most of a block. `pnpm bench:nearby` is the measure: every request
between the Near Me tap and the first trees, under a shaped network.

A generic read (the landmarks list, a chat's SQL) sees the cells held when it
runs and does not wait for loads in flight: a desktop is fetching the rest
of a metro in the background for a minute after it opens.

### A read names the cells it is about

DuckDB plans a `UNION ALL` a branch at a time. Measured on native DuckDB 1.4
with the world cut into block tables, a bounding-box count over a view of:

| tables in the view | 10 | 50 | 150 | 400 | 1,301 |
|---|---|---|---|---|---|
| ms per query | 4 | 18 | 63 | 133 | 470 |

So the reads that run on every pan do not go through `trees_fast`:

- **Point chunks** (`getPointChunks`) read the chunk's own block table.
- **Sprites** (`getMapTrees`) read the held cells under the view
  (`scopeSql`), and fall back to the view only when the view is too wide to
  list (64 cells).
- **The tree card** names `TREES_AT_TOKEN` and sends the tree's position;
  the worker loads that cell if it was dropped and substitutes its table.
- **A filter** written against the published schema (`FROM trees`) follows
  the scope: the scoped read defines a CTE named `trees` over the same
  cells, which shadows the view. Every query the map publishes is row-local
  (a URL species filter, a chat's id list), which is what makes that sound.

Tiles (the phone, and a desktop without WebGL2) still read the view, as the
phone's window did; a phone holds at most a hundred or so cells.
`trees_fast` is redefined lazily, before the next read that uses it, not
after every load.

### The desktop's resident layer

`src/src/composables/treeDotsLayer.ts`. The chunk directory is the manifest:
every block on the planet with its count, from no query. The layer asks for
the chunks in view nearest first, in batches of at most 300,000 trees or 24
files so the near ones draw while the rest download, then the chunks within
45 km on the ground in the background, and keeps up to 6,000,000 points as
typed arrays (more than the worker holds as rows: a chunk the worker has
dropped is still drawn, and is loaded again if its card or its colours are
asked for).

A point's identity is its chunk and its row number in the block file (`rn`),
which is stable for as long as the manifest is.

**Below z9 nothing new is loaded** (`LOAD_MIN_ZOOM`). A 1280 px view at z9 is
about 200 km across; a zoom lower is several metros and millions of trees
for dots a fraction of a pixel wide. What is held stays drawn, and the
city markers say where the rest is.

### The bookmark follows the camera

`followBookmark` in `TreeMap.vue`, on every `moveend` at z10 or closer. The
city under the map's centre is the leading city of its block, or of the
fullest block within two (`bookmarkAt`), so a park or a harbour is still
that city and open country is no city. When it differs from the bookmark:

1. the worker puts the new city in context (`setCityContext`: its projection
   behind `trees_published`, its landmarks; two small reads, no tree moves);
2. the lifecycle adopts it with no phase change, so no overlay and no intro;
3. the URL's `city` is replaced, and the selector, the title and the
   landmarks follow.

Nothing is redrawn. The map's default query is the world's
(`WORLD_MAP_QUERY`), so the worker sees the same query before and after, and
the query watcher returns early when nothing the map draws has changed.

The bookmark does not follow while a chat answer about the city is painted
on the map (a published id filter or colour override), or in the nearby
picker. Picking a city from the selector or a globe marker still flies there
with the swoop; it now ends on trees that load for the view, not a city load.

### What is still a city

- `trees_published`: the bookmarked city's projection file, read in place,
  for a species count or a deep-linked tree. Unchanged for phones; a desktop
  used to read these from its loaded table, and now reads the file's
  `tree_id` column (Boston: 33 of its 75 MB, less than the 75 MB the old
  city load fetched before it could answer).
- Landmarks, the chat's scope, the dashboards, the species page.
- The colour of the season: the leaf-shade scheme takes its phase from the
  bookmark's hemisphere, for the whole map.

### Colour by trunk diameter

Was a per-tree table built from the trees held when the scheme was turned
on, so a tree paged in afterwards had no row in it and took the fallback
colour (a phone's window already had this gap). It is now a rule over the
row (`dbhColorSql`), like the tree-form colours.

## Tests

- `data/raw/tests/test_publish_world_blocks.py`: the cut, the dedup, the
  vocabulary, determinism, the guards, what the clean-up may delete, the job
  wiring, and the column contract with the worker.
- `src/src/workers/cityTables.test.ts`, `worldBlocks.test.ts`: the tables and
  the manifest helpers under native DuckDB.
- `src/src/workers/duckdbPipeline.wasm.test.ts` and
  `duckdbPipeline.window.wasm.test.ts`: the worker on DuckDB-WASM against a
  synthetic two-city world, as a desktop and as a phone.
- `src/e2e/seamless-world.spec.ts`: Boston to Burlington to San Francisco on
  a desktop, the URL it ends on reopening there, and Boston to Burlington on
  a phone, against the published blocks.

## Not done

- **Low zooms draw only what is held.** Below z9 a metro that was never
  visited is its city marker (the markers run to z9 for that reason). A
  first cut drew a dot per block from the manifest instead; it read as
  green smudges beside the real trees and was taken out. A sampled or aggregated
  level per coarse tile would let the real colours reach z6 everywhere.
- **A desktop's first view of a dense block is the whole block.** Bogotá's
  densest is 322k rows, about 8 MB, and nothing draws there until it has
  arrived. Splitting the blocks over some size into their z14 cells in the
  manifest would let the middle of the view draw first.
- **A phone's travel between cities is still the selector's flight.** Its
  zoom floor is 13, so it cannot zoom out and pan across a state; what is
  seamless on a phone is a border (Boston into its neighbours).
- **The per-city projections are still published.** They are the block
  job's input and the bookmark's city-wide reads. Building the blocks from
  the rollup and reading `trees_published` from the blocks of a city would
  retire them.
- **Load time has not been compared with the city load it replaces.** Run
  `pnpm bench:memory` and `pnpm bench:tiles` against a preview before and
  after; the numbers in `docs/MOBILE_LOAD_BUDGET.md` are the city load's.
