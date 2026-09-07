# Japan: what is publishable, and what only looks like it

Findings from sweeping Japan's open tree data. Measured 2026-09-07.

**Verdict: Japan has one large, open, species-bearing street-tree inventory,
and it is Tokyo's.** That one is excellent and is now wired as `JPTYO`. Almost
everything else that turns up in a search is one of three things that cannot be
ingested: an aggregate count, a designation register with empty coordinate
columns, or 3D geometry with no taxonomy. Each is worth naming so the next
person does not re-measure it.

---

## Wired: Tokyo (`JPTYO`)

**Source:** 「都道の街路樹」 — street trees on *metropolitan* roads, published by
the Tokyo Metropolitan Government Bureau of Construction (東京都建設局) on
`catalog.data.metro.tokyo.lg.jp`, package `t000014d2000000029`, **CC BY 4.0**.

One package, two CSV resources, which are one inventory split by survey
campaign:

| resource | rows | encoding | surveyed | covers |
|---|---:|---|---|---|
| 街路樹（都道：23区） | 144,183 | Shift-JIS | FY2020-23 | the 23 special wards |
| 街路樹（都道：多摩地域） | 83,875 | UTF-8 | FY2023-24 | 26 Tama municipalities |

228,058 rows in, **264,241 published** (223,830 municipal after dropping
unusable rows, plus 40,411 OSM survivors of 43,910 staged).

**Scope, stated plainly because the city name over-promises it.** These are the
trees on 都道 — roads the *metropolis* maintains. Every ward and every Tama city
also maintains its own street trees on its own roads, published separately where
published at all. So this is a large sample of Tokyo's street trees, not Tokyo's
street-tree inventory, and that is equally true of the 23-ward half and the Tama
half.

### What it cost, in the order the surprises arrived

**No tree id, in either file.** `整理番号` looks like one and is the *route*
number — 316 is a road, and 5,702 rows share one. This is the second synthesised
`tree_id` on the map after Longueuil, built from the coordinate rounded to 7 dp
(which is the precision both files publish). 4,221 rows at 2,107 shared
coordinates are dropped rather than resolved, the same call Longueuil made;
notably the Tama file has **no** shared coordinates at all — 83,875 distinct
points over 83,875 rows.

**Two encodings inside one package.** The 23-ward file is Shift-JIS with
Japanese column headers, the Tama file UTF-8 with romanised ones. This is what
`_ckan_shared.read_csv_rows` was added for — the CSV reader the module's own
docstring had been deferring until a source needed one. It detects the encoding
per resource, and the order it tries is load-bearing: cp932 decodes almost any
byte string without raising, so trying it before UTF-8 would turn valid UTF-8
Japanese into mojibake *silently*.

**Species is a Japanese vernacular name and nothing else** — 446 distinct
values, no binomial column anywhere. `_japanese_species.py` is
`_common_name_species` applied to a second language: a curated 427-entry table
that resolves **99.98%** of the trees. It is a separate module because the keys
normalise by different rules — the English table's `common_name_key` reduces a
value to `[a-z ]`, which erases a katakana name entirely.

GBIF's backbone carries Japanese vernaculars and drafted 290 of the 446, which
is what made the curation affordable. It is a drafting aid, not the authority,
and the reason is the same one the Canadian table records about inverting the
enrichment index — it is right most of the time and wrong without saying so:

- `ツバキ` came back *Camellia hiemalis*. `ツバキ` is the common camellia,
  *C. japonica*; *C. hiemalis* is `カンツバキ`, published here 966 times as its
  own value.
- `アメリカヒイラギ` came back *Cartrema americana* (devilwood). The name is
  "American holly" and means *Ilex opaca* — and the data publishes
  `セイヨウヒイラギ`, `ヒイラギモチ` and `シナヒイラギ` alongside it, all hollies.
- `ミモザ` came back *Mimosa pudica*, the sensitive plant, where Japanese
  horticulture means *Acacia dealbata*.

