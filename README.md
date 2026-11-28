# Urban Tree Reporting

### Share a filtered city map

Map links use the hash route, for example:
`https://greenmtnboy.github.io/tree_reporting/#/?city=USNYC&mode=explore&filter.species=Platanus+x+hispanica&colorBy=dbh`.

Supported query parameters:

| Parameter | Meaning |
| --- | --- |
| `city` | Published city code, such as `USNYC` or `USSFO`. |
| `mode=explore` | Open the city map directly, including on mobile. A recognized nonempty filter also opens exploration when mode is omitted. |
| `filter.species` | Exact published scientific name, including genus-only inventory entries. |
| `filter.genus` | First word of the published scientific name; includes all matching species and genus-only entries. |
| `colorBy` | `dbh` for trunk diameter in inches, or `tree_form` for the normal category colors. Filtered links default to DBH. |

Encode values with `URLSearchParams`; a slug or common name is not a species key.
Repeat a filter key for alternatives (OR); different filter fields combine with
AND. Unknown filter keys and empty values are ignored. An unmatched name shows
an empty map. URL filters intersect with chat filters and survive city changes,
reloads and browser history. Chat color overrides take precedence until a color
is selected in the legend. “Show all trees” clears URL filters and URL coloring;
any separate chat filter still applies. DBH's gray category includes missing,
nonfinite and nonpositive measurements, without substituting a rendered size.

