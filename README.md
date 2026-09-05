# Urban Tree Reporting

An interactive browser-based map of city trees. Explore hundreds of thousands of street trees by location, species, and ecological attributes.

Urban forests are a key part of modern urban design - they can reduce city heat, absorb runoff, improve air quality, and enhance the mental health of residents. Read more [here](https://www.climatecentral.org/climate-matters/the-power-of-urban-trees-2023).

Plus they look nice! Charismatic megaplants.

There are several projects that collect open tree data; this one aims to be more exploratory and fun. Raw data is available in parquet, and of course also from the original sources.

Currently has data from the following cities:
- [San Francisco, CA, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USSFO)
- [New York City, NY, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USNYC)
- [Boston, MA, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USBOS)
- [Burlington, VT, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USBTV)
- [Washington, DC, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USWAS)
- [Los Angeles, CA, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USLAX)
- [Tempe, AZ, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USTEM)
- [Denver, CO, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USDEN)
- [Vancouver, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAVAN)
- [London, United Kingdom](https://greenmtnboy.github.io/tree_reporting/#/?city=GBLON)
- [Amsterdam, Netherlands](https://greenmtnboy.github.io/tree_reporting/#/?city=NLAMS)
- [Berlin, Germany](https://greenmtnboy.github.io/tree_reporting/#/?city=DEBER)
- [Melbourne, Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AUMEL)
- [Paris, France](https://greenmtnboy.github.io/tree_reporting/#/?city=FRPAR)
- [Buenos Aires, Argentina](https://greenmtnboy.github.io/tree_reporting/#/?city=ARBUE)
- [Athens, Greece](https://greenmtnboy.github.io/tree_reporting/#/?city=GRATH)
- [Milos, Greece](https://greenmtnboy.github.io/tree_reporting/#/?city=GRMLO)
- [Santorini, Greece](https://greenmtnboy.github.io/tree_reporting/#/?city=GRSAN)

**[Live page](https://greenmtnboy.github.io/tree_reporting/)**

---

## What it does

Shows you the trees of a city on a map. Typically these will be a subset of all trees; private land is often excluded. London, for example, has about a million trees in their dataset and estimates over 4 million total in the city.

### Map Page

Pulls in trees onto a navigable map.

Clicking a tree opens a popup with its common name, species, planting date, trunk diameter, and enriched species data (native status, evergreen status, mature height, bloom season, wildlife value, fire risk, etc.).

For supported cities you'll also get a landmark list with a search filter; clicking one flies the map camera to that location.

A chat panel to use with LLMs (bring your own API key or use a test key) lets you interact with the map in natural language - ask questions, fly around, or update what's shown.

---

## Data sources

### Tree inventories

| City | Source | Link |
|------|--------|------|
| San Francisco | SF Open Data Portal - Street Tree List | https://data.sfgov.org/City-Infrastructure/Street-Tree-List/tkzw-k3nq |
| New York City | NYC Open Data - Street Tree Census | https://data.cityofnewyork.us/Environment/2015-Street-Tree-Census-Tree-Data/uvpi-gqnh |
| Boston | Analyze Boston - Trees | https://data.boston.gov/dataset/bprd-trees |
| Boston (Cambridge) | Cambridge Open Data - Trees | https://data.cambridgema.gov/ |
| Boston (Brookline) | Brookline GIS - Tree Viewer | ArcGIS FeatureServer |
| Boston (Arboretum) | Arnold Arboretum / Harvard GIS | https://gis.arboretum.harvard.edu/ |
| Burlington | City of Burlington GIS - Tree Sites | https://maps.burlingtonvt.gov/ |
| Washington, DC | Open Data DC - Urban Forestry Street Trees | https://opendata.dc.gov/datasets/DCGIS::urban-forestry-street-trees |
| Los Angeles | Los Angeles Open Data - Street Tree Inventory - 1990s | https://data.lacity.org/api/views/vt5t-mscf |
| Tempe | City of Tempe - Tree Inventory | https://data.tempe.gov/datasets/tempegov::tree-inventory |
| Denver | Denver Open Data - Parks, Medians, and Parkway Trees | https://opendata-geospatialdenver.hub.arcgis.com/datasets/geospatialDenver::parks-medians-and-parkway-trees |
| Vancouver | Vancouver Open Data - Public Trees | https://opendata.vancouver.ca/explore/dataset/public-trees/ |
| London | London Datastore - Public Realm Trees | https://data.london.gov.uk/dataset/2r45m |
| Amsterdam | Amsterdam Data Portal - Bomen (Stamgegevens) | https://api.data.amsterdam.nl/v1/bomen/stamgegevens/ |
| Berlin | Berlin Geodateninfrastruktur - Baumbestand (WFS) | https://gdi.berlin.de/services/wfs/baumbestand |
| Melbourne | Melbourne Open Data - Urban Forest Trees | https://data.melbourne.vic.gov.au/explore/dataset/trees-with-species-and-dimensions-urban-forest/ |
| Paris | Paris Open Data - Les Arbres | https://opendata.paris.fr/explore/dataset/les-arbres/ |
| Buenos Aires | Buenos Aires Data - Arbolado publico lineal | https://data.buenosaires.gob.ar/dataset/arbolado-publico-lineal |
| Athens | City of Athens Open Data - Trees of the National Garden | https://opendata.cityofathens.gr/dataset/gis-athens-8303d4c8-371b-11ec-b388-0242ac120009 |
| Milos | Community submissions (no municipal inventory published) | (contributed via the app) |
| Santorini | Community submissions (no municipal inventory published) | (contributed via the app) |
| All cities (supplemental) | © OpenStreetMap contributors (ODbL) | https://www.openstreetmap.org/copyright |
| All cities | Community submissions, reviewed and approved before publication | (contributed via the app) |

Every tree row carries a `data_source` column identifying which of the above it came from.

### Landmarks

| City | Source | Link |
|------|--------|------|
| San Francisco | SF Open Data - Landmarks | https://data.sfgov.org/Geographic-Locations-and-Boundaries/Landmarks/rzic-39gi |
| New York City | NYC Open Data - Landmarks | https://data.cityofnewyork.us/Housing-Development/Individual-Landmark-Sites/buis-pvji |
| Boston | Analyze Boston - Landmarks | https://data.boston.gov/dataset/92137315-e846-4c75-8c3d-2b7e93e38d03 |
| Burlington | City of Burlington - State Register of Historic Places + Nominatim geocoding | https://www.burlingtonvt.gov/ |
| Washington, DC | No landmark dataset in production yet | n/a |
| Los Angeles | No landmark dataset in production yet | n/a |
| Tempe | No landmark dataset in production yet | n/a |
| Denver | Denver Open Data - Historic Landmark Structures | https://opendata-geospatialdenver.hub.arcgis.com/datasets/geospatialDenver::historic-landmarks |
| Vancouver | Vancouver Open Data - Heritage Sites | https://opendata.vancouver.ca/explore/dataset/heritage-sites/ |
| London | OpenStreetMap (Overpass API) - historic=* | https://overpass-api.de/ |
| Amsterdam | Amsterdam Data Portal - Monumenten | https://api.data.amsterdam.nl/v1/monumenten/monumenten/ |
| Berlin | OpenStreetMap (Overpass API) - historic=* | https://overpass-api.de/ |
| Melbourne | Melbourne Open Data - Landmarks and Places of Interest | https://data.melbourne.vic.gov.au/explore/dataset/landmarks-and-places-of-interest-including-schools-theatres-health-services-spor/ |
| Paris | Ile-de-France Open Data - Monuments Historiques | https://data.iledefrance.fr/ |
| Buenos Aires | No landmark dataset in production yet | n/a |
| Athens | Curated landmark list + Nominatim geocoding | https://nominatim.openstreetmap.org/ |
| Milos | Curated landmark list + Nominatim geocoding | https://nominatim.openstreetmap.org/ |
| Santorini | Curated landmark list + Nominatim geocoding | https://nominatim.openstreetmap.org/ |

### Species enrichment

Per-species attributes (native status, evergreen, mature height, canopy spread, growth rate, lifespan, drought tolerance, bloom season, wildlife value, fire risk, tree category, map icon) are pulled in from a combination of the following services:

- Wikipedia REST API and MediaWiki API
- Plants of the World Online (POWO / Kew) - powo.science.kew.org
- GBIF species APIs - api.gbif.org
- SelecTree (Cal Poly UFEI) APIs - selectree.calpoly.edu

:::info
Tree info has been automatically extracted from the available APIs per species label and may be inaccurate - corrections very welcome, especially by people that know trees. Don't cite this in your paper!
:::

### Species images

Tree species photos are sourced from the [iNaturalist API](https://api.inaturalist.org/v1/docs/). Images are licensed by their original photographers under Creative Commons licenses. [iNaturalist](https://www.inaturalist.org/) is a joint initiative of the California Academy of Sciences and the National Geographic Society.

### Basemap

CARTO Dark Matter vector tile style.

---

## Tech stack

If you're someone who cares:

| Layer | Library / Service |
|---|---|
| Framework | Vue 3 (TypeScript) |
| Build | Vite 6 |
| Map | MapLibre GL 4 |
| In-browser SQL | DuckDB WASM |
| State | Pinia |
| Routing | Vue Router 4 |
| Preprocessing/data access | Trilogy |

Data pipeline dependencies: Python 3.13, PyArrow, Pytrilogy, DuckDB, Pillow, instructor, google-genai. The fun stuff!

---

## Repository

https://github.com/greenmtnboy/tree_reporting

## Similar

[Urban Forestry](https://bsm.sfdpw.org/urbanforestry/)

## Dev

### City Additions

Each city is an independent ingest pipeline: its own freshness probe, its own
model, its own OSM extract, and its own three entries in `data/trilogy.toml`
that schedule them. Full runbook in [EXTENDING.md](EXTENDING.md); the short
version is

- ingest watermark script (`{city}_update_time.py`)
- data ingest script for trees (`{city}_tree_info.py`)
- optional - landmark script or empty CSV
- trilogy model (`{city}_tree_info.preql`) + its import in `raw/tree_info.preql`
  and its stub in `raw/full_tree_publish.preql`
- OSM extract model (`osm_staging/{code}_osm_staging.preql`; the row script
  `osm_staging/osm_rows.py` is shared and selects the city from a `where` clause)
- three `[[cloud.job]]` entries in `data/trilogy.toml`: `city-{code}`,
  `osm-{code}`, and a `landmarks-{code}` if the landmarks are a curated CSV
- update `README.md` links/attribution and `src/src/data/sourceCatalog.ts` so the info page stays in sync

`data/raw/tests/test_cloud_jobs.py` fails if a city is missing a job, so a
half-added city is a red test rather than a parquet that quietly never
rebuilds.

### Update Data

The scheduled refresh runs on trilogy-cloud — `data/trilogy.toml` declares one
job per city plus a daily core, and `.github/workflows/cloud-sync.yml` deploys
them on merge to main. Nothing needs to run locally for data to stay current.

To rebuild one city by hand (GCS writes; bucket/locations would need to be
parameterized for anyone else):

```bash
cd data && trilogy refresh raw/ussfo/sf_tree_info.preql
```

or fire the deployed job, which needs no local Google credential at all:

```bash
trilogy cloud jobs run urban-tree-city-ussfo --wait
```

How often each portal actually publishes, measured rather than assumed:

```bash
cd data/raw && uv run ./portal_cadence.py --record
```
