# The data pipeline: what is load-bearing and why

The runbook for adding a city is `EXTENDING.md`; this file is the shorter
list of invariants the pipeline rests on, each with the failure it replaced.

## Where things live under `data/`

```
data/
  trilogy.toml            every [[cloud.job]], and the rationale for each cadence
  raw/
    *.preql               the core model: core, tree_common, tree_dedup, the
                          rollup and landmark publishers, enrichment, predictions
    *.py                  the scripts those models' datasources run -- the
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
                          the calibrators, the two model fits.  See its README.
    tests/                `pytest tests -q` from data/raw; offline, seconds
  osm_staging/            one thin model per city over the shared osm_rows.py
  landmark_staging/       the curated-CSV landmark publishers
```

`shared/` is the only one of those the workspace bundle ships whole. The CLI
drops `tests/` itself, and `tools/` and `enrichment/admin/` are excluded by
`[cloud] exclude` because no job runs them -- so a workstation script added to
`tools/` needs no new exclude entry, and one added beside the models does.

## Data ingest

The map's parquets are built by scheduled jobs on trilogy-cloud, declared as
`[[cloud.job]]` entries in `data/trilogy.toml` and deployed by
`.github/workflows/cloud-sync.yml` on merge to main. Read that file's header
before changing anything under `data/` — it carries the rationale for every
cadence — and `EXTENDING.md` for the city-addition runbook.

The shape, in one paragraph: **each city is an independent pipeline** with its
own three jobs (`osm-{code}` weekly extraction, `city-{code}` refresh on a
cadence matched to its portal, and a `landmarks-{code}` publish with no cron
where the landmarks are a curated CSV), and a **daily core** (`publish-full`,
`refresh-enrichment`, `refresh-predictions`, `validate-core`,
`refresh-ecoregions`, plus weekly `refresh-landmarks`) that reads only
published parquets.

Four things about that are load-bearing, and each replaced something that broke:

- **A job's bundle is its entrypoint's reachable imports.** `trilogy refresh`
  adopts every managed datasource it can reach, so what a model imports decides
  what a job builds, probes and needs memory for. A city model importing only
  `tree_common`, `community_tree_info` and the shared `tree_dedup` (which has
  no managed datasource of its own) is what makes `city-{code}` exactly
  one city. Check with `trilogy refresh --dry-run <entrypoint>`: more than one
  asset for a city job means an import reaches too far.
- **The core must not reach a portal.** `raw/full_tree_publish.preql` reads the
  published city parquets directly (one `file [...]` multi-file scan) and the
  enrichment job's entrypoint reaches the rollup through a root datasource;
  neither imports the city models. While enrichment imported `tree_info` it
  could rebuild any city's parquet inside its own container.
  `data/raw/tests/test_cloud_jobs.py` pins this.
- **The job's file set and the browser's are different on purpose.** The
  enrichment *job* runs `raw/enrichment_refresh.preql`, which imports the
  enrichment model plus `raw/full_tree_info_source.preql`; the *browser* bundles
  `raw/tree_enrichment.preql`, which is species-only. Collapsing the two puts a
  second tree source in the planner's scope and changes what the charts return —
  see the join-type bug under Dashboard query compilation below.
- **Cadence is measured.** `data/raw/tools/portal_cadence.py --record` samples every
  freshness probe and derives each portal's real publishing interval from the
  distinct watermarks it has recorded in `portal_cadence.json`. Do not retune a
  cron from a single observation.
- **A consumer of published data reaches it through a `_source.preql`.**
  `raw/full_tree_info_source.preql` and `raw/tree_enrichment_source.preql`
  are root (unmanaged) views of the rollup and the enrichment table, named
  the same as the managed datasources that write them; that shared name is
  the derived edge trilogy-cloud orders a tick by. `raw/tree_predictions.preql`
  imports both and nothing else, which is what makes `refresh-predictions`
  run after its two producers and never rebuild either. Neither source file
  may enter the frontend's model bundle (`test_tree_predictions.py`).
