# Named trees and crown position: scope

Status: scoping (branch `named-trees-crown-position`), nothing built.
Two additions to the tree row, sharing one rollout:

1. **Named trees.** A tree can have its own name and a significance blurb.
   Notable trees get an extra glow on the map.
2. **Crown position.** A crown point separate from `latitude`/`longitude`,
   which become explicitly the **base** (trunk). For now every crown equals
   its base; the column is there for sources that later separate them
   (leaning, skewed, overhanging trees).

**Performance is the overriding constraint.** Nothing here may make a load,
a pan, a colour change or a phone window heavier. Every map-side change
below either adds no bytes per tree or swaps one out for a cheaper one, and
the frontend PR is held to before/after runs of `pnpm bench:memory`,
`bench:tiles`, `bench:dashboard` and `test:wasm`.

## Decisions (2026-11-15)

| | Decision |
|---|---|
| Glow | A tree glows when it has a name **or** a significance blurb (`is_notable`). |
| Crown sources | No crown source in this change. Satellite and prediction points stay as they are until there is a dedicated source and more QA. The crown columns ship inert. |
| On the map | One **bit** per tree in the map load. Name and blurb are fetched only when a notable tree is clicked. No separate index. |
| Authoring | The public tree-report form can **propose** a name and blurb; the reviewer approves, as for every other report. |

---

## 1. Named trees

### What `tree_name` holds today, measured

`tree_name` is in `TREE_COLUMN_TYPES`, bound as `raw_tree_name` in the
`tree_partition` template, merged `@by_source`, published, carried through
the projection, the world blocks and the vocabulary ENUM. Its documented
meaning is "original name field from source data, which may be unique to
certain trees", but a scan of every published projection (2026-11-15) found
**no ingest that fills it from a proper-name field**:

- ~60 municipal ingests write the source's **common-name** column through
  `normalize_tree_name` (Calgary `ASH, GREEN` → `Green ash`, Quebec's
  `NOM_FRANCAIS`, Amsterdam's Dutch name, Athens' Greek name, ...).
- Boston, Buenos Aires, Paris and `community_tree_info.py` fall back to the
  **species** when there is no common name.
- OSM (`shared/osm.py`) does not read the `name` tag at all.
- The values that occur on only one or two trees are rare common names and
  cultivars (`'Hearts Of Gold' Eastern Redbud`, `Tomlinson Ash`), not names.

Where it is used: only as the middle fallback of
`display_name <- coalesce(common_names[1], tree_name, species)`
(`data/raw/city_derived.preql:120`). The enrichment name wins nearly
everywhere: the fallback fires for 2 trees in SF, 0 in NYC, Winnipeg and
Boston, 28 in London and ~700 in Paris and Calgary, and those are junk rows
(`Non spécifié`, `Poplar, brooks, number 6`).

### Columns

- **`tree_name`** becomes the tree's own name ("The Senator"). Null on nearly
  every tree; ingests stop writing common names and species into it.
- **`significance`** (new, string): a short blurb on why the tree matters.
- **`significance_source`** (new, string): attribution (URL or register
  name). Required for a blurb copied from a register; optional for one
  written by us or a reporter.
- **`is_notable`** (derived): `tree_name is not null or significance is not null`.

### Data flow

