# Handoff: the urban-tree jobs failing on prod since 2026-09-27

Investigated 2026-09-28 from the `trilogy-data` inbox on prod. It held 12 open
`job_failed` items, all urban-tree jobs. There are two causes. One of them
accounts for 11 of the 12.

## TL;DR

1. **#103 (`crown_width_m`) shipped without the rebuild it needs** (11 jobs).
   The merge synced the new column into every model at 2026-09-27 02:51 UTC
   (workspace `urban-tree-data` v32), but the parquets those models read were
   still the old shape. Each city refresh now asks for a column its input
   doesn't have, and DuckDB refuses to bind the query. The fix is operational:
   re-extract the OSM staging, force every city, then run `urban-tree-full`.
   No code change is needed, but the runbook needs a step it's missing.
2. **Montreal's portal returns 403** (`urban-tree-city-camtl`, 2 runs). This is
   unrelated to #103.

## Cause 1: `crown_width_m` added without rebuilding the parquets

### Symptom

The inbox card shows this, and it is **misleading**:

```
Could not remove staged object trilogy_public_models/duckdb/trees/.trilogy-staging/cahfx_tree_info_v2.parquet.40739dc1.tmp: [Errno 2] ...
```

That line is pytrilogy failing to clean up a temp object that the failed COPY
never wrote. The real error comes a few lines further down in stderr:

```
Unexpected error in raw/cahfx/halifax_tree_info.preql: Failed to refresh datasource 'halifax_tree_info' ...
Binder Error: Table "cahfx_osm_tree_info" does not have a column named "crown_width_m"
```

`urban-tree-full` fails the same way on `city_published_trees`.

### Mechanism

#103 (7499bcc, merged 2026-09-26 19:50 PT) added `crown_width_m: ?raw_crown_width_m`
to every city's root datasources. That includes `{code}_osm_tree_info`, which
reads `staging/{code}_osm_staging.parquet`. It also added the column to
`city_published_trees` in `full_tree_publish.preql`, which reads every
`trees/{code}_tree_info_v2.parquet`. `cloud-sync.yml` pushed this to prod one
minute after the merge.

Nothing rewrote the parquets underneath:

- **OSM staging** is rebuilt only by the weekly `urban-tree-osm-{code}` jobs.
  Checked 2026-09-28: **27 of 41** staging parquets still lack the column:
  `arbue aumel cabur cacal caedm cahfx cakel cakgn calet calon camis canwe
  caott cator cavan deber dkcph fihel frpar gblon jptyo nlams twtpe usden
  uslax ustem uswas`. Every one whose OSM job last ran before 02:51 UTC on
  09-27 lacks the column, and every one re-extracted after has it. When a
  city's source goes stale, its refresh reads this parquet and fails. So far
  that has hit cahfx, caedm, cacal, cabur, canwe, calet, cator, usden and
  cavan. More of the 27 will fail this week as their sources tick over.
  `usbos` re-extracted at 2026-09-29 02:30 UTC and should pass its next tick.
- **Published city parquets**: **37 of 41** lack the column. Only `ussfo`,
  `cawpg`, `caajx` and `cobog` have rebuilt since. A new column does not make
  a parquet stale, and most cities report `up_to_date`, so **they will never
  rebuild on their own**. `urban-tree-full` (daily 06:00 UTC) will keep
  failing until all of them are forced.

EXTENDING.md § Data versioning already says "Refresh before you deploy either
way" and "A new column does not make a parquet stale. Force each city…". That
step was skipped. The runbook also never mentions that the OSM staging must be
re-extracted *before* the cities can be forced.

### Fix: run these steps in order

