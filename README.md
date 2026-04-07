#  Urban Tree Reporting

An interactive browser-based map of city trees. Explore hundreds of thousands of street trees by location, species, and ecological attributes.

Urban forests are a key part of intentional live city design - they can reduce city heat, absorb runoff, improve air quality, and enhance the mental health of residents. Read more [here](https://www.climatecentral.org/climate-matters/the-power-of-urban-trees-2023).

Plus they look nice. 

There are several projects that collect open tree data; this one aims to be more exploratory and fun. Raw data is available in parquet. (and of course,
also from original sources.)

Currently has data from the following cities:
- [San Francisco, CA, United States](https://greenmtnboy.github.io/sf_tree_reporting/#/?city=USSFO)
- [New York City, NY, United States](https://greenmtnboy.github.io/sf_tree_reporting/#/?city=USNYC)
- [Boston, MA, United States](https://greenmtnboy.github.io/sf_tree_reporting/#/?city=USBOS)
- [Burlington, VT, United States](https://greenmtnboy.github.io/sf_tree_reporting/#/?city=USBTV)
- [Vancouver, Canada](https://greenmtnboy.github.io/sf_tree_reporting/#/?city=CAVAN)
- [London, United Kingdom](https://greenmtnboy.github.io/sf_tree_reporting/#/?city=GBLON)
- [Amsterdam, Netherlands](https://greenmtnboy.github.io/sf_tree_reporting/#/?city=NLAMS)
- [Berlin, Germany](https://greenmtnboy.github.io/sf_tree_reporting/#/?city=DEBER)
- [Melbourne, Australia](https://greenmtnboy.github.io/sf_tree_reporting/#/?city=AUMEL)
- [Paris, France](https://greenmtnboy.github.io/sf_tree_reporting/#/?city=FRPAR)

**[Live page](https://greenmtnboy.github.io/sf_tree_reporting/)**

---

## What it does

Shows you the trees of a city on a map. Typically these will be a subset of all trees; private land is often excluded. London, for example,
has about a million trees in their dataset and estimates over 4 million total in the city. 

### Map Page

Pulls in trees onto a navigable map. 

Clicking a tree opens a popup with its common name, species, planting date, trunk diameter,and enriched species data (native status, evergreen status, mature height, bloom season, wildlife value, fire risk, etc, etc)

For supported cities you'll also get a landmark list with a search filter; clicking one flies the map camera to that location.

A chat panel to use with LLMs (bring your own API key or use a test key) lets you interact with the map in natural language - ask questions, fly around, or update what's shown.

---

## Data sources

### Tree inventories

| City | Source | Link |
|------|--------|------|
| San Francisco | SF Open Data Portal — Street Tree List | https://data.sfgov.org/City-Infrastructure/Street-Tree-List/tkzw-k3nq |
| New York City | NYC Open Data — Street Tree Census | https://data.cityofnewyork.us/ |
| Boston | Analyze Boston — Trees | https://data.boston.gov/ |
| Boston (Cambridge) | Cambridge Open Data — Trees | https://data.cambridgema.gov/ |
| Boston (Brookline) | Brookline GIS — Tree Viewer | ArcGIS FeatureServer |
| Boston (Arboretum) | Arnold Arboretum / Harvard GIS | https://gis.arboretum.harvard.edu/ |
| Burlington | City of Burlington GIS — Tree Sites | https://maps.burlingtonvt.gov/ |
| Vancouver | Vancouver Open Data — Public Trees | https://opendata.vancouver.ca/explore/dataset/public-trees/ |
| London | London Datastore — Public Realm Trees | https://data.london.gov.uk/dataset/2r45m |
| Amsterdam | Amsterdam Data Portal — Bomen (Stamgegevens) | https://api.data.amsterdam.nl/v1/bomen/stamgegevens/ |
| Berlin | Berlin Geodateninfrastruktur — Baumbestand (WFS) | https://gdi.berlin.de/services/wfs/baumbestand |
| Melbourne | Melbourne Open Data — Urban Forest Trees | https://data.melbourne.vic.gov.au/ |
| Paris | Paris Open Data — Les Arbres | https://opendata.paris.fr/explore/dataset/les-arbres/ |

### Landmarks

| City | Source | Link |
|------|--------|------|
| San Francisco | SF Open Data — Landmarks | https://data.sfgov.org/Geographic-Locations-and-Boundaries/Landmarks/rzic-39gi |
| New York City | NYC Open Data — Landmarks | https://data.cityofnewyork.us/ |
| Boston | Analyze Boston — Landmarks | https://data.boston.gov/ |
| Burlington | City of Burlington — State Register of Historic Places + Nominatim geocoding | https://www.burlingtonvt.gov/ |
| Vancouver | Vancouver Open Data — Heritage Sites | https://opendata.vancouver.ca/explore/dataset/heritage-sites/ |
| London | OpenStreetMap (Overpass API) — historic=* | https://overpass-api.de/ |
| Amsterdam | Amsterdam Data Portal — Monumenten | https://api.data.amsterdam.nl/v1/monumenten/monumenten/ |
| Berlin | OpenStreetMap (Overpass API) — historic=* | https://overpass-api.de/ |
| Melbourne | Melbourne Open Data — Landmarks and Places of Interest | https://data.melbourne.vic.gov.au/ |
| Paris | Ile-de-France Open Data — Monuments Historiques | https://data.iledefrance.fr/ |

### Species enrichment

Per-species attributes (native status, evergreen, mature height, canopy spread, growth rate, lifespan, drought tolerance, bloom season, wildlife value, fire risk, tree category, map icon) are pulled in from a combination of the following services:

- Wikipedia REST API and MediaWiki API
- Plants of the World Online (POWO / Kew) — powo.science.kew.org
- GBIF species APIs — api.gbif.org
- SelecTree (Cal Poly UFEI) APIs — selectree.calpoly.edu

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

https://github.com/greenmtnboy/sf_tree_reporting

## Similar

[Urban Forestry](https://bsm.sfdpw.org/urbanforestry/)

## Dev

### City Additions

Update ingest pipelines with city source.
- ingest watermark script
- data ingest script for trees
- optional - landmark script or empty CSV
- trilogy file + imports and merge

### Update Data

TODO: instructions that would work for anyone else. (GCS writes; bucket/locations would need to be parameterized)

```bash
trilogy refresh data\raw\tree_info.preql 
```
