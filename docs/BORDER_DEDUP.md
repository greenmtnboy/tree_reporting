# Border dedup between neighbouring inventories

Boston's metro model unions five municipal inventories. Where two cities meet
at a street, both inventory the trees on it, and the shared merge
(`data/raw/tree_dedup.preql`) never merges two municipal rows: each is its own
cluster. So those trees publish twice. This note is the measurement, the model
block that would fix it, why it is not merged yet, and what Trilogy would need
for it to be a few lines in the shared merge rather than a city-local block.

## How big it is

Every municipal pair in the metro, nearest cross-source neighbour, measured
against the published parquet plus Somerville's TreeKeeper rows (2026-10-05):

| Pair | < 1 m | 1-2 m | 2-3 m | same genus < 1 m | 1-2 m | 2-5 m |
|---|---|---|---|---|---|---|
| Cambridge / Somerville | 92 | 13 | 7 | 0.90 | 0.85 | 0.35 |
| Brookline / Boston | 5 | 3 | 4 | 0.80 | 0.67 | 0.80 |
| Boston / Arboretum, Boston / Somerville | 0 | 0 | 0 | | | |

Under 2 m these are the same tree recorded twice (the diameters match too); at
2-3 m genus agreement on the Cambridge border falls to zero, the next tree
along the kerb. So the rule is: within ~2 m, never across a contradicting
genus, one to one, and the earlier-published inventory keeps the tree so ids
people have linked to stay stable. About 100 trees, 0.06% of the metro.

## Where it belongs

In `usbos/boston_tree_info.preql`. It is the one model that sees every metro
source, so the rule is stated once, gated to the one city that has the
problem, and the ingests stay one portal each.

A first cut did it in Python: Somerville's ingest fetched Cambridge,
Brookline's fetched Boston, and both neighbours grew a public
`build_*_table()`. It worked exactly (true radius, genus check, greedy 1:1),
but it made each ingest aware of its neighbours and left the precedence order
implicit in which script imported which. Rejected for that.

The block goes after the position merges, with the target's prune extended to
`where tree_id = cluster_id and usbos_border_duplicate = false`:

```
auto usbos_border_rank <- case
    when usbos_source = 'CITY_OF_BOSTON' then 1
    when usbos_source = 'ARNOLD_ARBORETUM' then 2
    when usbos_source = 'CAMBRIDGE' then 3
    when usbos_source = 'BROOKLINE' then 4
    when usbos_source = 'SOMERVILLE' then 5
    else null
end;
auto usbos_raw_genus <- case
    when strpos(raw_species, ' ') > 0 then substring(raw_species, 1, strpos(raw_species, ' ') - 1)
    else raw_species
end;

# 2m at 42.36N: 2 / 111320 degrees of latitude, and that over cos(42.36)
# for longitude.  The indices stay under 3e6, inside the 1e7 multiplier.
def usbos_border_cell(dx, dy) ->
    cast(floor(raw_longitude / 0.00002431 + dx) as bigint) * 10000000
    + cast(floor(raw_latitude / 0.00001797 + dy) as bigint);
def usbos_first_rank(cell) -> min(usbos_border_rank) by cell, usbos_raw_genus;

auto usbos_border_cell_a <- @usbos_border_cell(0, 0);
auto usbos_border_cell_b <- @usbos_border_cell(0.5, 0);
auto usbos_border_cell_c <- @usbos_border_cell(0, 0.5);
auto usbos_border_cell_d <- @usbos_border_cell(0.5, 0.5);

auto usbos_own_border_rank <- min(usbos_border_rank) by row_key;
auto usbos_border_duplicate <- coalesce(
    usbos_own_border_rank > least(
        @usbos_first_rank(usbos_border_cell_a),
        @usbos_first_rank(usbos_border_cell_b),
        @usbos_first_rank(usbos_border_cell_c),
        @usbos_first_rank(usbos_border_cell_d)
    ),
    false
);
```

It approximates the rule with what the language has:

- **Four half-cell-staggered 2 m grids**, the shared merge's construction: a
  guaranteed match within 1 m, a possible one out to the 2.8 m diagonal.
