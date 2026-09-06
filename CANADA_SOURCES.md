# Canadian expansion: evaluating CIF Open Urban Forests

Findings from evaluating <https://www.openurbanforests.cif-ifc.org/> (Canadian
Institute of Forestry / Institut forestier du Canada) as a tree source, and the
per-city direct sources it points at. Measured 2026-09-06.

**Verdict: do not ingest CIF. Use it as a discovery index and go to the
municipal portals directly.** The reasons are structural, not cosmetic — each
one breaks a rule this repo already enforces.

---

## What CIF is

An aggregation of municipal tree inventories from Canadian municipalities over
50,000 population, normalised onto one schema. Its About page is explicit that
it took data from municipalities that "had Open Data Portals containing open
access urban forest geospatial data **or urban forest geospatial data they were
willing to share**" — so it is a superset of the open portals, and that matters
for the cities below where no portal could be found.

It covers **39 municipalities across 8 provinces, 4,364,093 trees**.

### The API

The site is a Vite SPA over one AWS Lambda function URL
(`https://7mcamsxgdxqium7gst4n64n7tm0bewvl.lambda-url.us-east-1.on.aws`):

| endpoint | returns |
|---|---|
| `/overview` | `{provinces: {province: [cities]}, genus_species: [...]}` |
| `/count?city=&species=&min_dbh_cm=&max_dbh_cm=` | `{"count": n}` |
| `/download/{shapefile\|geojson\|csv}?<same filters>` | bulk export |
| `/tiles/trees/{z}/{x}/{y}`, `/tiles/cities/...` | MVT |
| `/contribution` | POST, data-sharing enquiry form |

Bulk download columns: `botanical_genus_name, botanical_name,
common_genus_name, common_name_english, common_name_french, cultivar, address,
dbh_cm, location (WKT POINT), city, province, country`.

That is genuinely a good schema match — `botanical_name` is a clean Latin
binomial, `cultivar` is already split out the way `enforce_tree_schema` wants
it, and every row parsed a valid coordinate in the three cities sampled.

## Why it still fails as a source

**1. There is no `tree_id` in the bulk download.** `tree_id` is the declared
grain of every city datasource and `enforce_tree_schema` refuses a null or
duplicate one — see "`tree_id` is the grain" in `EXTENDING.md`. The CSV and
GeoJSON exports carry no id of any kind (GeoJSON features have no `id` member
either). A 20-hex-char `tree_id` *does* exist, but only as a property inside the
vector tiles, so the only way to obtain it is to scrape 4.36M trees out of MVT
at high zoom. It is not a derivable content hash — md5/sha1/sha256/sha512/blake2
over every plausible column combination fails to reproduce it — so it cannot be
recomputed from the download either.

**2. There is no freshness watermark.** No metadata endpoint exists (`/health`,
`/metadata`, `/version`, … all 404; `/` returns
`{"version":"dev","commit":"none","buildDate":"unknown"}`). The download's
`last-modified` header is the time of the request, because the export is
generated on the fly. The app's own help text says "Data shown in this
interactive hub is updated once annually." A mandatory freshness probe
(`EXTENDING.md` step 4) has nothing honest to read.

**3. It is substantially behind the source portals.** This is the decisive one.
Every city checked has materially more trees on its own portal than CIF holds:

| city | CIF | city's own portal | CIF has |
|---|---:|---:|---:|
| Toronto | 586,901 | 688,335 | 85% |
| Calgary | 482,239 | 581,011 | 83% |
| Ottawa | 288,596 | 304,374 | 95% |
| Mississauga | 366,009 | 499,331 | 73% |
| Edmonton | 295,518 | 480,744 | 61% |
| Winnipeg | 277,286 | 305,385 | 91% |
| Vancouver | 103,257 | 184,169 (our published `CAVAN`) | 56% |

Ingesting CIF would mean taking an annual, id-less, 60-95% snapshot of data we
can read at full fidelity and daily freshness from the portal it came from.

