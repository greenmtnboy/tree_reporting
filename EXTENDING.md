# Extending the Tree Map: Adding a New City

The runbook. Each step is the rule and the check. The pipeline reference is
`docs/DATA_PIPELINE.md`; landmarks are `docs/LANDMARKS.md`; species handling
is `docs/SPECIES_ENRICHMENT.md`.

## The quick path

```bash
# 1. Find the portal's tree layer. For any ArcGIS Hub site, this is one command:
cd data/raw && uv run shared/platforms/arcgis.py opendata-geospatialdenver.hub.arcgis.com

# 2. Look up the ecoregion at the city centroid (RESOLVE ECO_ID):
curl -sG "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/Resolve_Ecoregions/FeatureServer/0/query" \
  --data-urlencode "geometry=-104.9903,39.7392" --data-urlencode "geometryType=esriGeometryPoint" \
  --data-urlencode "inSR=4326" --data-urlencode "spatialRel=esriSpatialRelIntersects" \
  --data-urlencode "outFields=ECO_ID,ECO_NAME" --data-urlencode "returnGeometry=false" --data-urlencode "f=json"

# 3. Scaffold every registry edit and every boilerplate file:
cd data/raw && uv run tools/new_city.py \
    --code USDEN --name Denver --slug denver \
    --center 39.7392,-104.9903 \
    --bounds 39.45,39.95,-105.65,-104.55 \
    --source-label DENVER_OPENDATA \
    --ecoregion 402 \
    --city-cron "0 40 15 * * SUN,WED" --osm-cron "0 30 2 * * SAT"
```

`--dry-run` lists the edits first. The scaffolder is idempotent and stages
everything in memory, so a failure leaves the tree untouched.

**Then the four things it does not do**, each needing a measurement or a look at the portal:

1. **Fill in `{slug}_tree_info.py` and `{slug}_update_time.py`** (steps 4 and 5).
2. **Find a landmark source** and write `{slug}_landmarks.py` plus its probe (Landmarks below).
3. **Bootstrap the OSM staging object**: `uv run {slug}/{slug}_osm_extract.py`.
4. **Calibrate the dedup cell size**: `uv run tools/osm_dedup_validation.py --city {CODE}`.
   Never copy one. Before the first cloud build pass `--osm-parquet` and
   `--inventory-parquet` to calibrate against local files.

Then the checks:

```bash
cd data/raw && uv run --no-project --with pytest --with pyarrow --with pytrilogy --with duckdb --with requests python -m pytest tests -q
cd data && trilogy refresh --dry-run raw/{code}/{slug}_tree_info.preql
cd data && trilogy refresh --dry-run osm_staging/{code}_osm_staging.preql
```

`test_city_wiring.py` walks every registry a city must appear in and names
anything missing. Each dry run must report **exactly one** asset; more means
an import reaches too far.

## City codes

Five uppercase letters: ISO 3166-1 alpha-2 country plus a three-letter city
abbreviation (`USSFO`, `USNYC`, `FRPAR`, `CAVAN`, `GRSAN`). Prefer a readable
abbreviation over the IATA code, since the code appears in URLs. Parquets use
the lowercase code: `frpar_tree_info_v{n}.parquet`.

## The shape you are adding to

Each city is an independent pipeline of three jobs in `data/trilogy.toml`:
`osm-{code}` (weekly Overpass extract to a staging parquet), `city-{code}`
(the refresh: portal plus community plus staged OSM, deduplicated, published
as `trees/{code}_tree_info_v{n}.parquet`) and `landmarks-{code}` (no cron
when the source is a curated CSV). A daily core reads only published
parquets and never a portal. The browser reads the parquets with DuckDB-WASM
and never touches raw data.

Rules that hold while wiring:

- **A city model's imports decide its job's bundle.** Import `tree_common`,
  `community_tree_info` and `tree_dedup` only (plus `tree_position` for a
  city with an Overture lookup, `docs/POSITION_CORRECTION.md`); never another
  city or the cross-city merge.
