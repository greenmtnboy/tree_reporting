# Extending the Tree Map: Adding a New City

This document captures the steps discovered when adding Paris (FR), following SF, NYC, and Boston. Use it as a runbook for the next city addition.

---

## Architecture Overview

```
OpenData API / CSV
        │
        ▼
data/raw/{city}/{city}_tree_info.py   ← fetch + transform script (Arrow IPC → stdout)
        │
        ▼  (Trilogy pipeline materialises to GCS)
GCS: trilogy_public_models/duckdb/trees/{code}_tree_info_v{version}.parquet
        │
        ▼  (loaded at runtime by the browser worker)
src/src/workers/duckdbPipeline.worker.ts  ← DuckDB-WASM reads parquet over HTTP
        │
        ▼
src/src/composables/useMapData.ts  ← CITY_CONFIG drives map center + default query
        │
        ▼
src/src/workers/parquetUrls.ts  ← builds versioned GCS URL from city code + DATA_VERSION
```

The browser never touches raw source data — it only fetches the pre-built parquet from GCS and queries it locally with DuckDB-WASM.

### Approved community submissions

The local reviewer promotes accepted submissions from `submissions` into the
`publishedTrees` Firestore collection. On approval it also:

1. Re-encodes each submission photo with `sharp` and writes it to the **public**
   `sf-tree-reporting-published` bucket under `community/photos/`.
2. Rewrites `community/published_trees.ndjson` (the approved-tree export) and
   `community/manifest.json` (the freshness timestamp) in the same bucket.

Approval itself does not refresh map data. During the normal scheduled refresh,
`data/raw/community_tree_info.py` reads that public export into the canonical
Arrow tree schema, and `community_update_time.py` reads the manifest as a
freshness input, so a new approval makes the affected city Parquet stale on the
next run.

**Freshness is per city.** `community_update_time.py` emits one *column* per
city (`ussfo_community_data_updated_through`, …) and each city's model probes
only its own, so approving a tree in Boston rebuilds Boston alone. A shared
scalar would mark all fourteen city Parquets stale and re-download every
municipal dataset to publish one tree.

Note the shape: per-city *columns*, not one row per city with a
`complete where city = 'X'` filter. Trilogy pushes a `complete where` clause
into row queries but **not** into the watermark probe, which stays a plain
`SELECT MAX(col) FROM uv_run(...)` over every row the script emits. The
row-per-city version was tried and measured — a Boston-only approval moved San
Francisco's watermark too. `data/raw/tests/test_data_sources.py` guards this.

**Why a public GCS export rather than reading Firestore directly.** The first
cut of this pipeline hit `firestore.googleapis.com/v1` on the theory that a
`allow read: if true` security rule made `publishedTrees` world-readable. It
does not: the Cloud REST API enforces IAM, and security rules only apply to
Firebase SDK clients, so an unauthenticated pipeline gets
`403 PERMISSION_DENIED`. Because every city's freshness now depends on the
community probe, that 403 aborted the *entire* `trilogy refresh raw` run — all
cities, plus landmarks and enrichment. Reading a public object keeps the
pipeline credential-free, keeps Firestore private, and gives the photos a
public URL in the same step. Both community scripts also treat a missing export
as zero rows rather than raising, so an optional source can never take the
whole refresh down again.

**Photos.** `submission_photo_url` on a tree row is a photo of *that specific
tree*, distinct from `species.photo_url` in `tree_enrichment.preql`, which is a
stock photo of the species. `TreeMap.vue` prefers the submission photo when
present. Private submission uploads are never made public: the `submissions`
bucket keeps `public_access_prevention = "enforced"`, and a reviewer approval is
the only thing that copies a photo into the public bucket.

**EXIF.** The web client strips metadata by re-encoding through a canvas
(`src/src/lib/image.ts`), which every upload path uses. The reviewer strips it
again server-side at the publish gate, because the storage rules only check
`contentType` — anything speaking the Storage API can upload a JPEG with intact
GPS tags, and publishing is the point where that stops being private.

### The `data_source` column

Every tree row carries the dataset it came from, materialized as a uniformly
named `data_source` column in every tree Parquet. The value list lives in
`DATA_SOURCES` in `data/raw/_ingest_shared.py`; display labels live in
`src/src/data/dataSources.ts`. `data/raw/tests/test_data_sources.py` asserts the
Python picklist, the preql enums, and the `complete where` clauses all agree.

The concept is modelled as a **per-city** enum key (`ussfo_source`,
`usbos_source`, …) declared in each city's tree model, *not* as one global enum
in `core.preql`. This is not a style choice: Trilogy proves a city's Parquet is
complete by checking that its raw sources cover every value of the partitioning
enum. A 30-value global enum is never covered by one city's two sources, and the
model fails with `UnresolvableQueryException: no complete sources found` — as
does a plain `key data_source string`. Both were tried. Each city then aliases
its key to the physical column (`data_source: ussfo_source`) so the Parquets
still share one column name, and `tree_info.preql` merges the 14 keys into a
single `data_source` concept for the cross-city Parquet. Do not add that merge
to a city model — it re-breaks that city's resolution.

This partitioning is also what makes community rows appear at all. A city whose
only source claims `complete where city = 'X'` leaves no room for a second
source: Trilogy treats the municipal source as covering the whole city and
silently emits **zero** community rows, with no error anywhere. Each raw source
must claim `complete where city = 'X' and {code}_source = 'Y'`.

### Supplemental OpenStreetMap sources

Every city carries a third partition of `natural=tree` nodes from OSM, labelled
`OSM_{CODE}` and listed in `OSM_DATA_SOURCES` in `data/raw/_ingest_shared.py`
— ~1.37M staged trees across the fourteen. It was opt-in while only Tempe and
Boston were wired; it no longer is, and a new city should wire it at the same
time as its municipal source. `test_osm_city_is_fully_wired` parametrises over
every city in `OSM_DATA_SOURCES` and asserts each half of the wiring below, so
a half-wired city fails loudly rather than silently emitting zero OSM rows.

The extraction itself lives once, in `_osm_shared.extract_city`; each city
keeps a ~28-line shim. They were 160-line copies differing in five lines, which
is how Boston's shipped with a docstring claiming it extracted Tempe's trees.

