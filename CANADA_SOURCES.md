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

> **Rows marked WIRED were measured field by field while being added; the rest
> were read off a hub listing and a first page.** That difference matters more
> than it sounds: of the six largest ArcGIS cities, *three* turned out not to
> be what this table said. Mississauga's `BOTNAME` and Ottawa's `SPECIES` are
> common names rather than binomials, and Markham's layer is the whole of York
> Region. Check the column, not the column *name*, before planning around a
> row here.

| city | prov | direct source | count | notes |
|---|---|---|---:|---|
| Toronto | ON | CKAN datastore `3dafa392` | 688,335 | `STRUCTID`, `BOTANICAL_NAME`, `DBH_TRUNK`; `last_refreshed` is a real watermark |
| Calgary | AB | Socrata `tfs4-3wwa` | 581,011 | |
| Mississauga | ON | ArcGIS `2023_City_Owned_Tree_Inventory` | 499,331 | **common name only.** `BOTNAME` is a six-letter code (`MANOOO`, `ASGROO`) and `BOTDESC` its expansion -- `NORWAY MAPLE`, `HONEY LOCUST`, 330 distinct. No botanical name anywhere in the layer. `UNITID`, `LATITUDE`/`LONGITUDE`, `layer_last_edit` all good; 41,481 rows are `SERVSTAT = FUTURE TREE SITE` |
| Edmonton | AB | Socrata `eecg-fc54` | 480,744 | |
| Winnipeg | MB | Socrata `hfwk-jp4h` | 305,385 | native `tree_id`, `botanical_name`, `diameter_at_breast_height` — near-perfect fit |
| Ottawa | ON | ArcGIS `Forestry/MapServer/0` | 304,374 | **common name only.** `SPECIES` is an inverted common name -- `Maple Sugar`, `Lilac Japanese`, `Spruce Blue/Colorado`, 169 distinct -- not a binomial. `TREEID` is a per-tree GUID, `DBH` is cm, and `field_max('MODIFYDATE')` reads 2026-09-03, so everything but the species is ready |
| Surrey | BC | ArcGIS `Park Specimen Trees` (+ Screen/Important) | 115,454 | split across several park layers. **Host not re-found:** `data.surrey.ca` serves HTML from the DCAT path and `cosmos.surrey.ca` / `surrey.maps.arcgis.com` 404, so `find_tree_layers` cannot reach it. The layer URLs need to come from somewhere other than the Hub feed |
| Markham | ON | ArcGIS `Biodiversity/MapServer/0` | **16,596** | **The layer is York Region, not Markham.** All 82,354 rows are `Regional ROW` trees across nine municipalities -- Vaughan 20,145, Markham 16,596, Richmond Hill 9,527, ... -- so CIF's "Markham 82,354" is the whole region. Filter `MUNICIPALITY='Markham'`. Clean binomials with quoted cultivars; no `editingInfo` and **no date column at all**, so it needs `hub_last_modified` |
| Burlington | ON | ArcGIS `COB/Urban_Forestry/MapServer/0` | 80,287 | **common name only**, no botanical name — needs a species lookup |
| Halifax | NS | ArcGIS `Public_Trees` | 80,051 | **WIRED as `CAHFX`.** `DBH` is a nine-band size class, not centimetres -- the layer publishes the bands as a coded-value domain. ~1,000 rows carry a shorthand code (`ACRU`, `QURU`) instead of a name |
| Kingston | ON | ArcGIS `Eng/City_Owned_Trees` | 55,891 | **WIRED as `CAKGN`** (46,883 after dropping 8,945 retired). `DBH_TRUNK` uses 999 as its not-measured sentinel. No `editingInfo`, no edit-date column |
| Ajax | ON | ArcGIS `Ajax_Open_Data/MapServer/8` | 53,848 | `SPCODE` (code, not name), `DBH`, `GPS_LAT`/`GPS_LON` |
| Lethbridge | AB | ArcGIS `odl_trees` | 45,433 | **WIRED as `CALET`** (45,202 after dropping 231 retired). Cleanest schema of the set. No `editingInfo`, no date column |
| Victoria | BC | ArcGIS `OpenData_Parks/MapServer/15` | 34,981 | **WIRED as `CAVIC`.** The "Parks trees database" title undersells it -- `TreeCategory` shows the whole municipal inventory. `Site` looks like an id and is not (5,913 distinct over 34,981 rows); `SiteID` is the key |
| Peterborough | ON | ArcGIS `Tree_Inventory` (host is `data-ptbo.opendata.arcgis.com`) | 29,455 | Clean binomials with quoted cultivars, and **no diameter column at all** -- same gap as Fredericton. `editingInfo` present but `dataLastEditDate` is 2022-02. Id is `FACILITYID`, still unchecked |
| Kelowna | BC | ArcGIS `OpenData_Environment/MapServer/17` | 24,599 | **WIRED as `CAKEL`.** `SITE_ID` is unusable -- 3,214 null and 195 rows sharing an id, one value on 33 -- and there is no `GLOBALID`, so it is keyed on `OBJECTID`, the runbook's last resort. `InventoryDate` is a live watermark |
| Fredericton | NB | ArcGIS `Tree_Inventory/FeatureServer/37` | 21,186 | `Genus_Spec`, `GlobalID`; **no DBH field** |
| New Westminster | BC | ArcGIS `Tree_Inventory_(PROD)_4_view` | 16,111 | **WIRED as `CANWE`.** `globalid` is clean; `FULL_NAME` is the curated taxon. No common-name column. A quarter of rows have no diameter |
| Moncton | NB | ArcGIS `Trees/FeatureServer/0` | 12,721 | **species code, not a name**: `BOTNAME` is `MapRed`, `PinEas`. Same problem as Ajax and Mississauga. `UNITID`, `editingInfo` and `last_edited_date` are all fine |
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