- **`data_source` is a per-city enum key**, and every raw source claims
  `complete where city = 'X' and {code}_source = 'Y'`. A source claiming the
  whole city alone silently drops the other partitions' rows.
- **`complete where` asserts; `where` filters.** The shared community and
  satellite ingests return every city, so each such partition also carries
  `where city = 'X'` after its file clause.
- **Staged parquets live in GCS, never in git.** Their watermark is the
  object's publication time.

## Step by step

### 1. Understand the source

You need a stable unique id, a scientific-name species field, WGS84
coordinates, and a diameter or a proxy (circumference in cm converts as
`dbh_in = circ_cm / (pi * 2.54)`). Missing fields are fine as null.

**The id is the grain; verify it before committing to it.** Preference: a
publisher-guaranteed per-feature id (`GLOBALID` on ArcGIS); then the source's
own asset id once checked unique and non-null over the whole layer; then
`OBJECTID` as a last resort. Never a field that merely sounds like an id, and
never a positional hash. `enforce_tree_schema` refuses a repeated or null
`tree_id`; drop unidentified rows in the ingest with a logged count.

**Read a coded field's domain before deciding what a column holds**
(`coded_value_domain(layer, field)`). A column that looks like common names
or size classes may map every code to the binomial or to published bands.

### 2-3. Register the code

The scaffolder does this: the `city` enum in `core.preql`, the source label in
`MUNICIPAL_DATA_SOURCES`, `CITY_BOUNDS` (the generous sanity box) and
`CITY_TERRITORY` (the non-overlapping rectangles that decide which city an
OSM node or community submission belongs to). **If the city has a neighbour on
the map, carve both territories along the real boundary**; the scaffolder
writes the envelope, and `test_city_territory.py` fails on an overlap.

### 4. The freshness probe (mandatory)

`{slug}_update_time.py` emits one row with the portal's last-modified time via
`emit_freshness("{CODE}", fetch_modified_at)`, fetching with
`get_json_with_retry` or the platform module's watermark helper. Without it
the city re-downloads on every tick.

- Availability problems degrade, never raise: the helpers classify a non-JSON
  200, a provider error envelope and a timeout as `UpstreamUnavailable`, and
  `emit_freshness` then emits the epoch, so the city sits out one tick.
- A schema change must raise (`RuntimeError`), or the city freezes silently.
- One `auto {code}_data_updated_through` per city in `tree_common.preql`. A
  city with several sub-sources coalesces them with `greatest()` into that one
  property, and only that property goes in `freshness by`.
- Check the portal's maintenance window before choosing a tick.

### 5. The fetch script

`{slug}_tree_info.py` downloads, maps to the canonical schema (`tree_id`,
`city`, `species`, `cultivar`, `plant_date` as `date32`, `latitude`,
`longitude`, `diameter_at_breast_height` in inches), calls
`enforce_tree_schema(table, city=..., data_source="{LABEL}_OPENDATA",
columns={...})` last, and writes an Arrow IPC stream to stdout.

- **Use the platform module**: `shared/platforms/arcgis.py`, `socrata.py`,
  `ckan.py`, `wfs.py`. They own paging, watermarks and geometry handling
  (page size from `maxRecordCount`, termination on `exceededTransferLimit`,
  the string `"NaN"` for a missing geometry). OpenDataSoft has no module.
  Write a shared module (platform or common-name table) when the third
  city needs it, not the first.
- **Types are enforced, not inferred.** `enforce_tree_schema` casts every
  canonical column and raises on a lossy cast. An all-null `plant_date` is
  still `date32`.
- **`species` is the scientific name only.** Strip a `:: common name`
  suffix; concatenate genus and epithet; leave a quoted cultivar in (the
  schema moves it to `cultivar`). A portal that publishes common names gets a
  `shared/species/{language}.py` table (English, Japanese, Spanish and
  Chinese exist); curate it against POWO, never derive it from the
  enrichment parquet.
- **Null a portal's stamped default planting date** in the ingest
  (`PLACEHOLDER_PLANT_DATE`). `enforce_tree_schema` already nulls dates
  before 1500 or in the future, and diameters that are zero, negative or
  over 200 inches.