- **The core validates what it published.** `raw/core_validate.preql` runs
  `validate datasource` over the rollup and the enrichment table after both
  land, and fails the tick on a repeated key. A refresh proves what it
  builds, not what it built: the rollup carried 23,078 OSM ids twice, one per
  neighbouring city, and every city's own parquet was clean.
- **An unattributed tree is assigned by territory, not by sanity box.**
  `CITY_BOUNDS` is the generous box a municipal row must fall in;
  `CITY_TERRITORY` (both in `shared/ingest.py`) is the explicit,
  non-overlapping set of rectangles that decides which city an OSM node or a
  community submission belongs to. `test_city_territory.py` checks every
  pair; a city that gains a neighbour has to carve both territories.
- **A missing job is silent.** Nothing errors when a city has no schedule; its
  parquet simply stops updating. `test_cloud_jobs.py` is the only thing that
  catches it, so run `cd data/raw && uv run --with pytest python -m pytest tests -q`
  after touching the job table.

### Reviewed aerial-imagery detections

`SATELLITE_{CODE}` is a fourth source partition for the cities imagery has
been run over (SF and Boston; `SATELLITE_DATA_SOURCES` in
`shared/ingest.py`). The reviewer's `/satellite` page
(`reviewer/satellite.ts`) shows a NAIP tile with the model's detections and
the published inventory trees over it, each ringed by its predicted crown
width, and publishes accepted detections to
`satellite/published_trees.ndjson`, which `raw/satellite_tree_info.py` reads
the way the community ingest reads its export. `raw/tree_dedup.preql`
classes the rows below municipal and community and above OSM, so a city that
later publishes a tree the imagery found absorbs the detection into its own
row with the satellite id in `merged_tree_ids`; a reviewer-linked duplicate
is exported at the inventory tree's coordinates so the grid merge cannot miss
it. The model's DBH estimate is never published as a measurement. See
"Reviewed aerial-imagery detections" in `EXTENDING.md`.

### Tree-level predictions

`raw/tree_predictions.preql` publishes `tree_predictions_v{n}.parquet`: one
row per rollup tree with a predicted crown width, a stand-density covariate,
and null placeholders for height and age. The crown model is a genus-level
power law fitted on the open Tallo database by `raw/tools/crown_allometry_fit.py`,
committed as `raw/crown_width_coefficients.csv` and applied in DuckDB (no
script at refresh time but the freshness probe). Read the fit script's
docstring before touching the model: it records why Tallo, the quality gate,
the fallback order, and the two things the model deliberately is not
(urban-calibrated, density-adjusted). A tree with a planting date and no
diameter gets its diameter predicted from its age first -- a second
genus-level power law, `raw/tools/dbh_age_fit.py`, fitted on the rollup's own 1.9M
dated, measured trees and committed as `raw/dbh_age_coefficients.csv` --
and the crown model is applied to that. What is *not* a planting date is the
ingest's problem, not the model's: `enforce_tree_schema` nulls a date before
1500 or in the future, and a portal's stamped default (Edmonton's
1990-06-01, Melbourne's 1900-01-01) is nulled by that city's ingest. The map
does not read this parquet yet; see "Tree-level predictions" in
`EXTENDING.md` for the refit and the shape.

### Correcting a species by hand