**A city-specific column has to be declared on the OSM partition too.** London's
municipal and community sources declare `borough`; its OSM source did not, so
that partition dropped out of the union and the remaining two stopped covering
the source enum. Trilogy reports this as `complete where` clauses "not provably
exhaustive over that type", which is a long way from "you forgot a column".
Pass it through `extract_city(..., extra_null_columns={"borough": pa.string()})`
and declare `borough: ?borough` on the datasource. London is the only instance
today. Dry-run every city after wiring from a template — per-city divergence is
exactly what a template hides.

**Extraction is decoupled from refresh.** `{city}_osm_extract.py` queries
Overpass and publishes `{code}_osm_staging.parquet` to GCS; the refresh
pipeline only ever reads that object. Two reasons: Overpass 429/504s routinely
under load (fetching at refresh time would couple every municipal rebuild to
Overpass being up), and the only cheap OSM watermark is the global database
timestamp, which advances every minute and would mark the city stale on every
tick. Instead `{city}_osm_probe.py` emits the staged object's publication time,
so re-running the extract is what makes the Parquet stale. The extract is
deliberately self-contained so it can later move onto a weekly `[[cloud.job]]`
without a rewrite.

**Staged parquets live in GCS, not in git — and the reason is the watermark.**
The first cut committed them next to the extract script and had the probe emit
the file's `st_mtime`. That works locally and is wrong everywhere else, because
**git does not preserve mtime**: a fresh clone stamps every file with the
checkout time. Every cloud job run is a fresh clone, so the watermark advanced
on each of the three daily ticks and Boston and Tempe rebuilt every time —
precisely the every-tick thrash staging was introduced to prevent, having merely
swapped Overpass's minute-resolution clock for a checkout clock. It is invisible
locally, where mtimes happen to be stable, and it was caught only by noticing
that four staging files committed at 08:55 and 10:24 all carried an mtime of
20:17, matching a branch switch.

A GCS object's `Last-Modified` is a real publication time that survives cloning.
`_ingest_shared` holds the three helpers — `staging_url` for the preql `file`
clause, `staging_modified_at` for the probe, `upload_staging` for the extract —
and `.gitignore` carries `*_staging.parquet` so a copy cannot drift back in.
`test_no_staging_parquet_is_committed` fails if one does.

The probe's HEAD request carries a cache-buster: the objects are served with
`Cache-Control: max-age=3600`, so a probe run just after an extract would
otherwise read the previous publication time and call the city fresh.

**Dedup is stacking + aggregate, not an anti-join.** OSM overlaps the
municipal inventory by construction. Trilogy's joins are equality-only (a
non-`=` join key is rejected at hydration), so a spatial anti-join cannot be
expressed in the model — and doing it in a script against the published
Parquet would dedup one rebuild cycle stale. The model instead derives a grid
cell from lat/lon in **four copies of a ~10m grid staggered by half a cell**
(x, y, and both), counts non-OSM anchors per cell with a filtered aggregate,
and flags an OSM row whose cell in *any* grid contains an anchor. Two points
within half a cell (~5m) always share a cell in at least one grid, so this is
an equi-join-shaped stand-in for "within 5m", with possible matches out to a
cell diagonal (~14m). An unnest-based 3x3 neighborhood would give an exact
radius but sits on the merged-unnest planner path that has regressed twice
upstream; don't.

**Why the cell is 10m (5m guarantee), not 20m.** Distance alone cannot
distinguish a re-mapped inventory tree from the next tree in a planted row:
Tempe's inventory has a *median* nearest-neighbor spacing of 6.5m, and 79.5%
of inventory trees have another inventory tree within 10m. What does separate
the populations is pair structure, measured by
`data/raw/ustem/tempe_dedup_validation.py` (exact haversine, mutual-NN, 1:1
matching, local density): OSM points within 5m of an inventory tree are
mutual nearest neighbors with it >=88% of the time (99.7% below 2m), sit
3-8x closer to it than to the second-closest, and appear where OSM:inventory
local density is 1:1 — the inventory re-mapped with GPS/imagery offset. In the
5-10m band mutual-NN collapses to 25%, meaning roughly three quarters of those
matches are distinct neighbors at planting-row spacing. A 20m cell (10m
guarantee, 28m reach) flagged ~80 more OSM rows, most of them likely real
trees; the error asymmetry favors the smaller cell, since a missed duplicate
double-renders one visible, toggleable dot while a false flag hides a real
tree. When wiring a new city, re-run the validation script against that city's
inventory before copying the cell size — the 5m break reflects Tempe's small
OSM positional offsets, and a city traced from misaligned imagery may need a
larger cell (Berlin and Paris can calibrate against `osm_ref` exact-id
matches). Per-city thresholds are expected as OSM rolls out.

**Calibrate every city; the answer differs.** `osm_dedup_validation.py --city
CODE` measures the mutual-NN rate per distance band against the staged extract
and the published inventory. Across the fourteen it split three ways:

| cell | cities | 5-10m mutual-NN |
|------|--------|-----------------|
| 10m | Paris, Berlin, Vancouver, Amsterdam, DC, SF, Melbourne | 15.9%-47.6% |
| 10m | London, NYC | 51.5%, 53.3% — coin flips, see below |
| 20m | Burlington, Buenos Aires, LA | 61.0%-67.2% |

**The threshold is 60%, not 50%, and that matters.** The first version of this
script used a bare `mutual-NN < 50%` cut and sent London (51.5% over n=18,076)
and New York (53.3% over n=5,059) to a 20m cell. Those readings are coin flips,
and the errors are not symmetric: flagging a 50/50 band hides about as many
real trees as duplicates it removes — roughly 8,800 in London and 2,400 in New
York. Only flag a band that is *clearly* duplicate-dominated; when the
measurement is ambiguous the asymmetry says leave the rows visible. The script
now reports which regime it saw ("coin flip" or "neighbour-dominated") rather
than emitting a bare number, because the number alone invited exactly this
mistake.

Berlin (6,157) and Paris (3,906) carry enough `osm_ref` values to cross-check
the geometric break against exact municipal-id matches; both break sharply at
5m (18.4% and 15.9%), which is the strongest confirmation the method has.

