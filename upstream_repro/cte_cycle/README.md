# Merged tree_info refresh: "CTE dependency graph contains a cycle"

Investigation notes for the `urban-tree-data` cloud job. **This is not an open
upstream bug** — the cycle is fixed in pytrilogy 0.3.330 by
`[Bug]: Planner Fixes (#645)`. What remains is a *version* problem (the worker
runs something older) plus one genuinely open finding (planning cost).

| finding | status |
|---|---|
| `tree_info` refresh raises `CTE dependency graph contains a cycle` | fixed in 0.3.330; worker needs upgrading |
| Planning this shape is exponential in city count (0.5 s at 8 cities → 546 s at 14) | **open** — still present on 0.3.331 |

## What fails, and where

```
✗ https://storage.googleapis.com/trilogy_public_models/duckdb/trees/full_tree_info_v2.parquet
    Error: Failed to refresh datasource 'tree_info' (stale because: freshness
    'local.latest_update_through' behind: 2026-08-07 23:02:06.399140-04:00 <
    2026-08-17 07:31:40-04:00): ValueError: CTE dependency graph contains a cycle
```

Raised from `reorder_ctes`, `trilogy/core/optimization.py:255`. The downstream
`tree_enrichment_v2.parquet` then reports `Skipped due to failed dependency`, so
one cycle stalls both published artifacts. The 14 per-city parquets the browser
actually reads are unaffected — each city's own script plans and refreshes
normally — so the deployed map kept updating throughout.

## The cycle

Five of 37 CTEs cannot be ordered (full dump in `cycle.txt`):

```
badger [UnionCTE]                      # the 14-way union of the per-city sources
    outputs: city, data_source, diameter_at_breast_height, latitude,
             longitude, plant_date, species, submission_photo_url
    * cycles with boa
barracuda [CTE <- badger]  outputs: city
bear      [CTE <- badger]  outputs: data_source
boa       [CTE <- badger]  outputs: species
cheetah   [CTE <- barracuda]           # the final select
```

The offending edge is `badger -> boa -> badger`: three single-concept
projections (`city`, `data_source`, `species`) are carved *out of* the union,
and then the union is made to depend on one of them.

`city` and `species` are exactly the grain of the model's only aggregate.
`data/raw/usbos/boston_tree_info.preql` is the one city that derives its
diameter rather than reading it:

```preql
property tree_id._raw_dbh float;
property tree_id._cleaned_db <- case when _raw_dbh < 0 then null else _raw_dbh end;
auto processed_dbh <- coalesce(_cleaned_db, avg(_cleaned_db) by city, species);
merge processed_dbh into diameter_at_breast_height;
```

That merge makes `diameter_at_breast_height` — a column of the union —
resolvable through an aggregate grouped by two other columns of the same union,
and the planner satisfied the aggregate's grain by projecting it back out of the
union it feeds.

The fix landed in `[Bug]: Planner Fixes (#645)` (2026-08-16, released as
0.3.330), a broad V4 planner pass whose files are exactly the ones this cycle
runs through — `v4_helper/group_graph.py` (+318), `processing/join_resolution.py`
(+185), `v4_helper/group_rules.py`, `v4_helper/functional_dependency.py` — and
which added `tests/test_union_arm_subset_join_full_grain.py`, a union-arm/grain
regression test in the same family.

(0.3.331's `#646` "sibling aggregate projection" reads like an even closer
description of this shape, but the bisect rules it out: 0.3.330 already
succeeds.)

## Version bisect

Run against the real model with `repro_refresh.py`:

| pytrilogy | result |
|---|---|
| ≤ 0.3.315 | worked in production (last good build 2026-08-08 06:24 UTC) |
| 0.3.327 | **FAILED** in 972 s — cycle |
| 0.3.329 | **FAILED** in 644 s — cycle |
| 0.3.330 | **OK** in 640 s — `RefreshResult(stale_count=1, refreshed_count=1, …)` |
| 0.3.331 | **OK** in 618 s |

0.3.316 ("V4 As Engine Default") was released 2026-08-08 14:37 UTC, hours after
the last successful build, and the model has not changed since 2026-07-29 — so
the regression window opens at the V4 default switch and closes at 0.3.330.

