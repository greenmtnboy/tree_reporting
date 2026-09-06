# Handoff: re-check every city's dedup cell against the marginal table

Written 2026-09-06, alongside the Canadian Socrata cities. Nothing here is
urgent and nothing here is broken; this is a measurement that says the cells
wired before it existed are all a little too big, and a record of what it would
cost to fix them.

## What changed

`DEDUP_CELL_METRES` sizes the grid the shared cluster merge matches rows on.
Until now the size came from one reading in `osm_dedup_validation.py`: walk the
distance bands outward, stop at the first that is not *clearly*
duplicate-dominated (>=60% mutual-NN), and take that as the guarantee. A 5 m
guarantee is a 10 m cell, which is what fifteen of eighteen cities carry.

**That test is a proxy, and it flatters the bigger cell.** A cell names a
*guarantee* — two points within half a cell always share a cell in at least one
of the four staggered grids — but the scheme goes on matching out to the cell
diagonal, ~1.41x further. So a size chosen from 5 m-wide bands can still be
pulling in a ring the bands never said to flag, and the band table cannot see
it.

`osm_dedup_validation.py` now also prints the thing we actually care about: of
the rows a given cell flags, how many are mutual nearest neighbours of the
inventory tree they matched (real duplicates) and how many are not (**real
trees the map would hide**), with the marginal trade between consecutive sizes.

Read the `marginal` column. The asymmetry that governs the whole calibration —
a missed duplicate double-renders a visible, toggleable dot; a false flag hides
a real tree — says a step up in size is worth taking only while it removes more
duplicates than it hides trees.

## What it says, on six cities

Three new, three published. Every one of them turns negative before 10 m.

| city | cell today | 4->6 | 6->8 | 8->10 | marginal says |
|---|---:|---:|---:|---:|---:|
| Calgary `CACAL` | **4** (new) | 0.47 | 0.22 | 0.14 | 4 m |
| Edmonton `CAEDM` | **6** (new) | 2.36 | 0.96 | 0.52 | 6 m |
| Winnipeg `CAWPG` | **6** (new) | 3.44 | 0.70 | 0.33 | 6 m |
| Tempe `USTEM` | 10 | 3.42 | 0.79 | 0.60 | 6 m |
| San Francisco `USSFO` | 10 | 4.45 | 1.42 | 0.65 | 8 m |
| Boston `USBOS` | 10 | 4.49 | 1.07 | 0.48 | 6-8 m |

(Calgary's row starts lower because its 2->4 step is the 2.12 that carries it;
its inventory is planted at a median 5.1 m and a quarter of its trees are
within 3.2 m of another.)

Two things are worth saying plainly:

- **The consistency is the finding.** Six cities on three continents, and
  `4->6` is a clear win every time (2.4-4.5) while `8->10` is a clear loss every
  time (0.14-0.65). This is not a per-city quirk; 10 m is simply past the turn
  for an urban tree inventory.
- **Tempe is the reference calibration** — the city every other cell was
  sanity-checked against — and it is over-sized too. That is the strongest
  reason to believe the band heuristic, not the individual cities, is what was
  off.

## What it would actually buy

Modest, and honest about it. False flags at the current cell versus the
marginal-table cell:

| city | false flags @ 10 m | @ its marginal size | real trees un-hidden |
|---|---:|---:|---:|
| Boston | 585 | 258 (6 m) | **327** |
| San Francisco | 248 | 186 (8 m) | **62** |
| Tempe | 61 | 32 (6 m) | **29** |

So on the three measured, a few hundred trees. Extrapolated across fifteen
cities still on 10 m — and the two on 20 m, London and New York, where the
error is larger by construction — plausibly a few thousand. Against ~7.2M
published trees that is a rounding error in aggregate and a real fix locally:
every one of those is a tree that exists, is mapped, and does not render.

## Why it was not done in that PR

**Re-cutting a published city's cell changes which of its rows survive the
prune.** The target carries `where tree_id = cluster_id`, so a different cell
means a different cluster assignment, which means a different row set in the
parquet. It is a full rebuild of each city, plus `urban-tree-full`, and every
`tree_id` that was absorbed under the old cell and survives under the new one
reappears (which is the point) while the reverse also happens. Doing that to
eighteen cities is its own change with its own verification, not a footnote to
adding three.

## How to do it

The published cities are the *easy* case: unlike a city being added, both
inputs already exist on GCS, so no local parquets and no `--inventory-parquet`.

```bash
cd data/raw
for c in USSFO USNYC USBOS FRPAR USBTV CAVAN DEBER NLAMS GBLON AUMEL \
         ARBUE USLAX USWAS USTEM GRATH GRMLO GRSAN USDEN; do
  echo "### $c"
  uv run osm_dedup_validation.py --city "$c" | sed -n '/what each cell size costs/,$p'
done
```

Then, per city:

1. Read the `marginal` column and take the last size comfortably above 1.0.
   Where it sits right at 1.0 (Boston's 6->8 is 1.07, SF's is 1.42) the
   asymmetry says round **down**, not up — a tie is not a reason to hide a tree.
2. Edit `DEDUP_CELL_METRES` in `_ingest_shared.py`, replacing the band-based
   comment with the marginal numbers, as the three Canadian entries do.
3. `uv run dedup_cells.py --write` — the model carries the cell as an inline
   `VALUES` block and `test_dedup_cells.py` fails if it goes stale.
4. Widen `test_cell_is_a_calibrated_size`'s allowed set if a size lands outside
   `(4, 6, 10, 20)`.
5. Rebuild each city and then the rollup. This is the expensive half:

```bash
cd data
trilogy refresh raw/{code}/{city}_tree_info.preql -f {city}_tree_info
# ... every city, then:
trilogy cloud jobs run urban-tree-full --wait
```

6. Verify each rebuild published only survivors — a planner that drops the
   prune gate is silent:

```sql
SELECT count(*) FROM read_parquet('.../{code}_tree_info_v2.parquet?cb=1')
WHERE tree_id <> cluster_id;   -- must be 0
```

## Two cautions

**London and New York were deliberately put on 20 m and that decision was
argued.** Both measured a coin flip in the 5-10 m band (51.5% over n=18,076 and
53.3% over n=5,059), and the write-up in `EXTENDING.md` explains that a bare
50% cut sent them there when the 60% threshold would not have. Those two are
the *most* likely to move and the most worth reading carefully — a 20 m cell
reaches 28 m, which in London spans several trees.

**Do not batch the edit ahead of the rebuilds.** A city whose cell has changed
in `_ingest_shared.py` but whose parquet has not been rebuilt is not broken —
the published rows are simply the old clustering — but `dashboard-pushdown` and
the map will disagree with the model until it is. Change and rebuild one city
at a time, or accept a window where the two are out of step and say so.

## And for the next new city

`new_city.py` still seeds `DEDUP_CELL_METRES` at 10 with a `NOT YET CALIBRATED`
comment, and `osm_dedup_validation.py` still has to be run before shipping —
that has not changed and the test still enforces it. But given six cities have
now turned negative before 10 m, **6 is the better starting guess than 10**, and
a new city that measures 10 is the surprising result rather than the default.