**4. The licence is unstated.** Neither the hub nor the project page states a
data licence, attribution requirement, or redistribution terms. The per-city
portals do carry licences (mostly municipal open data licences), which is what
`sourceCatalog.ts` and `README.md` attribution need.

**5. Vancouver is already on the map** as `CAVAN`, at nearly twice CIF's count.

---

## What CIF is good for

It is an excellent **discovery index**: it names 39 municipalities that have
tree inventory data, which is a shopping list for `new_city.py`. The sweep
below used the repo's own `_arcgis_shared.py <hub-host>` discovery tool.

### Confirmed direct sources (23 cities, plus Vancouver already wired)

Counts are live feature counts from the portal, taken 2026-09-06.

| city | prov | direct source | count | notes |
|---|---|---|---:|---|
| Toronto | ON | CKAN datastore `3dafa392` | 688,335 | `STRUCTID`, `BOTANICAL_NAME`, `DBH_TRUNK`; `last_refreshed` is a real watermark |
| Calgary | AB | Socrata `tfs4-3wwa` | 581,011 | |
| Mississauga | ON | ArcGIS `2023_City_Owned_Tree_Inventory` | 499,331 | `GlobalID`, `BOTNAME`, `DIAM`, `LATITUDE`/`LONGITUDE`; `layer_last_edit` works |
| Edmonton | AB | Socrata `eecg-fc54` | 480,744 | |
| Winnipeg | MB | Socrata `hfwk-jp4h` | 305,385 | native `tree_id`, `botanical_name`, `diameter_at_breast_height` — near-perfect fit |
| Ottawa | ON | ArcGIS `Forestry/MapServer/0` | 304,374 | `TREEID`, `GLOBALID`, `SPECIES`, `DBH`, `PLNTDATE`; MapServer has no `editingInfo`, use `field_max('MODIFYDATE')` |
| Surrey | BC | ArcGIS `Park Specimen Trees` (+ Screen/Important) | 115,454 | split across several park layers |
| Markham | ON | ArcGIS `Biodiversity/MapServer/0` | 82,354 | `TREEID`, `SPECIES`, `CURRENTDBH`, `YEARPLANTED` |
| Burlington | ON | ArcGIS `COB/Urban_Forestry/MapServer/0` | 80,287 | **common name only**, no botanical name — needs a species lookup |
| Halifax | NS | ArcGIS `Public_Trees` | 80,051 | `GLOBALID`, `TREEID`, `SP_SCIEN`, `DBH`, `INSTYR` |
| Kingston | ON | ArcGIS `Eng/City_Owned_Trees` | 55,891 | `TREE_ID`, `GLOBALID`, `SCIENTIFIC_NAME`, `DBH_TRUNK` |
| Ajax | ON | ArcGIS `Ajax_Open_Data/MapServer/8` | 53,848 | `SPCODE` (code, not name), `DBH`, `GPS_LAT`/`GPS_LON` |
| Lethbridge | AB | ArcGIS `odl_trees` | 45,433 | `botn_name`, `genus`, `species`, `cultivar`, `diameter` — cleanest schema of the set |
| Victoria | BC | ArcGIS `OpenData_Parks/MapServer/15` | 34,981 | `SiteID`, `Species`, `DiameterAtBreastHeight` |
| Peterborough | ON | ArcGIS `Tree_Inventory` | 29,455 | `BOTANICAL`, `GENUS`; id is `FACILITYID` — check it, DC's was not unique |
| Kelowna | BC | ArcGIS `OpenData_Environment/MapServer/17` | 24,599 | `Species`, `Genus`, `CultivarOrVariety`, `dbh_cm` |
| Fredericton | NB | ArcGIS `Tree_Inventory/FeatureServer/37` | 21,186 | `Genus_Spec`, `GlobalID`; **no DBH field** |
| New Westminster | BC | ArcGIS `Tree_Inventory_(PROD)_4_view` | 16,111 | `SPECIES`, `GENUS`, `CULTIVAR`, `DBH`, `PLANTINGDATE` |
| Moncton | NB | ArcGIS `Trees/FeatureServer/0` | 12,721 | `BOTNAME`, `DIAM`, `PlantedYear`, `last_edited_date` |
| Montreal | QC | CKAN `donnees.montreal.ca`, "Arbres publics sur le territoire de la Ville" | — | modified 2026-09-06 |
| Quebec City | QC | Données Québec, "Arbres répertoriés" | — | modified 2026-09-04 |
| Longueuil | QC | Données Québec, "Arbres" | — | modified 2026-02-09 |
| Vancouver | BC | already wired as `CAVAN` | 184,169 | |