- **A coded numeric field needs a parse-failure guard**: count the strings
  you could not read and refuse to publish above 1% of rows.
- Rows describing an empty site (`Vacant`, `Stump`, a planting site) are
  dropped by the shared rule; `Unknown` and `Dead` are trees and stay. That
  rule reads `species`, so a portal that records site status in a column of
  its own (Cambridge's `sitetype`) drops those rows in its ingest.

### 6. The city model

`{slug}_tree_info.preql`: the probe datasource, the `{code}_source` enum
(`{LABEL}_OPENDATA`, `COMMUNITY_{CODE}`, `OSM_{CODE}`), one root partial
datasource per partition mapping onto the shared `raw_*` concepts (never the
canonical ones; the dedup merge derives those), the staged-OSM partition,
`import ..tree_dedup;`, the dbh and position merges (`merge merged_dbh
into diameter_at_breast_height;`, `merge merged_latitude into latitude;`,
`merge merged_longitude into longitude;`), and the published target with
`complete where city = '{CODE}'`, `where tree_id = cluster_id`, and
`freshness by {code}_published_data_updated_through`. The scaffolder writes
this file; verify the dry run's SQL contains a `UNION ALL` and references
`community_tree_info.py`. A new city starts without the Overture position
correction; opting in is a separate step (`docs/POSITION_CORRECTION.md`).

**Mark any column a source can leave empty `?`** in every datasource that
maps it; an unmarked column joins with `=` and drops null rows without an
error. Compare each partition's row count against the source before calling
a rebuild good.

### 7-8. Cross-city registration and the OSM model

`tree_info.preql` gets the import and `merge {code}_source into
data_source;`. `full_tree_publish.preql` gets one line in its file list
(`test_rollup_reads_every_city`). `osm_staging/{code}_osm_staging.preql` is
the thin per-city extract model over the shared `osm_rows.py`, selected by
`where city = '{CODE}'`. Add the city to `OSM_DATA_SOURCES`, give it a
`DEDUP_CELL_METRES` row (start at 10, then measure), and run
`uv run tools/dedup_cells.py --write`. A city-unique column (`borough`) must
be declared on the OSM partition too (`OSM_EXTRA_NULL_COLUMNS`).

### 9. Schedule it

Three `[[cloud.job]]` entries in `data/trilogy.toml`. Nothing errors when
one is missing; `test_cloud_jobs.py` is the check.

- Write the day of week as a name (`SUN,WED`). The parser numbers days 1-7
  from Sunday, so a numeric day fires a day early, silently.
- Start in the twice-weekly tier (the day after the city's OSM extract and
  mid-week). Measure with `tools/portal_cadence.py --record --city {CODE}`
  over a few weeks and move to daily only if the portal moves daily. Twice
  weekly is the floor regardless, because approvals and the weekly OSM
  extract also make the parquet stale.
- Pick an OSM minute nothing else uses; extracts are thirty minutes apart
  and never concurrent (`test_osm_extract_jobs_never_fire_together`).
  Overpass answers an over-budget request with a 200 that looks like a city
  with no trees.

### 10-11. Frontend and attribution

`src/src/cityConfig.json` gets `"USDEN": { "name": "Denver", "center":
[lon, lat] }` (longitude first; `test_city_is_in_the_frontend_config`
catches a swap). That entry adds the city to the picker, the worker's parquet
URL, the chat's model glob and the `pnpm test:queries` sweep. Then
`README.md` and `src/src/data/sourceCatalog.ts` for attribution (OSM data is
ODbL: credit OpenStreetMap contributors), and a display label in
`src/src/data/dataSources.ts`.

## Community-only cities

A city with no municipal inventory has an empty `MUNICIPAL_DATA_SOURCES`
tuple, no municipal script or probe, and a published watermark over the
community and OSM columns only. A city with **exactly one** partition must
also pin that enum value on the published target's `complete where`, because
a lone partial source is never a union candidate. Start from
`grsan/santorini_tree_info.preql`.

## Landmarks (mandatory)

Every city needs `{slug}_landmarks.preql` even if it yields zero rows;
`landmark_common.preql` gets the freshness property and `landmark_info.preql`
the import. Schema: `landmark_id` (prefixed), `city`, `name`, `geometry_raw`
(WKT), `latitude`, `longitude`; city-specific extras are declared in
`landmark_common.preql`.

Source preference: an official designation registry; a heritage dataset on
the trees' own portal; Overpass, **staged** and never fetched at refresh
time; a web directory geocoded with Nominatim into a committed CSV, published
by an uncronned `landmarks-{code}` job (`landmark_staging/`). Prefer the last
pattern for any curated CSV.

**Build a new city's landmark parquet from its own preql**, never from
`landmark_info.preql`, and check the row count against the script's output:

```bash
cd data && trilogy refresh raw/{code}/{slug}_landmarks.preql -f {slug}_landmark_info
```

## Species rules an ingest must respect

- `species` is the join key into one city-agnostic enrichment table, so it
  must be the accepted binomial. `sanitize_species` (inside
  `enforce_tree_schema`) truncates to species rank, folds `SPECIES_SYNONYMS`
  and `SPECIES_MISSPELLINGS`, emits ASCII `x` for a hybrid, and turns
  non-taxa into sentinels (`Unknown`, `Palm`, `Shrub`, `Cactus`, `Dead`),
  never null.
- Never curate the synonym or misspelling maps by eye or by tree count:
  `tools/species_audit.py` asks POWO about every near pair. A name merely
  misapplied in the trade is not a synonym.
- A sentinel or a chimera (a real genus welded to another species' epithet)
  is never enriched; the exclusion and purge lists derive from
  `SPECIES_SENTINELS` and `CHIMERA_SPECIES`.
- The enrichment job runs daily from `main`, so a change to the shape of
  the published table is not done until it merges.

## Data versioning

`data_version` in `data/raw/core.preql` and `TREE_DATA_VERSION` /
`LANDMARK_DATA_VERSION` in `src/src/workers/parquetUrls.ts` move together.
Bump only for a change that breaks an existing reader (a type change, a
removed column). Additive columns stay on the current version, because a bump
re-materialises every parquet, including the LLM-backed enrichment table.
Refresh before you deploy either way.

## After adding a city

```bash
cd data/raw && uv run --no-project --with pytest --with pyarrow --with pytrilogy --with duckdb --with requests python -m pytest tests -q
cd data && trilogy refresh --dry-run raw/{code}/{slug}_tree_info.preql        # one asset
cd data && trilogy refresh --dry-run osm_staging/{code}_osm_staging.preql     # one asset
cd data/raw && uv run tree_enrichment_probe.py                                # lists species needing enrichment
```

To build the city in the cloud before the PR merges, push it as a throwaway
job rather than syncing the branch. The worker executes the bundle as a
directory, so exclude every other city and every root model the city does
not import:

```bash
trilogy cloud --org trilogy-data jobs push --source data --config adhoc.toml \
    --name adhoc-city-{code} --operation refresh --memory-mb 2048 \
    --secret-env GOOGLE_HMAC_KEY --secret-env GOOGLE_HMAC_SECRET \
    --exclude "raw/tests/*" --exclude "raw/enrichment/*" --exclude "raw/tools/*" \
    --exclude "osm_staging/*" --exclude "landmark_staging/*" \
    --exclude "raw/{every other city}/*" \
    --exclude raw/tree_info.preql --exclude raw/landmark_info.preql
trilogy cloud --org trilogy-data jobs run adhoc-city-{code} --wait --logs
trilogy cloud --org trilogy-data jobs delete adhoc-city-{code}
```

A new column does not make a parquet stale. Force each city with
`trilogy refresh raw/{code}/{slug}_tree_info.preql -f {slug}_tree_info`, San
Francisco first (it heads the rollup's file list), then run
`urban-tree-full`. A just-added city stays broken in a deploy until its first
refresh has published; `parquetSchema.test.ts` treats the 404 as a pass, so
a green build does not prove the parquet exists.
