# Landmarks

Every city has a landmark model, `raw/{code}/{slug}_landmarks.preql`, even
when it yields zero rows; the worker skips a missing parquet at runtime, but
a missing model fails the pipeline and leaves the chat with no geographic
context for the city.

## Schema

| Column | Type | Notes |
|---|---|---|
| `landmark_id` | string | prefixed with the city abbreviation |
| `city` | string | city code |
| `name` | string | |
| `geometry_raw` | string | WKT; a polygon where available, else `POINT(lon lat)` |
| `latitude`, `longitude` | float64 | centroid |

City-specific extras are declared in `landmark_common.preql` next to the
existing per-city blocks. Each city also adds its freshness property to
`landmark_common.preql` (and to the `greatest()` there) and its import to
`landmark_info.preql`.

## Sources, in order of preference

1. An official historic-landmark or heritage designation registry (city or
   national). Read live where the portal supports it.
2. A heritage dataset on the same portal as the trees.
3. An OpenStreetMap extract (`historic=*`). **Stage it**: an extract script
   publishes `staging/{code}_landmarks_staging.parquet` and the model reads
   only that object, with the probe emitting the object's publication time.
   Overpass is never called at refresh time.
4. A web directory geocoded with Nominatim (1 request/s, no key) into a
   committed `{slug}_landmarks.csv`, published as a staging parquet by an
   uncronned `landmarks-{code}` job (`landmark_staging/{code}_landmarks_staging.preql`,
   `operation = "run"`, `copy into`). Fire it after editing the CSV:
   `trilogy cloud jobs run urban-tree-landmarks-{code} --wait`. It must never
   gain a cron: an unconditional copy moves the staging object's
   `Last-Modified`, which is the city's landmark watermark.

Prefer pattern 4 for any curated CSV; it keeps upload credentials out of
the loop entirely.

## Files

```
raw/{code}/{slug}_landmarks.py          fetch + transform, Arrow IPC to stdout (or a CSV read directly)
raw/{code}/{slug}_landmarks_probe.py    freshness: the portal stamp, the staged object's time, or a hand-bumped constant
raw/{code}/{slug}_landmarks.preql       the model, versioned GCS URL, imports ..landmark_common only
```

A source with no timestamp at all gets a hand-bumped `LAST_REVIEWED`
constant in its probe; a row count is not a watermark.

## Building

Build a new city's landmark parquet from its own model, never from
`landmark_info.preql`: the shared entrypoint declares the published union as
a datasource, which can satisfy the concepts and leave a city not yet in the
union materialising zero rows with exit 0. Check the row count against the
script's output.

```bash
cd data && trilogy refresh raw/{code}/{slug}_landmarks.preql -f {slug}_landmark_info
```

`refresh-landmarks` rebuilds every city's landmark parquet plus the union
weekly; per-city freshness columns mean only cities whose source moved are
rebuilt. Landmarks stay one refresh lane because each city's fetch is its
own script; splitting them per city as the tree lane is split needs a shared
fetch first.
