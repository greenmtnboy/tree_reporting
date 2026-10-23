# Bucketed output for Trilogy targets

A proposal for upstream Trilogy (pytrilogy, trilogy-cloud): let a target be
written as a set of files keyed by an expression, with a manifest, in the
model language rather than in a Python side script. This is what the map's
second stage needs (`docs/MOBILE_LOAD_BUDGET.md`), and it is a general shape:
"bucket this output by a scheme" comes up for anything a browser reads by
viewport, period or tenant.

Status: draft, 2026-10-23. Nothing here is implemented.

## Why the model, not a script

Every published artifact in this repo is a Trilogy target: the city
parquets, the crown slices, the rankings, the landmark union. A per-cell
layout of a city written by a Python job would be the one artifact the
planner cannot see, so no datasource could read it, no `assert` could guard
it, and the next person wanting the same thing for another dataset would
copy the script. The ask is the same as for position correction and border
dedup: a declaration the planner understands, so reads prune and writes are
guarded, and the per-dataset work is one clause.

## The declaration

```trilogy
# A city's trees, one file per z12 web-mercator tile, rows ordered along a
# Hilbert curve so a tile's rows are contiguous and a viewport reads one
# row group.
datasource usnyc_tree_cells extends published_tree_info ()
grain (tree_id)
partition by tile_key(latitude, longitude, 12) as cell
order by hilbert_index(latitude, longitude, 16)
file f`https://storage.googleapis.com/trilogy_public_models/duckdb/trees/usnyc/cells/{cell}.parquet`
  :f`gcs://trilogy_public_models/duckdb/trees/usnyc/cells/{cell}.parquet`
manifest f`https://storage.googleapis.com/trilogy_public_models/duckdb/trees/usnyc/cells/manifest.json`
  :f`gcs://trilogy_public_models/duckdb/trees/usnyc/cells/manifest.json`;
```

Three new clauses on a datasource target, each independent:

- **`partition by <expr> as <name>`**: the build writes one file per
  distinct value of the expression. The file template must reference
  `{name}`. The value is also stored as a column in each file (hive-style
  path inference is not assumed; object stores serve bytes, not listings).
  A null partition value is an error at build time, caught by the same
  `assert` machinery as a lost row.
- **`order by <expr>`**: rows are written sorted. Within a partitioned
  target the sort is per file; on a plain target it sorts the one file. Row
  groups are cut on the sort order, so a reader filtering on the expression,
  or on anything correlated with it, prunes from the footer statistics.
  `row_group_size n` is an optional companion; the default is the engine's.
- **`manifest <file>`**: the build writes, after every partition file, a
  document listing the files that constitute the target. The manifest is the
  commit point: a reader that starts from it never sees a half-written
  target, and a stale cell file left behind by a build that produced fewer
  partitions is unreachable rather than wrong.

`order by` alone is worth shipping first. It makes the existing single-file
targets range-readable (`tree_rankings` is 312 MB ordered by city only
because the script happens to emit it that way; a declaration would hold
that) and it is the smaller change.

## The manifest

JSON, versioned, small enough for a phone to fetch before anything else:

```json
{
  "format": 1,
  "target": "usnyc_tree_cells",
  "published_through": "2026-10-22T10:25:12Z",
  "partition": { "column": "cell", "expression": "tile_key(latitude, longitude, 12)" },
  "order": "hilbert_index(latitude, longitude, 16)",
  "columns": [{ "name": "tree_id", "type": "VARCHAR" }, { "name": "species", "type": "VARCHAR" }],
  "files": [
    { "cell": "120320030", "path": "120320030.parquet", "rows": 18231, "bytes": 612040,
      "bounds": { "latitude": [40.70, 40.78], "longitude": [-74.02, -73.93] }, "etag": "..." }
  ],
  "dictionaries": { "path": "dictionary.parquet", "columns": ["species", "tree_name", "cultivar", "data_source"] }
}
```

- `files[].bounds` carries min/max of every column the model marks
  `property ... bounds;` (here latitude and longitude), so a reader can pick
  files by a bounding box without knowing the partition scheme.
- `dictionaries` is optional: for columns declared `dictionary` on the
  datasource, the build also writes one small parquet of each column's
  distinct values across all partitions. A reader that stores those columns
  as enumerations (the browser does) needs the whole vocabulary before the
  first partition arrives, and must not discover a new value in the fifth
  file it loads.
- Readers compare `published_through` to decide whether cached files are
  current; `etag` per file lets a loader refresh one cell.

## Reading a partitioned datasource

A datasource with `partition by` is addressable like any other. The planner
resolves it to a file list:

- With a predicate on the partition column, or on a `bounds` column, the
  planner reads the manifest at plan time and emits
  `read_parquet([<matching files>])`. On an engine that can glob the object
  store (native DuckDB over `gcs://`) it may emit the glob with the
  predicate pushed down instead; the result is the same.