**Flag, never drop.** The dedup materializes as an `is_duplicate` boolean
column (aliased from `{code}_is_duplicate`), and the Parquet keeps every row.
Dropping rows would break the partition-completeness proof (the Parquet must
contain the union of its declared sources); the flag also makes dedup tunable
and auditable — flagged rows stay queryable, so the cell size can be evaluated
against real data rather than re-derived.
Because the flag is computed from the same materialization's source rows, a
municipal update dedups against itself in the same rebuild; there is no
staleness window.

**`complete where` asserts; `where` filters.** These are different clauses and
a shared source needs both. `complete where city = 'X' and {code}_source = 'Y'`
is a *model-level assertion* — "this source holds the complete set of rows for
that partition" — and does not promise the planner will inject a predicate.
`community_tree_info.py` is read by all fourteen cities and returns *every*
city's approved submissions, so each city's datasource has to restrict its rows
itself, with a `where` clause after the file clause:

```preql
root partial datasource {code}_community_tree_info (
    ...
)
grain (tree_id)
complete where city = '{CODE}' and {code}_source = 'COMMUNITY_{CODE}'
file `../community_tree_info.py`
where city = '{CODE}';
```

Read together: "only {CODE}'s trees, and this is all of them."

This was missing for a long time without visible symptoms, because the planner
*happened* to inject a predicate for the twelve cities with no OSM partition
and not for the two with one — the presence of the `is_duplicate` column, whose
value comes from an aggregate across the stacked partitions, is what decides it.
Tempe's Parquet accordingly shipped three `city = 'USBOS'` rows. Do not rely on
the injection: write the `where`. Trilogy compiles it into both a SQL predicate
and a `--filter 'city={CODE}'` argument to the script, which
`community_tree_info.py` honours so that fourteen invocations do not each read
and emit the whole export. Write-up in
`upstream_repro/partition_filter_dropped/`.

**Every city carries the column; the frontend hides the flagged rows.** The
flag is only useful if something reads it, and for a while nothing did: Boston's
first OSM rebuild flagged 7,369 rows correctly and the map rendered all of them
anyway, stacked on top of the municipal trees on Boston Common. `is_duplicate`
is therefore materialized by **all fourteen** city models — the twelve with no
OSM partition emit `auto {code}_is_duplicate <- False;` — and
`duckdbPipeline.worker.ts` filters with a single
`AND NOT COALESCE(is_duplicate, false)` when it loads a city.

A missing column does not degrade: DuckDB raises a binder error and that city's
map fails to load entirely. So **refresh every city before deploying** a worker
change that selects it. `src/src/workers/parquetSchema.test.ts` asserts the
column against the live GCS Parquet for every city in `CITY_CONFIG`, which is
what turns "I forgot to refresh" into a red test rather than a broken city.

**A new column does not make a Parquet stale.** Staleness is decided by the
freshness probes, which watch the *source data*, so a plain `trilogy refresh
raw` after a model change reports every unchanged city "up to date" and rebuilds
nothing. Rolling this column out looked like it worked — the run exited 0 with
"All scripts executed successfully" — while twelve of the fourteen Parquets were
never touched. Force each one by name:

```bash
cd data && trilogy refresh raw/{city}/{city}_tree_info.preql -f {city}_tree_info
```

Then rebuild `full_tree_info`, which reads the per-city Parquets and would
otherwise still hold the pre-change rows.

**Do not merge `is_duplicate` in `tree_info.preql`.** `data_source` is merged
there, and doing the same for the dedup flag looks symmetric but does not plan:

```
UnresolvableQueryException: Planner emitted a keyless join between row-bearing
sources that share a join axis: ...unioned_at_local_data_source_local_tree_id
_grouped_by_local.usbos_cell_a... This would render as a cross join (ON 1=1)
and fan out; the join axis was lost upstream.
```

The flag comes from a per-city grid aggregate over that city's own rows;
merging the keys asks the planner to resolve that aggregate over the union of
all fourteen cities. The cross-city `full_tree_info` Parquet accordingly has no
`is_duplicate`, and the worker skips the filter on that fallback path.

Other details: OSM `circumference` defaults to metres but is frequently
mis-entered as bare centimetres — the extract treats a unitless value > 10 as
cm. The staging schema keeps the node's `ref` tag (`osm_ref`): empty for
Tempe, but Berlin (~6k) and Paris (~4k) carry the municipal inventory id
there, enabling exact-id dedup when those cities are wired. OSM data is ODbL:
add "(c) OpenStreetMap contributors" attribution in `README.md` and
`src/src/data/sourceCatalog.ts` when wiring a city.

---

## Data Versioning

All GCS parquet files use a versioned naming scheme: `{name}_v{DATA_VERSION}.parquet`.

The version is a single integer defined in two places — keep them in sync when bumping:

- **`data/raw/core.preql`** — `param data_version string default '2';` (the preql
  models interpolate it; `data/raw/enrichment/_tree_shared.py` reads it from here)
- **`src/src/workers/parquetUrls.ts`** — `TREE_DATA_VERSION` and
  `LANDMARK_DATA_VERSION` (used by the browser worker). Both track the single
  preql `data_version`, so bump all three together.

Preql datasource files use the `f\`` template syntax to interpolate the version:

```preql
file f`https://.../trees/{code}_tree_info_v{data_version}.parquet`:f`gcs://.../{code}_tree_info_v{data_version}.parquet`
```

**When to bump the version:** Any change that makes an existing consumer fail — a type change, or removing/renaming a column something actually reads. Bump the integer in both places, rebuild all parquets, and the old files remain on GCS untouched as a rollback path.

**When not to.** Purely additive columns do not need one. The deployed app selects columns by name, so it ignores new ones, and rolling the app back after a rebuild still works because its column set is a subset. Bumping is not free: `data_version` is a single param shared by *all* parquets, so a bump re-materializes the LLM-backed enrichment table and every landmark parquet too. The `data_source` / `submission_photo_url` addition deliberately stayed on v2 for this reason.

Either way, **refresh before you deploy** — the worker selects new columns by name and fails to load against a parquet that has not been rebuilt yet.

---

## City Code Convention

| City | Code | Pattern |
|------|------|---------|
| San Francisco | `USSFO` | `{ISO-3166-1-alpha-2}{IATA-airport-or-3-letter-abbr}` |
| New York City | `USNYC` | |
| Boston | `USBOS` | |
| Paris | `FRPAR` | |

All codes are **5 uppercase letters**: 2-letter country code + 3-letter city abbreviation. The parquet file name is the lowercase code: `frpar_tree_info_v1.parquet`.

---

## Step-by-Step: Adding a New City

### 1. Understand the Source Data

Find the city's open tree dataset. Key fields needed:
- **Unique tree ID** (any stable string/int)
- **Species** — the scientific name (Latin binomial, e.g. `"Platanus x hispanica"`). Do **not** embed a common name in this field — see the Species Enrichment section.
- **Latitude / Longitude** (decimal degrees WGS84)
- **Diameter at breast height** (DBH) in inches, or a proxy you can convert

Paris notes:
- Source: `https://opendata.paris.fr/explore/dataset/les-arbres/`
- API: `https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/les-arbres/exports/parquet`
- DBH not available — uses **circumference in cm** (`circonferenceencm`); convert: `dbh_in = circ_cm / (π × 2.54)`
- Genus/species are `genre`/`espece` — concatenate as `f"{genre} {espece}".strip()`
- Location is a nested struct `geo_point_2d: {lat, lon}`
- No `plant_date` field
- ~217k trees

### 2. Choose a City Code

Format: `{ISO2}{3-letter-city}`. Check it doesn't collide with existing codes. For Paris → `FRPAR`.

### 3. Register the City Code in the Trilogy Enum

**`data/raw/core.preql`** holds the `city` key as a typed enum. Add the new code:

```preql
key city enum<string>['USSFO', 'USNYC', 'USBOS', 'FRPAR'];
```

Trilogy will reject any `complete where city = '...'` clause whose value isn't in this enum, so this must be done before the preql files in the next steps will validate.

Also add the new city's source labels to `MUNICIPAL_DATA_SOURCES` in
**`data/raw/_ingest_shared.py`** (the community label is derived automatically)
and a display label to **`src/src/data/dataSources.ts`**. See "The `data_source`
column" above for why the enum values themselves live per-city rather than here.

### 4. Create the Freshness Probe

**This step is mandatory.** Without it, Trilogy re-downloads the full dataset on every pipeline run regardless of whether the source has changed. The probe is a lightweight script that fetches only the dataset's last-modified timestamp and emits a single-row Arrow table.

Create `data/raw/{city}/{city}_update_time.py`:

```python
#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit_freshness, get_json_with_retry

def fetch_modified_at() -> datetime:
    # Hit the lightest metadata endpoint your open data platform exposes.
    # For OpenDataSoft (Paris, and many European cities):
    #   GET /api/explore/v2.1/catalog/datasets/{dataset_id}
    #   Read: .metas.default.modified  (ISO 8601)
    # For CKAN (Boston, many US cities):
    #   GET /api/3/action/resource_show?id={resource_id}
    #   Read: .result.last_modified
    # For Socrata (SF, NYC):
    #   GET /api/views/{dataset_id}.json
    #   Read: .rowsUpdatedAt  (Unix timestamp)
    raise NotImplementedError

if __name__ == "__main__":
    emit_freshness("{CODE}", fetch_modified_at)
```

**Fetch JSON with `get_json_with_retry`, and emit with `emit_freshness`** — do
not hand-roll `requests.get(...).json()` or the Arrow table.  Both helpers exist
because of the same failure: every probe is a root datasource, and Trilogy
collects root watermarks in one planning phase that has no per-probe error
handling, so *one* probe raising ends the whole `trilogy refresh raw` run before
any city is refreshed.

- `get_json_with_retry` treats a 2xx whose body isn't JSON as a transient
  outage, because that is what it usually is: portals in maintenance answer
  every path with an HTML holding page and HTTP 200 (gdi.berlin.de serves a
  1.4 KB "Wartungsarbeiten" page for its WFS *and* its metadata API).  It
  retries with backoff and, when it gives up, raises `UpstreamUnavailable`
  naming the URL, status, content type and first line of the body — where
  `.json()` raised a bare `JSONDecodeError: Expecting value: line 2 column 9`.
- `get_json_with_retry` also classifies a 200 that carries a provider *error
  envelope* — ArcGIS's `{"error": {"code": 500, ...}}`, Socrata's
  `{"error": true}`, CKAN's `{"success": false}` — as an outage. Burlington's
  ArcGIS answered a statistics query exactly that way during an outage; because
  the body was valid JSON it reached the probe's "no features" guard, which
  raised a fatal `RuntimeError` and took the whole refresh down. Probes should
  keep their "missing field" guards, but they must never be the thing that sees
  a portal outage first.
- `emit_freshness` catches `UpstreamUnavailable` and emits the epoch instead.
  The epoch loses every `greatest()`, so the city's Parquet compares as fresh,
  sits out this run, and is picked up by the next tick once the portal is back —
  while the other thirteen cities refresh normally.

Anything that is *not* an availability problem must keep raising.  A missing
field or an unparseable date means the portal changed its schema and our mapping
is stale; degrading there would freeze that city's Parquet silently and forever.
Raise `RuntimeError` (as the existing probes do for a missing timestamp field),
not `UpstreamUnavailable`.

For a probe whose endpoint returns something other than JSON, classify the
failure yourself — see `deber/berlin_landmarks_probe.py`, which raises
`UpstreamUnavailable` when Overpass returns a body that is not an ISO timestamp.

**Check the portal's maintenance window before assuming the schedule is fine.**
Berlin publishes one (Thursdays 08:00-10:00 local), and the refresh's original
06:00 UTC tick sat inside it every summer Thursday.  The tick times in
`data/trilogy.toml` avoid the 06:00-09:00 UTC band for that reason; if a new
city's portal publishes a window that collides, move a tick rather than adding
one.

Also add the city's freshness property to **`data/raw/tree_common.preql`**:

```preql
property <*>.{city}_data_updated_through datetime;

auto latest_update_through <- greatest(..., {city}_data_updated_through);
```

