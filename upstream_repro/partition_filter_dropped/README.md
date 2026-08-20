# A derived aggregate column silently drops a partitioned datasource's `complete where` filter

Minimized from `data/raw/ustem/tempe_tree_info.preql` and
`data/raw/usbos/boston_tree_info.preql`, the two cities that carry an
OpenStreetMap partition and therefore derive an `is_duplicate` flag from a
cross-partition aggregate.

## The bug

A `partial datasource` declaring `complete where city = 'X'` normally compiles
to a persist projection ending in `WHERE "....city" = 'X'`. Add one column
whose value comes from an aggregate evaluated across the stacked partitions,
and **the WHERE clause disappears entirely** — no error, no warning. Every row
the union produces is written, including rows belonging to other partitions.

Ingredients (removing any one restores the filter):

1. A key partitioned across several `root partial datasource`s, each claiming
   `complete where city = 'X' and src = 'Y'`.
2. One raw source that returns rows for other partitions too. This is the shape
   of a shared feed every partition reads and filters — in the real model,
   `community_tree_info.py` returns every city's approved submissions and each
   city's `complete where` is what keeps the others out.
3. An aggregate over a derived grouping key, spanning the stacked partitions,
   surfaced as a column on the managed datasource
   (`auto anchor_count <- count(entity_id ? src != 'SHARED') by cell;`).

## Reproducing

```bash
cd upstream_repro/partition_filter_dropped
uv run repro_refresh.py
```

Fully offline — every root datasource is an inline `query` block and the run is
a dry run, so nothing external is read and nothing is written.

The printed persist SQL for `out_a` ends:

```sql
FROM
    "juicy"
    RIGHT OUTER JOIN "abundant" on 1=1
GROUP BY 1, 2, 3, 4, 5, 6
```

with no `WHERE` anywhere in the statement, so `s1` (`city = 'CITY_B'`, arriving
via `shared_feed`) lands in `CITY_A`'s output.

Comment out the `is_duplicate: is_dupe` column in `repro.preql` and re-run —
the same query regains:

```sql
WHERE
    "cheerful"."city" = 'CITY_A'
```

That one-line ablation is the whole bug.

## Confirmed against the real model

Same ablation, on the unmodified Tempe model:

```bash
cd data
trilogy refresh raw/ustem/tempe_tree_info.preql -f tempe_tree_info --dry-run
```

| `tempe_tree_info` column list | generated SQL |
|---|---|
| as committed (`is_duplicate: ustem_is_duplicate,`) | no `WHERE` clause anywhere |
| that one line removed | `WHERE "yummy"."city" = 'USTEM'` |

Paris, which has no OSM partition and therefore no `is_duplicate`, is otherwise
structurally identical and does emit `WHERE "abundant"."city" = 'FRPAR'`.

Observed effect on published data: `ustem_tree_info_v2.parquet` contains three
rows with `city = 'USBOS'` / `data_source = 'COMMUNITY_USBOS'`. Six non-OSM
cities checked (`frpar`, `nlams`, `uslax`, `usnyc`, `aumel`, `usbtv`) contain
zero foreign community rows. Boston is equally unfiltered but cannot show it,
because the only community rows that currently exist are Boston's own.

## Things that do NOT fix it

- Making the column optional (`is_duplicate: ?ustem_is_duplicate`)
- Adding the partition key to the aggregate grain
  (`... by ustem_cell_a, city`)

Both still produce a filterless projection.

## Version

Observed on **pytrilogy 0.3.335** (current latest). Not bisected against older
releases: the Tempe OSM partition had never successfully materialized before
0.3.335 — its Parquet was stale since 2024-09-24 and Boston's refresh was
blocked outright by the persist-projection bug in `../dbh_resolution/` — so
this is the first build in which the leak could be observed at all.

Note the related hydration guard added by 0.3.335: a `complete where` on a
datasource that is neither `partial` nor has `~` columns is now a hard
`HydrationError` saying the clause "has no effect". That guard is exactly right,
and this bug is the same class of silent no-op one layer further down — the
clause is accepted, then dropped during planning.