**Done (PR 2):** `_ckan_shared.py`, with Toronto, Montreal, Quebec City and
Longueuil wired onto it and Boston's probe moved across -- 1,278,553 trees. All
four CKAN cities in the handoff below. Longueuil publishes no tree id and is
the one city here with a **synthesised** one; see "Longueuil" below for what
that costs and why it was taken anyway.

**Done (PR 3):** the six largest ArcGIS cities that publish a botanical name
*and* a diameter -- Halifax, Kingston, Lethbridge, Victoria, Kelowna and New
Westminster -- 247,776 trees. `_arcgis_shared` gained a third freshness
watermark (`hub_last_modified`) and an Esri-geometry-to-WKT converter; the
shared species hygiene gained the non-taxa these six turned up. Landmarks are
official designation registries in all six cases, read live: no geocoding, no
committed CSV, no staging object.

**Done (PR 4): the common-name cities.** `_common_name_species.py`, with
Mississauga, Ottawa, Burlington ON, Ajax and Moncton wired onto it. The five
layers hold 951,561 rows and publish **712,693** trees; the gap is removed
trees and empty planting sites the sources keep in the same table, and
Mississauga alone accounts for 228,353 of it. `_arcgis_shared` gained `coded_value_domain`
and a NaN-safe `esri_point`. Landmarks are official registries or municipal
cultural inventories in all five cases, read live. What the plan got wrong is
recorded in "What PR 4 measured that the plan got wrong" below -- the short
version is that Ottawa was never a common-name city at all.

**Next, the rest of the ArcGIS set:** Peterborough (29,455) and Fredericton
(21,186), which are clean except that neither publishes a diameter at all;
Markham (16,596) once someone decides whether a York Region layer filtered to
one municipality is the right thing to publish; and Surrey (115,454) if its
layer URLs can be found without the Hub feed. `_common_name_species` is there
for any of them that needs it, and its table is the standard North American
street-tree palette, so a new Ontario city should resolve most of its values
without a single new entry.

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
  `datastore_search` is the row reader for every datastore-backed resource.

- **Not every CKAN portal has a datastore.** Longueuil has none at all, so the
  module also carries `read_geojson_features` for a plain file resource. A CSV
  or shapefile resource still has no reader; that waits for a source needing
  one.

And one the handoff did not anticipate at all:

- **CKAN silently caps `limit` at 32,000** (`ckan.datastore.search.rows_max`)
  on all four portals, exactly as ArcGIS caps `maxRecordCount`. A loop that
  ends on a short page therefore ends after *one* page whenever the caller asks
  for more -- Toronto would have published 32,000 of its 688,335 trees and
  looked like a portal that shrank. CKAN echoes the applied `limit` and reports
  `total`, so `iter_datastore_rows` takes its page size from the response and
  terminates on `total`, never on a short page.

### Longueuil: no published id, and the exception that was made for it

Longueuil publishes exactly one tree dataset on Donnees Quebec (`package_search`
for "arbres Longueuil" returns one result), and **it carries no tree id of any
kind**. The GeoJSON's 99,345 features have exactly two properties, `Espece` and
`Diametre_Tronc`, and no feature-level `id` member; the shapefile is an older
63,773-record extract of the same two fields; the KMZ's `kml_1`, `kml_2` are
sequence numbers its exporter assigns, which is the `OBJECTID` trap rather than
an id. The city runs no ArcGIS or WFS service. There is nothing else to read.

