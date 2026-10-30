# `order by` on Trilogy datasource targets

A request for pytrilogy: let a datasource that Trilogy writes declare the
order its rows are written in and the row-group size, so a reader filtering
on a sort-correlated column prunes row groups using the parquet footer. This
is the `order by` part of `docs/TRILOGY_BUCKETED_OUTPUT_SPEC.md`, split out
because it is the part worth shipping first and it does not depend on the
rest (per-file partitioning, manifests).

Status: requested 2026-10-27; shipped in pytrilogy #778 (0.3.414) as
`order by` plus `storage (row_group_size = n)`, with `hilbert_index` as a
`std.geography` def. The syntax there is what this repo uses; the proposal
below is kept as the request was made.

0.3.414 cannot build this repo's projection (2026-10-28). The sort is planned
as a select over the target pinned to the build's rows, and two things break
it. That select grain-pins the stored derived columns (`display_name`,
`tree_form`) to its keys and tries to re-derive them from inputs the pinned
scope does not bind. And a CTE usage walk renders `array_reduce` with the
generic dialect, which has no form for it. Both are fixed together upstream
in pytrilogy #783 (released in 0.3.417, on which every city's sort plans;
0.3.417 and 0.3.418 break the tree_info builds instead, pytrilogy #786, fixed
in 0.3.419, which this repo floors at). A local Boston build
with the fix gave 166 row groups of ~10k rows (the published file has 15)
and the same 1,695,102 rows. A 4.5 km window at Blue Hills overlaps 5 of
the groups. The file is 71.3 MB against the published 73.0 MB; the ~47 MB
estimate below did not hold.

## Why

The mobile map crashes Safari tabs on dense cities. The plan
(`docs/MOBILE_LOAD_BUDGET.md`) is a fixed zoom-out limit on phones and a
worker that loads only a lat/long window of the city (about 300k trees), in
chunks, as the user pans. That only works if a window query reads just the
window's bytes. Today it reads the whole file:

Boston's published browser projection (`usbos_tree_browser_v2.parquet`,
1.57M rows, 15 columns), measured from the parquet footer statistics, i.e.
the row groups an HTTP range reader would fetch:

| Layout | File | Dense 4.5 km window (112k trees) | Downtown 4.5 km (40k trees) |
|---|---:|---:|---:|
| As published (13 row groups, no order) | 67.7 MB | 13 groups, 67.7 MB | 13 groups, 67.7 MB |
| `order by latitude, longitude`, 10,240-row groups | 44.3 MB | 25 groups, 7.0 MB | 27 groups, 7.8 MB |
| Hilbert order with Boston's own bounds, 10,240-row groups | 46.9 MB | 17 groups, 5.2 MB | 10 groups, 2.8 MB |
| Hilbert order with whole-world bounds, 10,240-row groups | 47.2 MB | 17 groups, 5.1 MB | 10 groups, 2.9 MB |
| Hilbert order with Boston's own bounds, 20,480-row groups | 42.9 MB | 10 groups, 5.6 MB | 8 groups, 4.2 MB |

What this shows:

- Ordering the rows is enough. There is no need for files per tile or a
  manifest for this use: DuckDB, native and WASM, already skips row groups
  using the footer statistics.
- A sort on columns we already have (`latitude, longitude`) gets roughly a
  10x saving. A Hilbert key gets 13-23x, and also shrinks the file by about
  a third because neighbouring rows compress better together.
- A Hilbert key computed over the whole world's bounds prunes as well as one
  fitted to the city. So the key function needs no bounds argument and does
  not need to read the data first.
- `row_group_size` matters as much as the order. DuckDB's default row
  groups (about 122k rows here) are too coarse to prune a 4 km window.

The same clause also helps targets that aren't spatial: `tree_rankings`
(312 MB) is ordered by city today only because the script that writes it
happens to emit rows that way, and nothing declares or keeps that order.

## The declaration

```trilogy
template datasource city_tree_browser (
    tree_id,
    ...
    latitude: ?latitude,
    longitude: ?longitude,
)
grain (tree_id)
freshness by prediction_inputs_published_through
order by hilbert_index(latitude, longitude)
row group size 10240
assert @kept(tree_id, 0.75);
```

- **`order by <expr> [asc|desc], ...`**: the rows of each written file
  appear in this order. Each term is any expression over the datasource's
  concepts; it does not have to be a column the datasource binds. That
  matters: `hilbert_index(...)` is not stored. Reuse the existing select
  `order_list` grammar so the expression and direction rules are the same.
- **`row group size <int>`**: written as the parquet row-group size. It is
  only valid on a parquet target. On a CSV or JSON target it is a parse or
  validation error, not something silently ignored. (Exact spelling is
  upstream's call; `row_group_size` matching DuckDB's option name is fine
  too.)
- Both clauses are inherited through `extends` like `grain` and
  `incremental_by` (`_single_setting` in `trilogy/core/models/datasource.py`).
  A partial that extends the template above inherits the order, and can
  override it.
- Both are write-side only. They do not change what the datasource answers,
  how the planner chooses sources, or the asset's freshness and state. A
  model that adds `order by` to an existing target should not make the job
  look stale except on the first build after the clause is added. Whether
  the order counts toward the asset's schema or definition hash is upstream's
  call, but the change has to be able to roll out: this repo has already hit
  a column addition that needed a manual job trigger before it rebuilt
  (pytrilogy 0.3.383).