1. **Re-extract the 27 staging parquets listed above.** Trigger the jobs
   (`trilogy cloud --org trilogy-data jobs run urban-tree-osm-{code}`), but
   **stagger them**. Overpass allows two concurrent slots per IP and answers
   an over-budget request with HTTP 200 plus an error remark, so a collision
   silently publishes a city with zero OSM trees (see the comment on
   `osm-cahfx` in `data/trilogy.toml`). Run two at a time, or wait for each
   to finish. Verify with a DuckDB `DESCRIBE` of each
   `https://storage.googleapis.com/trilogy_public_models/duckdb/staging/{code}_osm_staging.parquet`.
2. **Force every city**, San Francisco first, per EXTENDING.md:
   `trilogy refresh raw/{code}/{slug}_tree_info.preql -f {slug}_tree_info`.
   That's 37 cities; `ussfo cawpg caajx cobog` already have the column.
   A city can only be forced after its staging from step 1 has landed.
3. **Run `urban-tree-full`**. Then reset or dismiss the inbox items. A city
   job's item also closes on its own at the next green run.

Check for a missing column like this (it takes about a minute over HTTP):

```python
import duckdb; con = duckdb.connect(); con.execute("INSTALL httpfs; LOAD httpfs")
url = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/{c}_tree_info_v2.parquet"
"crown_width_m" in [r[0] for r in con.execute(f"describe select * from '{url}'").fetchall()]
```

### To prevent the next one

- **Runbook**: add the staging step to EXTENDING.md § Data versioning. An
  additive column on a staged input means re-extract the staging, then force
  the cities, then the rollup, all **before merging**, because merging is
  deploying (`cloud-sync.yml`).
- **Cheap guard (optional)**: a CI test that `DESCRIBE`s every published and
  staging parquet and fails when a declared column is missing. It would have
  failed on #103 before the merge. Note that `parquetSchema.test.ts` treats a
  404 as a pass.
- **Upstream (pytrilogy, optional)**: have refresh treat a target that lacks a
  declared column as stale. That would make the city parquets heal themselves.
  It would not fix the staging reads, so the runbook step is still needed.

## Cause 2: Montreal's open-data portal returns 403

`urban-tree-city-camtl` failed on 2026-09-24 and 2026-09-28 with:

```
requests.exceptions.HTTPError: 403 Client Error: Forbidden for url:
https://donnees.montreal.ca/api/3/action/datastore_search?resource_id=64e28fe6-...&limit=32000&offset=64000...
```

The 09-24 run got the 403 on `resource_show` (the freshness probe). The 09-28
run paged successfully up to offset 64000 before it got one. That pattern
looks like rate limiting or bot filtering by the portal, not a moved dataset.
`shared/ingest.py::get_with_retry` doesn't retry 4xx other than 429, so one
403 fails the run. Camtl's staging parquet already has `crown_width_m`, so
this failure is independent of Cause 1.

Before changing code, check two things:

- Does a request from a GCE IP (the prod worker) consistently get 403 while
  a request from a workstation doesn't?
- Does the portal want a User-Agent? `shared/ingest.py` already sets one for
  Overpass, but the CKAN path may not.

Possible fixes, cheapest first:

1. Send a descriptive User-Agent on the CKAN requests.
2. Slow the paging down.
3. Treat 403 as retryable for this host only.
4. Switch to the resource's bulk download URL (`resource_download_url`),
   which is one request instead of a series of pages.

## Earlier failures on the same jobs (already cleared)

`urban-tree-city-cavan` also failed on 09-16, 09-20 and 09-23 with
`400 Bad Request` from Vancouver's parquet export. The 09-27 run got past the
source and failed on Cause 1, so that problem appears to have cleared.
Confirm after the step-2 force.

## Platform-side note (trilogy-cloud / pytrilogy)

The inbox card shows the head of stderr (trilogy-cloud #150). Here the head
was pytrilogy's `Could not remove staged object … [Errno 2]` cleanup warning,
which hid the binder error on all 11 cards. Two small fixes would help:

- pytrilogy should not warn when removing a staged temp that was never written
  (ENOENT).
- The inbox excerpt could prefer the `Unexpected error in …` line when there
  is one.

Neither is needed to fix these jobs.