Every value was then put to POWO the way `species_audit.py` does it, reading
every exact match rather than the first. 285 of 336 came back `accepted`; the
rest are documented entry by entry in the module.

**Where POWO and the published table disagreed, the published table won.** The
same argument `SPECIES_SYNONYMS` makes: the same taxon under two names is two
enrichment rows and two entries in every rollup. POWO calls `Cinnamomum
camphora` a synonym of `Camphora officinarum`; the enrichment table has carried
`Cinnamomum camphora` since San Francisco, so 6,882 Tokyo camphor trees join the
row that exists. Two cases pointed the other way and became `SPECIES_SYNONYMS`
entries instead — `Sapium sebiferum` → `Triadica sebifera` and `Callistemon
citrinus` → `Melaleuca citrina` — each reclaiming a row that was being paid for
twice.

**`行政区` is the ward, and it rides `borough`.** London declares `borough` for
the same concept and the shared community and OSM paths already emit it, so a
second column for "the administrative subdivision this tree sits in" would be a
parallel copy of one that exists. 23 wards populated; the Tama file publishes no
municipality at all — only route names, and its routes cross municipal
boundaries — so those rows are null.

**Seven Tama rows have the latitude in the longitude column** (`35.72888356,
35.728884` — the same value at two precisions), so the longitude is not
recoverable from anything in the row. Dropped with their own logged count rather
than left to `validate_coordinates`, because "outside CITY_BOUNDS" is a much
less alarming fact than "this file has a column-order bug".

**`幹周` is circumference, not diameter** (`幹周(cm）` closes with a full-width
parenthesis). 17,046 rows record `0`, an unmeasured tree rather than one of no
width; two record 840 cm and 1,427 cm against a 99.99th percentile of 350.

**The dedup cell is 8 m, one step below what the band rule prints.** Tokyo's
inventory is the tightest-planted measured anywhere here — a median **3.2 m** to
the nearest other inventory tree against Tempe's 6.5 — because the 23-ward
survey counts 中木, the medium-height plantings that run as a near-continuous
line along a verge. Planting spacing therefore starts much closer in than the
band rule assumes, and the marginal trade turns accordingly: 6→8 removes 108
duplicates for 95 hidden trees (1.14), 8→10 removes 48 for 97 (0.49).

**`CITY_BOUNDS` is the mainland metropolis and excludes the Izu Islands.**
Hachijōjima and Aogashima are administratively Tokyo and 290 km south. No tree
in either file is on them, and the bounds are what the Overpass extraction
asks for — an island-inclusive box would sweep 1,700 km of ocean. Eighteen
designated cultural properties *are* on them and are filtered by the city's own
bounds, counted separately from parse failures.

### Landmarks

Two CSV registers from the Tokyo Metropolitan Board of Education (東京都教育庁)
on the same catalog, CC BY 4.0: 文化財一覧 (245 designated cultural properties)
and 東京都指定史跡データ一覧 (42 historic sites). **230 published.** This is the
runbook's first-preference source — an official designation registry, same
portal as the trees, read live, no geocoding and no staging object.

Both registers, because they are mostly disjoint: only 12 names are shared, so
taking the larger alone would drop 22 designated sites. The cultural-property
register wins a tie — it carries an official English name for all 245 rows,
which is what the map shows.

---

## Not ingestible, and why

### PLATEAU: geometry without taxonomy, in 2.7 GB zips

Japan's national **Project PLATEAU** publishes CityGML for dozens of cities, and
individual trees appear as `SolitaryVegetationObject` — Kyoto, Saitama, Kumagaya
(whose own tutorial labels its LOD2 vegetation 植生［街路樹］, "vegetation [street
trees]"), Hachioji, Morioka and Kasukabe all have downloadable vegetation. It
reads like the largest per-tree geometry source in the country, and it is.

It is still not a tree inventory, for two independent reasons:

1. **`veg:species` is not used by the standard product specification.** The
   schema has the field; the national spec says the standard product does not
   populate it. What PLATEAU reliably gives is position, height, trunk diameter
   and crown diameter — a different dataset class from SF/Boston, and one whose
   species dashboards would be empty by construction, since
   `REAL_SPECIES_PREDICATE` keeps sentinels out of every species rollup.
2. **The distribution unit is the whole city model.** Kyoto's 2025 CityGML is a
   single **2,700,713,512-byte** zip covering buildings, roads, terrain and
   everything else. A city refresh job has a 2 GiB container and downloads its
   source each run.

Neither is fatal on its own and together they are. If PLATEAU ever publishes
vegetation as its own artifact *and* a city populates `veg:species`, revisit —
Kyoto alone claims ~40,000 tall street trees.

### Okayama: watch, do not ingest yet

The FY2025-26 PLATEAU documentation for Okayama describes a project integrating
street-tree and park management data into GIS/CityGML: **60,502 street trees**
across ~400 routes, citywide vegetation at LOD0, 1,131 individual LOD3 trees on
seven routes. If the underlying register carries species and it survives into
published attributes, this would be the second-best Japanese source. The
finished FY2025 package is not indexed publicly on G空間情報センター
(`plateau-33100-okayama-shi-2025` exists; the vegetation artifact does not).
**WATCH / REQUEST DATA.**

### Aggregate counts, not inventories

- **Kitakyushu** 街路樹樹種別本数 (`data.bodik.jp`, XLSX) — planting counts by
  species. Useful for composition statistics, no geometry.
- **Meguro** 街路樹・緑地の状況の推移 — a time series of totals.
- **Minato** 街路樹 (行政資料集) — six years of annual summary CSVs.

### Registers whose coordinate columns are empty

Tokyo's catalog carries a standardised 保護指定樹木 (designated protected trees)
dataset published by ~20 wards and Tama cities on one recommended-dataset
template with `緯度`/`経度` columns. **The columns are there and the values are
not** — Suginami's 66 rows carry a species name and nothing else. Worth
re-checking periodically, since the template is right and only the fill is
missing; a ward that populates it would be a small but real supplement.

### Dead links

**Suginami** is the ward most often cited as unusually open, publishing both a
street-tree and a park-tree CSV. Both catalog entries point at
`www2.wagmap.jp/suginami/OpenDataDetail?lid=50&mids=10`, which **404s**. If the
layer is relocated it is worth a second look; the catalog entry was last touched
2018.

### Not found

Direct probes of the obvious open-data hosts for Yokohama, Osaka, Nagoya, Kobe,
Kyoto, Sapporo, Saitama, Chiba, Kawasaki, Sendai, Hiroshima, Okayama, Niigata,
Kumamoto, Fukuoka, Sagamihara, Machida and twelve more found CKAN APIs on only
four (Yokohama, Sapporo, Machida, and BODIK for the Kyushu cities) and **no tree
dataset on any of them**. That is a negative result from guessed hostnames, not
a proof: a portal under a host not guessed would not appear. The cross-catalog
at `search.ckan.jp/backend/api/package_search` indexes 15 Japanese catalogs and
is the better discovery tool — it is what turned up everything in this file.

---

## Tools worth knowing about

| what | where |
|---|---|
| Cross-catalog search over 15 Japanese portals | `https://search.ckan.jp/backend/api/package_search?q=街路樹&rows=200` |
| Tokyo's own CKAN | `https://catalog.data.metro.tokyo.lg.jp/api/3/action/package_search` |
| G空間情報センター (PLATEAU, 国土数値情報) | `https://www.geospatial.jp/ckan/api/3/action/package_search` |
| Japanese vernacular → binomial, as a *draft* | GBIF `species/search?qField=VERNACULAR` |

The national catalog at `www.data.go.jp/data/api/3/action/package_search`
responds and is **not** useful for this: it holds national-ministry data, and its
free-text search ignores the query — every term returns the same 18,142-row
count and the same irrelevant first page.