- Without such a predicate it reads every file in the manifest. That is the
  city-wide question, and it is what the single-file city parquet already
  answers; a model may declare both and the planner prefers the unpartitioned
  file for an unpruned read.

For the resolver this means a dashboard or chat query compiles to the same
SQL shape as today. For the browser it means the worker reads the manifest
itself and streams cells with plain `read_parquet(url)` per file, which is
what DuckDB-wasm does well: it downloads a whole object on first read, so
one object per cell is the layout that makes that cost proportional to the
viewport.

## Guards

`assert @kept(tree_id, 0.75)` and the other ratio guards apply to the target
as a whole, before any file is published. Two additions:

- `assert partitions <= n`: a scheme that explodes (a too-fine key, or a
  null-heavy one) refuses to publish rather than writing ten thousand
  objects.
- `assert @kept_per_partition(tree_id, r)`: a cell that lost a quarter of
  its rows is refused even when the city total passed.

## Functions the language needs

- `tile_key(lat, lon, z)`: the web-mercator tile of a point at zoom `z`,
  as a quadkey string. Pure arithmetic; `tree_position.preql` already derives
  a metric grid key the same way, so this is a named version of an existing
  pattern, not an extension dependency.
- `hilbert_index(lat, lon, bits)`: a space-filling-curve key for `order by`.
  Also arithmetic.
- `h3_cell(lat, lon, res)`: the hexagonal alternative, if the DuckDB `h3`
  extension is acceptable in the worker image. Not required for the first
  cut; quadkeys align with the tile grid the renderer already indexes on.

## Build and schedule

A partitioned target is one job with one asset, so `test_cloud_jobs.py`'s
"exactly one asset per city model" rule holds if the cells are declared in
the city model, or the cells get their own `[[cloud.job]]` ordered after
`city-{code}` the way the crown slices follow the predictions job. The
second is the right default: the city parquet stays the thing every other
model reads, and the cells are a projection of it, rebuilt when it moves and
never a second source in the planner's scope (the frontend bundle rule in
`AGENTS.md`).

Writes are all-or-nothing from the reader's view: partition files first,
manifest last, under the same freshness watermark the target already
carries. Incremental per-partition rebuilds are explicitly out of scope for
the first version; a city rebuild rewrites every cell.

## What this repo would do with it

1. `order by` on `tree_rankings.preql` and on `published_tree_info`, with
   `row_group_size`, so the resolver and the browser's point lookups prune.
2. A `{code}_tree_cells` target per city, `partition by tile_key(..., 12)`,
   `dictionary` on species, tree_name, cultivar and data_source, `bounds` on
   latitude and longitude.
3. The worker's loader reads the manifest and dictionary first, builds the
   enumerations, then loads cells by viewport into the same `trees_fast`
   shape it uses today (`src/src/workers/cityTables.ts`). A query the
   resolver reports as not row-local (an aggregate, a window, a limit) loads
   every cell first.

Open questions for upstream: whether `partition by` belongs on the
datasource or on a separate `target` statement; how a manifest is versioned
across `data_version` bumps; whether the planner should read manifests at
plan time (network at compile) or require the model to declare the
partition list statically.
