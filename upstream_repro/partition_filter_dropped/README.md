# `complete where` is injected as a filter inconsistently

**Read this first: the modelling in the original report was wrong.**
`complete where` is a *model-level assertion* — "this source contains the
complete set of rows for this partition". It is not a request to filter, and a
planner is not obliged to inject a predicate for it. Our per-city models
declared only `complete where city = 'X'` on a shared feed that returns every
city's rows, and then depended on a filter appearing. That was our bug, and it
is fixed in the model by adding an actual filter to the datasource:

```preql
root partial datasource ustem_community_tree_info (
    ...
)
grain (tree_id)
complete where city = 'USTEM' and ustem_source = 'COMMUNITY_USTEM'
file `../community_tree_info.py`
where city = 'USTEM';
```

`complete where` before the file clause asserts completeness; `where` after it
restricts the rows. Both are wanted here: "only Tempe trees, and this is all of
them."

## What is still worth an upstream look

The injection is *inconsistent*, and that inconsistency is what let the
modelling error hide for so long. With only `complete where` declared and no
`where`:

| model | generated persist SQL |
|---|---|
| Paris (no OSM partition) | `WHERE "abundant"."city" = 'FRPAR'` |
| Tempe / Boston (OSM partition) | no `WHERE` clause anywhere |

The two models are otherwise structurally identical. The single difference is
one column on the materialized datasource whose value comes from an aggregate
evaluated across the stacked partitions:

```preql
auto anchor_count <- count(entity_id ? src != 'SHARED') by cell;
auto is_dupe <- src = 'SHARED' and anchor_count > 0;
```

Adding that column removes the predicate; removing it brings it back. So a
`complete where` was silently load-bearing in twelve models and silently inert
in two, decided by whether an unrelated aggregate column was present.

## Reproducing

```bash
cd upstream_repro/partition_filter_dropped
uv run repro_refresh.py
```

Offline — every root datasource is an inline `query` block and the run is a dry
run, so nothing external is read and nothing is written.

`repro.preql` models the same shape: two partitions, a `shared_feed` that
claims `complete where city = 'CITY_A'` while returning a `CITY_B` row, and an
`is_duplicate` column derived from a cross-partition aggregate. As committed,
the persist SQL for `out_a` ends with `GROUP BY 1, 2, 3, 4, 5, 6` and no
`WHERE`. Comment out the `is_duplicate: is_dupe` column and re-run — the same
query regains `WHERE "cheerful"."city" = 'CITY_A'`.

The same one-line ablation on the unmodified Tempe model:

```bash
cd data
trilogy refresh raw/ustem/tempe_tree_info.preql -f tempe_tree_info --dry-run
```

| `tempe_tree_info` column list | generated SQL |
|---|---|
| with `is_duplicate: ustem_is_duplicate,` | no `WHERE` clause anywhere |
| that one line removed | `WHERE "yummy"."city" = 'USTEM'` |

Neither making the column optional (`?ustem_is_duplicate`) nor adding the
partition key to the aggregate grain (`by ustem_cell_a, city`) changes this.

## Observed effect before the model fix

`ustem_tree_info_v2.parquet` contained three rows with `city = 'USBOS'` /
`data_source = 'COMMUNITY_USBOS'`, because `community_tree_info.py` returns
every city's approved submissions. Six non-OSM cities checked (`frpar`,
`nlams`, `uslax`, `usnyc`, `aumel`, `usbtv`) contained zero foreign rows —
they were getting the injected predicate. Boston was equally unfiltered but
could not show it, since the only community rows that exist are its own.

## Version

pytrilogy **0.3.335**. Not bisected: the Tempe OSM partition had never
materialized before 0.3.335 (its Parquet was stale since 2024-09-24) and
Boston's refresh was blocked outright by `../dbh_resolution/`, so this is the
first build in which the difference could be observed at all.

Related: 0.3.335 added a hydration guard rejecting `complete where` on a
datasource that is neither `partial` nor has `~` columns, on the grounds that
the clause "has no effect" there. Worth deciding whether a `complete where`
that generates no predicate deserves similar treatment, or whether the docs
should simply be explicit that it never promises one.