**One freshness timestamp per city (important):** The final materialized parquet's `freshness by` clause must reference a single per-city `auto` property, not a list of sub-source raw properties. If a city ingests from multiple sub-sources (e.g. Boston has `boston_city_data_updated_through` and `arboretum_data_updated_through`), define individual raw properties for each sub-source and one `auto` that coalesces them:

```preql
# tree_common.preql
property <*>.{city}_source_a_data_updated_through datetime;
property <*>.{city}_source_b_data_updated_through datetime;
auto {city}_data_updated_through <- greatest({city}_source_a_data_updated_through, {city}_source_b_data_updated_through);
```

The `freshness by {city}_data_updated_through` in the parquet datasource then references only the `auto`. Adding raw sub-source properties directly to `freshness by` is incorrect and will cause Trilogy to treat the datasource as needing multiple independent freshness checks.

Paris probe details:
- **Metadata URL:** `https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/les-arbres`
- **Timestamp field:** `.metas.default.modified` (ISO 8601, already timezone-aware)
- This is a ~2 KB JSON response vs. the ~50 MB full dataset export

### 5. Create the Fetch Script

Create `data/raw/{city}/{city}_tree_info.py`. Follow the pattern of `boston_tree_info.py`.

The script must:
1. Download the source data (CSV, JSON, or parquet from the open data portal)
2. Transform to this schema:

| Column | Type | Notes |
|--------|------|-------|
| `tree_id` | `string` | Prefix with `{abbr}-` e.g. `par-12345` for global uniqueness |
| `city` | `string` | The city code, e.g. `FRPAR` |
| `species` | `string` | **Scientific name only** — e.g. `"Platanus x hispanica"`. No `:: Common Name` suffix. |
| `plant_date` | `date32` | All-null is fine, but the column must still be `date32` — never `pa.null()` |
| `latitude` | `float64` | |
| `longitude` | `float64` | |
| `diameter_at_breast_height` | `float64` | Inches; `null` if unavailable |

3. Call `enforce_tree_schema(table, city="{City}")` immediately before `emit`.
4. Emit the Arrow IPC stream to `sys.stdout.buffer` via `pa.ipc.new_stream`.

**Column types are enforced, not inferred (important):** Trilogy passes the Arrow types from your script straight through to the materialised parquet — it does *not* coerce them to the types declared in `tree_common.preql`. A column left to inference silently produces the wrong parquet type, and the failure surfaces much later as a DuckDB binder error in the browser. Two real cases:

- Paris emitted an all-null `plant_date` as `pa.null()`. A null-typed Arrow column carries no type, so it materialised as `INT32` and every `year(plant_date)` query failed with `No function matches the given name and argument types 'year(INTEGER)'`.
- SF's `dbh` came from CSV inference; every value was a whole number, so pyarrow chose `int64` and the parquet column became `BIGINT` instead of `DOUBLE`.

`enforce_tree_schema` (in `data/raw/_ingest_shared.py`) is the single chokepoint that prevents this. It casts each canonical column to the type in `TREE_COLUMN_TYPES`, raises if a required column (`tree_id`, `city`, `species`) is missing, and passes city-specific extras (`borough`, `usbos_source`, …) through untouched. Casts are *safe* — a lossy conversion raises rather than corrupting values.

Scripts that emit source-native column names rather than canonical ones pass a `columns` map:

```python
table = enforce_tree_schema(
    table,
    city="San Francisco",
    columns={
        "tree_id": "treeid",
        "species": "qspecies",
        "plant_date": "plantdate",
        "diameter_at_breast_height": "dbh",
    },
)
```

**Species key rule:** The `species` field must contain only the Latin binomial (e.g. `"Platanus x hispanica"`). The `:: Common Name` convention was retired — common names now come exclusively from the enrichment table. If source data has a `:: suffix` (SF does), strip it: `v.split("::")[0].strip()`. For cities that provide genus and species epithet as separate fields (Paris), concatenate them: `f"{genre} {espece}".strip()`. Empty or null species should be emitted as `None`.

### 6. Create the Trilogy Data Model

Create `data/raw/{city}/{city}_tree_info.preql`. Wire in the freshness probe via `freshness by` on both the raw datasource and the materialized parquet. Use the `f\`` template syntax for the versioned GCS URL:

```preql
import ..tree_common;

root partial datasource {city}_update_time (
    data_updated_through: {city}_data_updated_through
)
grain (city)
complete where city = '{CODE}'
file `./{city}_update_time.py`
freshness by {city}_data_updated_through;

key {code}_source enum<string>['{CITY}_OPENDATA', 'COMMUNITY_{CODE}'];

auto {code}_published_data_updated_through <- greatest({code}_data_updated_through, {code}_community_data_updated_through);

# Only this city's column, so an approval elsewhere does not rebuild it.
root datasource {code}_community_update_time (
    {code}_community_data_updated_through: {code}_community_data_updated_through
)
file `../community_update_time.py`;

root partial datasource {city}_raw_tree_info (
    tree_id: tree_id,
    city: city,
    data_source: {code}_source,
    species: species,
    plant_date: ?plant_date,
    latitude: ?latitude,
    longitude: ?longitude,
    diameter_at_breast_height: ?diameter_at_breast_height,
    submission_photo_url: ?submission_photo_url,
)
grain (tree_id)
complete where city = '{CODE}' and {code}_source = '{CITY}_OPENDATA'
file `./{city}_tree_info.py`;


# Mandatory. Without this partition, approved community trees for this city are
# silently dropped — see "The `data_source` column" above.
root partial datasource {code}_community_tree_info (
    tree_id: tree_id,
    city: city,
    data_source: {code}_source,
    species: species,
    tree_name: ?tree_name,
    plant_date: ?plant_date,
    diameter_at_breast_height: ?diameter_at_breast_height,
    latitude: ?latitude,
    longitude: ?longitude,
    submission_photo_url: ?submission_photo_url,
)
grain (tree_id)
complete where city = '{CODE}' and {code}_source = 'COMMUNITY_{CODE}'
file `../community_tree_info.py`;


partial datasource {city}_tree_info (
    tree_id,
    city,
    data_source: {code}_source,
    species,
    ?plant_date,
    ?diameter_at_breast_height,
    ?latitude,
    ?longitude,
    ?submission_photo_url,
    {code}_published_data_updated_through,
)
grain (tree_id)
complete where city = '{CODE}'
file f`https://storage.googleapis.com/trilogy_public_models/duckdb/trees/{code}_tree_info_v{data_version}.parquet`:f`gcs://trilogy_public_models/duckdb/trees/{code}_tree_info_v{data_version}.parquet`
freshness by {code}_published_data_updated_through;
```

Also add `import ..community_tree_info;` at the top, declare
`property <*>.{code}_community_data_updated_through datetime;` in
`community_tree_info.preql`, and pass `data_source="{CITY}_OPENDATA"` to
`enforce_tree_schema` in the fetch script.

Verify before moving on — a missing community partition produces no error:

```bash
cd data && trilogy refresh raw/{city}/{city}_tree_info.preql --dry-run -f {city}_tree_info
```

The generated SQL must contain a `UNION ALL` and reference
`community_tree_info.py`. If it doesn't, the community partition isn't wired up.

### 7. Import in the Merged Data Model

Add to `data/raw/tree_info.preql`:

```preql
import {city}.{city}_tree_info;
```

### 8. Build and Upload the Parquet via Trilogy

Run the Trilogy pipeline to materialise the per-city parquet and push it to GCS:

```bash
trilogy run data/raw/tree_info.preql --city {CODE}
```

### 9. Update the Frontend

**a) `src/src/workers/parquetUrls.ts`** — the `cityTreeParquetUrl` and `cityLandmarkParquetUrl` functions use a regex `/^[a-z]{2}[a-z]{3}$/` to validate city codes (5 lowercase letters). No code changes needed for new cities — just ensure the `DATA_VERSION` constant matches `data/raw/enrichment/_tree_shared.py`.

**b) `src/src/trilogyModels.ts`** — add raw imports and entries in `ALL_MODEL_SOURCES` for both the tree and landmarks preql files. The agent chat's query resolver loads models from this list at runtime; omitting a city here means the agent cannot resolve queries against that city's data even though the parquet is loaded in DuckDB.

```ts
import {CITY}_TREE_INFO_MODEL from '../../data/raw/{city}/{city}_tree_info.preql?raw'
import {CITY}_LANDMARKS_MODEL from '../../data/raw/{city}/{city}_landmarks.preql?raw'
```

Add to `ALL_MODEL_SOURCES` alongside the other per-city entries:

```ts
{ alias: '{city}.{city}_tree_info', contents: {CITY}_TREE_INFO_MODEL },
{ alias: '{city}.{city}_landmarks', contents: {CITY}_LANDMARKS_MODEL },
```

**c) `src/src/composables/useMapData.ts`** — add the city to `CITY_CONFIG`:

```ts
export const CITY_CONFIG = {
  // ... existing cities
  FRPAR: { name: 'Paris', center: [2.3522, 48.8566] as [number, number] },
} as const
```

The `center` is `[longitude, latitude]` (GeoJSON/MapLibre order). Use the city center, not a corner.

That's it — the city button appears automatically in the UI, the worker loads `{code}_tree_info_v{DATA_VERSION}.parquet` from GCS, and DuckDB queries it exactly like any other city.

---

### 10. Update Attribution and Docs

Do not stop after the parquet and city config are working. Every city addition must also update the public attribution surfaces:

- `README.md` - add or update the source links in the tree inventory / landmarks tables.
- `src/src/data/sourceCatalog.ts` - add or update the portal metadata that powers the Info page attribution. `InfoView.vue` renders from this catalog; avoid hardcoding new portal links directly in the view.

If you change the source story significantly, review `src/src/components/WelcomeModal.vue` as well so the onboarding copy does not drift.

## Landmarks (Mandatory)

Every city **must** have a landmarks preql file, even if it yields zero rows. The worker silently skips missing landmark *parquets* at runtime, but a missing preql file will cause the Trilogy pipeline to fail and the agent will have no geographic context for the city. A landmarks dataset with zero rows is acceptable; a missing file is not.

> **Burlington pattern — local CSV + Nominatim geocoding:**
> When no structured spatial landmark source exists (e.g. the city only publishes a web directory), use a two-step approach:
> 1. Run a one-off geocoder script (`{city}_landmarks_geocode.py`) that scrapes the city's landmark list and geocodes each entry via Nominatim (free, no key, 1 req/sec). Results are saved to a local `{city}_landmarks.csv`. The script is resumable — it skips already-geocoded rows so you can interrupt and continue.
> 2. The preql datasource points directly at `{city}_landmarks.csv` — no Arrow redirect script needed. An empty CSV (header-only) is valid and produces zero rows.
> 3. The freshness probe (`{city}_landmarks_probe.py`) emits the CSV file's mtime as the freshness timestamp; Trilogy only re-materialises the parquet if the CSV changes.
>
> Commit `{city}_landmarks.csv` to the repo so the pipeline can run without re-geocoding. Re-run the geocode script periodically to pick up new entries.
> See `data/raw/burlington/` for the reference implementation.

### Landmark Data Schema

| Column | Type | Notes |
|--------|------|-------|
| `landmark_id` | `string` | Prefix with `{abbr}-` for global uniqueness |
| `city` | `string` | City code |
| `name` | `string` | Human-readable landmark name |
| `geometry_raw` | `string` | WKT geometry. Use a polygon/multipolygon if available; for point-only sources construct `POINT(lon lat)` |
| `latitude` | `float64` | Centroid latitude (can be derived from `geometry_raw` by Trilogy, or set directly for point sources) |
| `longitude` | `float64` | Centroid longitude |

City-specific extra fields are fine — declare them in `landmark_common.preql` alongside the SF, NYC, Boston, and Paris-specific blocks already there.

### Finding a Landmarks Source

Preference order:
1. **Official historic landmark / heritage designation registry** from the city or national government — most consistent with how SF/NYC/Boston landmarks work (e.g. NYC Landmarks Preservation Commission, SF landmark designations)
2. **Same open data platform as the trees** if a heritage dataset exists there
3. OpenStreetMap extract via Overpass API (`historic=*` tags)
4. **Web directory + Nominatim geocoding** — scrape the city's landmark list, geocode addresses, commit the resulting CSV (see Burlington pattern above)

**Paris:** The best source is the national Monuments Historiques registry filtered to Paris (dept 75), hosted on the Île-de-France regional open data platform — not opendata.paris.fr. It has ~1,885 officially classified/listed monuments, parquet export, and point coordinates.

```
Dataset: immeubles-proteges-au-titre-des-monuments-historiques
Platform: data.iledefrance.fr  (OpenDataSoft v2 — same API as opendata.paris.fr)
Parquet export URL:
  https://data.iledefrance.fr/api/explore/v2.1/catalog/datasets/
  immeubles-proteges-au-titre-des-monuments-historiques/exports/parquet
  ?where=departement_format_numerique%3D%2275%22
  &select=reference,titre_editorial_de_la_notice,adresse_forme_editoriale,
          commune_forme_editoriale,date_et_typologie_de_la_protection,
          denomination_de_l_edifice,coordonnees_au_format_wgs84