That is blocker #1 from the CIF verdict at the top of this file: `tree_id` is
the declared grain and `enforce_tree_schema` refuses a null or duplicate one.
The first pass therefore dropped the city. **That call was reversed**: 97,475
mapped trees beat zero, and the cost of the alternative is bounded and
measurable. `calon/longueuil_tree_info.py` carries the full reasoning; in
short:

- **The id is the rounded coordinate and nothing else**, because position is
  the most stable thing the source has. Folding `Diametre_Tronc` into the key
  would churn the id of every re-measured tree, and re-measuring is what a tree
  inventory is for.
- **Rounded to 7 dp (~1 cm), because the portal already publishes two
  precisions** -- the same tree is `-73.50224994604028` in the GeoJSON and
  `-73.5022499460403` in the KMZ. An unrounded key would have churned the day
  someone regenerated the export with a different writer.
- **Stacked coordinates are dropped, not resolved.** 662 coordinates carry more
  than one tree and one carries 58 -- trees never individually surveyed, mapped
  to a block or park centroid. 1,870 rows, 1.9% of the file.

The exception is worth naming as an exception: this is the only city on the map
whose `tree_id` the publisher cannot confirm, and a community check-in recorded
against one is orphaned if Longueuil ever corrects that coordinate. If the city
ever publishes an id, switch to it and accept the one-time churn.

---

## What PR 3 measured that the table got wrong

Recorded here rather than only in the table above, because each was a plan
that did not survive contact and the next batch will be planned the same way.

- **Three of the six biggest ArcGIS cities do not publish a botanical name.**
  The table said Mississauga had `BOTNAME` and Ottawa had `SPECIES`, which is
  true and means nothing: Mississauga's is a six-letter code and Ottawa's is
  an inverted common name. A column called `SPECIES` holding `Maple Sugar` is
  the single most expensive assumption in this file, and it cost the planned
  city list about 800k trees.

- **CIF's per-city counts inherit its sources' scope.** Markham's 82,354 is a
  York Region layer covering nine municipalities; Markham itself is 16,596.
  The same trap the Waterloo note already flagged, one row further down.

- **A layer's own field domain is worth reading.** Halifax's `DBH` runs 1-9
  and is a size class; the layer publishes the class boundaries as a
  coded-value domain (`fields[].domain`), so the conversion is read off the
  portal rather than guessed. Anything that looks like a measurement but has
  a suspiciously small range is worth one `?f=json`.

- **Four of the six had no freshness watermark inside the layer.** Kingston,
  Lethbridge, Victoria and Kelowna publish no `editingInfo` and (mostly) no
  edit-date column, which is what `hub_last_modified` is for: the Hub
  catalogue's own `modified` stamp for the dataset, matched on the REST
  endpoint. It is the last resort of the three watermarks and its limits are
  documented on the function -- where a city has a second stamp, take the
  **maximum**, never a preference order.

- **`OBJECTID` is not always unique.** Kelowna's heritage registry reports
  `objectIdField: null` -- it is a query layer, not a registered feature class
  -- and `OBJECTID` repeats exactly where the parcel id does. The Pandosy
  Mission is eight registered buildings on one parcel with one `OBJECTID`
  between them.

- **Vancouver's ecoregion was wrong, and is fixed here.** `CAVAN` was wired to
  RESOLVE `ECO_ID` 319, which is **Indochina mangroves**; every land point in
  Vancouver returns 364, Puget lowland forests, as do New Westminster and
  Burnaby. Vancouver's *centroid* falls in a coverage gap and returns nothing,
  which is how the wrong id got in. Its nativeness classification has been
  computed against a mangrove ecoregion since the city was added; the fix
  lands with this PR and takes effect on Vancouver's next rebuild. When
  `curl`ing the RESOLVE service for a new city, check that it returned a
  feature at all.

## What PR 4 measured that the plan got wrong

The plan above called these "the common-name cities" and expected one shared
module plus five thin shims. The module was right. Almost everything else in
that paragraph was not.

- **Ottawa is not a common-name city.** `SPECIES` stores `Maple Sugar`,
  `Lilac Japanese`, `Spruce Blue/Colorado`, which is what the table above
  recorded -- and it is a *coded-value* field whose domain maps all 174 of
  those codes to the binomial (`Acer saccharum`, `Syringa reticulata`,
  `Picea pungens`). Ottawa's foresters published the identification; it lives
  in `fields[].domain` rather than in a column. 304,374 trees were nearly
  wired through a common-name index that would have thrown that away.
  **Read a field's domain before concluding a portal does not identify its
  trees** -- the third time in this file that a layer's own `?f=json` answered
  a question the column names could not, after Halifax's DBH size classes and
  Ajax's species symbols. `_arcgis_shared.coded_value_domain` now does it in
  one call.

