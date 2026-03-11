# SF Tree Reporting

An interactive browser-based map of San Francisco's urban forest with a chat assistant (free to try!). Explore hundreds of thousands of street trees by location, species, and ecological attributes.

Zoom around. Find interesting clusters.

**[Live page](https://greenmtnboy.github.io/sf_tree_reporting/)**

---

## What it does

The page loads San Francisco street tree records into an in-browser DuckDB instance and renders them on a MapLibre GL map with adaptive display.

Clicking a tree opens a popup with its common name, species, planting date, trunk diameter, site description, and enriched species data (native status, evergreen status, mature height, bloom season, wildlife value, fire risk).

The sidebar lists San Francisco landmarks with a search filter; clicking one flies the map camera to that location.

A chat panel (bring your own key or use a test key) lets you interact with the map in natural language - ask questions, fly around, or update what's shown.

---

## Data sources

**Tree inventory**
SF Open Data Portal — SF Street Trees dataset (ID: `tkzw-k3nq`)
https://data.sfgov.org/City-Infrastructure/Street-Tree-List/tkzw-k3nq

**Species enrichment**
Per-species attributes (native status, evergreen, mature height, canopy spread, growth rate, lifespan, drought tolerance, bloom season, wildlife value, fire risk, tree category, map icon) are pulled in from a combination of the following services.

- Wikipedia REST API and MediaWiki API
- Plants of the World Online (POWO / Kew) — powo.science.kew.org
- GBIF species APIs — api.gbif.org
- SelecTree (Cal Poly UFEI) APIs — selectree.calpoly.edu

Structured fields are extracted from that text using AI for the first pass - corrections very welcome. I will human edit as needed. 

**Landmarks**
SF Open Data Portal — Landmarks dataset
https://data.sfgov.org/Geographic-Locations-and-Boundaries/Landmarks/rzic-39gi/about_data

**Basemap**
CARTO Dark Matter vector tile style.

---

## Tech stack

| Layer | Library / Service |
|---|---|
| Framework | Vue 3 (TypeScript) |
| Build | Vite 6 |
| Map | MapLibre GL 4 |
| In-browser SQL | DuckDB WASM |
| State | Pinia |
| Routing | Vue Router 4 |
| Query language | Trilogy |

Data pipeline dependencies: Python 3.13, PyArrow, DuckDB, Pillow, instructor, google-genai.

---

## Repository

https://github.com/greenmtnboy/sf_tree_reporting

## Similar

[Urban Forestry](https://bsm.sfdpw.org/urbanforestry/)

## Dev

### Update Data

TODO: instructions that would work for anyone else. (does GCS writes; bucket/locations would need to be parameterized)

```bash
 trilogy refresh data\raw\tree_info.preql --env=.env
```