Metadata URL (for probe):
  https://data.iledefrance.fr/api/explore/v2.1/catalog/datasets/
  immeubles-proteges-au-titre-des-monuments-historiques
  → .metas.default.modified
```

Key field mapping for Paris:
- `reference` → `landmark_id` (prefix `"frpar-"`)
- `titre_editorial_de_la_notice` → `name`
- `coordonnees_au_format_wgs84` (struct `{lon, lat}`) → construct `POINT(lon lat)` as `geometry_raw`
- `commune_forme_editoriale` → `arrondissement` (Paris-specific)
- `date_et_typologie_de_la_protection` → `protection_type` (Paris-specific, e.g. `"1862 : classé MH"`)
- `denomination_de_l_edifice` → `denomination` (Paris-specific, e.g. `"immeuble"`, `"église"`)

### Files to Create

```
data/raw/{city}/{city}_landmarks.py          ← fetch + transform (same Arrow IPC pattern)
data/raw/{city}/{city}_landmarks_probe.py    ← freshness probe (same pattern as tree probe)
data/raw/{city}/{city}_landmarks.preql       ← datasource with versioned GCS URL
```

**If the source is Overpass, stage it instead** — the same decoupling the OSM
tree extracts use, for the same reason:

```
data/raw/{city}/{city}_landmarks_extract.py  ← queries Overpass, publishes the staging parquet to GCS
gs://…/duckdb/staging/{code}_landmarks_staging.parquet  ← the refresh only ever reads this
data/raw/{city}/{city}_landmarks_probe.py    ← emits the staged object's publication time
```

Overpass allows **two concurrent slots per client IP** (`GET /api/status`
reports them), and answers an over-budget request with HTTP 200 carrying either
an HTML page or a body whose `remark` is a `runtime error` — never a 4xx/5xx.
London and Berlin both fetched at refresh time, which meant a full refresh with
`parallelism = 3` could put three Overpass callers in flight against those two
slots and fail a city on a transient: `london_landmark_info` died that way and
took `full_landmark_info` with it as a failed dependency, while the same script
run alone finished in **6.8s**. The query was never the problem; the concurrency
was.

Staging removes Overpass from the refresh path entirely, and re-running the
extract becomes what marks the city stale — the OSM watermark alternative (the
global database timestamp) advances every minute and would rebuild the city on
every tick. Publish the staged parquet to GCS rather than committing it; see
the watermark note above for why a committed copy reintroduces that same thrash.

```bash
cd data/raw && uv run {city}/{city}_landmarks_extract.py   # publishes to GCS; nothing to commit
```

Add the city's landmark freshness property and update the `greatest()` expression in **`data/raw/landmark_common.preql`**:

```preql
property <*>.{city}_landmark_data_updated_through datetime;
auto latest_landmark_update_through <- greatest(..., {city}_landmark_data_updated_through);
```

Add the import to **`data/raw/landmark_info.preql`**:

```preql
import {city}.{city}_landmarks;
```

The landmark preql follows the same versioned `f\`` URL pattern as tree files. See `paris_landmarks.preql` as the reference implementation.

---

## Species Enrichment

### How It Works

The `tree_enrichment_v{DATA_VERSION}.parquet` at GCS is city-agnostic. It maps **scientific species names** (Latin binomials only) to:
- `common_names` — comma-separated English common names, most familiar first
- `tree_form` — visual form (`broadleaf`, `conifer`, `palm`, `columnar`, `ornamental`, `spreading`, `weeping`, `multi_trunk`, `default`) used for icon and color
- Ecological metadata: `native_status`, `is_evergreen`, `mature_height_ft`, `bloom_season`, etc.

The browser worker joins on `t.species = se.species` (exact match on scientific name) and derives `common_name` as `split_part(se.common_names, ',', 1)` — the first enrichment common name, falling back to the scientific name if unenriched.

### Species Key Rule (Important)

The `species` field in all tree parquets **must be the scientific name only** (no `:: suffix`). This is what makes the single enrichment table work across all cities:

- SF raw data has `"Platanus x hispanica :: London Plane"` → strip to `"Platanus x hispanica"` in `sf_tree_info.py`
- Paris has separate `genre`/`espece` fields → concatenate to `"Platanus hispanica"`
- NYC/Boston already emit scientific names directly

If you add a city whose source data embeds a common name in the species field (any `::` pattern), strip it in the fetch script before emitting.

### Species hygiene is enforced centrally, not per city

`normalize_species` only fixes casing.  Deciding whether a value is a taxon at
all is `sanitize_species`, called for every city from `enforce_tree_schema`, so
a new city inherits it without doing anything.  It drops what is not a
scientific name — inventory placeholders (`Vacant`, `Unknown`, `Onbekend`,
`No identificado`, `Empty pit/planting site`), free-typed OSM tags (`Pin oak`,
`Serviceberry or dogwood?`), abbreviated genera (`Amel. laevis 'spring
flurry'`) — and truncates the rest to species rank, so
`Gleditsia triacanthos var. inermis` and `Prunus serrulata 'kwanzan'` collapse
onto the binomial the enrichment table is keyed on.  Applied to the published
data this cut distinct species 6,418 → 3,776 and the enrichment backlog
1,568 → 734 city/species pairs.

Both hybrid spellings are preserved verbatim: SF publishes
`Platanus x hispanica`, OSM publishes `Citrus × limon` (U+00D7).  Normalising
one into the other would orphan every already-enriched hybrid, so don't.