- **The genus folded into the grouping key**, so an unnamed side cannot match
  (stricter than "not contradicting").
- **Rank instead of 1:1 assignment.** Two rows of one inventory never mark
  each other, but two Somerville rows beside one Cambridge tree are both
  dropped.
- **The absorbed row is pruned outside the merge**, so its id never reaches
  `merged_tree_ids`, and an OSM point whose municipal anchor was the absorbed
  row is pruned with it. The tree itself is still published, from the earlier
  inventory.

The row's own rank is a self-aggregate on `row_key` for the same reason the
shared merge uses one: comparing a cell aggregate with a row-level `case`
directly is the keyless-join bug (`upstream_repro/keyless_join_cell_aggregate`).

## Why it is not merged

It does not plan on pytrilogy 0.3.374, and AGENTS.md is explicit about not
working around a planner bug in a model. Three failures, reproduced in
`upstream_repro/adhoc_select_drops_columns/` and all present back to 0.3.348:

1. **An ad-hoc select silently drops a `case` over a merged-from concept**,
   and any aggregate in the same select with it.
   `select usbos_source, source_class, usbos_border_duplicate, count(tree_id)`
   returns one column and no error. Without the `merge` every shape survives,
   and plain functions over the same concept survive. This is the one that
   blocks verifying anything ad hoc: even the shared merge's `source_class` and
   `cluster_id` cannot be inspected on any city. Standalone 40-line repro.
2. **The prune and a second aggregate-derived gate together** fail to render
   ("Missing source reference to local.source_label"). Each alone plans.
3. **An aggregate grouped by a concept derived from a partial-union property**
   (`count(tree_id) by usbos_raw_genus`, the genus being a `substring` of
   `raw_species`) is a keyless join. Grouped by `raw_species` itself it plans.

Until those land the ~100 border trees publish twice. Somerville's trees are
otherwise new to the map; before this they were only in OSM.

## What would make this easy from the Trilogy side

In the order each would pay off:

1. **Fix the three planner bugs above.** With those, the block lands as is.
2. **A proximity join, or a neighbourhood aggregate.** Everything about the
   staggered grids (four copies of every aggregate, a cell table per city, a
   half-cell guarantee instead of a radius) exists because joins are equality
   only. Either `geo_distance(...) < 2` usable as a join condition between two
   rowsets, or an aggregate scoped to the rows within r of this one, replaces
   the grids here and in the shared merge and makes the radius exact.
   `POSITION_CORRECTION.md` wants the same primitive.
3. **Row-relative predicates inside a grouped aggregate.** "The
   lowest-ranked row near me whose genus does not contradict mine" needs the
   aggregate's filter to read the current row. Today that is spelled by
   folding the genus into the group key (which changes the semantics), plus
   the `row_key` self-aggregate the shared merge already needs to dodge the
   keyless join. A way to say `min(rank ? genus = <this row>.genus) by cell`
   removes both.
4. **`arg_min` / `arg_max`.** The shared merge encodes "the most specific
   species" as a sortable string key because there is no arg_max. A 1:1 match
   is "my nearest partner", an arg_min over the neighbourhood. Global greedy
   assignment is probably out of scope for the language; nearest partner by
   rank is close enough.
5. **A per-city extension point on the shared cluster id.** The right home
   for border dedup is the merge itself: an absorbed border row should join
   the survivor's cluster, so its id lands in `merged_tree_ids`, its label in
   `merged_sources`, and OSM rows anchored on it follow. Today `cluster_id` is
   one shared `auto`, and a city cannot add a tier to its `coalesce` without a
   merge conflict. A way for a city to contribute an anchor tier (a
   `merge`-able anchor list, or an overridable `def` with a default) would let
   the shared file own the mechanism and each city own only its precedence.
6. **Ordered enums.** The precedence is a five-arm `case` over the source
   enum. An ordinal on an enum (`ordinal(usbos_source)`) would make it the
   enum's declared order.

With 1 and 5 the block moves into `tree_dedup.preql` as one more anchor tier,
gated on a city declaring a precedence; with 2-4 it also becomes exact and
about a third of the length.