### Not resolved (14 cities)

No tree inventory layer found at the hosts tried. Some of these certainly have
one under a host not guessed; others may be exactly the case CIF's About page
describes — data shared with CIF by a municipality that publishes no open
portal, in which case CIF is the only source and the blockers above apply.

Brampton (only a planting tracker found), Cambridge, Guelph, Kitchener,
London, Maple Ridge, Niagara Falls, North Vancouver, Regina, St. Catharines,
Strathcona, Vaughan, Welland, Whitby, Windsor.

Two specific traps in that list:

- **Cambridge / Kitchener / Waterloo** all resolve to the same Region of
  Waterloo ArcGIS service (`services1.arcgis.com/qAo1OsXi67t7XgmS`
  `Tree_Inventory`), which holds 99,376 rows — but CIF reports 215,912 across
  the three. The regional layer is not the union of the three inventories;
  each city needs its own source located before wiring.
- **London ON**'s hub search returns a CAD basemap layer
  (`OpenData_BaseMaps/MapServer/45`, 872,044 rows of `Theme`/`FeatureCode`),
  which is not a tree inventory. The real layer was not found.

---

## Status

**Done (PR 1):** `_socrata_shared.py`, and Calgary, Edmonton and Winnipeg
wired onto it -- 1,325,431 trees. The three existing Socrata cities' freshness
probes moved onto the same module, and New York's ingest gained the `$order`
its `$offset` paging always needed.

**Done (PR 2):** `_ckan_shared.py`, with Toronto, Montreal and Quebec City
wired onto it and Boston's probe moved across -- 1,181,078 trees. Three of the
four CKAN cities in the handoff below; **Longueuil is not wirable and was
dropped**, for the measured reason under "Longueuil" below.

**Next (a third PR):** the ArcGIS cities, which are 16 of the 23 confirmed and
already have `_arcgis_shared.py` under them. Then the snowflakes -- Burlington
ON (common name only) and Fredericton (no DBH).

### What PR 2 measured that the handoff got wrong

Two of the notes below were written from a first look and did not survive
contact:

- **The freshness watermark is a maximum, not a preference order.** The
  handoff said to prefer the resource's `last_modified`. Toronto's datastore is
  updated in place, so that stamp still reads **2022-05-02** while the data was
  refreshed 2026-06-04 -- following the handoff would have frozen Toronto's
  parquet on its first build and never rebuilt it. Boston's datastore resource
  stamp, on the same CKAN version, does move. `data_last_modified` takes the
  later of the resource stamp and the package's `last_refreshed`, and keeps
  `metadata_modified` as a last resort only, since that one moves for a
  description edit.

- **`datastore_search_sql` cannot be relied on.** Toronto answers it with a
  404 and Donnees Quebec rejects a `CAST` with a 403. The paged
  `datastore_search` is the only row reader in the module.

And one the handoff did not anticipate at all:

- **CKAN silently caps `limit` at 32,000** (`ckan.datastore.search.rows_max`)
  on all four portals, exactly as ArcGIS caps `maxRecordCount`. A loop that
  ends on a short page therefore ends after *one* page whenever the caller asks
  for more -- Toronto would have published 32,000 of its 688,335 trees and
  looked like a portal that shrank. CKAN echoes the applied `limit` and reports
  `total`, so `iter_datastore_rows` takes its page size from the response and
  terminates on `total`, never on a short page.

### Longueuil: measured, and not wirable

Longueuil publishes exactly one tree dataset on Donnees Quebec (`package_search`
for "arbres Longueuil" returns one result), and **it carries no tree id of any
kind**. The GeoJSON's 99,345 features have exactly two properties, `Espece` and
`Diametre_Tronc`, and no feature-level `id` member; the shapefile is an older
63,773-record extract of the same two fields. There is nothing else to read.

That is blocker #1 from the CIF verdict at the top of this file, restated:
`tree_id` is the declared grain of every city datasource and
`enforce_tree_schema` refuses a null or duplicate one. Nor is a derived key
available -- `EXTENDING.md` rules out a positional hash on principle, and the
data rules it out on the numbers: the 99,345 features sit on 98,208 distinct
coordinates, 611 points carry more than one tree and one carries 58. Even
hashing (coordinate, species, diameter) yields 98,464 distinct keys for 99,345
rows, so 881 trees would silently vanish into collisions.

Wiring Longueuil needs Longueuil to publish an id. Worth re-checking if the
dataset is ever republished -- it last moved 2024-03-01.

---

## Handoff: `_ckan_shared` + the CKAN cities

Toronto, Montreal, Quebec City and Longueuil are all CKAN, which with Boston
makes five -- past the threshold `EXTENDING.md` sets for writing a shared
module ("write the module when the third city arrives"). Do the module first,
the way this PR did for Socrata: it is what turns the fourth and fifth city
into a thin shim, and it is where the paging and freshness bugs get fixed once.

### The cities

| city | code | portal | dataset | rows | licence |
|---|---|---|---|---:|---|
| Toronto | `CATOR` | `ckan0.cf.opendata.inter.prod-toronto.ca` | `street-tree-data`, resource `3dafa392-c6ab-4f37-9bf9-21ddf7308eaf` | 688,335 | not specified |
| Montreal | `CAMTL` | `donnees.montreal.ca` | `b89fd27d-4b49-461b-8e54-fa2b34a628c4` "Arbres publics sur le territoire de la Ville" | ~330k | CC-BY 4.0 |
| Quebec City | `CAQUE` | `www.donneesquebec.ca/recherche` | `34103a43-3712-4a29-92e1-039e9188e915` "Arbres repertories" | ~130k | CC-BY 4.0 |
| Longueuil | `CALON` | `www.donneesquebec.ca/recherche` | `9ed153b2-4751-4e03-862f-6d4027e6f2a6` "Arbres" | ~75k | CC-BY 4.0 |

Quebec City and Longueuil are both on Donnees Quebec, so one host covers two
cities -- and Repentigny and Saguenay publish there too, if the appetite is
there for cities CIF never listed.

> **Read the Status section above before this one.** The handoff below is
> kept as written, because it is what the work was planned from; three of its
> notes turned out to be wrong or incomplete and the corrections are recorded
> up there rather than edited in here.

### What `_ckan_shared.py` should carry

Mirror `_socrata_shared.py`, which mirrors `_arcgis_shared.py`. CKAN's shape:

- **`CkanDataset(host, package_id)`** with `package_show` and `resource_show`
  endpoints, plus a `datastore_search` / `datastore_search_sql` row reader for
  the portals that have the datastore enabled (Toronto does) and a CSV/GeoJSON
  resource reader for those that do not (Donnees Quebec).
- **`package_metadata` / `resource_last_modified`** -- the freshness watermark.
  CKAN publishes three plausible stamps and they are not interchangeable:
  `metadata_modified` on the package moves for a *description* edit,
  `last_modified` on the resource moves when the file is replaced, and Toronto
  adds a non-standard `last_refreshed` on the package which is the real one.
  Prefer the resource's `last_modified`, fall back to the package's
  `last_refreshed`, then `metadata_modified` -- and raise rather than degrade
  when none is present, the same rule `rows_updated_at` follows.