Deploy this map support before publishing arborary.world's species links.

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
- [Boulder, CO, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USBOU)
- [Naperville, IL, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USNPV)
- [Columbus, OH, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USCOL)
- [Eugene, OR, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USEUG)
- [Portland, OR, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USPOR)
- [Seattle, WA, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USSEA)
- [Philadelphia, PA, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USPHL)
- [Pittsburgh, PA, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USPIT)
- [San Jose, CA, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USSJC)
- [Sacramento, CA, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USSAC)
- [Baltimore, MD, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USBAL)
- [Stoughton, WI, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USSTO)
- [Austin, TX, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USAUS)
- [Houston, TX, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USHOU)
- [Durham, NC, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USDUR)
- [Charlotte, NC, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USCLT)
- [Atlanta, GA, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USATL)
- [New Orleans, LA, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USNOL)
- [Naples, FL, United States](https://greenmtnboy.github.io/tree_reporting/#/?city=USNAP)
- [Vancouver, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAVAN)
- [Calgary, AB, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CACAL)
- [Edmonton, AB, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAEDM)
- [Winnipeg, MB, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAWPG)
- [Toronto, ON, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CATOR)
- [Montreal, QC, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAMTL)
- [Quebec City, QC, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAQUE)
- [Longueuil, QC, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CALON)
- [Halifax, NS, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAHFX)
- [Kingston, ON, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAKGN)
- [Lethbridge, AB, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CALET)
- [Victoria, BC, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAVIC)
- [Kelowna, BC, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAKEL)
- [New Westminster, BC, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CANWE)
- [Mississauga, ON, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAMIS)
- [Ottawa, ON, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAOTT)
- [Burlington, ON, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CABUR)
- [Ajax, ON, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAAJX)
- [Moncton, NB, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAMON)
- [Tokyo, Japan](https://greenmtnboy.github.io/tree_reporting/#/?city=JPTYO)
- [Taipei, Taiwan](https://greenmtnboy.github.io/tree_reporting/#/?city=TWTPE)
- [Bogotá, Colombia](https://greenmtnboy.github.io/tree_reporting/#/?city=COBOG)
- [Copenhagen, Denmark](https://greenmtnboy.github.io/tree_reporting/#/?city=DKCPH)
- [Milan, Italy](https://greenmtnboy.github.io/tree_reporting/#/?city=ITMIL)
- [Prague, Czechia](https://greenmtnboy.github.io/tree_reporting/#/?city=CZPRG)
- [Helsinki, Finland](https://greenmtnboy.github.io/tree_reporting/#/?city=FIHEL)
- [London, United Kingdom](https://greenmtnboy.github.io/tree_reporting/#/?city=GBLON)
- [Nottingham, United Kingdom](https://greenmtnboy.github.io/tree_reporting/#/?city=GBNOT)
- [Amsterdam, Netherlands](https://greenmtnboy.github.io/tree_reporting/#/?city=NLAMS)
- [Berlin, Germany](https://greenmtnboy.github.io/tree_reporting/#/?city=DEBER)
- [Vienna, Austria](https://greenmtnboy.github.io/tree_reporting/#/?city=ATVIE)
- [Zürich, Switzerland](https://greenmtnboy.github.io/tree_reporting/#/?city=CHZUR)
- [Basel, Switzerland](https://greenmtnboy.github.io/tree_reporting/#/?city=CHBAS)
- [Frankfurt, Germany](https://greenmtnboy.github.io/tree_reporting/#/?city=DEFRA)
- [Cologne, Germany](https://greenmtnboy.github.io/tree_reporting/#/?city=DECGN)
- [Madrid, Spain](https://greenmtnboy.github.io/tree_reporting/#/?city=ESMAD)
- [Lyon, France](https://greenmtnboy.github.io/tree_reporting/#/?city=FRLYO)
- [Hamburg, Germany](https://greenmtnboy.github.io/tree_reporting/#/?city=DEHAM)
- [Bristol, United Kingdom](https://greenmtnboy.github.io/tree_reporting/#/?city=GBBRS)
- [Ghent, Belgium](https://greenmtnboy.github.io/tree_reporting/#/?city=BEGNT)
- [Hamilton, Ontario, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAHAM)
- [Utrecht, Netherlands](https://greenmtnboy.github.io/tree_reporting/#/?city=NLUTC)
- [Toulouse, France](https://greenmtnboy.github.io/tree_reporting/#/?city=FRTLS)
- [Geneva, Switzerland](https://greenmtnboy.github.io/tree_reporting/#/?city=CHGVA)
- [Leipzig, Germany](https://greenmtnboy.github.io/tree_reporting/#/?city=DELEJ)
- [Wellington, New Zealand](https://greenmtnboy.github.io/tree_reporting/#/?city=NZWLG)
- [Bordeaux, France](https://greenmtnboy.github.io/tree_reporting/#/?city=FRBOD)
- [Strasbourg, France](https://greenmtnboy.github.io/tree_reporting/#/?city=FRSXB)
- [Eindhoven, Netherlands](https://greenmtnboy.github.io/tree_reporting/#/?city=NLEIN)
- [Bern, Switzerland](https://greenmtnboy.github.io/tree_reporting/#/?city=CHBRN)
- [Rennes, France](https://greenmtnboy.github.io/tree_reporting/#/?city=FRRNS)
- [Antwerp, Belgium](https://greenmtnboy.github.io/tree_reporting/#/?city=BEANR)
- [San Diego, California](https://greenmtnboy.github.io/tree_reporting/#/?city=USSAN)
- [Kitchener, Ontario, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAKIT)
- [Sydney, Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AUSYD)
- [Louisville, Kentucky](https://greenmtnboy.github.io/tree_reporting/#/?city=USLOU)
- [Montpellier, France](https://greenmtnboy.github.io/tree_reporting/#/?city=FRMPL)
- [Grenoble, France](https://greenmtnboy.github.io/tree_reporting/#/?city=FRGNB)
- [Madison, Wisconsin](https://greenmtnboy.github.io/tree_reporting/#/?city=USMSN)
- [Richmond, Virginia](https://greenmtnboy.github.io/tree_reporting/#/?city=USRIC)
- [Spokane, Washington](https://greenmtnboy.github.io/tree_reporting/#/?city=USGEG)
- [Waterloo, Ontario, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAWAT)
- [Nantes, France](https://greenmtnboy.github.io/tree_reporting/#/?city=FRNTE)
- [Santa Monica, California](https://greenmtnboy.github.io/tree_reporting/#/?city=USSMO)
- [Oakville, Ontario, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAOAK)
- [Rochester, New York](https://greenmtnboy.github.io/tree_reporting/#/?city=USROC)
- [Syracuse, New York](https://greenmtnboy.github.io/tree_reporting/#/?city=USSYR)
- [Las Vegas, Nevada](https://greenmtnboy.github.io/tree_reporting/#/?city=USLAS)
- [Brest, France](https://greenmtnboy.github.io/tree_reporting/#/?city=FRBES)
- [Groningen, Netherlands](https://greenmtnboy.github.io/tree_reporting/#/?city=NLGRQ)
- [Riverside, California](https://greenmtnboy.github.io/tree_reporting/#/?city=USRAL)
- [Oakland, California](https://greenmtnboy.github.io/tree_reporting/#/?city=USOAK)
- [Edinburgh, United Kingdom](https://greenmtnboy.github.io/tree_reporting/#/?city=GBEDI)
- [Belfast, United Kingdom](https://greenmtnboy.github.io/tree_reporting/#/?city=GBBFS)
- [Guelph, Ontario, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAGLP)
- [Providence, Rhode Island](https://greenmtnboy.github.io/tree_reporting/#/?city=USPVD)
- [Lisbon, Portugal](https://greenmtnboy.github.io/tree_reporting/#/?city=PTLIS)
- [Fingal (County Dublin), Ireland](https://greenmtnboy.github.io/tree_reporting/#/?city=IEFIN)
- [Canberra, Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AUCBR)
- [Christchurch, New Zealand](https://greenmtnboy.github.io/tree_reporting/#/?city=NZCHC)
- [Melbourne, Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AUMEL)
- [Adelaide, Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AUADL)
- [Perth, Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AUPER)
- [Ballarat, Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AUBAL)
- [Wodonga, Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AUWOD)
- [Paris, France](https://greenmtnboy.github.io/tree_reporting/#/?city=FRPAR)
- [Buenos Aires, Argentina](https://greenmtnboy.github.io/tree_reporting/#/?city=ARBUE)
- [Athens, Greece](https://greenmtnboy.github.io/tree_reporting/#/?city=GRATH)
- [Milos, Greece](https://greenmtnboy.github.io/tree_reporting/#/?city=GRMLO)
- [Santorini, Greece](https://greenmtnboy.github.io/tree_reporting/#/?city=GRSAN)
- [Cockburn, Western Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AUCKB)
- [Basingstoke, United Kingdom](https://greenmtnboy.github.io/tree_reporting/#/?city=GBBSK)
- [York, United Kingdom](https://greenmtnboy.github.io/tree_reporting/#/?city=GBYRK)
- [Hastings, New Zealand](https://greenmtnboy.github.io/tree_reporting/#/?city=NZHAS)
- [Red Deer, Alberta, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CARED)
- [Greater Bendigo, Victoria, Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AUBXG)
- [Fareham, United Kingdom](https://greenmtnboy.github.io/tree_reporting/#/?city=GBFAR)
- [Launceston, Tasmania, Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AULST)
- [Macedon Ranges, Victoria, Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AUMRS)
- [Fredericton, New Brunswick, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAFRE)
- [Medicine Hat, Alberta, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAMHT)
- [Greater Shepparton, Victoria, Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AUSHT)
- [Sioux Falls, South Dakota](https://greenmtnboy.github.io/tree_reporting/#/?city=USFSD)
- [Surrey, British Columbia, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CASRY)
- [Latrobe City, Victoria, Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AULTV)
- [Barrie, Ontario, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CABAR)
- [Langley Township, British Columbia, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CALNG)
- [Maple Ridge, British Columbia, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAMPR)
- [Prince George, British Columbia, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAPRG)
- [Penticton, British Columbia, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAPEN)
- [Fort St. John, British Columbia, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAFSJ)
- [Welland, Ontario, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAWLD)
- [Bassendean, Western Australia, Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AUBSD)
- [Colac Otway Shire, Victoria, Australia](https://greenmtnboy.github.io/tree_reporting/#/?city=AUCOL)
- [Barcelona, Spain](https://greenmtnboy.github.io/tree_reporting/#/?city=ESBCN)
- [Singapore](https://greenmtnboy.github.io/tree_reporting/#/?city=SGSIN)
- [Baton Rouge, Louisiana](https://greenmtnboy.github.io/tree_reporting/#/?city=USBTR)
- [Lansing, Michigan](https://greenmtnboy.github.io/tree_reporting/#/?city=USLAN)
- [Boise, Idaho](https://greenmtnboy.github.io/tree_reporting/#/?city=USBOI)
- [Port St. Lucie, Florida](https://greenmtnboy.github.io/tree_reporting/#/?city=USPSL)
- [Missoula, Montana](https://greenmtnboy.github.io/tree_reporting/#/?city=USMSO)
- [Bismarck, North Dakota](https://greenmtnboy.github.io/tree_reporting/#/?city=USBIS)
- [Springfield, Missouri](https://greenmtnboy.github.io/tree_reporting/#/?city=USSPF)
- [Grand Junction, Colorado](https://greenmtnboy.github.io/tree_reporting/#/?city=USGJT)
- [Seoul, South Korea](https://greenmtnboy.github.io/tree_reporting/#/?city=KRSEL)
- [York Region, ON, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAYRK)
- [Whitby, ON, Canada](https://greenmtnboy.github.io/tree_reporting/#/?city=CAWHI)
- [Salem, Oregon](https://greenmtnboy.github.io/tree_reporting/#/?city=USSLM)
- [Oxnard, California](https://greenmtnboy.github.io/tree_reporting/#/?city=USOXR)
- [Redlands, California](https://greenmtnboy.github.io/tree_reporting/#/?city=USRDL)
- [Davenport, Iowa](https://greenmtnboy.github.io/tree_reporting/#/?city=USDVN)
- [Plano, Texas](https://greenmtnboy.github.io/tree_reporting/#/?city=USPLN)
- [Bend, Oregon](https://greenmtnboy.github.io/tree_reporting/#/?city=USBND)
- [Orland Park, Illinois](https://greenmtnboy.github.io/tree_reporting/#/?city=USOPK)
- [Evanston, Illinois](https://greenmtnboy.github.io/tree_reporting/#/?city=USEVN)
- [Moorhead, Minnesota](https://greenmtnboy.github.io/tree_reporting/#/?city=USMHD)
- [Sarasota County, Florida](https://greenmtnboy.github.io/tree_reporting/#/?city=USSRQ)
- [Pinellas County, Florida](https://greenmtnboy.github.io/tree_reporting/#/?city=USPIE)
- [Bozeman, Montana](https://greenmtnboy.github.io/tree_reporting/#/?city=USBZN)
- [Loveland, Colorado](https://greenmtnboy.github.io/tree_reporting/#/?city=USLVL)
- [San Mateo, California](https://greenmtnboy.github.io/tree_reporting/#/?city=USSMQ)
- [St. Louis Park, Minnesota](https://greenmtnboy.github.io/tree_reporting/#/?city=USSLP)
- [Coral Springs, Florida](https://greenmtnboy.github.io/tree_reporting/#/?city=USCRS)
- [Portland, Maine](https://greenmtnboy.github.io/tree_reporting/#/?city=USPWM)

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
| San Francisco | SF Open Data Portal - San Francisco Street Tree Inventory | https://data.sf.gov/City-Infrastructure/San-Francisco-Street-Tree-Inventory/tkzw-k3nq |
| New York City | NYC Open Data - Street Tree Census | https://data.cityofnewyork.us/Environment/2015-Street-Tree-Census-Tree-Data/uvpi-gqnh |
| Boston | Analyze Boston - Trees | https://data.boston.gov/dataset/bprd-trees |
| Cambridge, MA | Cambridge Open Data - Trees | https://data.cambridgema.gov/ |
| Cambridge (MIT) | MIT Facility Information Systems - Campus Trees (no licence stated; displayed with attribution only) | https://services5.arcgis.com/FXrk7QY7CN707pF1/arcgis/rest/services/NEW_Administrative_Basemap_Layers/FeatureServer/56 |
| Brookline, MA | Brookline GIS - Tree Viewer | ArcGIS FeatureServer |
| Somerville, MA | City of Somerville - TreeKeeper public tree inventory | https://somervillema.treekeepersoftware.com/ |
| Belmont, MA | Town of Belmont - TreeKeeper public tree inventory | https://belmontma.treekeepersoftware.com/ |
| Watertown, MA | City of Watertown - 2022 Tree Inventory (MapGeo) | https://watertownma.mapgeo.io/ |
| Salem, MA | City of Salem (SalemGIS, Salem DPW) - Salem Street Trees (no licence stated; displayed with attribution only) | https://www.arcgis.com/home/item.html?id=df1d66111f2d481db82564d5b820dee0 |
| Dedham, MA | Town of Dedham GIS - Tree Inventory (no licence stated; displayed with attribution only) | https://www.arcgis.com/home/item.html?id=9d23e28c88984fa0903da95792883d4a |
| East Lansing, MI | City of East Lansing - Trees, current status (no licence stated; displayed with attribution only) | https://gis2.cityofeastlansing.com/arcgis/rest/services/TREES_STATUS/FeatureServer/0 |
| Beaverton, OR | City of Beaverton - Tree Inventory (no licence stated; displayed with attribution only) | https://gisweb.beavertonoregon.gov/server/rest/services/Hosted/Tree_Inventory_Hosted/FeatureServer/0 |
| Plainfield, IL | Plainfield Park District - Public Trees, park trees only (no licence stated; displayed with attribution only) | https://services8.arcgis.com/eOqUuAWRL4PqiMCt/arcgis/rest/services/Plainfield_Public_Trees/FeatureServer/0 |
| Arlington, MA | Town of Arlington GIS - Public Shade Trees (no licence stated; displayed with attribution only) | https://www.arcgis.com/home/item.html?id=dc385c1084d1432ebc792ffcb20bcfa4 |
| Boston (Arboretum) | Arnold Arboretum / Harvard GIS | https://gis.arboretum.harvard.edu/ |
| Boston (Northeastern) | Northeastern University Arboretum Viewer (ArcGIS Online) | https://experience.arcgis.com/experience/81872a0c5c044ba5a70613cc1086bff3 |
| Boston (notable trees) | Tree Boston - Big Old Trees of Boston (volunteer registry) | https://treeboston.org/bot/ |
| Burlington VT | City of Burlington GIS - Tree Sites | https://maps.burlingtonvt.gov/ |
| Washington, DC | Open Data DC - Urban Forestry Street Trees | https://opendata.dc.gov/datasets/DCGIS::urban-forestry-street-trees |
| Los Angeles | LA GeoHub - Trees (Bureau of Street Services) | https://www.arcgis.com/home/item.html?id=266c6255b1fc4ae8b8f100d8696e1fa4 |
| West Hollywood | City of West Hollywood - City Tree Inventory (2015, CC0) | https://data.weho.org/d/qqwt-wx9z |
| Tempe | City of Tempe - Tree Inventory | https://data.tempe.gov/datasets/tempegov::tree-inventory |
| Denver | Denver Open Data - Parks, Medians, and Parkway Trees | https://opendata-geospatialdenver.hub.arcgis.com/datasets/geospatialDenver::parks-medians-and-parkway-trees |
| Boulder, CO | City of Boulder - Tree Inventory Open Data (ArcGIS, CC0) | https://www.arcgis.com/home/item.html?id=dbbae8bdb0a44d17934243b88e85ef2b |
| Naperville, IL | City of Naperville - Right of Way Tree Inventory (ArcGIS, CC0) | https://www.arcgis.com/home/item.html?id=9ee23fb99f1d4391824b66fddc2f10f1 |
| Columbus, OH | City of Columbus Recreation and Parks, Urban Forestry - Public Owned Trees (ArcGIS, CC0) | https://www.arcgis.com/home/item.html?id=674e4a358e8042f69a734f229a93823c |
| Eugene, OR | City of Eugene, Parks and Open Space - Public Trees (ArcGIS) | https://www.arcgis.com/home/item.html?id=defd8da338a443818b6a6f8b65b8076b |
| Portland, OR | City of Portland, Portland Parks & Recreation Urban Forestry - Street Tree Inventory (Active Records) and Parks Tree Inventory (ArcGIS) | https://www.arcgis.com/home/item.html?id=c2b02440053f459db71e1109b9947e51 |
| Seattle, WA | City of Seattle - SDOT Trees (in service) and Seattle Parks and Recreation Tree View (ArcGIS, Seattle Open Data Terms of Use) | https://cos-data.seattle.gov/d/gapw-b2rf |
| Philadelphia, PA | Philadelphia Parks & Recreation - Tree Inventory, newest yearly layer (OpenDataPhilly, City of Philadelphia terms of use) | https://opendataphilly.org/datasets/philadelphia-tree-inventory/ |
| Pittsburgh, PA | City of Pittsburgh, Department of Public Works Forestry Division - City of Pittsburgh Trees (WPRDC, CC BY) | https://data.wprdc.org/dataset/city-trees |
| San Jose, CA | City of San José - Street Tree (ArcGIS, CC BY) | https://data.sanjoseca.gov/dataset/street-tree |
| Sacramento, CA | City of Sacramento Public Works, Urban Forestry - City Maintained Trees (ArcGIS, City of Sacramento Open Data Terms of Use) | https://www.arcgis.com/home/item.html?id=b9b716e09b5048179ab648bb4518452b |
| Baltimore, MD | Baltimore City Recreation & Parks, Forestry - Street Tree Inventory (ArcGIS) | https://gis.baltimorecity.gov/egis/rest/services/Foresty/TreeInventory_Public/MapServer/0 |
| Stoughton | City of Stoughton - Public Tree Inventory (ArcGIS) | https://experience.arcgis.com/experience/f9b95eddac104c708619ccaf4c2c9de0 |
| Austin, TX | City of Austin - Tree Inventory (Socrata, public domain) | https://data.austintexas.gov/d/wrik-xasw |
| Houston, TX | City of Houston Parks & Recreation, Urban Forestry - street-tree inventory (ArcGIS) | https://geohwp.houstontx.gov/arcgis/rest/services/03_BaseData_External/Land_Base/FeatureServer/0 |
| Durham, NC | City of Durham - Trees & Planting Sites (ArcGIS) | https://webgis2.durhamnc.gov/server/rest/services/PublicServices/Environmental/FeatureServer/11 |
| Charlotte, NC | City of Charlotte - Landscape Management Trees (ArcGIS) | https://gis.charlottenc.gov/arcgis/rest/services/ENG/LSM_Trees/MapServer/0 |
| Atlanta, GA | Trees Atlanta - Plant Inventory (ArcGIS) | https://www.arcgis.com/home/item.html?id=9a8404b7b48d499ab76b36c237434542 |
| New Orleans, LA | City of New Orleans - Parks and Parkways tree assets (311 asset service, ArcGIS) | https://maps.nola.gov/server/rest/services/OpenGov/OpenGov311_Assets/MapServer/7 |
| Naples, FL | City of Naples - City Trees (ArcGIS) | https://g.naplesgov.com/arcgis/rest/services/Community_Services/CityTrees/FeatureServer/0 |
| Vancouver | Vancouver Open Data - Public Trees | https://opendata.vancouver.ca/explore/dataset/public-trees/ |
| Calgary | City of Calgary Open Data - Public Trees | https://data.calgary.ca/Environment/Public-Trees/tfs4-3wwa |
| Edmonton | City of Edmonton Open Data - Trees | https://data.edmonton.ca/Environmental-Services/Trees/eecg-fc54 |
| Edmonton (St. Albert) | City of St. Albert - Tree Inventory (Open Data Licence - City of St. Albert) | https://www.arcgis.com/home/item.html?id=4cd6fa57df0b44b895ca6dd69118f10a |
| Winnipeg | City of Winnipeg Open Data - Tree Inventory | https://data.winnipeg.ca/Parks/Tree-Inventory/hfwk-jp4h |
| Toronto | City of Toronto Open Data - Street Tree Data | https://open.toronto.ca/dataset/street-tree-data/ |
| Montreal | Donnees Montreal - Arbres publics sur le territoire de la Ville (CC-BY 4.0) | https://donnees.montreal.ca/dataset/arbres |
| Quebec City | Donnees Quebec - Ville de Quebec, Arbres repertories (CC-BY 4.0) | https://www.donneesquebec.ca/recherche/dataset/vque_arbrerepertorie |
| Longueuil | Donnees Quebec - Ville de Longueuil, Arbres (CC-BY 4.0) | https://www.donneesquebec.ca/recherche/dataset/arbres |
| Halifax | Halifax Regional Municipality Open Data - Public Trees | https://catalogue-hrm.opendata.arcgis.com/datasets/public-trees |
| Kingston | City of Kingston Open Data - City Owned Trees | https://opendatakingston.cityofkingston.ca/explore/dataset/city-owned-trees/ |
| Lethbridge | City of Lethbridge Open Data - Trees | https://opendata.lethbridge.ca/datasets/trees |
| Victoria | City of Victoria Open Data - Tree Species (Parks trees database) | https://opendata.victoria.ca/datasets/tree-species-parks-trees-database |
| Kelowna | City of Kelowna Open Data - Tree Inventory | https://opendata.kelowna.ca/datasets/tree-inventory |
| New Westminster | City of New Westminster Open Data - Tree Inventory (Active Trees) | https://opendata.newwestcity.ca/datasets/tree-inventory-active-trees |
| Mississauga | City of Mississauga Open Data - City Owned Tree Inventory | https://data.mississauga.ca/datasets/city-owned-tree-inventory |
| Ottawa | City of Ottawa Open Data - Tree Inventory | https://open.ottawa.ca/datasets/tree-inventory |
| Burlington ON | City of Burlington Open Data - City Owned Trees | https://navburl-burlington.opendata.arcgis.com/datasets/city-owned-trees |
| Ajax | Town of Ajax Open Data - Town Trees | https://opendata.ajax.ca/datasets/town-trees |
| Moncton | City of Moncton Open Data - Trees | https://open.moncton.ca/datasets/trees |
| Tokyo | Tokyo Open Data Catalog - Bureau of Construction, Street Trees on Metropolitan Roads (CC BY 4.0) | https://catalog.data.metro.tokyo.lg.jp/dataset/t000014d2000000029 |
| Taipei | Taipei City Open Data - Parks and Street Lights Office, Street and Park Trees (OGDL-Taiwan-1.0) | https://data.gov.tw/dataset/128274 |
| Bogotá | Datos Abiertos Bogotá - Jardín Botánico de Bogotá, Arbolado Urbano (CC BY 4.0) | https://datosabiertos.bogota.gov.co/dataset/censo-arbolado-urbano |
| Copenhagen | City of Copenhagen Open Data - Tree Register (trae_basis, CC BY 4.0) | https://www.opendata.dk/city-of-copenhagen/trae_basis |
| Milan | Comune di Milano - Territorio: localizzazione degli alberi (DS2484, 2023-03-31 release, CC BY 4.0) | https://dati.comune.milano.it/dataset/ds2484_infogeo_alberi_localizzazione |
| Milan (notable trees) | Comune di Milano - Gli alberi monumentali nel Comune di Milano (DS2489, CC BY 4.0) | https://dati.comune.milano.it/dataset/ds2489-gli-alberi-monumentali-nel-comune-di-milano |
| Prague | Geoportál Praha / IPR Praha - Sdílená data o zeleni, vegetační prvky (CC BY, datový podklad © IPR Praha) | https://opendata-iprpraha.hub.arcgis.com/datasets/iprpraha::sdílená-data-o-zeleni-vegetační-prvky-body-1 |
| Helsinki | Helsinki Region Infoshare - City of Helsinki Tree Register (Puurekisteri, CC BY 4.0) | https://hri.fi/data/en_GB/dataset/helsingin-kaupungin-puurekisteri |
| London | London Datastore - Public Realm Trees | https://data.london.gov.uk/dataset/2r45m |
| Nottingham | Nottingham City Council - Trees (NCC Maintained) (ArcGIS, OGL v3) | https://www.arcgis.com/home/item.html?id=854f59402043403eabce650cc7ab1150 |
| Amsterdam | Amsterdam Data Portal - Bomen (Stamgegevens) | https://api.data.amsterdam.nl/v1/bomen/stamgegevens/ |
| Berlin | Berlin Geodateninfrastruktur - Baumbestand (WFS) | https://gdi.berlin.de/services/wfs/baumbestand |
| Vienna | Stadt Wien Open Data - Baumkataster (BAUMKATOGD, CC BY 4.0; Datenquelle: Stadt Wien - data.wien.gv.at) | https://www.data.gv.at/katalog/dataset/c91a4635-8b7d-43fe-9b27-d95dec8392a7 |
| Zürich | Stadt Zürich Open Data - Baumkataster (Grün Stadt Zürich, CC0) | https://data.stadt-zuerich.ch/dataset/geo_baumkataster |
| Basel | Stadtgärtnerei Basel-Stadt - Baumkataster: Baumbestand (data.bs.ch, CC BY 4.0, Geodaten Kanton Basel-Stadt) | https://data.bs.ch/explore/dataset/100052/ |
| Frankfurt | Stadt Frankfurt am Main Offene Daten - Baumkataster (Grünflächenamt, dl-de/by-2.0) | https://offenedaten.frankfurt.de/dcat/dataset/de-he-frankfurtam-baumkataster_op.html |
| Cologne | Stadt Köln Offene Daten - Baumkataster (Amt für Landschaftspflege und Grünflächen, Datenlizenz Deutschland Zero 2.0) | https://offenedaten-koeln.de/dataset/baumkataster-koeln-fortlaufend-aktualisiert |
| Madrid | Ayuntamiento de Madrid - Arbolado en parques y zonas verdes (detalle), datos.madrid.es (CC BY 4.0) | https://datos.madrid.es/dataset/300761-0-arbolado-especies |
| Lyon | Métropole de Lyon - Arbres d'alignement (data.grandlyon.com WFS, Licence Ouverte) | https://data.grandlyon.com/portail/fr/jeux-de-donnees/arbres-alignement-metropole-lyon/info |
| Hamburg | Freie und Hansestadt Hamburg, BUKEA - Straßenbaumkataster (WFS, dl-de/by-2.0) | http://www.hamburg.de/fhh-permalink/1065232 |
| Christchurch | Christchurch City Council, Tree (OpenData) (CC BY 4.0) | https://www.arcgis.com/home/item.html?id=0c557c04453e435ba1e50389db0ede90 |
| Fingal | Fingal County Council Open Data - Trees_FCC (CC BY 4.0; locations approximate) | https://data.fingal.ie/datasets/FingalCoCo::trees-fcc-1 |
| Canberra | ACT Government - ACTGOV Tree Assets (CC BY 4.0) | https://www.arcgis.com/home/item.html?id=d401a7266ba74dc18c1902ae6e71fe90 |
| Lisbon | Câmara Municipal de Lisboa - Arvoredo (Lisboa Aberta, CC0) | https://dados.cm-lisboa.pt/dataset/arvoredo |
| Ghent | Stad Gent Groendienst - Locaties bomen Gent (Modellicentie Gratis Hergebruik) | https://data.stad.gent/explore/dataset/locaties-bomen-gent/ |
| Bristol | Bristol City Council - Trees (Open Government Licence v3.0) | https://www.arcgis.com/home/item.html?id=7a99218a4bf347ff948f0e5882406a8c |
| Hamilton | City of Hamilton - Public Tree Inventory (City of Hamilton Open Data Licence v1.1) | https://open.hamilton.ca/datasets/public-tree-inventory |
| Utrecht | Gemeente Utrecht - Bomenkaart 2026 (CC BY 4.0) | https://open.utrecht.nl/dataset/bomenkaart-2026/ea248c2e-7ae4-47b3-bd57-f34b2d876667 |
| Toulouse | Toulouse Métropole - Arbres urbains (Licence Ouverte v2.0) | https://data.toulouse-metropole.fr/explore/dataset/arbres-urbains/ |
| Geneva | SITG - Inventaire cantonal des arbres (Ville de Genève, Conservatoire et Jardin botaniques; Données SITG, open access; 1976 paper-inventory records omitted) | https://sitg.ge.ch/donnees/sipv-ica-arbre-isole |
| Leipzig | Stadt Leipzig - Baumkataster (dl-de/by-2.0) | https://opendata.leipzig.de/dataset/baumkataster-stadt-leipzig1 |
| Wellington | Wellington City Council - WCC Trees (CC BY 4.0) | https://data-wcc.opendata.arcgis.com/datasets/93ca9097fd4b487e9fcf55ad75941136_53 |
| Bordeaux | Bordeaux Métropole - Patrimoine arboré (Licence Ouverte) | https://opendata.bordeaux-metropole.fr/explore/dataset/ec_arbre_p/ |
| Strasbourg | Ville et Eurométropole de Strasbourg - Patrimoine arboré (Licence Ouverte v2.0) | https://data.strasbourg.eu/explore/dataset/patrimoine_arbore/ |
| Eindhoven | Gemeente Eindhoven - Bomen (public domain) | https://data.eindhoven.nl/explore/dataset/bomen/ |
| Bern | Stadt Bern - Baumkataster (Stadtgrün Bern; opendata.swiss open use) | https://opendata.swiss/en/dataset/baumkataster1 |
| Rennes | Rennes Métropole - Arbres (ODbL 1.0) | https://data.rennesmetropole.fr/explore/dataset/arbre/ |
| Antwerp | Stad Antwerpen - boom (Gratis Open Data Licentie; bron stad Antwerpen) | https://portaal-stadantwerpen.opendata.arcgis.com/datasets/stadAntwerpen::boom |
| San Diego | City of San Diego Transportation Department, Urban Forestry - Trees (Street Trees) (no licence stated; displayed with attribution only, not licensed for reuse) | https://webmaps.sandiego.gov/arcgis/rest/services/DSD/Environment/MapServer/20 |
| Kitchener | City of Kitchener - Tree Inventory (Open Government Licence - The Corporation of the City of Kitchener v1.0) | https://open-kitchenergis.opendata.arcgis.com/datasets/KitchenerGIS::tree-inventory |
| Sydney | City of Sydney - Trees (CC BY 4.0) | https://data.cityofsydney.nsw.gov.au/datasets/cityofsydney::trees |
| Louisville | Louisville Metro Urban Forestry - Urban Forestry Trees, public data view (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=a04b388a30d84f8baeba9c9fa67b639e |
| Montpellier | Montpellier Méditerranée Métropole - Arbres de Montpellier (ODbL) | https://data.montpellier3m.fr/dataset/arbres-de-montpellier |
| Grenoble | Ville de Grenoble and Grenoble-Alpes Métropole - Arbres and Patrimoine arboré du territoire métropolitain (ODbL 1.0) | https://data.metropolegrenoble.fr/visualisation/information/?id=arbres-grenoble |
| Madison | City of Madison Parks Department, Urban Forestry - Street Trees (open data under Madison General Ordinances 3.72; source attributed) | https://www.arcgis.com/home/item.html?id=b700541a20e446839b18d62426c266a3 |
| Richmond | City of Richmond Public Works, Urban Forestry - Tree Inventory, public view (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=902a0284181e4500aa220a6037d06d3d |
| Spokane | City of Spokane Parks Department - Tree Inventory (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=d84eda620819425185d114a06a94d047 |
| Waterloo | City of Waterloo - Street Tree Inventory (Waterloo Open Data User Licence) | https://data.waterloo.ca/datasets/2447415303e74bb9acdf0f43c2236b72 |
| Nantes | Nantes Métropole - Patrimoine arboré de Nantes Métropole et de la Ville de Nantes (Licence Ouverte) | https://data.nantesmetropole.fr/explore/dataset/244400404_patrimoine-arbore-nantes-metropole/ |
| Santa Monica | City of Santa Monica, Public Landscape Division - Trees (ODC-By 1.0) | https://www.arcgis.com/home/item.html?id=64f5db2cbb224f6d87387999a300b962 |
| Oakville | Town of Oakville - Trees (Town of Oakville Open Data Licence; contains information licensed under the Open Government Licence - Town of Oakville) | https://www.arcgis.com/home/item.html?id=64fabcdb4feb40139ad20d728bfef2d4 |
| Rochester, NY | City of Rochester Department of Environmental Services, Forestry Division - Trees Open Data (DataROC Terms of Use). The data made available here has been modified for use from its original source, which is the official website of the City of Rochester, NY. The City of Rochester makes no claims as to the accuracy, timeliness, content, or completeness of any of the data provided in this application. The City of Rochester makes no warranty of the accuracy or fitness for any particular use; nor are any such warranties to be implied or inferred with respect to the information or data furnished herein. The data is subject to change as modifications and updates are complete. | https://www.arcgis.com/home/item.html?id=4c209944e2984b4a908a14b0cbe48075 |
| Syracuse | City of Syracuse Department of Parks & Recreation, Forestry - Tree Inventory (TreeKeeper; Open Data Syracuse Terms of Use). The City of Syracuse makes no representation, warranty or guarantee relating to the data or analyses derived from these data. | https://data.syr.gov/pages/termsofuse |
| Las Vegas | City of Las Vegas - Park Trees (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=05622dec3c454882abf26c4e0ec8370f |
| Brest | Brest métropole - Les arbres (Licence Ouverte v2.0; species and position only) | https://www.data.gouv.fr/datasets/les-arbres/ |
| Groningen | Gemeente Groningen - Bomen (CC BY 4.0) | https://data.groningen.nl/dataset/bomen/04da3775-07f0-4388-bc12-7cd7536b04cd |
| Providence | City of Providence Parks Department, Forestry Division - Tree Inventory (TreeKeeper; no licence stated; displayed with attribution only, not licensed for reuse) | https://providenceri.treekeepersoftware.com/ |
| Guelph | City of Guelph Open Data - Trees (Open Government Licence - City of Guelph; contains information licensed under the Open Government Licence - City of Guelph) | https://explore.guelph.ca/datasets/cityofguelph::trees |
| Belfast | Belfast City Council - Belfast Trees (Open Government Licence v3.0) | https://www.opendatani.gov.uk/dataset/belfast-trees |
| Edinburgh | City of Edinburgh Council - Trees (Open Government Licence v3.0; Copyright City of Edinburgh Council, contains Ordnance Survey data © Crown copyright and database right; survey predates 2017) | https://www.arcgis.com/home/item.html?id=6c224ada230b4d349cfd739015c7846d |
| Oakland | City of Oakland Public Works, Park & Tree Services - Oakland Public Tree Inventory, August 2023 (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=1aeca241016e483cbfac4f2d1974692e |
| Riverside | City of Riverside Open Data - Street, Park, Utility-Line and Miscellaneous Trees, April 2023 (no licence stated; the City Open Data Policy asks for the credit "Data provided by the City of Riverside") | https://www.arcgis.com/home/item.html?id=18ac0db05b7340d1b463d953fdc672fc |
| Cockburn | City of Cockburn - Street Trees (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=4e25763b23fe47c1bb928920152789a9 |
| Basingstoke | Basingstoke and Deane Borough Council - BDBC Trees (no licence stated; displayed with attribution only, not licensed for reuse) | https://gis.basingstoke.gov.uk/server/rest/services/AGOL/TreesInspections/FeatureServer/6 |
| York | City of York Council - Council owned trees and Private trees (Open Government Licence v3.0; contains Ordnance Survey data © Crown copyright and database right) | https://data.yorkopendata.org/dataset/council-owned-trees |
| Hastings | Hastings District Council - Hastings DC Trees (CC BY 4.0, per the council's open data portal) | https://hastings-dc-open-data-hdcgis.hub.arcgis.com/ |
| Red Deer | City of Red Deer - PARK_Trees_inservice (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=4ede9512512a4d5187045a35e063a92d |
| Fareham | Fareham Borough Council - Council Trees (Open Government Licence v3.0; contains OS data © Crown copyright and database right) | https://maps.fareham.gov.uk/server/rest/services/Corporate/Trees/MapServer/1 |
| Launceston | City of Launceston - Trees (CC BY 4.0, per the council's copyright notice for its mapping data) | https://opendata.launceston.tas.gov.au |
| Macedon Ranges | Macedon Ranges Shire Council - Macedon Ranges Shire Trees on data.gov.au (CC BY 2.5 AU) | https://data.gov.au/data/dataset/macedon-ranges-shire-trees |
| Fredericton | City of Fredericton - Tree Inventory (Open Government License - City of Fredericton) | https://data-fredericton.opendata.arcgis.com/datasets/1778707bb5a44f72b40aebcb297da092_37 |
| Medicine Hat | City of Medicine Hat - Park Tree Inventory (City of Medicine Hat Open Data Licence) | https://www.arcgis.com/home/item.html?id=af05aa132ad24367a7d32d46af50bb38 |
| Greater Shepparton | Greater Shepparton City Council - Street and Park Trees on data.gov.au (CC BY 4.0) | https://data.gov.au/data/dataset/greater-shepparton-city-council-street-and-park-trees |
| Sioux Falls | City of Sioux Falls GIS - Trees (CC BY 4.0) | https://www.arcgis.com/home/item.html?id=c880d62ae5fb4652b1f8e6cbca244107 |
| Surrey | City of Surrey - Park Specimen Trees (Open Government License - City of Surrey; contains information licensed under the Open Government License - City of Surrey) | https://opendata-surrey.hub.arcgis.com/datasets/626b2212e0594eadb8a86ccf0b9640c2 |
| Latrobe City | Latrobe City Council - Tree Inventory (CC BY 4.0) | https://latrobecc.maps.arcgis.com/home/item.html?id=f41d6a33a5a545e9b84aca02d8448377 |
| Barrie | City of Barrie Open Data - Tree (Open Government Licence - Barrie; contains information licensed under the Open Government Licence - Barrie) | https://opendata.barrie.ca/datasets/d053284adf6341d7939486d13bfd6911 |
| Langley Township | Township of Langley - Tree Inventory (Open Government License - Township of Langley; contains information licensed under the Open Government License - Township of Langley) | https://www.arcgis.com/home/item.html?id=07ed48e3e633476aba03b1a800d616af |
| Maple Ridge | City of Maple Ridge - Street Tree (Open Government Licence - Maple Ridge; contains information licensed under the Open Government Licence - Maple Ridge) | https://www.arcgis.com/home/item.html?id=290505c676d64cf09989eca1320aedd3 |
| Prince George | City of Prince George - Trees (Open Government License - City of Prince George; contains information licensed under the Open Government License - City of Prince George) | https://www.arcgis.com/home/item.html?id=382244b24c054f0ab92ca9d76facd44c |
| Penticton | City of Penticton - City Owned Trees (Open Government Licence - City of Penticton; contains information licenced under the Open Government Licence - City of Penticton) | https://www.arcgis.com/home/item.html?id=e0905b0677bf4b468b57fb52d2d725db |
| Fort St. John | City of Fort St. John - Tree Inventory (Open Government License - City of Fort St. John; contains information licensed under the Open Government License - City of Fort St. John) | https://data-fortstjohn.opendata.arcgis.com/datasets/c4ee1a5aa7c7496b8a70544a8fcf4e1a_0/about |
| Welland | City of Welland Open Data - Welland Trees (Open Government Licence - City of Welland; contains information licensed under the Open Government Licence - City of Welland) | https://www.arcgis.com/home/item.html?id=afcb6b5308de40b1830a3ebb2369a83b |
| Bassendean | Town of Bassendean - All Trees (street, park and private-land trees) (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=5973d2d7b04648a4bb000fe13cc4db74 |
| Colac Otway | Colac Otway Shire - Colac Otway Shire Trees (data.gov.au, CC BY 3.0 AU) | https://data.gov.au/data/dataset/colac-otway-shire-trees |
| Barcelona | Ajuntament de Barcelona - Atles de la Biodiversitat, tree inventory (Open Data BCN, CC BY 4.0) | https://opendata-ajuntament.barcelona.cat/data/en/dataset/atles-biodiversitat-arbrat-geometries |
| Singapore | National Parks Board - Heritage Trees via data.gov.sg (Singapore Open Data Licence v1.0) | https://data.gov.sg/datasets/d_644ff187b6d14d6316f47284a4a6c81f/view |
| Baton Rouge | East Baton Rouge City-Parish GIS - Tree Inventory (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=51b996ab35bc44bfb8d9e8d032b458c7 |
| Lansing | City of Lansing - Trees (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=156f8898d26a471dae7c858e46851b29 |
| Boise | City of Boise Parks and Recreation - Managed Park and Street Trees (CC BY 4.0) | https://opendata.cityofboise.org/datasets/61e4dc96bc0745ecb42c3f3892728bd6 |
| Port St. Lucie | City of Port St. Lucie - PSL Trees (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=767ea62a6ae34d898185ec5050319c0d |
| Missoula | City of Missoula - Tree Locations (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=478d76729bfc4236ae12bafb644fb635 |
| Bismarck | City of Bismarck Forestry - Tree Inventory (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=c84565e8695d41fbbde0973470cefab6 |
| Springfield | City of Springfield, MO - Tree Inventory (no licence stated; displayed with attribution only, not licensed for reuse). This data has been modified for use from its original source, www.springfieldmo.gov; the City makes no claims as to its content, accuracy, timeliness or completeness, and it is used at one's own risk | https://gisdata-cosmo.opendata.arcgis.com/datasets/705ae4c71b014910a0617ba5c7b25b98_34 |
| Grand Junction | City of Grand Junction GIS and Parks Forestry - Trees (CC0 1.0) | https://www.arcgis.com/home/item.html?id=cef93c83233749eb9265914aca06b35c |
| Seoul | Seoul Metropolitan Government - Seoul Street Tree Locations, Seoul Open Data Plaza OA-23040 (no restriction on use) | https://data.seoul.go.kr/dataList/OA-23040/F/1/datasetView.do |
| York Region | Regional Municipality of York - Street Trees (York Region Open Data Licence) | https://insights-york.opendata.arcgis.com/datasets/york::street-trees |
| Whitby | Town of Whitby - Trees (Open Government Licence - The Corporation of the Town of Whitby) | https://www.arcgis.com/home/item.html?id=5be6efcf4f264639874bf07009f155d2 |
| Salem | City of Salem - City Trees (resale prohibited by the City; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=e5397978d59f46ec96f72a1dfc660919 |
| Oxnard | City of Oxnard GIS Division - Trees (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=a5aa2d1dfd344ef79d61507d33cdbc02 |
| Redlands | City of Redlands - Street Trees (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=9f9d4ad74a0d44fba8bf27c829e810f2 |
| Davenport | City of Davenport GIS - Trees (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=1e73252e386144799b39e95dbaa684ef |
| Plano | City of Plano - Shade Map Trees, PlanIT Geo inventory 2023 (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=a7e06ad57e2747738fb2b2117e23d68e |
| Bend | City of Bend - Volunteer Tree Inventory (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=3a518917e1fc407cbc5d72ab4260101b |
| Orland Park | Village of Orland Park - Public Trees (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=780d9c0a3883498b82e046ef231144c9 |
| Evanston | City of Evanston Open Data - Trees (CC BY 4.0) | https://www.arcgis.com/home/item.html?id=845a3acb3dc0442a80b219691889f992 |
| Moorhead | City of Moorhead GIS - TreePlotter inventory (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=efd9f39819d54020b610ef0f5f0c0c04 |
| Sarasota County | Sarasota County GIS - TreeInventory (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=d83796771ba64ea88f958c058ddcfa79 |
| Pinellas County | Pinellas County Public Works - Tree Inventory (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=4d5bed2b3a3c4985aa4ffb2c7670fd8b |
| Bozeman | City of Bozeman Forestry Division - Tree Inventory (no licence stated; displayed with attribution only, not licensed for reuse) | https://gisweb.bozeman.net/arcgis/rest/services/Public/Forestry_Public_Web_Map/MapServer/2 |
| Loveland | City of Loveland GIS - City of Loveland Trees (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=0f66e47e8c7a4e60804ec871d5827ff1 |
| San Mateo | City of San Mateo Public Works - Street Trees (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=67c8b57d2d91459c9f951df9de961a06 |
| St. Louis Park | City of St. Louis Park - Trees (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=d8e68ae495d24f06a9adc233bf6e4c55 |
| Coral Springs | City of Coral Springs - Cityworks Horticulture tree inventory (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=5a5fd0d701e04a75ba945bf94e71db9f |
| Portland, ME | City of Portland Forestry - City Owned Trees (no licence stated; displayed with attribution only, not licensed for reuse) | https://gis.portlandmaine.gov/portal/home/item.html?id=b12173632dfd448aa7a5bea80b6da47b |
| Bendigo | City of Greater Bendigo - Environment - Trees (CC BY 2.5 AU) | https://data.gov.au/data/dataset/city-of-greater-bendigo-environment-trees |
| Melbourne | Melbourne Open Data - Urban Forest Trees | https://data.melbourne.vic.gov.au/explore/dataset/trees-with-species-and-dimensions-urban-forest/ |
| Melbourne (Port Phillip) | City of Port Phillip - Trees (CC BY 2.5 AU) | https://data.gov.au/data/dataset/city-of-port-phillip-trees |
| Melbourne (Yarra) | City of Yarra - Street and Park Trees (CC BY 4.0) | https://data.gov.au/data/dataset/yarra-street-park-trees |
| Melbourne (Glen Eira) | Glen Eira City Council - Park and Street Trees (CC BY 2.5 AU) | https://data.gov.au/data/dataset/park-and-street-trees |
| Melbourne (Hobsons Bay) | Hobsons Bay City Council - Street and Park Trees (CC BY 2.5 AU) | https://data.gov.au/data/dataset/hbcc-street-and-park-trees |
| Melbourne (Brimbank) | Brimbank City Council - Street Trees (data.gov.au, 2019, CC BY 2.5 AU) | https://data.gov.au/data/dataset/brimbank-street-trees |
| Melbourne (Brimbank) | Brimbank City Council - Open Space Trees (data.gov.au, 2019, CC BY 2.5 AU) | https://data.gov.au/data/dataset/brimbank-open-space-trees |
| Melbourne (Manningham) | Manningham City Council - Street Trees (data.gov.au, 2019, CC BY 3.0 AU) | https://data.gov.au/data/dataset/manningham-streettrees |
| Adelaide | City of Adelaide - Street Trees (data.sa.gov.au, CC BY 4.0) | https://data.sa.gov.au/data/dataset/street-trees |
| Adelaide (Unley) | City of Unley - Street Trees (ArcGIS; no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=60272d3be3d34d7598a05f495fe347be |
| Adelaide (Burnside) | City of Burnside - Burnside Street Trees (data.sa.gov.au, 2019, CC BY 4.0) | https://data.sa.gov.au/data/dataset/burnside-street-trees |
| Perth | City of Perth - Trees in the City (ArcGIS, CC BY 4.0) | https://www.arcgis.com/home/item.html?id=c5ce51d9789a4e9a8510acb8c8f3ecf4 |
| Ballarat | City of Ballarat Open Data - Trees (CC BY) | https://data.ballarat.vic.gov.au/explore/dataset/trees/ |
| Ballarat (notable trees) | City of Ballarat Open Data - Exceptional Tree Register (CC BY 3.0 AU) | https://data.ballarat.vic.gov.au/explore/dataset/exceptional-tree-register/ |
| Wodonga | City of Wodonga - Street and Reserve Trees (ArcGIS, CC BY 4.0) | https://www.arcgis.com/home/item.html?id=f6e5ca95559a4253bf7d25897bf8ed15 |
| Wodonga (Albury) | Albury City Council - Trees (Public) (ArcGIS; no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=605dc59091604f4b8aa99eaf56d71c3a |
| Paris | Paris Open Data - Les Arbres | https://opendata.paris.fr/explore/dataset/les-arbres/ |
| Buenos Aires | Buenos Aires Data - Arbolado publico lineal | https://data.buenosaires.gob.ar/dataset/arbolado-publico-lineal |
| Athens | City of Athens Open Data - Trees of the National Garden | https://opendata.cityofathens.gr/dataset/gis-athens-8303d4c8-371b-11ec-b388-0242ac120009 |
| Milos | Community submissions (no municipal inventory published) | (contributed via the app) |
| Santorini | Community submissions (no municipal inventory published) | (contributed via the app) |
| Newton, Milton, Peabody, Lynn, Medford, Quincy, Saugus, Needham, Swampscott, Waltham, Marblehead, Revere, Malden, Everett, Chelsea, Winthrop, Beverly, Lynnfield, Melrose, Wellesley, Nahant, Danvers, Winchester, Canton (MA) | Community submissions, OpenStreetMap and the Boston metro's aerial-imagery predictions (no municipal inventory published) | (contributed via the app) |
| Chapel Hill (NC), Aurora (IL), Oswego (IL), Louisville (CO), Chula Vista (CA) | Community submissions and OpenStreetMap (no municipal inventory published) | (contributed via the app) |
| All cities (supplemental) | © OpenStreetMap contributors (ODbL) | https://www.openstreetmap.org/copyright |
| All cities | Community submissions, reviewed and approved before publication | (contributed via the app) |

Every tree row carries a `data_source` column identifying which of the above it came from.

### Landmarks

| City | Source | Link |
|------|--------|------|
| San Francisco | SF Open Data - Landmarks | https://data.sfgov.org/Geographic-Locations-and-Boundaries/Landmarks/rzic-39gi |
| New York City | NYC Open Data - Landmarks | https://data.cityofnewyork.us/Housing-Development/Individual-Landmark-Sites/buis-pvji |
| Boston | Analyze Boston - Landmarks | https://data.boston.gov/dataset/92137315-e846-4c75-8c3d-2b7e93e38d03 |
| Burlington VT | City of Burlington - State Register of Historic Places + Nominatim geocoding | https://www.burlingtonvt.gov/ |
| Washington, DC | No landmark dataset in production yet | n/a |
| Los Angeles | No landmark dataset in production yet | n/a |
| Tempe | No landmark dataset in production yet | n/a |
| Denver | Denver Open Data - Historic Landmark Structures | https://opendata-geospatialdenver.hub.arcgis.com/datasets/geospatialDenver::historic-landmarks |
| Stoughton | City of Stoughton - Historical Landmarks (Stoughton Landmarks Commission) | https://stoughtonwi.maps.arcgis.com/home/item.html?id=0ac41bae1fac4f4abdb873deed6f8d75 |
| Columbus, OH | City of Columbus - Register of Historic Properties (CC0); National Park Service - National Register of Historic Places | https://maps2.columbus.gov/arcgis/rest/services/Schemas/Development/MapServer/0 |
| Eugene, OR | City of Eugene Planning Division - Historic Sites (City Landmarks and National Register) | https://www.arcgis.com/home/item.html?id=e012d77a3a624a1e9f1ce01c8bb0ae8d |
| Portland, OR | City of Portland Bureau of Planning and Sustainability - Historic and Conservation Landmarks; National Park Service - National Register of Historic Places | https://www.portlandmaps.com/od/rest/services/COP_OpenData_ZoningCode/MapServer/131 |
| Seattle, WA | City of Seattle - Landmarks (SDCI); National Park Service - National Register of Historic Places | https://www.arcgis.com/home/item.html?id=462a18bf1f9f459eb7fd8ea08c843880 |
| Philadelphia, PA | National Park Service - National Register of Historic Places, marked where the Philadelphia Register of Historic Places also designates them | https://opendataphilly.org/datasets/philadelphia-registered-historic-properties/ |
| Pittsburgh, PA | City of Pittsburgh - City Designated Individual Historic Sites; National Park Service - National Register of Historic Places | https://data.wprdc.org/dataset/city-designated-individual-historic-sites |
| San Jose, CA | City of San José - Historic Resources Inventory, City Landmarks (CC BY); National Park Service - National Register of Historic Places | https://data.sanjoseca.gov/dataset/historical-resources-inventory |
| Sacramento, CA | City of Sacramento - Register of Historic and Cultural Resources, Historic Landmarks; National Park Service - National Register of Historic Places | https://services5.arcgis.com/54falWtcpty3V47Z/arcgis/rest/services/Historic_Landmarks/FeatureServer/0 |
| Baltimore, MD | Baltimore City Landmark List (CHAP, Open Baltimore); National Park Service - National Register of Historic Places | https://data.baltimorecity.gov/datasets/8a570598d14c41ada202a6a0155bb0aa |
| Austin, TX | City of Austin - Historical Landmarks (Socrata) | https://data.austintexas.gov/d/8k49-bptr |
| Houston, TX | City of Houston - Historic Sites and Landmarks (Landmarks and Protected Landmarks) | https://services.arcgis.com/NummVBqZSIJKUeVR/arcgis/rest/services/COH_Historic_Sites_and_Landmarks_view/FeatureServer/6 |
| Durham, NC | Durham City-County Planning - Local Historic Landmarks and National Register listings | https://webgis2.durhamnc.gov/server/rest/services/PublicServices/Planning/MapServer/1 |
| Charlotte, NC | Charlotte-Mecklenburg Historic Landmarks Commission - designated properties (Mecklenburg County GIS) | https://meckgis.mecklenburgcountync.gov/server/rest/services/LocalHistoricProperty/FeatureServer/0 |
| Atlanta, GA | City of Atlanta - Landmark and Historic Building/Sites (Urban Design Commission) | https://gis.atlantaga.gov/dpcd/rest/services/OpenDataService1/FeatureServer/16 |
| New Orleans, LA | City of New Orleans - HDLC Landmarks (Historic District Landmarks Commission) | https://maps.nola.gov/server/rest/services/Administrative/Historic_Preservation/MapServer/0 |
| Naples, FL | City of Naples - Historic Buildings; National Park Service - National Register of Historic Places | https://g.naplesgov.com/arcgis/rest/services/Building/HistoricBuildings/MapServer/0 |
| Vancouver | Vancouver Open Data - Heritage Sites | https://opendata.vancouver.ca/explore/dataset/heritage-sites/ |
| Calgary | City of Calgary Open Data - Historic Resource | https://data.calgary.ca/Government/Historic-Resource/99yf-6c5u |
| Edmonton | City of Edmonton Open Data - Register and Inventory of Historic Resources | https://data.edmonton.ca/City-Administration/The-Register-and-Inventory-of-Historic-Resources-in/jgsn-dhai |
| Winnipeg | City of Winnipeg Open Data - Historical Resources | https://data.winnipeg.ca/Heritage/Historical-Resources/ptpx-kgiu |
| Toronto | City of Toronto Open Data - Places of Interest and Toronto Attractions | https://open.toronto.ca/dataset/places-of-interest-and-toronto-attractions/ |
| Montreal | Repertoire du patrimoine culturel du Quebec - immeubles classes et cites (CC-BY 4.0) | https://www.donneesquebec.ca/recherche/dataset/immeubles-patrimoniaux-classes-par-le-ministre-de-la-culture-et-des-communications |
| Quebec City | Repertoire du patrimoine culturel du Quebec - immeubles classes et cites (CC-BY 4.0) | https://www.donneesquebec.ca/recherche/dataset/immeubles-patrimoniaux-classes-par-le-ministre-de-la-culture-et-des-communications |
| Longueuil | Donnees Quebec - Ville de Longueuil, Parcs (CC-BY 4.0) | https://www.donneesquebec.ca/recherche/dataset/parcs-longueuil |
| Halifax | Halifax Regional Municipality Open Data - Heritage Properties | https://catalogue-hrm.opendata.arcgis.com/datasets/heritage-properties |
| Kingston | City of Kingston Open Data - Designated Heritage Site | https://opendatakingston.cityofkingston.ca/explore/dataset/designated-heritage-site/ |
| Lethbridge | City of Lethbridge Open Data - Historic Places | https://opendata.lethbridge.ca/datasets/historic-places |
| Victoria | City of Victoria Open Data - Prominent Heritage Sites (OCP) | https://opendata.victoria.ca/datasets/prominent-heritage-sites-ocp |
| Kelowna | City of Kelowna Open Data - Heritage Registry | https://opendata.kelowna.ca/datasets/heritage-registry |
| New Westminster | City of New Westminster Open Data - Heritage Register | https://opendata.newwestcity.ca/datasets/heritage-register |
| Mississauga | City of Mississauga Open Data - City Landmarks | https://data.mississauga.ca/datasets/city-landmarks |
| Ottawa | City of Ottawa Open Data - Cultural Spaces Inventory (Heritage) | https://open.ottawa.ca/datasets/ottawa::cultural-spaces-inventory-heritage |
| Burlington ON | City of Burlington Open Data - Heritage Properties | https://navburl-burlington.opendata.arcgis.com/datasets/heritage-properties |
| Ajax | Town of Ajax Open Data - Heritage Inventory | https://opendata.ajax.ca/datasets/heritage-inventory |
| Moncton | City of Moncton Open Data - Cultural Assets | https://open.moncton.ca/datasets/cultural-assets |
| Tokyo | Tokyo Open Data Catalog - Board of Education, Designated Cultural Properties and Historic Sites (CC BY 4.0) | https://catalog.data.metro.tokyo.lg.jp/dataset/t000021d0000000017 |
| Taipei | Bureau of Cultural Heritage, Ministry of Culture - Monuments and Historic Buildings (OGDL-Taiwan-1.0) | https://data.gov.tw/dataset/6246 |
| Bogotá | Datos Abiertos Bogotá - IDPC, Monumentos and Bienes de Interés Cultural (CC BY-SA 4.0) | https://datosabiertos.bogota.gov.co/dataset/inventario-patrimonio-mueble-bogota-d-c |
| Copenhagen | City of Copenhagen WFS - Monuments (monumenter, CC BY 4.0) | https://wfs-kbhkort.kk.dk/k101/ows?service=WFS&request=GetCapabilities |
| Milan | Comune di Milano - Spazi freschi: parchi ed aree verdi (DS3018, CC BY 4.0) | https://dati.comune.milano.it/dataset/ds3018-spazi-freschi-parchi-ed-aree-verdi |
| Prague | Národní památkový ústav (NPÚ) - Kulturní památky, Ústřední seznam kulturních památek (CC BY 4.0) | https://geoportal.npu.cz/arcgis/rest/services/Tematicke/CP_UAP_PVO/MapServer/1 |
| Helsinki | City of Helsinki WFS - Buildings Protected by Detailed Plans, Protected Natural Monuments (CC BY 4.0) | https://hri.fi/data/en_GB/dataset/asemakaavoilla-suojellut-rakennukset-ja-alueet-helsingissa |
| London | OpenStreetMap (Overpass API) - historic=* | https://overpass-api.de/ |
| Nottingham | Historic England - National Heritage List for England (OGL), clipped to the ONS district boundary | https://historicengland.org.uk/listing/the-list/ |
| Amsterdam | Amsterdam Data Portal - Monumenten | https://api.data.amsterdam.nl/v1/monumenten/monumenten/ |
| Berlin | OpenStreetMap (Overpass API) - historic=* | https://overpass-api.de/ |
| Vienna | Stadt Wien Open Data - Historische Gartenanlagen and Sehenswürdigkeiten (CC BY 4.0) | https://data.wien.gv.at/ |
| Zürich | Stadt Zürich Open Data - Denkmalpflege-Inventar, regional and cantonal objects (CC0) | https://data.stadt-zuerich.ch/dataset/geo_denkmalpflege_inventar |
| Basel | Kanton Basel-Stadt - Inventar der schützenswerten Gartenanlagen (CC BY 4.0) | https://www.geo.bs.ch/ |
| Frankfurt | Stadt Frankfurt am Main, Umweltamt - Naturdenkmale and Geschützte Landschaftsbestandteile (WFS, dl-de/by-2.0) | https://geowebdienste.frankfurt.de/WFS_Naturdenkmale?request=GetCapabilities&service=WFS |
| Cologne | Stadt Köln Offene Daten - Denkmalliste, named monuments (Datenlizenz Deutschland Zero 2.0) | https://offenedaten-koeln.de/dataset/liste-der-koelner-denkmaeler |
| Madrid | Ayuntamiento de Madrid / Comunidad de Madrid - Bienes de Interés Cultural y Patrimonial (CC BY 4.0) | https://datos.madrid.es/dataset/300717-0-cultural-bien-mapas |
| Lyon | Ministère de la Culture - Immeubles protégés au titre des Monuments Historiques (Mérimée, Licence Ouverte) | https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2 |
| Hamburg | Freie und Hansestadt Hamburg - Denkmalkartierung: listed gardens, monuments and buildings (WFS, dl-de/by-2.0) | https://geodienste.hamburg.de/HH_WFS_Denkmalschutz?SERVICE=WFS&REQUEST=GetCapabilities |
| Christchurch | Christchurch District Plan heritage items (Christchurch City Council, CC BY 4.0) | https://www.arcgis.com/home/item.html?id=f756c115142041fea96c3ac778ecbde4 |
| Fingal | Fingal County Council - Record of Protected Structures, Development Plan 2023-2029 (CC BY 4.0) | https://data.fingal.ie/datasets/601387c1d5ed4f6aa172fcf231a61fd3 |
| Canberra | ACT Heritage Register (ACT Government, CC BY 4.0) | https://www.arcgis.com/home/item.html?id=d03d3fa41dcc49c4a4ea9058f3f44060 |
| Canberra | Commonwealth Heritage List (DCCEEW, CC BY 3.0 AU) | https://www.arcgis.com/home/item.html?id=bf02ea66970c40d18b15e782b20a2fa9 |
| Lisbon | Câmara Municipal de Lisboa - Património classificado and Estatuária (CC0) | https://services.arcgis.com/1dSrzEWVQn5kHHyK/arcgis/rest/services/Patrimonio/FeatureServer |
| Ghent | Agentschap Onroerend Erfgoed - protected monuments, landscapes and town views (Gratis open data licentie Vlaanderen) | https://inventaris.onroerenderfgoed.be/erfgoedobjecten?gemeente=44021 |
| Bristol | Historic England - National Heritage List for England (OGL) | https://historicengland.org.uk/listing/the-list/ |
| Hamilton | City of Hamilton - Heritage Properties (City of Hamilton Open Data Licence v1.1) | https://open.hamilton.ca/datasets/heritage-properties |
| Utrecht | Gemeente Utrecht - Rijksmonumenten and Gemeentelijke monumenten, named objects (CC BY-SA 4.0) | https://open.utrecht.nl/dataset/rijksmonumenten/e30f329c-a1d9-45d8-a68b-91fbdf723703 |
| Toulouse | Ministère de la Culture - Immeubles protégés au titre des Monuments Historiques (Licence Ouverte) | https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2 |
| Geneva | SITG - Objets classés (Inventaire des monuments d'art et d'histoire du canton de Genève) | https://sitg.ge.ch/donnees/dps-classement |
| Leipzig | Stadt Leipzig - Grünflächen, Amt für Stadtgrün und Gewässer (no licence stated; displayed with attribution only, not licensed for reuse) | https://geodienste.leipzig.de/l3/OpenData/wfs |
| Wellington | Wellington City Council - 2024 District Plan heritage schedules SCHED1-3 (CC BY 4.0) | https://data-wcc.opendata.arcgis.com/datasets/WCC::heritage-buildings-sched1-2024-district-plan/about |
| Bordeaux | Ministère de la Culture - Immeubles protégés au titre des Monuments Historiques (Licence Ouverte) | https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2 |
| Strasbourg | Ministère de la Culture - Immeubles protégés au titre des Monuments Historiques (Licence Ouverte) | https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2 |
| Eindhoven | Gemeente Eindhoven - Monumenten, gemeentelijke monumenten (public domain) | https://data.eindhoven.nl/explore/dataset/monumenten/ |
| Bern | Stadt Bern - Sehenswürdigkeiten and Bauinventar Baugruppen (opendata.swiss open use) | https://opendata.swiss/en/dataset/bauinventar1 |
| Rennes | Ministère de la Culture - Immeubles protégés au titre des Monuments Historiques (Licence Ouverte) | https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2 |
| Antwerp | Agentschap Onroerend Erfgoed - protected monuments, landscapes and town views (Gratis open data licentie Vlaanderen) | https://inventaris.onroerenderfgoed.be/erfgoedobjecten?gemeente=11002 |
| San Diego | City of San Diego Historical Resources Board - designated resources (no licence stated; displayed with attribution only, not licensed for reuse); National Park Service - National Register of Historic Places | https://webmaps.sandiego.gov/arcgis/rest/services/Planning/Historic_Preservation_Resources/MapServer/0 |
| Kitchener | City of Kitchener - Heritage Properties (Open Government Licence - The Corporation of the City of Kitchener v1.0) | https://open-kitchenergis.opendata.arcgis.com/datasets/KitchenerGIS::heritage-properties |
| Sydney | NSW State Heritage Register curtilages (DCCEEW, CC BY) | https://data.nsw.gov.au/data/dataset/state-heritage-register-curtilages1c5ee |
| Louisville | Louisville Metro Landmarks Commission individual landmarks (LOJIC, Louisville Metro Open Data, PDDL); National Park Service - National Register of Historic Places | https://www.arcgis.com/home/item.html?id=db2b664ddfee430fad4fa1da4123be30 |
| Montpellier | Ministère de la Culture - Immeubles protégés au titre des Monuments Historiques (Licence Ouverte) | https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2 |
| Grenoble | Ministère de la Culture - Immeubles protégés au titre des Monuments Historiques (Licence Ouverte) | https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2 |
| Madison | City of Madison - Local Landmarks (open data under Madison General Ordinances 3.72); National Park Service - National Register of Historic Places | https://www.arcgis.com/home/item.html?id=279a5ad5f2f94d0bb20f898c523d5b5a |
| Richmond | City of Richmond - Old and Historic Sites (no licence stated; displayed with attribution only, not licensed for reuse); National Park Service - National Register of Historic Places | https://services1.arcgis.com/k3vhq11XkBNeeOfM/arcgis/rest/services/City_Old_and_Historic_Sites/FeatureServer/0 |
| Spokane | National Park Service - National Register of Historic Places | https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer |
| Waterloo | City of Waterloo - Heritage Buildings, Municipal Heritage Register (Waterloo Open Data User Licence) | https://data.waterloo.ca/datasets/3822d7856d034f07a14e67e25c6ced4b |
| Nantes | Ministère de la Culture - Immeubles protégés au titre des Monuments Historiques (Licence Ouverte) | https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2 |
| Santa Monica | City of Santa Monica - Historic Landmarks and Structures of Merit (ODC-By 1.0); National Park Service - National Register of Historic Places | https://www.arcgis.com/home/item.html?id=abe20ceaf96d4f8bbb5774d6aa230563 |
| Oakville | Town of Oakville - Heritage Properties, Municipal Heritage Register (Town of Oakville Open Data Licence) | https://www.arcgis.com/home/item.html?id=6a57f4591e314fb5b5c88e067add8872 |
| Rochester, NY | National Park Service - National Register of Historic Places | https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer |
| Syracuse | National Park Service - National Register of Historic Places | https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer |
| Las Vegas | City of Las Vegas - Historic Property Register (no licence stated; displayed with attribution only, not licensed for reuse); National Park Service - National Register of Historic Places | https://services1.arcgis.com/F1v0ufATbBQScMtY/arcgis/rest/services/HistoricAreas/FeatureServer/163 |
| Brest | Ministère de la Culture - Immeubles protégés au titre des Monuments Historiques (Licence Ouverte) | https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2 |
| Groningen | Gemeente Groningen - Monumenten (CC BY 4.0) | https://data.overheid.nl/dataset/groningen-monumenten-gemeente-groningen |
| Groningen | Rijksdienst voor het Cultureel Erfgoed - Rijksmonumenten via PDOK (CC BY 4.0) | https://service.pdok.nl/rce/ps-ch/wfs/v1_0?request=GetCapabilities&service=WFS |
| Providence | National Park Service - National Register of Historic Places | https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer |
| Guelph | City of Guelph Heritage Register - Cultural Heritage Resources (no licence stated; displayed with attribution only, not licensed for reuse) | https://explore.guelph.ca/apps/cityofguelph::cultural-heritage-resources |
| Belfast | Department for Communities NI, Historic Environment Division - listed buildings and scheduled monuments (Open Government Licence v3.0) | https://www.opendatani.gov.uk/dataset/historic-environment-division-esri-rest-api |
| Edinburgh | Historic Environment Scotland - listed buildings, scheduled monuments, gardens and designed landscapes (Open Government Licence v3.0) | https://portal.historicenvironment.scot/ |
| Oakland | City of Oakland Bureau of Planning - Landmarks Board Designated properties (no licence stated; displayed with attribution only, not licensed for reuse); National Park Service - National Register of Historic Places | https://gismaps.oaklandca.gov/server/rest/services/Planning_and_Zoning_Controls/MapServer/35 |
| Riverside | City of Riverside - Historic Properties Designations, City Landmarks ("Data provided by the City of Riverside"); National Park Service - National Register of Historic Places | https://www.arcgis.com/home/item.html?id=c0310676be0b427d831b64e19d26c122 |
| Cockburn | City of Cockburn - Local Heritage Survey (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=19b51e4ff02a4c32ad0c92d7f08e005a |
| Basingstoke | Historic England - National Heritage List for England (Open Government Licence) | https://historicengland.org.uk/listing/the-list/ |
| York | Historic England - National Heritage List for England (Open Government Licence) | https://historicengland.org.uk/listing/the-list/ |
| Hastings | Hastings District Council - District Plan heritage features (no licence stated; displayed with attribution only, not licensed for reuse) | https://storymaps.arcgis.com/stories/0d80ff4cad20442282c4622bbbce3989 |
| Red Deer | City of Red Deer - Zoning Bylaw historical sites (no licence stated; displayed with attribution only, not licensed for reuse) | https://arcgis.reddeer.ca/arcgis/rest/services/CoRD_View_Layers/LUB_HistoricalSite_approved/FeatureServer/0 |
| Fareham | Historic England - National Heritage List for England (Open Government Licence) | https://historicengland.org.uk/listing/the-list/ |
| Launceston | Heritage Tasmania - Tasmanian Heritage Register via theLIST © State of Tasmania (CC BY 3.0 AU) | https://services.thelist.tas.gov.au/arcgis/rest/services/HT/HT_Public/MapServer/0 |
| Macedon Ranges | Department of Transport and Planning - Victorian Heritage Register via Vicmap (CC BY 4.0) | https://discover.data.vic.gov.au/dataset/victorian-heritage-register |
| Fredericton | City of Fredericton - Heritage Preservation Areas (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=7a92b67c2593433196e35dc1f3e6e763 |
| Medicine Hat | City of Medicine Hat - Historic Resources (no licence stated; displayed with attribution only, not licensed for reuse) | https://services3.arcgis.com/2gQ2Dv4Fb016qFVU/arcgis/rest/services/Historic_Map_Tour_layer/FeatureServer/0 |
| Greater Shepparton | Department of Transport and Planning - Victorian Heritage Register via Vicmap (CC BY 4.0) | https://discover.data.vic.gov.au/dataset/victorian-heritage-register |
| Sioux Falls | City of Sioux Falls - Historic Properties (local register, CC BY 4.0); National Park Service - National Register of Historic Places | https://www.arcgis.com/home/item.html?id=7b0407feca3e4f47bfe54559b9c1dd5d |
| Surrey | City of Surrey Heritage Register - Heritage Sites (Open Government License - City of Surrey) | https://opendata-surrey.hub.arcgis.com/datasets/b65ea74c6e4743928ae25392935ca08f |
| Latrobe City | Department of Transport and Planning - Victorian Heritage Register via Vicmap (CC BY 4.0) | https://discover.data.vic.gov.au/dataset/victorian-heritage-register |
| Barrie | City of Barrie Municipal Heritage Register - Heritage Properties (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=aa31258e142d4a7e9a01567e8fc2550f |
| Langley Township | Township of Langley - Langley's Historic Places inventory (no licence stated; displayed with attribution only, not licensed for reuse); Province of British Columbia - Heritage Conservation Areas of BC (Open Government Licence - British Columbia) | https://www.arcgis.com/home/item.html?id=838d297431b747128230a452f038b503 |
| Maple Ridge | City of Maple Ridge - Heritage Register and Heritage Designation (Open Government Licence - Maple Ridge) | https://www.arcgis.com/home/item.html?id=85e758ffcd7a4aaeb8dd476143b70fd7 |
| Prince George | City of Prince George - Heritage Register (no licence stated; displayed with attribution only, not licensed for reuse), placed on City civic address points (Open Government License - City of Prince George) | https://www.princegeorge.ca/community-culture/heritage/heritage-register-properties |
| Penticton | City of Penticton - Community Heritage Register (no licence stated; displayed with attribution only, not licensed for reuse) | https://maps.penticton.ca/utility/rest/services/External_PRD/Planning/MapServer/235 |
| Fort St. John | City of Fort St. John - Recreation Areas and Points of Interest (Open Government License - City of Fort St. John) | https://data-fortstjohn.opendata.arcgis.com/datasets/66b0dcbb2f9e4478b8f2751cb4742475_0/about |
| Welland | City of Welland - Designated Heritage Properties (Open Government Licence - City of Welland) | https://www.arcgis.com/home/item.html?id=0aa78d20dd6a4dac95e2e8e5bca0e8c2 |
| Bassendean | Town of Bassendean - Heritage List, adopted 26 July 2022 (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=225a54f515224e1996e6d5f6c6613f36 |
| Colac Otway | Department of Transport and Planning - Victorian Heritage Register via Vicmap (CC BY 4.0) | https://discover.data.vic.gov.au/dataset/victorian-heritage-register |
| Barcelona | Ajuntament de Barcelona - Punts d'interès cultural (Open Data BCN, CC BY 4.0) | https://opendata-ajuntament.barcelona.cat/data/en/dataset/punts-informacio-turistica |
| Singapore | National Heritage Board - National Monuments and Historic Sites via data.gov.sg (Singapore Open Data Licence v1.0) | https://data.gov.sg/datasets/d_b29c230ec6b609e29ed42f71ca9a8767/view |
| Baton Rouge | City of Baton Rouge-Parish of East Baton Rouge - Local Historic Landmarks (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=ef927acb261c48c3bbef735602b9f5dc |
| Baton Rouge | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Lansing | City of Lansing - Historic Properties, local designations (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=a0f8c6e4d0a44291a7711103367329f4 |
| Lansing | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Boise | City of Boise - Historic Landmarks (CC BY 4.0) | https://opendata.cityofboise.org/datasets/b44aba318fd746988f1a48887bb0f990 |
| Boise | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Port St. Lucie | City of Port St. Lucie - Parks (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=65f2da9f968e463c8cf240ca452177f7 |
| Missoula | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Bismarck | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Springfield | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Grand Junction | City of Grand Junction - Historic Sites (CC0 1.0) | https://www.arcgis.com/home/item.html?id=474f2237f2c44e6bbe5b6588d7638a43 |
| Grand Junction | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Seoul | Seoul Metropolitan Government - Protected and Veteran Trees, Seoul Open Data Plaza OA-1323 (no restriction on use) | https://data.seoul.go.kr/dataList/OA-1323/S/1/datasetView.do |
| York Region | Regional Municipality of York - Heritage Properties (no licence stated; displayed with attribution only, not licensed for reuse) | https://storymaps.arcgis.com/stories/b20c80927bea4b56896e535eb1bc9a45 |
| Whitby | Town of Whitby - Parks and Open Spaces; Town Facilities (Open Government Licence - The Corporation of the Town of Whitby) | https://geohub-whitby.hub.arcgis.com/ |
| Salem | City of Salem - Historic Buildings (resale prohibited by the City; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=a49aaabfe16945ad90053f340b2c96bf |
| Salem | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Oxnard | City of Oxnard GIS Division - Ventura County Designated Historic Landmarks and Points of Interest (no licence stated; displayed with attribution only, not licensed for reuse) | https://maps.oxnard.org/arcgis/rest/services/HistoricData/MapServer/0 |
| Oxnard | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Redlands | City of Redlands - Historic Buildings and Structures (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=3701fa66edc9456d87a0584c9c011122 |
| Redlands | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Davenport | City of Davenport Planning and Zoning - Local Historic Landmarks (no licence stated; displayed with attribution only, not licensed for reuse) | https://gis.davenportiowa.com/arcgis/rest/services/Hosted/Individually_Listed_LHL/FeatureServer/3 |
| Davenport | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Plano | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Bend | City of Bend - Historic Resources (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=70b88002805f42dfa94d0c470d49eec1 |
| Bend | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Orland Park | Village of Orland Park - Old Orland Historic District contributing structures (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=99feff2aaed940efab4e39b8898e128a |
| Orland Park | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Evanston | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Moorhead | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Sarasota County | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Pinellas County | Pinellas County - Historic Landmarks, unincorporated county (no licence stated; displayed with attribution only, not licensed for reuse) | https://www.arcgis.com/home/item.html?id=a050d3efccf2453091db507bb71be718 |
| Pinellas County | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Bozeman | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Loveland | City of Loveland Development Services - Designated Local Historic Properties (no licence stated; displayed with attribution only, not licensed for reuse) | https://services1.arcgis.com/d4MV7N9xmuHYbLrF/arcgis/rest/services/Historic_Properties/FeatureServer/5 |
| Loveland | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| San Mateo | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| St. Louis Park | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Coral Springs | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Portland, ME | City of Portland - Local Landmarks (no licence stated; displayed with attribution only, not licensed for reuse) | https://gis.portlandmaine.gov/portal/home/item.html?id=13d2773085ab41a1afad4f79d7b4b9d6 |
| Portland, ME | National Park Service - National Register of Historic Places | https://www.nps.gov/subjects/nationalregister/data-downloads.htm |
| Bendigo | Heritage Victoria - Victorian Heritage Register via Vicmap (CC BY 4.0) | https://discover.data.vic.gov.au/dataset/victorian-heritage-register |
| Sydney | Sydney LEP 2012 heritage items (NSW DPHI, CC BY) | https://data.nsw.gov.au/data/dataset/environmental-planning-instrument-heritage-her |
| Eindhoven | Rijksdienst voor het Cultureel Erfgoed - Rijksmonumenten via PDOK (CC BY 4.0) | https://service.pdok.nl/rce/ps-ch/wfs/v1_0?request=GetCapabilities&service=WFS |
| Melbourne | Melbourne Open Data - Landmarks and Places of Interest | https://data.melbourne.vic.gov.au/explore/dataset/landmarks-and-places-of-interest-including-schools-theatres-health-services-spor/ |
| Paris | Ministere de la Culture - Immeubles proteges au titre des Monuments Historiques (Licence Ouverte) | https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2 |
| Buenos Aires | No landmark dataset in production yet | n/a |
| Athens | Curated landmark list + Nominatim geocoding | https://nominatim.openstreetmap.org/ |
| Milos | Curated landmark list + Nominatim geocoding | https://nominatim.openstreetmap.org/ |
| Santorini | Curated landmark list + Nominatim geocoding | https://nominatim.openstreetmap.org/ |

### Species enrichment

Per-species attributes (native status, evergreen, mature height, canopy spread, growth rate, lifespan, drought tolerance, bloom season, wildlife value, fire risk, tree category, map icon) are pulled in from a combination of the following services:

- Wikipedia REST API and MediaWiki API
- Kew's World Checklist of Vascular Plants (WCVP, the names and distributions behind POWO; Govaerts R (ed.), Royal Botanic Gardens, Kew, CC BY) - sftp.kew.org/pub/data-repositories/WCVP/
- GBIF species APIs - api.gbif.org
- SelecTree (Cal Poly UFEI) APIs - selectree.calpoly.edu

:::info
Tree info has been automatically extracted from the available APIs per species label and may be inaccurate - corrections very welcome, especially by people that know trees. Don't cite this in your paper!
:::

### Tree-level predictions

`{code}_tree_predictions_v{n}.parquet` carries a predicted crown width per tree, from
a genus-level stem-diameter power law fitted on the
[Tallo](https://doi.org/10.5281/zenodo.6637599) database (Jucker et al. 2022,
*Global Change Biology*, [doi:10.1111/gcb.16302](https://doi.org/10.1111/gcb.16302);
CC BY 4.0). Tallo is forest-plot data, so the crowns run narrower than an
open-grown street tree's; the parquet is a model-curation input today and
does not drive the map. Refit with `data/raw/tools/crown_allometry_fit.py --write`.
A tree with a planting date and no measured diameter gets one predicted from
its age, by a genus-level power law fitted on the map's own dated, measured
trees on every build, and the crown model is applied to that;
`crown_dbh_source` says which diameter a row's crown came from.

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
cd data/raw && uv run ./tools/portal_cadence.py --record
```
