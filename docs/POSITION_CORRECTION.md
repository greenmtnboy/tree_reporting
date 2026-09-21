# Position correction

Moving a tree that sits inside a building or a road to the nearest open
ground, using Overture Maps, one city at a time. Burlington (USBTV) is the
pilot. This is the reference; the model is `data/raw/tree_position.preql`,
the builder is `data/raw/shared/overture.py`, and the tests are
`data/raw/tests/test_position_grid.py`.

## Why

Municipal inventories geocode from addresses, OpenStreetMap nodes are placed
by hand, and community submissions carry phone GPS. A share of every city's
points lands in the carriageway or under a roof, and the map draws them
there. The correction is a preprocessing step shared by every city that opts
in: a city model imports one file and declares one lookup datasource.

## The shape

Three pieces, and where each lives:

1. **The grid key, derived in the model.** `tree_position.preql` computes
   `snap_cell` from each raw row's own `raw_latitude` and `raw_longitude`:
   a fixed lat/lon cell (`snap_cell_lat_deg` 0.00002, `snap_cell_lon_deg`
   0.00003, about 2 m by 2.4 m at 45 degrees) packed as
   `lon_index * 1e8 + lat_index`, the same way `tree_dedup.preql` derives its
   dedup cell. Municipal, community, OSM and satellite rows all go through
   it, because they are all partitions of the same union. No ingest stamps
   it and no partition maps it; `shared/overture.py` keys the staging table
   from the same two constants and `test_position_grid.py` pins that the
   model's `const`s equal them.

2. **The lookup, a staging parquet.** `overture_staging/{code}_overture_
   staging.preql` runs `overture_snap_rows.py` and publishes
   `staging/{code}_overture_snap.parquet`: one row per cell whose centre is
   inside an Overture building footprint or road carriageway, with the
   centre of the nearest open cell, why the cell is blocked (`building`,
   `road`), and the distance in metres. Open ground has no row. The
   geometry is one DuckDB spatial query (`snap_sql`), and the watermark is
   the Overture release date.

3. **The correction, in Trilogy.** `tree_position.preql` joins each raw
   row's `snap_cell` to the lookup, coalesces the open point over the row's
   own, carries the result through the cluster merge with the same
   `@by_source` the other attributes use, and merges it into `latitude` and
   `longitude`. The survivor's own point is kept as `source_latitude` and
   `source_longitude`; `position_adjustment` and `position_shift_m` say
   what happened. The rollup carries only the corrected point.

Clustering still runs on the raw positions. Two sources that both put the
same tree under the same roof are still the same tree.

## Where the language stops us, and what would move it

The derivation above was blocked until pytrilogy 0.3.360. Before it, the
planner handled a lookup keyed on a derived concept against **one** tree
datasource -- computing the cell on the tree rows and hash-joining the
lookup on it, LEFT when the coordinates are nullable -- but not against the
shape every city model has, two or more `root partial` datasources with
`complete where` partitions on an enum. `select tree_id, cell` planned (the
union computed the cell) while `select tree_id, label` raised
`Could not resolve connections`, and asking for both gave
`Planner emitted a keyless join ... This is a planner bug`. Spelling the key
as `key cell` plus `merge derived into cell` was worse: it planned, rendered
`LEFT OUTER JOIN lookup ON 1=1` and gave every tree the lookup's row -- a
silent wrong answer rather than an error.

So the key was stamped as a physical column by `enforce_tree_schema` and
mapped by every partition. 0.3.360 plans the union case correctly, the
derivation moved into the model, and the ingest column went away; the floor
is pinned in `requirements.txt` and in `cloud-sync.yml`'s `TRILOGY_CLI`.

Note that the **hosted resolver's** pytrilogy pin floats independently of
both. The browser never plans this join -- the dashboards and the chat read
the published parquet, where `latitude` is already corrected -- so a resolver
behind 0.3.360 does not break the app. Anything that asks the resolver to
reach through the raw partitions to the lookup needs it upgraded first.

What the language would still need to own the whole correction, in the order
each would pay off:

1. **A non-equality join or a spatial predicate.** Trilogy joins are
   equality only, and its geography functions are `geo_point`,
   `geo_distance`, `geo_from_text`, `geo_centroid`, `geo_x`, `geo_y` and
   `geo_transform`. "Is this point inside that polygon" needs either
   `geo_contains` usable as a join condition or a way to declare a
   relationship between a point source and a polygon source. With that,
   the blocked-cell table stops being a raster and becomes a polygon table.
2. **Buffer, boundary and closest point** (`geo_buffer`, `geo_boundary`,
   `geo_closest_point`, an aggregate `geo_union`), so the carriageway
   polygon and the "nearest open point" could be expressed in-model rather
   than in a staging query.
3. **A dialect escape hatch**: a declared function whose body is SQL for a
   named dialect. Everything in `snap_sql` would fit in one.

`test_position_grid.py` keeps the model and `shared/overture.py` agreeing on
the cell.

## Why the staging is one DuckDB query