Anything that survives as a non-taxon becomes a **sentinel**, never null — see
the next section for why.  `enforce_tree_schema` prints a per-ingest summary of
how many values it reshaped, so a run that rewrites a tenth of its species
column says so in the refresh log rather than doing it quietly.

Most non-taxa merge into `UNKNOWN_SPECIES` (`"Unknown"`), but a value that
names a *growth form* keeps it: `Palm`, `Shrub` and `Cactus` are their own
sentinels, because the form is what the map icon and colour are chosen from and
merging throws away the one fact the source did record.
`_FORM_SENTINEL_ALIASES` carries the multilingual spellings (`arbusto`,
`struik`, `palmera`, …); the full set is `SPECIES_SENTINELS`, and a new
sentinel added there needs a matching entry in `src/src/data/species.ts`.

### Sentinels are excluded from enrichment, and purged from it

A sentinel is not a taxon, and `species` is the join key into the enrichment
table, so an enrichment row for one is not inert — it labels **every** tree
carrying that value.  This is not hypothetical.  A row keyed `Unknown` was
enriched in April 2026 and came back as *Orania timikae*, a critically
endangered single-stemmed palm from the heath forests of western New Guinea:

```
species='Unknown'  genus='Unknown'  tree_form='palm'
description='Orania timikae is a small, single-stemmed palm reaching up to 4
             meters tall, distinctive for its subdistichous crown…'
photo_url=<an iNaturalist photo of a palm>
```

Once `UNKNOWN_SPECIES` adopted the same string, that one row labelled **189,139
trees across all fourteen cities** — 67.6k in LA, 37.9k in NYC, 27.9k of
Boston's OSM rows — each rendered with a palm icon, a palm photo, and a
description of an endangered New Guinea palm.

Two mechanisms, and you need both:

- **Exclusion** keeps a sentinel out of the enrichment queue.  It lives in
  `SKIP_SPECIES` / `SPECIES_EXCLUSION_SQL` (`enrichment/_tree_shared.py`),
  which are now derived from `SPECIES_SENTINELS` so the two cannot drift.  A
  new *non-sentinel* placeholder value belongs in `SKIP_SPECIES` directly.
- **Purge** removes a row that is already there.  Exclusion alone does not:
  `get_already_enriched` reads whatever the Parquet holds and
  `merge_with_existing` concatenates it forward, so a row that got in before
  the exclusion existed survives every run for ever.  `purge_non_taxa` runs
  inside `load_existing_table`, so the removal lands on the next enrichment run
  whether or not any new species were processed.

Presentation for the sentinels is **hardcoded** in `src/src/data/species.ts` —
label, `tree_form`, and the note shown where a description would go — rather
than fetched.  Asking a model to describe "Palm" does not fail loudly; it
returns a plausible, specific and wrong species, which is exactly how this
happened.  The worker applies the label and form once in `trees_fast`, and
`REAL_SPECIES_PREDICATE` keeps all four sentinels out of every species rollup.

### Mark a nullable column `?` or Trilogy will silently drop rows

A datasource column declared without `?` is non-nullable, and Trilogy generates
plain `=` joins for it.  `NULL = NULL` is never true, so **every row with a
null in that column vanishes from the materialised Parquet, with no error**.

Boston is the worked example.  Its dbh imputation
(`auto processed_dbh <- coalesce(_cleaned_db, avg(_cleaned_db) by city, species)`)
compiles to a join of the species-average CTE back onto the rows:

```sql
INNER JOIN "abhorrent" on "macho"."city" = "abhorrent"."city"
                      AND "macho"."species" = "abhorrent"."species"
```

Boston's municipal sources always carry a species, so this was invisible for
months.  Wiring OSM exposed it: OSM `natural=tree` nodes are ~99% species-less,
and the first rebuild wrote **349 of 28,163** OSM rows.  Declaring
`spp_bot: ?species` (and the same on every other Boston datasource) changes the
generated predicate to `is not distinct from`, which matches null to null:

```sql
AND "macho"."species" is not distinct from "abhorrent"."species"
```

Boston is currently the only city with a species-keyed aggregate, so it is the
only one that needed it — but the rule is general.  If a city adds an aggregate
keyed on a column that any of its sources can leave empty, mark that column
`?` in **every** datasource that maps it, and check the rendered SQL:

```bash
cd data && trilogy refresh raw/{city}/{city}_tree_info.preql -f {city}_tree_info --dry-run
```

Row counts are the cheap tell — compare each `data_source` partition in the new
Parquet against the source row count before assuming a rebuild succeeded.

### After Adding a New City

Run the enrichment probe to measure coverage:

```bash
cd data/raw && uv run tree_enrichment_probe.py
```

It prints `true` if every species has complete enrichment, `false` + a list of missing species otherwise. Then run `tree_enrichment.py` to fill in missing entries (this calls the LLM — costs money, runs slowly):

```bash
cd data/raw && uv run tree_enrichment.py --limit 50 --output tree_enrichment.parquet
```

---

## What to Optimize / Streamline Next Time

### 2. `core.preql` City Enum Is Easy to Miss
**Problem:** The `city` key in `data/raw/core.preql` is a typed enum. Forgetting to add the new code there causes Trilogy to reject every `complete where city = '...'` clause in the new preql files — but the error message points at the preql files, not `core.preql`.
**Suggestion:** Do this as step 3 (it already is), and double-check it's the very first edit before creating any other files.

### 3. Parquet Column Types Drift Silently
**Problem:** Trilogy does not coerce a raw script's Arrow types to the types declared in `tree_common.preql` — whatever the script emits is what lands in the parquet. Inference-driven types (`pa.null()` for an all-null column, `int64` for a whole-number CSV column) therefore produce parquets that disagree with the model and with each other, and the failure only surfaces as a DuckDB binder error in the browser, one city at a time.
**Suggestion:** Never rely on inference for a canonical column. `enforce_tree_schema` is called by every tree ingest script before `emit` and is the place to add any new canonical column — add it to `TREE_COLUMN_TYPES` and every city is covered at once. To audit the live parquets, `DESCRIBE SELECT * FROM read_parquet(...)` each city's GCS file and diff the column types.