`data/raw/enrichment/admin/server.py` is a localhost form over the enrichment table
for the case where a reviewer already knows the answer -- a photo of the wrong
plant, a description of the wrong taxon, a trait that is off -- and re-asking
the model (`enrichment/backfill.py`) is the long way round. Edits are staged
locally; **Publish** re-reads the live parquet, patches the staged rows in
(and every alias row of the taxon), uploads, and reads it back to verify, the same
path `tree_enrichment.py --limit` takes. An edited row carries today's
`enriched_at`, which is what keeps `refresh-enrichment` from overwriting it,
and the form refuses a row without a common name and a growth form because
the freshness probe would report the table stale for ever. Sentinel rows are
authored in `shared/ingest.py` and are read-only there, as is the alias row
of a name in `SPECIES_SYNONYMS` or `SPECIES_MISSPELLINGS` (the daily job
rewrites it from the accepted row). A duplicate species is merged from the
form by adding its name to the accepted row's `synonyms`; making the *ingest*
publish the accepted name takes a pair in `SPECIES_SYNONYMS` when Kew lists
one name under the other, and in `SPECIES_MISSPELLINGS` when the duplicate is
a name that does not exist -- `species_audit.py` decides which, and see
"Synonyms" and "Misspellings" in `EXTENDING.md`. Needs
`gcloud auth application-default login`; `tests/test_enrichment_admin.py`
pins the invariants. arborary.world shows the change on its next build.

### Adding a city

Do not hand-write the twenty-odd registry edits. `data/raw/tools/new_city.py` writes
the mechanical ones from a single spec and `data/raw/tests/test_city_wiring.py`
walks the same list and names anything still missing — the enum, the ecoregion
case, four sets of freshness properties, the cross-city imports and merges, the
rollup file list, the frontend config, the attribution. Almost every one of
those fails *silently* when it is skipped, which is why the sweep exists.
See the quick path at the top of `EXTENDING.md`.

Shared modules carry what used to be copied per city, and a new city should
reach for them before writing anything:

- **`shared/osm.py`** — the Overpass extraction every city's `osm-{code}` job
  runs, so a city's OSM wiring is one ~28-line shim.
- **`shared/platforms/arcgis.py`** — layer paging, three freshness watermarks, Esri's
  epoch-milliseconds, field domains, geometry-to-WKT, and a Hub catalogue
  search (`uv run shared/platforms/arcgis.py <hub-host>` lists a portal's tree layers).
  ArcGIS is what most North American cities publish on. Three details in it are
  correctness rather than convenience and were bugs in the copies it replaced:
  the page size comes from the layer's own `maxRecordCount` (asking for more is
  silently capped, and a capped page reads as the end of the data), paging
  terminates on `exceededTransferLimit` rather than the short-page heuristic,
  and `esri_point` refuses the *string* `"NaN"` that a server sends for a
  feature with no geometry.
- **`shared/platforms/socrata.py`** and **`shared/platforms/ckan.py`** — the same for the other
  two platforms this repo reads more than twice.
- **`shared/platforms/wfs.py`** — an OGC WFS 2.0 (GeoServer) reader for Copenhagen and
  Helsinki, whose trees and heritage registers are both on one: sorted
  `startIndex` paging terminated on `numberMatched`, plus `wfs_max_property`,
  the one-row descending-sort read that is both cities' freshness probe
  (nulls excluded explicitly — GeoServer sorts them first).
- **`shared/species/english.py`** — a curated common-name → accepted-binomial
  table, for the portals that publish an English name where the binomial should
  be. Not a platform module: what those cities shared was a question, not an
  API. Read its docstring before reaching for the enrichment table's inverse
  instead — that was tried, measured, and does not work, because the enrichment
  table carries the misspelled binomials the cities themselves published.
  `shared/species/japanese.py`, `shared/species/spanish.py` and `shared/species/chinese.py` are
  the same shape for Tokyo, Bogotá and Taipei — one module per language,
  because the keys normalise by different rules.

**Read a field's domain before deciding what a column holds.**
`coded_value_domain(layer, field)` has now answered three questions the column
names could not: Halifax's `DBH` is a size class whose bands the layer
publishes, Ajax's species symbols are named in English there, and Ottawa's
`SPECIES` — which stores `Maple Sugar`, `Oak Red` and reads exactly like a
common-name-only column — maps all 174 of its codes to the binomial. A layer
that looks like it does not identify its trees may be keeping the
identification in `fields[].domain`.

The judgement steps are deliberately left manual: the field mapping, the
freshness probe, the landmark source, and the dedup cell size — which is
calibrated per city with `osm_dedup_validation.py` and must never be copied
from another city.