**a. Reports, proposed by the public and approved in the reviewer.** Extend
the approved tree-modification flow rather than add a pipeline: it already
does latest-report-wins per field, `clearFields` (#212), and a partial
`~tree_id` merge that only ever touches the tree a report names.

- Public app, tree-report form (`src/`): optional *Name* and *Why it's
  notable* fields, plus an optional source link. Length limits on the
  client and again in the reviewer (name ≤ 60 chars, blurb ≤ 500).
- `reviewer/modifications.ts` + `modifications_page.html`: show the
  proposed text, let the reviewer edit it before approving, and let an admin
  author one with no report behind it. Free text from the public reaches the
  map only through this approval, the same as photos.
- Export: `treeName`, `significance`, `significanceSource` on an approved
  modification; `clearFields` can name any of them.
- `data/raw/tree_modification_info.py`: three more SCHEMA columns, folded
  like species and DBH.
- `data/raw/tree_dedup.preql` ("Approved corrections"):
  `modified_tree_name`, `modified_significance`,
  `modified_significance_source`;
  `merged_tree_name <- coalesce(modified_tree_name, @by_source(raw_tree_name))`
  and the same for the other two.
- Caveat, shared with every correction today: a report keys on the
  *published* tree id, so a re-cluster that changes the survivor orphans it.
  Add a test or check that lists modification ids no published row carries
  (`merged_tree_ids` resolves most of them).

**b. Source-native, later, one source at a time, each verified on its
values** (and its coded-value domain) before it's trusted.

| Candidate | Where | Gives |
|---|---|---|
| OSM `name`, `denotation=natural_monument/landmark` on `natural=tree` | `shared/osm.py` (one place, every city) | name, flag |
| SF `legalstatus` (Landmark / Significant) | `ussfo/sf_tree_info.py` (not in `SELECT` today) | flag; Landmark trees are a short list worth naming |
| Paris *arbres remarquables* | `frpar` portal | flag, maybe name |
| Melbourne Exceptional Tree Register, NYC Great Trees, UK Ancient Tree Inventory | separate registers | name + blurb, matched to an inventory tree spatially |

A separate register names trees that are already in the inventory.

**Built (2026-11-15): notable registers as a source class.** Milan's
monumental trees and Tree Boston's Big Old Trees are the first two, as
`NOTABLE_{code}` partitions (docs/DATA_PIPELINE.md, "Sources"). Each register
row is linked once, in its ingest, to the inventory row it is (same genus,
within 30 m, trunk within 30%; `shared/notable.py`). In the model it joins
only that tree's cluster and lends it a name and a blurb. A register row with
no credible match publishes as a tree of its own at the register's point.
Milan links 6 of 13 and Boston 8 of its 16 placed trees. A reviewer
check of the unlinked ones (moving a link, or confirming a register tree the
inventory lacks) would be the admin-authored modification above, and has not
been built. `significance` reaches the model without a `tree_partition`
column: the template binds it to a literal null (`raw(...)`) and the register
partition re-binds it, so no OSM staging file needed a restage.

### Model and schema changes

**Step 1 (built, branch `named-trees-crown-position`):**

- `shared/ingest.py`, `enforce_tree_schema(..., proper_names=False)`: an
  ingest's `tree_name` is **published as null unless the ingest declares
  `proper_names=True`**. One choke point, so every existing ingest's common
  name is gone in one place with nothing to miss. With the flag, a value on
  more than `TREE_NAME_MAX_REPEATS` (3) rows is a common name and is nulled,
  with a count in the refresh log.
- No ingest writes a common name any more: the `tree_name` code is gone from
  every script, along with `normalize_tree_name`, TreeKeeper's
  `tree_name_for`, the local common-name helpers in LA, DC and Denver, and
  the common-name fields 18 portal requests fetched only for it. Scripts that
  read a common name to *resolve the species* (Charlotte, Durham, New
  Orleans, Arlington) or as a duplicate key (Cambridge) still read it. Paris
  still fetches `libellefrancais`, because its duplicate-row scoring counts
  it.
- The chat no longer sees `tree_name`: it's out of `tree_info.preql`'s union
  datasource, both prompt concept lists, the chat panel's field list, and the
  browser's `trees` view.
- `tree_modification_info.py`: `treeName`, `significance`,
  `significanceSource` from the export (trimmed, capped at 60/500/300
  chars); `clearFields` → `''`.
- `tree_common.preql`: `tree_name` redefined; `significance`,
  `significance_source`, `is_notable`; `latitude`/`longitude` documented as
  the base; `crown_latitude`/`crown_longitude` derived (see §2).
- `tree_dedup.preql`: `modified_tree_name/_significance/_significance_source`
  on the corrections table;
  `merged_tree_name <- nullif(coalesce(modified, @by_source(raw)), '')`;
  `significance` from reports only; both in `published_tree_info`.
- `city_derived.preql`: `display_name <- coalesce(common_names[1], species)`.
  `tree_name` is out, so a proper name never becomes a species label.

**Why the new columns are *not* in `tree_partition` yet.** Every partition
binds the template's columns, and the OSM partitions read pre-staged
parquets. A new template column breaks every city build until all OSM
staging jobs have re-run. No source supplies a significance today (names come
from reports), so it comes in through the corrections table alone. The
first source-native name or blurb adds `raw_significance` to the template,
`staging_common.preql` and `osm.py`, and restages OSM before the cities
build.

**Step 2 (after every city has rebuilt):** the readers of published
parquets, which fail on a city file that lacks a column they name:
`full_tree_publish.preql` / `full_tree_info_source.preql` (the rollup reads
every city in one scan), `tree_info.preql` (the resolver's model),
`city_derived.preql` (`city_tree_browser` gains `notable: is_notable`; the
vocabulary grain drops `tree_name`).

### What the map loads: one bit, text on click

**World blocks** (`data/world/publish_blocks.py`):
- **Add** `notable BOOLEAN`, null when false. In parquet an almost
  all-null column is a few bytes per row group.
- **Remove** `tree_name` from `COLUMNS` and `VOCAB_COLUMNS`. It's an ENUM
  every resident row carries today and it will be null on ~all of them.
  Swapping it for a 1-byte flag is a net saving in block bytes, vocab size
  and the worker's per-row width.
- **Publish a notable file** beside the vocabulary: content-addressed,
  named in the manifest (the commit point), one row per notable tree with
  `tree_id, city, tree_name, significance, significance_source`. Hundreds of
  rows, a few KB. Built from the projections' notable rows in the same run.

**Worker** (`workers/cityTables.ts`):
- `WORLD_BLOCK_COLUMNS` / `worldRowsSql`: `notable` in, `tree_name` /
  `source_tree_name` out (`CITY_ENUM_TYPES.sourceName`, `VOCAB_KINDS`).
- Clean up the alias: `display_name` is read as `display_name`, not as
  `tree_name`.
- The notable file is **not** fetched on load. It's fetched the first time a
  notable tree's card opens, then cached for the manifest's lifetime. A tree
  without the bit never triggers it.

**Desktop dots** (`composables/treeDotsLayer.ts`, `workers/cityPoints.ts`):
- The per-tree attribute is a `vec4` of bytes and `.w` is unused
  (`attr[i*4+3]`, line ~723). Put the bit there; `pointChunksSql` adds
  `CAST(coalesce(tf.notable, false) AS UTINYINT)`. Same buffer, same upload,
  same draw call.
- Draw the glow in the same pass: for a flagged tree the vertex shader
  widens `gl_PointSize` and the fragment shader adds a soft outer ring. No
  second pass over all points; unflagged trees take the existing code path.
- The colour-refresh path doesn't touch the bit.

**Desktop sprites** (`treeCrownLayer.ts`, `workers/crownQuery.ts`): add
`tf.notable` to `nearby` and a halo quad under flagged sprites only. That's
one boolean column in a query that already reads the row.

**Phone** (MVT, `duckdbPipeline.worker.ts` feature table, `useMapLayers.ts`):
- Add a `notable` property to the prepared feature struct, **only when
  true**. A null property isn't written, so ordinary features keep their
  size.
- One extra circle layer filtered on `['has', 'notable']`, with
  `circle-blur`, under the tree layers. It matches almost nothing, so its
  render cost is near zero; confirm with `bench:tiles`.

**Tree card** (`components/TreeMap.vue:134`, card query ~1600): if the row's
bit is set, show the card immediately and fill the name (as the title, with
the species under it), the blurb and the attribution when the notable file
answers.

**Chat:** `useChat.ts:210` and `ChatPanel.vue:40` describe `tree_name` as
`"Swamp Myrtle"`; rewrite both. On the `trees` view `tree_name` goes away
with the block column; the chat reaches names through the notable file
(register it as a table) or the semantic model, which reads the published
parquets.

**Known limit:** desktop loads trees from z9 (`LOAD_MIN_ZOOM`), so the glow
shows at city zoom. A phone loads nothing below z13, so on a phone notable
trees glow only from neighbourhood zoom in. Getting them onto a phone at
city zoom would mean loading the notable file up front; that's a later
decision, outside this change.

---

## 2. Crown position (inert in this change)

### Derive the crown now, store the observed one when a source exists

- `latitude`, `longitude`: unchanged, documented from now on as the **base**.
- **Step 1 (built):** `crown_latitude <- latitude`, `crown_longitude <- longitude`
  in `tree_common.preql`. Every tree has a crown position, it matches the
  base, and it costs no bytes and no rollout.
- **With the first crown source:** `observed_crown_latitude`,
  `observed_crown_longitude` (float, nullable, set only where a source saw
  the crown apart from the base) go into `TREE_COLUMN_TYPES`,
  `tree_partition` (an OSM restage, as above) and `published_tree_info`, and
  the derivation becomes `coalesce(observed_crown_latitude, latitude)`.
  `crown_offset_m` comes with it. Trilogy has no `cos()`, so the distance
  needs `std.geography`'s distance or a per-city scale constant.

Why derive instead of storing a filled-in pair:
- **It would drift from the base.** Overture snapping and approved position
  reports move the base after the source merge. A stored crown merged from
  the raw source point would then differ from the published base. Deriving
  from the final `latitude` keeps crown = base by construction.
- **Cost.** A filled-in pair is two duplicate DOUBLEs × ~13M trees; an
  all-null column is nearly free.
- **No per-city line.** Fifty cities merge their own position; the derived
  form needs no change in any of them.

### Merge

`tree_dedup.preql`: `@crown_by_source`, ranked by who sees crowns:
satellite > community > municipal > osm, predictions only for a
prediction-only cluster. Merged in the shared file, inert until a partition
supplies the column.

### Deferred: needs a dedicated source and QA

- Reviewed satellite detections and predictions put the **crown centre** in
  `latitude`/`longitude` today. They are *not* switched to emit
  `observed_crown_*` here. Most of the offset between a detection and its
  inventory row is geocoding error, not lean, so it needs a plausibility
  gate and a look at the distribution first.
- **Nothing crown-related goes into the projection, the blocks or the
  worker** until a source exists: no offset columns and no shader work. The
  later shape is in the appendix.

---

## Rollout (order matters)

`tree_name` changes meaning in published files, so nothing on the map may
key on it, and the frontend must not ship until the blocks carry `notable`.

1. **Model + ingest PR** (built): the `tree_name` choke point, report fields,
   `significance` published, `display_name` without `tree_name`, the crown
   derived. Offline suite green (10,268 + the new tests); dry runs of SF,
   Burlington VT (`tree_position`) and Boston each plan one asset, and SF's
   plan has the same 18 joins as main, with the new columns riding the
   existing corrections join.
2. Each city rebuilds on its next run: the planner reports `schema changed:
   missing column significance`, so no city is `up_to_date`. Trigger the
   `city-{code}` jobs to roll it sooner. Cloud only.
3. Verify: `tools/published_columns.py significance` covers every city, and
   `tree_name` is non-null on ~0 rows per city. Then the step-2 readers
   (rollup, resolver model, projection `notable`), then the `derived-{code}`
   jobs.
4. **World blocks PR**: `notable` in, `tree_name` out, the notable file,
   manifest field. `test_publish_world_blocks.py` keeps `COLUMNS` and
   `WORLD_BLOCK_COLUMNS` in step, **so this lands together with the worker
   change in step 5.** The live frontend reads `tree_name` from the blocks
   by name; mixing old and new would fail the read. Either ship 4 and 5 as
   one release, or have the worker read both shapes for one cycle.
5. **Frontend PR**: worker shape, dots/sprite/phone glow, card, chat docs.
   Benchmarks before and after: `pnpm bench:memory`, `bench:tiles`,
   `bench:dashboard`, `test:wasm`. **The bar is no regression on any of
   them.** The expected direction is a small saving, from the `tree_name`
   ENUM leaving the resident row.
6. **Reports**: public form fields, reviewer approval/editing, export,
   `tree_modification_info.py`.
7. Source-native names, one source at a time.

## Appendix: the crown on the map, once a source exists

- Projection/blocks: `crown_dx_m`, `crown_dy_m` FLOAT, null when coincident.
- Dots: needs two more bytes per tree (int8 decimetres, ±12.7 m), so it
  needs a `bench:memory` budget check. The crown disc draws at the offset
  and the trunk circle at the base; sprites anchor at the base and lean.
- Card: "Leans ~3 m NE" past a threshold.
- Reported position moves stay base moves; the crown follows unless
  observed.