**Action:** the refresh runs on trilogy-cloud workers, whose pytrilogy version is
set platform-side. `TRILOGY_CLI: 'pytrilogy>=0.3.327'` in
`.github/workflows/cloud-sync.yml` governs only the Actions step that calls
`cloud sync`, not the worker. Confirm the worker resolves ≥ 0.3.330 — and note
that the current floor, 0.3.327, is a version this bisect proves is broken for
this model.

## Still open: planning cost is exponential in partition count

`generate.py` builds an N-city copy of the model shape from local CSVs — no GCS,
no python datasources, no credentials — and plans a refresh of its merged
datasource:

```bash
cd upstream_repro/cte_cycle
uv run generate.py 3 5 8 10 11 12 14   # sweep sizes
uv run generate.py 8 --imputed 1       # give K cities Boston's imputed dbh
```

Measured on 0.3.327:

| cities | 3 | 5 | 8 | 10 | 11 | 12 | 14 |
|---|---|---|---|---|---|---|---|
| plan + render | 0.1 s | 0.2 s | 0.5 s | 8.8 s | 26.3 s | 70.0 s | 546 s |

Roughly ×2.8 per additional city past ~9, and **the fix does not improve it**:
on 0.3.330 and 0.3.331 the real 14-city model still costs ~150 s to plan and
~465 s to render one datasource. This is the finding that bounds the project —
at this growth rate a 15th or 16th city puts a single planning pass near the
job's 3600 s timeout, and the job refreshes far more than one datasource.

Worth reporting upstream on its own, with `generate.py` as the repro.

## The model shape

`tree_info.preql` unions 14 per-city materialized datasources and merges their
per-city source enums into one key:

```preql
key data_source string;
merge ussfo_source into data_source;   # ... 14 of these

auto latest_update_through <- greatest(
    ussfo_published_data_updated_through, ...);   # ... 14 of these

datasource tree_info (
    tree_id, city, data_source, species, tree_name,
    ?plant_date, ?diameter_at_breast_height, ?latitude, ?longitude,
    ?submission_photo_url, latest_update_through
)
grain (tree_id)
file f`https://.../full_tree_info_v{data_version}.parquet`:f`gcs://.../full_tree_info_v{data_version}.parquet`
freshness by latest_update_through;
```

Each `{code}_published_data_updated_through` is itself
`greatest(municipal_probe, community_probe)`, so freshness resolves through ~28
single-row root datasources bridged with `FULL JOIN … on 1=1`. Each city
datasource is `partial … complete where city = '{CODE}'`, and each city's raw
sources partition further on a per-city source enum.

## Reproducing

`repro_refresh.py` drives the same planner the CLI uses — `create_refresh_plan`
+ `execute_refresh_plan(dry_run=True)` — restricted to the merged target, which
is what directory mode does once each city script has refreshed its own asset:

```bash
cd data
python ../upstream_repro/cte_cycle/repro_refresh.py raw/tree_info.preql tree_info
```

It patches `reorder_ctes` to print the strongly connected component instead of
only the `ValueError`. Takes ~11-16 min on the real model.

The merged file as a *single* CLI target stops earlier on an unrelated (and
expected) resolution error, because it tries to rebuild the per-city assets too;
reproducing through the CLI needs directory mode:

```bash
cd data && trilogy refresh raw --dry-run     # ~13 min
```

Note `--dry-run` writes nothing but still prints `[1 datasource updated]` with
real durations in directory mode, unlike the `Would refresh` wording single-file
mode uses. Verified against GCS `Last-Modified`: nothing was written.

## What the synthetic model does *not* reproduce

| synthetic variant | result |
|---|---|
| 14 cities, plain | OK (546 s) |
| 8 cities + Boston-style imputation on one | OK (0.5 s) |
| 14 cities + Boston-style imputation on one | OK (282 s) |
| 8 cities + imputation + one shared community source for all cities | OK (0.5 s) |

So the ingredients are individually insufficient to close the cycle; untested
asymmetries include Boston's *four* municipal partitions with its nested
`greatest(greatest(4 municipal), community)`, the optional `tree_name` /
`submission_photo_url` columns, and the shared `tree_common.preql` property
layer. Minimisation stopped here because the real-model repro is reliable and
the bug turned out to be already fixed.

## Why this directory is at the repo root

`generate.py` writes an untracked `generated/` tree of `.preql` files, and
`trilogy cloud sync` bundles the working tree rather than git. Under `data/`
that would ship to production and become a directory-mode refresh target.
