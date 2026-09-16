# The data pipeline

What builds the parquets the map reads, and the rules that keep it correct.
The city-addition runbook is `EXTENDING.md`.

## Where things live under `data/`

```
data/
  trilogy.toml            every [[cloud.job]], with the reason for each cadence
  raw/
    *.preql               the core model: core, tree_common, tree_dedup, the
                          rollup and landmark publishers, enrichment, predictions
    *.py                  the scripts those models' datasources run: the
                          community and satellite ingests, the parquet probes
    *.csv                 the committed coefficients tree_predictions.preql reads
    {code}/               one directory per city: its ingest, its probes, its
                          tree and landmark models
    shared/               the shared ingest library: ingest, osm, ecoregions,
                          platforms/ (arcgis, ckan, socrata, wfs) and species/
                          (one common-name table per language)
    enrichment/           the LLM enrichment package, plus admin/ (the localhost
                          correction form) and backfill.py
    tools/                workstation-only scripts: the scaffolder, the audit,
                          the calibrators, the two model fits (see its README)
    tests/                `pytest tests -q` from data/raw; offline, seconds
  osm_staging/            one thin model per city over the shared osm_rows.py
  landmark_staging/       the curated-CSV landmark publishers
```

`shared/` ships whole in the workspace bundle. The CLI drops `tests/`;
`tools/` and `enrichment/admin/` are excluded by `[cloud] exclude` in
`trilogy.toml`, so a workstation script added to `tools/` needs no new
exclude entry and one added beside the models does.

## Jobs

Every job is a `[[cloud.job]]` in `data/trilogy.toml`, deployed by
`.github/workflows/cloud-sync.yml` on merge to main.

```
per city, three independent schedules
  osm-{code}         weekly, staggered      osm_staging/{code}_osm_staging.preql
  city-{code}        daily or twice weekly  raw/{code}/{slug}_tree_info.preql
  landmarks-{code}   no cron, by hand       landmark_staging/{code}_landmarks_staging.preql

the core, daily, reading only published parquets
  publish-full          raw/full_tree_publish.preql     -> full_tree_info_v{n}.parquet
  refresh-ecoregions    raw/ecoregion_info.preql
  refresh-enrichment    raw/enrichment_refresh.preql    -> tree_enrichment_v{n}.parquet
  refresh-predictions   raw/tree_predictions.preql      -> tree_predictions_v{n}.parquet
  validate-core         raw/core_validate.preql
  refresh-landmarks     raw/landmark_info.preql (weekly)
```

Rules:

- **A job's bundle is its entrypoint's reachable imports.** `trilogy refresh`
  adopts every managed datasource it can reach, so what a model imports
  decides what a job builds and how much memory it needs. Check with
  `trilogy refresh --dry-run <entrypoint>`: a city job must show one asset.
- **The core never reaches a portal.** `full_tree_publish.preql` reads the
  city parquets with one `file [...]` multi-file scan; the enrichment and
  prediction jobs reach published tables through root `_source.preql` views
  named the same as the managed datasources that write them. That shared
  name is the edge trilogy-cloud orders a tick by, so `refresh-predictions`
  runs after its two producers. `test_cloud_jobs.py` pins that the core
  imports no city model, and `test_tree_predictions.py` pins that no
  `_source.preql` enters the frontend bundle.
- **The job's file set and the browser's are different on purpose.** The
  enrichment job runs `raw/enrichment_refresh.preql`; the browser bundles
  `raw/tree_enrichment.preql`, which is species-only. A second tree source in
  the browser's scope changes join types and chart answers (`docs/TESTING.md`).
- **`validate-core` runs `validate datasource` over the rollup and the
  enrichment table after both land** and fails the tick on a repeated key.
- **Cadence is measured.** `tools/portal_cadence.py --record` samples every
  freshness probe into `portal_cadence.json` and derives each portal's real
  interval; its verdict column compares against the live cron. Do not retune
  a cron from one observation. Twice weekly is the floor for any city.
- **A missing job is silent.** `test_cloud_jobs.py` is the only thing that
  notices. Run the data tests after touching the job table.

## Partitions and the `data_source` column

Every tree row carries `data_source`. The value list is `DATA_SOURCES` in
`shared/ingest.py`; display labels are in `src/src/data/dataSources.ts`;
`tests/test_data_sources.py` keeps the picklist, the preql enums and the
`complete where` clauses in agreement.

- The concept is a **per-city enum key** (`ussfo_source`, ...) declared in
  each city model and aliased to the physical `data_source` column;
  `tree_info.preql` merges the keys for the cross-city view. Never add that
  merge to a city model.
- Each raw source claims `complete where city = 'X' and {code}_source = 'Y'`.
  A source claiming the whole city alone leaves no room for the others and
  they emit zero rows with no error.
- `complete where` asserts; `where` filters. Shared ingests that return every
  city (community, satellite) carry `where city = 'X'` after the file clause;
  Trilogy passes it to the script as `--filter`.
- A column any source can leave empty is marked `?` in every datasource that
  maps it, or the generated `=` join drops the null rows silently.
- `tree_id` is the grain. `enforce_tree_schema` refuses a repeated or null
  id. Drop unidentified rows in the ingest with a logged count; never
  synthesise an id from position.

### Sources

- **Municipal**: the city's portal, through `shared/platforms/`.
- **Community** (`COMMUNITY_{CODE}`): the reviewer publishes approved
  submissions to a public GCS export (`community/published_trees.ndjson`) and
  a manifest; `community_tree_info.py` and `community_update_time.py` read
  them. Freshness is one column per city, so an approval rebuilds that city
  alone. Photos are re-encoded and EXIF-stripped at the publish gate; private
  submission uploads are never made public. A missing export is zero rows,
  never an error.