The engine the jobs run on is DuckDB with the spatial extension already
enabled (`enable_spatial` in `trilogy.toml`), so the geometry runs there and
Python only names the release, sets the S3 region and hands the Arrow
stream to Trilogy. The first cut rasterised with rasterio and used a scipy
distance transform; that was a second geometry stack for nothing.

The stages (`snap_sql`):

- `buildings`, `segments`: `read_parquet` over the Overture S3 release with
  a bounding-box predicate over `CITY_BOUNDS` padded by the search radius.
  The `bbox` struct makes it a row-group prune; the cost is a footer read
  per file.
- `road_polys`: centrelines buffered to half a carriageway in a transverse
  Mercator frame centred on the city. A recorded `width_rules` value wins
  when it is between 2 and 40 m; otherwise `ROAD_HALF_WIDTH_M` by class.
  Footways, cycleways, paths, steps, tracks and pedestrian ways are not
  roads here; tunnels are skipped; caps are flat.
- `pieces`: polygons clipped to 64-cell tiles so the next stage's bounding
  box enumeration stays close to the polygon's own area.
- `blocked`: every cell in a piece's box whose centre it contains, buildings
  winning over roads.
- `frontier`: the open cells adjacent to a blocked one. The nearest open
  cell to any blocked cell is always on the frontier.
- `nearest`: `arg_min` over hash-join candidates in passes of widening reach
  (`SEARCH_PASSES`: exact offsets within 1 cell, then 4, then 12-cell and
  40-cell buckets). Each pass accepts a candidate only when its distance is
  provably the minimum, which is `(reach + 1)` cells; the rest go on. The
  first attempt paired every blocked cell with every frontier cell in a
  36-cell window and spilled tens of gigabytes on a 10 km2 box, because a
  7 m road is three cells wide and two thirds of blocked cells touch open
  ground. Every join is on one packed bigint; a condition written as
  arithmetic across two relations made the planner nest-loop.

The cap `SNAP_MAX_METRES` is 60 m. Past that the point is a bad geocode,
not a tree by a wall, and it is left alone.

## The pilot: Burlington, Overture 2026-08-19.0

Built on a workstation from the cached S3 read, then joined to the
published `usbtv_tree_info_v2.parquet`.

| | |
|---|---|
| S3 read, buildings + segments | 342 s + 157 s |
| Footprints in the padded box | 43,791 buildings, 20,255 road segments |
| Geometry query | 131 s |
| Blocked cells | 4,401,301 (1,890,419 building, 2,510,882 road) |
| Staged parquet | 17.5 MB |
| Published trees | 12,816 |
| Trees in a blocked cell | 912 (7.1%): 886 in a road, 21 in a building, 5 more OSM |
| Shift | median 2.4 m, 90th percentile under 3 m, maximum 20 m |

Almost every move is a street tree a metre or two inside the buffered
carriageway edge, which is what the default half widths decide: 3.5 m for a
residential street. Those defaults are the calibration knob. Before rolling
another city on, check a sample of `road` moves against imagery; if the
defaults are wide for that city, the knob is `ROAD_HALF_WIDTH_M`. The four
farthest moves, 16 to 20 m, are one cluster at 44.4616, -73.2152 inside a
single footprint.

The `overture-usbtv` job is sized from this: 3600 s timeout, 2048 MB. The
city refresh joins 12,816 rows to 4.4M with a hash on the smaller side.

## Rolling a city on

1. Add the code to `OVERTURE_SNAP_CITIES` in `shared/overture.py`.
2. `overture_staging/{code}_overture_staging.preql`, copied from Burlington's
   with the code changed, and an `overture-{code}` `[[cloud.job]]` on the
   city's OSM day, an hour or two before the OSM extract.
3. In the city model: `import ..tree_position;`, drop the two position
   merges, add the `{code}_overture_snap` datasource and the
   `{code}_overture_update_time` probe, fold `{code}_overture_data_updated_through` into the published
   watermark (and declare it in `tree_common.preql`), and map
   `?source_latitude`, `?source_longitude`, `?position_adjustment`,
   `?position_shift_m` on the target. `{slug}_overture_probe.py` and
   `{slug}_overture_extract.py` beside the city's other scripts.
4. Stage the table by hand before the city's next refresh:
   `cd data/raw && uv run {code}/{slug}_overture_extract.py`. The model
   reads the object by URL; a refresh before it exists fails on the read.
   No OSM re-extract is needed: the cell is derived from coordinates the
   staged object already carries.
5. Force the city (`trilogy refresh raw/{code}/{slug}_tree_info.preql -f
   {slug}_tree_info`), then `urban-tree-full`.

`test_position_grid.py` names anything missing from steps 1 to 3, and
`trilogy refresh --dry-run` on the city model and the staging model must
each report one asset.

## Not done

- The map does not read `position_adjustment` yet. A marker for a moved
  tree, with the source point on hover, is the obvious next piece.
- Water is not a blocking layer. Lake Champlain would dominate Burlington's
  table if it were, and a tree 5 km into the lake is a bad geocode that
  should be dropped by the ingest, not moved to the shore.
- Only Burlington is wired. The other forty cities merge their own position
  and are unaffected.