- **Inverting the enrichment table does not work, and the reason is our own
  data.** The plan's "obvious seed" was that the enrichment table is already a
  scientific-name to common-names map. Measured: of 11,070 distinct common
  names on species-rank rows, 2,328 are claimed by more than one species, and
  the ambiguous ones are exactly the trees these cities are made of. "Norway
  spruce" is claimed by `Picea abies`, `Picea excelsa`, `Pinus abies` and
  `Picea x mariorika`; "tulip tree" by `Liriodendron tulipifera` and three
  misspellings of it. The competitors are misspelled binomials that some city
  published and `sanitize_species` deliberately keeps, so no tie-break inside
  the index removes them -- an automatic index resolved about 60% of
  Mississauga's rows and would have silently mislabelled some of the rest. The
  reverse index was still worth building as a drafting aid; what it could not
  be is the authority. `COMMON_NAME_SPECIES` is curated, 398 entries, and every key
  is a value one of these portals actually publishes.

- **Two thirds of Mississauga's layer is not a living tree.** 499,331 rows,
  271,056 published. `SERVSTAT = 'EXPIRED'` is 152,289 of them and looks
  arguable -- they carry a species and a 14 cm median diameter, and "expired"
  could mean an expired warranty. It does not: EXPIRED's four commonest
  species are GREEN ASH, ASH SPP., NORWAY MAPLE and WHITE ASH (29% of the
  bucket is ash), and STUMP and DEAD appear in it and essentially nowhere
  else, while the maintained bucket has no ash in its top eleven. That is the
  emerald ash borer. Only 2.1% of EXPIRED rows share a coordinate with a
  living tree, so they are not replants either. **A status column worth
  filtering on can be decided by the species mix behind it.**

- **A coded value does not have to match the case of its domain entry**, and a
  code that fails to resolve publishes as `Unknown` without reporting anything.
  Ottawa's layer stores `Staghorn Sumac` where its domain lists `Staghorn
  sumac`, which is 265 trees; folding both sides is one line and there is no
  reason not to. Two of its domain *values* are also rejected outright by
  `sanitize_species` -- `Malus apple species` and `Malus crabapple species`,
  which are 11,638 trees between them and mean `Malus` -- and two more are
  misspelled at the source (`Sorubus Intermedia` for the Swedish whitebeam,
  `Crataegus crusgalli` for the cockspur hawthorn). Reading a domain is one
  request; reading what is *in* it is the part that takes a minute.

- **A column named like an id is still not one, twice more.** Moncton's
  `UNITID` is aliased "Tree ID" and 484 rows share one across 64 values;
  Ottawa's `TREEID` is a per-tree GUID that is unique on 304,373 of 304,374
  rows. One duplicate would have failed the whole city's refresh at
  `enforce_tree_schema`, which is the check working. Both cities are keyed on
  `GLOBALID`.

- **An ArcGIS server returns a missing geometry as the *string* "NaN".**
  Ajax and Burlington ON both publish rows like that, and pyarrow refuses them
  with `Could not convert 'NaN' with type str`, which reads as a type bug
  rather than "this feature has no location". `_arcgis_shared.esri_point` is
  the shared coercion, and Halifax was reading the geometry dict directly and
  had the same latent failure waiting.

- **The catalogue is where the licence lives.** Ottawa's Part IV heritage
  designation register (444 named properties) is served publicly from the same
  map server as the trees and is *not* listed in open.ottawa.ca's DCAT feed,
  while the Heritage Conservation Districts layer from the same service is,
  under Ottawa's Open Data Licence 2.0. An unstated licence is blocker #4 from
  the CIF verdict at the top of this file, so the landmarks come from the
  catalogued "Cultural Spaces Inventory - Heritage" instead -- 205 named
  places rather than 444, a third of which had no name anyway.

- **Two cities can be called Burlington.** `USBTV` is Vermont and `CABUR` is
  Ontario, both publish a tree inventory on ArcGIS, and the city picker and the
  attribution catalogue both key on the display name. Both now carry their
  province the way "Washington, DC" always has.

- **Moncton's species codes had to be decoded, and Ottawa paid for it.**
  `BOTNAME` is `MapNor`, `LinLit`, `SprWhi` -- three letters of each word of
  the inverted common name -- with no domain and no other species column, so
  Halifax's rule (drop a shorthand code rather than guess a taxon) would have
  left the whole city unidentified. 65 of the 147 codes expand mechanically
  against Ottawa's published domain by a unique-prefix rule, covering 9,588
  rows; the rest were read by hand, and five that could not be read
  confidently are left as `Unknown`. Both halves are in
  `camon/moncton_tree_info.py`, separated, so a reviewer can see which is
  which.

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