- **OSM** (`OSM_{CODE}`, `OSM_DATA_SOURCES`): `shared/osm.py` extracts
  `natural=tree` nodes bounded by `CITY_TERRITORY`, publishes
  `staging/{code}_osm_staging.parquet`, and the city refresh reads only that
  object. The staging probe emits the object's publication time, so
  publishing a new extract is what makes the city stale. Extract jobs are
  thirty minutes apart and never concurrent: Overpass allows two slots per
  IP and answers over-budget with a 200 carrying an error remark.
  `circumference` without a unit above 10 is read as centimetres. A
  city-unique column goes in `OSM_EXTRA_NULL_COLUMNS` and the city's own
  staging model.
- **Satellite** (`SATELLITE_{CODE}`, `SATELLITE_DATA_SOURCES`): reviewed
  aerial-imagery detections published by the reviewer's `/satellite` page to
  `satellite/published_trees.ndjson`, read by `raw/satellite_tree_info.py`
  with one freshness column per city. The model's DBH estimate is never
  published as a measurement. A rejection is stored and never published.

### Territory versus bounds

`CITY_BOUNDS` is the generous box a municipal row must fall in.
`CITY_TERRITORY` is the explicit, non-overlapping set of rectangles that
decides which city an unattributed tree (OSM node, community submission)
belongs to. `test_city_territory.py` checks every pair; a city that gains a
neighbour has to carve both. Municipal ingests keep using the box.

## Deduplication (`raw/tree_dedup.preql`)

One shared cluster merge every city imports. Trilogy joins are equality
only, so the model derives a grid cell from lat/lon in four copies of a grid
staggered by half a cell; two points within half a cell share a cell in at
least one grid. Every row resolves to one canonical cluster id (municipal
first, then community, then satellite, then OSM), each attribute is picked
across the cluster by a value function (community first for photo and
person, most specific species name), and the survivor is the row whose
`tree_id` equals its cluster id. `merged_sources` and `merged_tree_ids`
record what it absorbed. The published target carries
`where tree_id = cluster_id`, so a parquet holds exactly one row per tree.

- The cell size is per city in `DEDUP_CELL_METRES`, rendered into the model
  by `tools/dedup_cells.py --write`. Calibrate with
  `tools/osm_dedup_validation.py --city CODE`, which reports the mutual
  nearest-neighbour rate per distance band and names the regime; flag a
  band only when it is clearly duplicate-dominated (above 60%), never on a
  coin flip. A missed duplicate double-renders a dot; a false flag hides a
  real tree.
- The merge cannot see a duplicate inside one source, because every
  municipal row is its own cluster. A portal feed that holds several
  inventories is resolved in its ingest (`usbos/cambridge_tree_info.py`).
  Once candidates are gated on species, mutual-NN stops separating
  duplicates from neighbours (distinct Cambridge trees score ~75% at every
  band to 15 m), so calibrate such a match against a distinct-tree control
  and read `d1/d2`.
- Needs pytrilogy 0.3.348 or later for the target-side `where`.
- The cross-city rollup does not carry the dedup columns, and
  `tree_info.preql` derives nothing.
- A city that imports the file but skips the `where` gate still builds,
  with counts that are simply high; `test_every_city_prunes_absorbed_rows`
  catches it.

## Rebuilding

Staleness is decided by the freshness probes, which watch the source data,
so a model change does not rebuild anything on its own. Force a city with
`trilogy refresh raw/{code}/{slug}_tree_info.preql -f {slug}_tree_info`,
then republish the rollup with `trilogy cloud jobs run urban-tree-full
--wait`. For a new column every partition maps: fire the `osm-{code}` jobs
two at a time (the city model reads the staged OSM parquet by column), force
each city with San Francisco first (it heads the rollup's file list), then
run `urban-tree-full`. DuckDB's multi-file scan takes its schema from the
first file and raises on a later file that lacks a column.

Data versioning: `data_version` in `core.preql` and the two constants in
`src/src/workers/parquetUrls.ts` move together, only for a change that breaks
an existing reader.

## Tree-level predictions (`raw/tree_predictions.preql`)

Publishes `tree_predictions_v{n}.parquet`, one row per rollup tree: a
predicted crown width (a genus-level power law fitted on Tallo, committed as
`crown_width_coefficients.csv`, refit with `tools/crown_allometry_fit.py
--write --coverage`), a diameter predicted from planting age where the tree
has a date and no diameter (`dbh_age_coefficients.csv`, refit with
`tools/dbh_age_fit.py --write`), a stand-density covariate, and null
placeholders for height and age. Each prediction records the model level
(genus, family, division, global) and sample size. The model is not
urban-calibrated and not density-adjusted; both are left to curation. The
map does not read this parquet yet.

Planner facts the model relies on: `**` is the power operator and there is
no `exp`, `ln` or `cos`; DuckDB's `greatest()` skips nulls; the enrichment
source is `root partial` so the join is FULL rather than INNER; a genus in
one coefficient table keeps its row from the other. `test_tree_predictions.py`
runs the model's SQL over fixtures and checks the CSVs against the fit gate.

## Correcting a species by hand

`data/raw/enrichment/admin/server.py` is a localhost form over the
enrichment table. Edits are staged locally; **Publish** re-reads the live
parquet, patches the staged rows and every alias row of the taxon, uploads
and reads back to verify. An edited row carries today's `enriched_at`, which
keeps the daily job from overwriting it. Sentinel rows and alias rows are
read-only. Needs `gcloud auth application-default login`;
`tests/test_enrichment_admin.py` pins the invariants. See
`docs/SPECIES_ENRICHMENT.md`.