### Name clash to avoid

`datasource_partition_clause: "partition" "by" column_list` already exists
(incremental and append partitions, `docs/partition_ddl_plan.md` in
pytrilogy). The bucketed-output spec's `partition by <expr> as <name>` would
clash with it. That is a problem for later, but this request should not take
the keywords `partition` or `bucket` for anything.

## The function

`hilbert_index(lat, lon)`: a Hilbert-curve key over the whole world's bounds
(lon -180..180, lat -90..90), returning an integer, with no bounds argument.
The measurement above used DuckDB spatial's

```sql
ST_Hilbert(longitude, latitude, {'min_x': -180, 'min_y': -90, 'max_x': 180, 'max_y': 90}::BOX_2D)
```

That is 16 bits per axis, so cells about 600 m wide at Boston's latitude,
which is fine for 10k-row groups. If loading `spatial` in the build is a
problem, a pure-SQL Hilbert or Morton (Z-order) key is acceptable; a Morton
key would prune somewhat worse than Hilbert but much better than
`latitude, longitude`. Trilogy has no `ln`/`cos`/`exp` (`**` only), so
whichever it is should be a builtin with a dialect rendering, not something
a model composes by hand.

If the function is a second PR, `order by latitude, longitude` with
`row group size` is still a useful first ship (row 2 of the table).

## Where it goes in pytrilogy

These are pointers, not a design; the implementing agent should read the
surrounding code.

- Grammar: `trilogy/parsing/trilogy.lark`. The `datasource` rule (line ~104)
  takes a fixed sequence of optional clauses; add `_ds_order_opt` and
  `_ds_row_group_opt` near `_ds_partition_opt`. `order_by` / `order_list`
  (line ~397) already exist for `select`.
- Model: `trilogy/core/models/datasource.py`. Add the fields next to
  `partition_by` (lines ~336 and ~506), carry them through `with_namespace`
  (~375, ~674), the copy at ~705, and template inheritance (~455).
- Write path: `trilogy/executor.py`, the `ProcessedCopyStatement` handler
  (~1170-1265). There are three COPY sites, and each must keep the order and
  the row-group size:
  1. `COPY ({sql}) TO '{staged}' ({options})` (~1212): the normal path.
     Either append `ORDER BY` to the generated select, or wrap it in
     `SELECT * FROM (...) ORDER BY ...`. Add `ROW_GROUP_SIZE n` to the
     options. `_copy_format_options` (~223) is the natural home for the
     option, given the target's settings.
  2. `_copy_through_local_stage` (~1240): writes locally, audits, then
     write-through `COPY (SELECT * FROM {reader}) TO '{target}'` (~1262).
     The re-read has to keep the order: either `ORDER BY` again, or rely
     on DuckDB's insertion order (it is preserved by default and pytrilogy
     never sets `preserve_insertion_order`, but it should not depend on that
     silently). It also needs the row-group option again, or the published
     file won't have the audited row groups.
  3. `append_to` (`_with_existing_rows`, ~1178): an incremental append
     rewrites the file from old rows plus new rows. The sort has to apply to
     the union, not just the new rows.
- The order expression may reference concepts that are not output columns
  (`hilbert_index(latitude, longitude)`). The query that builds the target
  has to compute them for the sort and drop them from the output. Make sure
  the generated SQL does not emit the key as an extra column, which would
  then fail the schema check against the declared columns.
- Other dialects: BigQuery and others have no row groups. `order by` there
  can be a no-op with a warning, or an error. The DuckDB parquet path is
  the only one this repo needs.

## Tests that would satisfy this repo

- A parse test for both clauses, including inheritance through `extends`
  and an override on the child.
- A DuckDB build test: write a target of about 50k synthetic points with
  `order by hilbert_index(...)` and `row group size 1024`, then assert from
  `parquet_metadata()` that there are about 50 row groups, and that a small
  bounding box overlaps a small fraction of them by their lat/long
  statistics. The same check through the local-stage write-through path,
  and through an append.
- The output schema has no extra column for the sort key.
- `row group size` on a CSV target is refused.

## What this repo does once it ships

1. Add `import std.geography;`, `order by @hilbert_index(latitude,
   longitude)` and `storage (row_group_size = 10240)` to the
   `city_tree_browser` template in
   `data/raw/tree_browser_city.preql`. That rewrites the existing
   `{code}_tree_browser_v2` files, with no new asset and no new job, and
   removes the "Rows are not ordered" note there. Bump the resolver pin
   (trilogy-studio-core/pyserver) and the cloud worker. Cloud jobs build a
   city's projection once the worker is on the new pytrilogy; until then
   build them locally (see `docs/MOBILE_LOAD_BUDGET.md`).
2. Probably the same on `published_tree_info`, so the resolver's point
   lookups prune too. That is a separate decision, not part of this request.
3. The phone's windowed load (`src/workers/cityWindow.ts`, stage 2 in
   `docs/MOBILE_LOAD_BUDGET.md`) already filters each load on a lat/long
   box, so it prunes as soon as the files are sorted. Re-measure with
   `MAP_BENCH_PANS` and raise the normal tier's 300k row budget in
   `src/composables/useMapWindow.ts` toward 500k.

## Out of scope

Partitioning a target into one file per key, manifests, per-partition
guards, `bounds`/`dictionary` column markers: all in
`docs/TRILOGY_BUCKETED_OUTPUT_SPEC.md`, none needed for this.