- **`iter_datastore_rows`** with **an explicit sort**, for exactly the reason
  `iter_rows` requires `$order`: CKAN's `datastore_search` pages by `offset`
  and guarantees nothing without `sort`. Use `_id`, the datastore's own row
  key.
- **`find_tree_datasets(host)`** via `package_search?q=tree` (and `q=arbres`
  for the francophone portals), with the same canopy exclusion the other two
  modules need, and a `__main__` so `uv run _ckan_shared.py <host>` is the
  first step of the runbook.

### Traps already found, so nobody pays for them twice

- **Montreal publishes a consolidated file *and* one file per borough** in the
  same package. The ingest wants "Inventaire arbres publics - Fichier
  consolide" only; reading the resource list naively either double-counts or,
  worse, silently picks one borough.
- **Toronto's `STRUCTID` is the per-tree id**, not `OBJECTID` -- and check it
  for nulls and duplicates across the whole 688k before committing, the way
  Calgary's `wam_id` was checked here. `OBJECTID` is a local row number a
  republish can reassign.
- **Toronto's dataset carries no licence.** Ask before publishing, or note the
  absence in the attribution the way the other rows do.
- **The Quebec portals are francophone**: `essence`/`essence_latin` for
  species, `dhp` for diameter (centimetres, like every Canadian portal).
  `sanitize_species` already strips accents before its rules run, so
  `Melese` and the rest arrive fine, and `_NON_TAXON_REWRITES` already carries
  French common names -- but re-run the species sweep against each city's real
  values before assuming, the way this PR did.
- **Every Canadian portal publishes DBH in centimetres.** `cm_to_inches`.

### Then the same checklist this PR followed

1. `uv run _ckan_shared.py <host>` to find the dataset.
2. Check the id column for uniqueness *and* nulls over the whole table.
3. Resolve the ecoregion at the centroid, measure the coordinate extents from
   the data rather than guessing `CITY_BOUNDS`.
4. `uv run new_city.py ...`, then fill the four things it leaves alone.
5. Bootstrap the OSM staging object, then calibrate the dedup cell
   (`osm_dedup_validation.py --city CODE`) -- never copy a cell size.
6. `uv run dedup_cells.py --write`, then `pytest tests -q`.

---

## Recommended next step

Add the confirmed cities directly with `new_city.py`, largest first. That is
~3.0M trees from the seven biggest alone, at full fidelity with real
watermarks and real ids, versus 4.36M stale id-less rows from CIF.

Platform-wise this is mostly a solved problem here:

- **ArcGIS** — 16 of the 23 confirmed. `_arcgis_shared.py` already covers
  paging, both freshness watermarks and Esri epoch-milliseconds.
- **Socrata** — Calgary, Edmonton, Winnipeg. That takes Socrata to six cities
  in this repo (with SF, NYC, LA) and past the threshold `EXTENDING.md` sets
  for writing a shared module: *"Write the module when the third city arrives."*
  **`_socrata_shared.py` should be written before or alongside the first of
  these three**, not after.
- **CKAN** — Toronto, Montreal, Québec City, Longueuil. That takes CKAN to
  five (with Boston) and likewise earns `_ckan_shared.py`. Québec City and
  Longueuil are both on Données Québec, so one module covers both.

Everything else is per-city judgement the runbook already isolates: the field
mapping, the freshness probe, the landmark source, and the dedup cell size
(`osm_dedup_validation.py --city {CODE}` — never copy a cell size).

Note that Canadian portals publish **DBH in centimetres**; the canonical column
is inches. Fredericton has no DBH at all and Burlington publishes only a common
name — both are wirable but need a call on what to do with the gap.
