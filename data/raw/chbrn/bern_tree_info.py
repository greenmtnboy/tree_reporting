#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Bern's tree cadastre (Baumkataster), from the city geoportal's ArcGIS layer.

Source: `Geoportal/Baumkataster/MapServer/0` on `map.bern.ch`, published by
Geoinformation Stadt Bern for Stadtgrün Bern and catalogued on opendata.swiss
as `baumkataster1` ("Baumkataster"), every resource licensed
`terms_open` (opendata.swiss: "Open use. Use for commercial purposes
allowed.").  It is the register of the trees Stadtgrün manages -- street,
park, school and cemetery trees and the city's fruit orchards -- **24,015
points** when this was written, all inside the municipality.  The catalogue
also lists a WFS on the same MapServer; the REST query is used because it
pages by `resultOffset`, reprojects server-side (the layer is stored in LV95,
EPSG:2056; `outSR=4326` comes back within half a metre of swisstopo's
formulas) and answers `returnCountOnly`, which the read is checked against.

The portal map server answers requests that carry python-requests' own
User-Agent with an "Access Denied" HTML page; `get_json_with_retry` sends the
project's.

**Keyed on `Nummer`, the tree number**, as `bern-{Nummer}`.  The layer has
no GlobalID.  `Nummer` is non-null on every row and unique on all but five:
in each of those pairs a replacement tree was planted on (or within metres
of) an old one and given its number, both rows still published (`18148`: a
1965 Norway maple and a 2015 sycamore at the same coordinates).  The later
planting is kept and the earlier dropped, with a count.  `Objectid` is unique
too but is a row number of the published table (sparse, up to 354,211 for
24k rows), not a fact about the tree.

**Species: the genus is Latin, the species is German.**  `Gattung_beschrieb`
is a Latin genus (`Acer`); `Art_beschrieb` is the German name of the species
(`Spitzahorn`), sometimes with a quoted cultivar (`Spitzahorn 'Columnare'`) --
"Gattung, Art, deutscher Name" in the catalogue's words, but there is no Latin
epithet anywhere: not on this layer, not on the WFS, not on the
`SGB_Baumausstellung` copy of it.  So the binomial comes from `GERMAN_NAMES`
below, keyed by (genus column, German name without its cultivar), curated
against POWO/WCVP for this register's 296 genus+name pairs.  Keying on the
pair rather than the name alone keeps the register's own mismatches honest:
a handful of rows file a name under the wrong genus (`Betula` / `Flaumeiche`,
`Tilia` / `Zuckerbirke`), and those publish as the genus column says rather
than as either guess.  A pair not in the table -- a species the register adds
later -- publishes as its genus, counted in the log; it is never dropped.

* A German name that names only a genus (`Linde`, `Ulme`, `Zierkirsche`) or a
  group of hybrids (`Resistenz Ulme`, `Lobel-Ulme`) maps to the genus.
* The fruit orchards sit under pseudo-genera -- `Malus, Kultursorte`,
  `Pyrus Kultursorte`, `Cydonya Kultursorte`, `Prunus Kultursorte`,
  `Mirabelle Kultursorte`, `Reineclaude Kultursorte` -- whose `Art_beschrieb`
  is the variety (`Boskoop`, `Williams Christbirne`) or `... Sorte nicht
  bekannt`.  Apple, pear and quince varieties are *Malus domestica*, *Pyrus
  communis* and *Cydonia oblonga*; mirabelles and greengages are *Prunus
  domestica*, into which POWO sinks *P. insititia*.  `Prunus Kultursorte` mixes
  cherries, plums, apricots and peaches: the fruit word in the name decides
  (`Kirsche, Sorte nicht bekannt`, `Zwetschge 'Tophit'`), then
  `PRUNUS_VARIETIES` for named varieties, and an unknown variety publishes as
  *Prunus*.  The variety itself goes to `cultivar`.
* The cultivar is the quoted part of the name (`'Columnare'`, also with
  typographic quotes or an unclosed quote); `Sorte nicht bekannt` ("variety
  not known") gives none.
* 50 rows have no genus at all (`Typ_beschrieb` `unbekannt` and a few
  others) and publish as Unknown.

`Pflanzjahr` is a planting year on every row, 1820-2026, heavily stacked on
1905/1925/1945/1965/1985 (~11,500 trees) -- the register's estimated age
classes, an estimate it states rather than a placeholder -- and publishes as
January 1 of that year.  The cadastre records no trunk diameter and no crown
spread, so `diameter_at_breast_height` is null and `crown_width_m` is not
emitted.  The layer has no status column, no removed trees and no
planting-site records: `Typ_beschrieb` is the tree kind (Laubbaum, Nadelbaum,
Obstbaum, unbekannt).
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    emit,
    enforce_tree_schema,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.arcgis import FeatureLayer, esri_point, feature_count, iter_features

LAYER = FeatureLayer(
    "https://map.bern.ch/arcgis/rest/services/Geoportal/Baumkataster/MapServer/0",
    timeout=180,
)
OUT_FIELDS = "Objectid,Nummer,Gattung_beschrieb,Art_beschrieb,Pflanzjahr"

# --------------------------------------------------------------------------
# Species
# --------------------------------------------------------------------------

# The register's pseudo-genera for fruit varieties, and its two misspelt or
# compound genus labels, mapped to a Latin genus.
GENUS_LABELS = {
    "malus, kultursorte": "Malus",
    "pyrus kultursorte": "Pyrus",
    "cydonya kultursorte": "Cydonia",
    "prunus kultursorte": "Prunus",
    "mirabelle kultursorte": "Prunus",
    "reineclaude kultursorte": "Prunus",
    "malus-hybriden": "Malus",
    "magnolia-soulangiana-hybriden": "Magnolia",
    "cladastris": "Cladrastis",
}

# The species a fruit-variety pseudo-genus stands for, where it is one species.
VARIETY_SPECIES = {
    "malus, kultursorte": "Malus domestica",
    "pyrus kultursorte": "Pyrus communis",
    "cydonya kultursorte": "Cydonia oblonga",
    "mirabelle kultursorte": "Prunus domestica",
    "reineclaude kultursorte": "Prunus domestica",
}

# A fruit word leading a variety name (`Kirsche, Sorte nicht bekannt`,
# `Apfel 'Alant'`, `Zwetschge 'Tophit'`) decides the species, whatever
# pseudo-genus the row is filed under.
FRUIT_WORDS = {
    "apfel": "Malus domestica",
    "birne": "Pyrus communis",
    "quitte": "Cydonia oblonga",
    "kirsche": "Prunus avium",
    "weichsel": "Prunus cerasus",
    "zwetschge": "Prunus domestica",
    "pflaume": "Prunus domestica",
    "mirabelle": "Prunus domestica",
    "reneklode": "Prunus domestica",
    "aprikose": "Prunus armeniaca",
    "pfirsich": "Prunus persica",
}

# Named `Prunus Kultursorte` varieties whose fruit is not in the name.  Only
# varieties whose species is not in doubt; the rest publish as Prunus.
PRUNUS_VARIETIES = {
    # sweet cherries
    "basler adler": "Prunus avium",
    "basler langstieler": "Prunus avium",
    "benjaminler": "Prunus avium",
    "berner kirsche": "Prunus avium",
    "hofmattkirsche": "Prunus avium",
    "kordia": "Prunus avium",
    "lampnästler": "Prunus avium",
    "magda": "Prunus avium",
    "regina": "Prunus avium",
    "rote herzkirsche": "Prunus avium",
    "rote lauber": "Prunus avium",
    "späte schwarze": "Prunus avium",
    "zopfkirsche": "Prunus avium",
    # sour cherry
    "aemli sauerkirsche": "Prunus cerasus",
    # plums, damsons, greengages
    "anna späth": "Prunus domestica",
    "basler hauszwetschge": "Prunus domestica",
    "bérudge": "Prunus domestica",
    "bühler": "Prunus domestica",
    "cacaks schöne": "Prunus domestica",
    "damassine": "Prunus domestica",
    "ersiger": "Prunus domestica",
    "ersinger frühzwetschge": "Prunus domestica",
    "fellenberg": "Prunus domestica",
    "frühe fellenberg": "Prunus domestica",
    "grosse grüne reineclaude": "Prunus domestica",
    "hanita": "Prunus domestica",
    "hauszwetschge (typ rudin)": "Prunus domestica",
    "hauszwetschge rinklin": "Prunus domestica",
    "katinka": "Prunus domestica",
    "löhrpflaume": "Prunus domestica",
    "opal": "Prunus domestica",
    "pfirsichpflaume": "Prunus domestica",
    "reneclaude d'oullins": "Prunus domestica",
    "schöne von loewen": "Prunus domestica",
    "topper": "Prunus domestica",
    "viktoriapflaume": "Prunus domestica",
    "wangenheims frühzwetschge": "Prunus domestica",
    # apricots
    "bergeron": "Prunus armeniaca",
    "holub's zuckeraprikose": "Prunus armeniaca",
    "königsaprikose / royal": "Prunus armeniaca",
    "luizet": "Prunus armeniaca",
    "pfirsichaprikose von nancy": "Prunus armeniaca",
    # peaches and nectarines
    "grosse mignonne": "Prunus persica",
    "nectared / nectarine": "Prunus persica",
}

# (Latin genus column, German name without its cultivar), lowercased, ->
# accepted binomial; a bare genus where the German name names no one species.
# Names as POWO/WCVP accepts them, or as `SPECIES_SYNONYMS` folds them.
GERMAN_NAMES = {
    ("abies", "kolorado- grau-tanne"): "Abies concolor",
    ("abies", "nordmannstanne"): "Abies nordmanniana",
    ("abies", "weisstanne"): "Abies alba",
    ("acer", "bergahorn"): "Acer pseudoplatanus",
    ("acer", "davids-ahorn"): "Acer davidii",
    ("acer", "dreispitz-ahorn"): "Acer buergerianum",
    ("acer", "eschenahorn"): "Acer negundo",
    ("acer", "feldahorn"): "Acer campestre",
    ("acer", "feldhahorn"): "Acer campestre",
    ("acer", "feuer-ahorn"): "Acer tataricum",
    ("acer", "feuerahorn"): "Acer tataricum",
    ("acer", "französischer ahorn"): "Acer monspessulanum",
    ("acer", "hainbuchenahorn"): "Acer carpinifolium",
    ("acer", "japanischer ahorn"): "Acer palmatum",
    ("acer", "japanischer feuerahorn"): "Acer",
    ("acer", "kolchischer ahorn"): "Acer cappadocicum",
    ("acer", "kugelfeldahorn"): "Acer campestre",
    ("acer", "rotahorn"): "Acer rubrum",
    ("acer", "rotahorn, kompakt"): "Acer rubrum",
    ("acer", "rotblättriger bergahorn"): "Acer pseudoplatanus",
    ("acer", "roter feldahorn"): "Acer campestre",
    ("acer", "roter schlangenhaut-ahorn"): "Acer capillipes",
    ("acer", "schneeball-ahorn"): "Acer opalus",
    ("acer", "silberahorn"): "Acer saccharinum",
    ("acer", "spitzahorn"): "Acer platanoides",
    ("acer", "säulen-spitzahorn"): "Acer platanoides",
    ("acer", "zimt-ahorn"): "Acer griseum",
    ("acer", "panaschierter bergahorn"): "Acer pseudoplatanus",
    ("aesculus", "gelbe pavie"): "Aesculus flava",
    ("aesculus", "haarrosskastanie"): "Aesculus",
    ("aesculus", "indische rosskastanie"): "Aesculus indica",
    ("aesculus", "purpurkastanie"): "Aesculus",
    ("aesculus", "rosskastanie"): "Aesculus hippocastanum",
    ("aesculus", "rosskastanie, gefüllte"): "Aesculus hippocastanum",
    ("aesculus", "rotblühende rosskastanie"): "Aesculus x carnea",
    ("aesculus", "rotblühende rosskastanie, gefüllte"): "Aesculus x carnea",
    ("aesculus", "rote pavie"): "Aesculus pavia",
    ("aesculus", "strauchrosskastanie"): "Aesculus parviflora",
    ("ailanthus", "götterbaum"): "Ailanthus altissima",
    ("albizia", "seidenbaum"): "Albizia julibrissin",
    ("alnus", "grau-erle, weiss-erle"): "Alnus incana",
    ("alnus", "italienische erle"): "Alnus cordata",
    ("alnus", "purpur - erle"): "Alnus x spaethii",
    ("alnus", "rot-erle, schwarz-erle"): "Alnus glutinosa",
    ("amelanchier", "felsenbirne"): "Amelanchier",
    ("betula", "goldbirke"): "Betula ermanii",
    ("betula", "himalaja-birke"): "Betula utilis",
    ("betula", "moorbirke"): "Betula pubescens",
    ("betula", "papierbirke"): "Betula papyrifera",
    ("betula", "rote china-birke"): "Betula albosinensis",
    ("betula", "sand-, weissbirke"): "Betula pendula",
    ("betula", "schwarzbirke"): "Betula nigra",
    ("betula", "szetschuan-birke"): "Betula",
    ("betula", "weissrindige himalaya birke"): "Betula utilis",
    ("betula", "zuckerbirke"): "Betula lenta",
    ("buxus", "buchsbaum"): "Buxus sempervirens",
    ("calocedrus", "flusszeder"): "Calocedrus decurrens",
    ("carpinus", "hainbuche, weissbuche"): "Carpinus betulus",
    ("carpinus", "japanische hainbuche"): "Carpinus japonica",
    ("carpinus", "säulen - hainbuche"): "Carpinus betulus",
    ("carya", "königsnuss"): "Carya laciniosa",
    ("carya", "pekannuss"): "Carya illinoinensis",
    ("carya", "schuppenrinden-hickory"): "Carya ovata",
    ("castanea", "esskastanie"): "Castanea sativa",
    ("catalpa", "kugel-trompetenbaum"): "Catalpa bignonioides",
    ("catalpa", "trompetenbaum (bignonioides)"): "Catalpa bignonioides",
    ("catalpa", "trompetenbaum (speciosa)"): "Catalpa speciosa",
    ("catalpa", "trompetenbaum (x erubescens)"): "Catalpa x erubescens",
    ("cedrus", "atlaszeder"): "Cedrus atlantica",
    ("cedrus", "libanonzeder"): "Cedrus libani",
    ("celtis", "zürgelbaum (australis)"): "Celtis australis",
    ("celtis", "zürgelbaum (occidentalis)"): "Celtis occidentalis",
    ("cercidiphyllum", "katsurabaum, kuchenbaum"): "Cercidiphyllum japonicum",
    ("cercis", "judasbaum"): "Cercis siliquastrum",
    ("cercis", "kanadischer judasbaum"): "Cercis canadensis",
    ("chamaecyparis", "gartenzypresse stewart"): "Chamaecyparis lawsoniana",
    ("chamaecyparis", "scheinzypresse"): "Chamaecyparis",
    ("chamaecyparis", "scheinzypresse (lawsoniana)"): "Chamaecyparis lawsoniana",
    ("chitalpa", "schmalblättriger trompetenbaum"): "x Chitalpa tashkentensis",
    ("cladastris", "gelbholz"): "Cladrastis kentukea",
    ("cornus", "amerikanischer blumen-hartriegel"): "Cornus florida",
    ("cornus", "etagen hartriegel"): "Cornus controversa",
    ("cornus", "japanischer blumen-hartriegel"): "Cornus kousa",
    ("cornus", "kornelkirsche, tierlibaum"): "Cornus mas",
    ("corylus", "baumhasel"): "Corylus colurna",
    ("corylus", "haselnuss"): "Corylus avellana",
    ("crataegus", "pflaumenblättrigen weissdorn"): "Crataegus persimilis",
    ("crataegus", "weissdorn"): "Crataegus",
    ("crataegus", "weissdorn (laevigata)"): "Crataegus laevigata",
    ("crataegus", "weissdorn (x lavallei)"): "Crataegus x lavalleei",
    ("cryptomeria", "sicheltanne"): "Cryptomeria japonica",
    ("davidia", "taubenbaum, taschentuchbaum"): "Davidia involucrata",
    ("elaeagnus", "schmalblättrige ölweide"): "Elaeagnus angustifolia",
    ("eucommia", "gummiulme"): "Eucommia ulmoides",
    ("fagus", "blutbuche"): "Fagus sylvatica",
    ("fagus", "blutbuche (veredlung)"): "Fagus sylvatica",
    ("fagus", "buche- weiss-rosa-dunkelrotbunt"): "Fagus sylvatica",
    ("fagus", "farnbuche"): "Fagus sylvatica",
    ("fagus", "grüne häng-buche"): "Fagus sylvatica",
    ("fagus", "rotbuche"): "Fagus sylvatica",
    ("fagus", "süntel-buche"): "Fagus sylvatica",
    ("fraxinus", "esche"): "Fraxinus excelsior",
    ("fraxinus", "fraxinus pennsylvanica"): "Fraxinus pennsylvanica",
    ("fraxinus", "hängeesche"): "Fraxinus excelsior",
    ("fraxinus", "kugel-blumenesche"): "Fraxinus ornus",
    ("fraxinus", "kugel-blumenesche kugel-blumenesche kugel-blumenesche"): "Fraxinus ornus",
    ("fraxinus", "manna-, blumenesche"): "Fraxinus ornus",
    ("fraxinus", "pennsylvanische esche"): "Fraxinus pennsylvanica",
    ("fraxinus", "weiss-esche"): "Fraxinus americana",
    ("fraxinus", "weissesche"): "Fraxinus americana",
    ("ginkgo", "gingko"): "Ginkgo biloba",
    ("gleditsia", "gleditschie, lederhülsenbaum"): "Gleditsia triacanthos",
    ("gleditsia", "gleditschie, lederhülsenbaum f. inermis"): "Gleditsia triacanthos",
    ("gymnocladus", "geweihbaum"): "Gymnocladus dioicus",
    ("ilex", "stechpalme"): "Ilex aquifolium",
    ("juglans", "schwarznussbaum"): "Juglans nigra",
    ("juglans", "walnussbaum"): "Juglans regia",
    ("juniperus", "virginische wacholder"): "Juniperus virginiana",
    ("koelreuteria", "blasenbaum"): "Koelreuteria paniculata",
    ("laburnum", "goldregen"): "Laburnum",
    ("larix", "europäische lärche"): "Larix decidua",
    ("larix", "japanische lärche"): "Larix kaempferi",
    ("liquidambar", "amberbaum"): "Liquidambar styraciflua",
    ("liriodendron", "tulpenbaum"): "Liriodendron tulipifera",
    ("maclura", "osagedorn"): "Maclura pomifera",
    ("magnolia", "kobushi magnolie"): "Magnolia kobus",
    ("magnolia", "magnolie"): "Magnolia",
    ("magnolia", "tulpen-mangolie"): "Magnolia x soulangeana",
    ("malus-hybriden", "dreilappiger apfel"): "Malus trilobata",
    ("malus-hybriden", "japanischer wildapfel"): "Malus floribunda",
    ("malus-hybriden", "wilder apfel"): "Malus sylvestris",
    ("malus-hybriden", "wollapfel"): "Malus tschonoskii",
    ("malus-hybriden", "zier-apfelbaum"): "Malus",
    ("malus-hybriden", "zier-apfelbaum, rotlaubig"): "Malus",
    ("malus-hybriden", "zierapfel"): "Malus",
    ("malus-hybriden", "zierapfel sieboldii"): "Malus toringo",
    ("mespilus", "mispel"): "Mespilus germanica",
    ("metasequoia", "urwelt-mammutbaum"): "Metasequoia glyptostroboides",
    ("morus", "maulbeerbaum"): "Morus",
    ("morus", "schwarzer maulbeerbaum"): "Morus nigra",
    ("morus", "weisser maulbeerbaum"): "Morus alba",
    ("nothofagus", "scheinbuche"): "Nothofagus",
    ("nyssa", "wald-tupelobaum"): "Nyssa sylvatica",
    ("ostrya", "hopfenbuche"): "Ostrya carpinifolia",
    ("parrotia", "eisenholzbaum"): "Parrotia persica",
    ("paulownia", "blauglockenbaum"): "Paulownia tomentosa",
    ("phellodendron", "amur-korkbaum"): "Phellodendron amurense",
    ("picea", "blau-fichte (sämling)"): "Picea pungens",
    ("picea", "fichte"): "Picea abies",
    ("picea", "hängefichte"): "Picea",
    ("picea", "kosters blau-fichte (veredelt)"): "Picea pungens",
    ("picea", "likiangfichte"): "Picea likiangensis",
    ("picea", "mähnen-fichte"): "Picea breweriana",
    ("picea", "orientalische fichte"): "Picea orientalis",
    ("picea", "orientalische gold-fichte"): "Picea orientalis",
    ("picea", "serbische fichte"): "Picea omorika",
    ("picea", "stechfichte"): "Picea pungens",
    ("picea", "säulenfichte"): "Picea",
    ("pinus", "bergföhre"): "Pinus mugo",
    ("pinus", "blaue mädchenkiefer"): "Pinus parviflora",
    ("pinus", "drehkiefer"): "Pinus contorta",
    ("pinus", "gemeine kiefer, föhre"): "Pinus sylvestris",
    ("pinus", "grannen-kiefer"): "Pinus aristata",
    ("pinus", "mazedonische kiefer"): "Pinus peuce",
    ("pinus", "mädchen-kiefer"): "Pinus parviflora",
    ("pinus", "schlangenhautkiefer"): "Pinus heldreichii",
    ("pinus", "säulenschwarzföhre"): "Pinus nigra",
    ("pinus", "tränenkiefer"): "Pinus wallichiana",
    ("pinus", "weymouthskiefer"): "Pinus strobus",
    ("pinus", "zirbelkiefer"): "Pinus cembra",
    ("pinus", "zwergföhre"): "Pinus mugo",
    ("pinus", "österreichische schwarzkiefer"): "Pinus nigra",
    ("platanus", "platane (occidentalis)"): "Platanus occidentalis",
    ("platanus", "platane (orientalis)"): "Platanus orientalis",
    ("platanus", "platane (x acerifolia)"): "Platanus x hispanica",
    ("platanus", "säulen platane"): "Platanus x hispanica",
    ("platanus", "säulenplatane"): "Platanus x hispanica",
    ("populus", "balsampappel"): "Populus balsamifera",
    ("populus", "birken-pappel"): "Populus simonii",
    ("populus", "graupappel"): "Populus x canescens",
    ("populus", "italienische säulenpappel"): "Populus nigra",
    ("populus", "kanadische pappel"): "Populus x canadensis",
    ("populus", "ostasiatische pappel"): "Populus",
    ("populus", "schwarzpappel"): "Populus nigra",
    ("populus", "silberpappel"): "Populus alba",
    ("populus", "säulen-zitterpappel"): "Populus tremula",
    ("populus", "zitterpappel, espe"): "Populus tremula",
    ("prunus", "blut-pflaume"): "Prunus cerasifera",
    ("prunus", "felsenkirsche"): "Prunus mahaleb",
    ("prunus", "gefülltblühende vogelkirsche"): "Prunus avium",
    ("prunus", "herbstblühende schnee-kirsche"): "Prunus x subhirtella",
    ("prunus", "japanische säulen-zierkirsche"): "Prunus serrulata",
    ("prunus", "japanische zierkirsche"): "Prunus serrulata",
    ("prunus", "kirsch-pflaume"): "Prunus cerasifera",
    ("prunus", "kugel-steppenkirsche"): "Prunus fruticosa",
    ("prunus", "scharlach-kirsche"): "Prunus sargentii",
    ("prunus", "schwarzdorn"): "Prunus spinosa",
    ("prunus", "spiegelrinden-kirsche"): "Prunus serrula",
    ("prunus", "steppenkirsche"): "Prunus fruticosa",
    ("prunus", "tokyo-kirsche"): "Prunus x yedoensis",
    ("prunus", "traubenkirsche"): "Prunus padus",
    ("prunus", "vogelkirsche, süsskirsche"): "Prunus avium",
    ("prunus", "zierkirsche"): "Prunus",
    ("pseudolarix", "goldlärche"): "Pseudolarix amabilis",
    ("pseudotsuga", "douglasie"): "Pseudotsuga menziesii",
    ("pterocarya", "flügelnuss"): "Pterocarya fraxinifolia",
    ("pyrus", "birnbaum"): "Pyrus communis",
    ("pyrus", "chinesische wild-birne"): "Pyrus calleryana",
    ("pyrus", "wildbirne"): "Pyrus pyraster",
    ("pyrus", "zierbirne"): "Pyrus",
    ("quercus", "amerikanische roteiche"): "Quercus rubra",
    ("quercus", "flaumeiche"): "Quercus pubescens",
    ("quercus", "kastanienblättrige eiche"): "Quercus castaneifolia",
    ("quercus", "persische eiche"): "Quercus macranthera",
    ("quercus", "scharlacheiche"): "Quercus coccinea",
    ("quercus", "schindel-eiche"): "Quercus imbricaria",
    ("quercus", "spanische eiche"): "Quercus x hispanica",
    ("quercus", "stieleiche"): "Quercus robur",
    ("quercus", "sumpf-eiche"): "Quercus palustris",
    ("quercus", "säulen-eiche"): "Quercus robur",
    ("quercus", "säuleneiche"): "Quercus robur",
    ("quercus", "traubeneiche"): "Quercus petraea",
    ("quercus", "ungarische eiche"): "Quercus frainetto",
    ("quercus", "wintergrüne eiche"): "Quercus x turneri",
    ("quercus", "zerreiche"): "Quercus cerris",
    ("rhus", "essigbaum"): "Rhus typhina",
    ("robinia", "kugel-akazie"): "Robinia pseudoacacia",
    ("robinia", "robinie, scheinakazie"): "Robinia pseudoacacia",
    ("salix", "korkenzieher-weide"): "Salix babylonica",
    ("salix", "salweide"): "Salix caprea",
    ("salix", "silberweide"): "Salix alba",
    ("salix", "silberweide liempde"): "Salix alba",
    ("salix", "trauerweide"): "Salix x sepulcralis",
    ("sambucus", "schwarzer holunder"): "Sambucus nigra",
    ("sciadopitys", "schirmtanne"): "Sciadopitys verticillata",
    ("sequoiadendron", "mammutbaum"): "Sequoiadendron giganteum",
    ("sorbus", "berg-mehlbeere"): "Sorbus",
    ("sorbus", "breitblättrige mehlbeere"): "Sorbus latifolia",
    ("sorbus", "eberesche, vogelbeere"): "Sorbus aucuparia",
    ("sorbus", "elsbeere"): "Sorbus torminalis",
    ("sorbus", "finnland-mehlbeere"): "Sorbus hybrida",
    ("sorbus", "grosslaubige mehlbeere"): "Sorbus aria",
    ("sorbus", "mahagoni-eberesche"): "Sorbus",
    ("sorbus", "mehlbeere (aria)"): "Sorbus aria",
    ("sorbus", "mehlbeere (latifolia)"): "Sorbus latifolia",
    ("sorbus", "oxelbeere"): "Sorbus intermedia",
    ("sorbus", "schwedische mehlbeere"): "Sorbus intermedia",
    ("sorbus", "speierling"): "Sorbus domestica",
    ("styphnolobium", "hängender schnurbaum"): "Styphnolobium japonicum",
    ("styphnolobium", "schnurbaum"): "Styphnolobium japonicum",
    ("taxodium", "sumpfzypresse"): "Taxodium distichum",
    ("taxus", "eibe"): "Taxus baccata",
    ("taxus", "säulen-eibe"): "Taxus baccata",
    ("tetradium", "samthaarige stinkesche"): "Tetradium daniellii",
    ("thuja", "lebensbaum"): "Thuja",
    ("thuja", "riesenlebensbaum"): "Thuja plicata",
    ("thujopsis", "hibalebensbaum"): "Thujopsis dolabrata",
    ("tilia", "amerikanische linde"): "Tilia americana",
    ("tilia", "holländische linde"): "Tilia x europaea",
    ("tilia", "kaiser-linde"): "Tilia x europaea",
    ("tilia", "kegellinde"): "Tilia",
    ("tilia", "krim-linde"): "Tilia x euchlora",
    ("tilia", "linde"): "Tilia",
    ("tilia", "mongolische linde"): "Tilia mongolica",
    ("tilia", "silberlinde"): "Tilia tomentosa",
    ("tilia", "sommerlinde"): "Tilia platyphyllos",
    ("tilia", "strassen linde"): "Tilia",
    ("tilia", "ungarische silberlinde"): "Tilia tomentosa",
    ("tilia", "winterlinde"): "Tilia cordata",
    ("tsuga", "hemlockstanne"): "Tsuga canadensis",
    ("ulmus", "berg-ulme"): "Ulmus glabra",
    ("ulmus", "feldulme"): "Ulmus minor",
    ("ulmus", "flatter-ulme"): "Ulmus laevis",
    ("ulmus", "florentiner ulme"): "Ulmus",
    ("ulmus", "holländische ulme"): "Ulmus x hollandica",
    ("ulmus", "hybrid-ulme"): "Ulmus",
    ("ulmus", "hänge-ulme"): "Ulmus glabra",
    ("ulmus", "kork feldulme"): "Ulmus minor",
    ("ulmus", "lobel-ulme"): "Ulmus",
    ("ulmus", "resista ulme"): "Ulmus",
    ("ulmus", "resistenz ulme"): "Ulmus",
    ("ulmus", "strassenulme"): "Ulmus",
    ("ulmus", "säulenulme"): "Ulmus",
    ("ulmus", "ulme"): "Ulmus",
    ("ulmus", "ulme lisle"): "Ulmus",
    ("xanthocyparis", "scheinzypresse"): "Xanthocyparis nootkatensis",
    ("xanthocyparis", "scheinzypresse (nootkatensis)"): "Xanthocyparis nootkatensis",
    ("zelkova", "japanische zelkove"): "Zelkova serrata",
    ("x sorbopyrus", "hagebuttenbirne"): "x Sorbopyrus auricularis",
}

# A cultivar whose species is not the one its German name says: the register
# files the narrow-leaved ash 'Raywood' under `Esche` (Fraxinus excelsior).
CULTIVAR_SPECIES = {
    ("fraxinus", "raywood"): "Fraxinus angustifolia",
    ("crataegus", "paul's scarlet"): "Crataegus laevigata",
    ("laburnum", "vossii"): "Laburnum x watereri",
    ("chamaecyparis", "alumii"): "Chamaecyparis lawsoniana",
    ("chamaecyparis", "alumigold"): "Chamaecyparis lawsoniana",
}

# The cultivar is the quoted part of the name, first quote to the end, in
# straight or typographic quotes, closed or not (`'Atropunicea`,
# `Goldregen'Vossii'`, `Weissdorn 'Paul's Scarlet'`).
_QUOTES = "'‘’"
_UNKNOWN_VARIETY = re.compile(r",?\s*sorte nicht bekannt\s*$", re.IGNORECASE)
# A fruit variety written as fruit word + quoted variety (`Apfel 'Alant'`).
# Only this shape is split: an apostrophe inside a bare variety name
# (`Reneclaude d'Oullins`, `Cox's Orangen Reinette`) is part of the name.
_FRUIT_QUOTED = re.compile(rf"^(\w+)\s*[{_QUOTES}](.+?)[{_QUOTES}]?$")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def split_name(name: str) -> tuple[str, str | None]:
    """`Spitzahorn 'Columnare'` -> (`Spitzahorn`, `Columnare`)."""
    cut = min((i for i in (name.find(q) for q in _QUOTES) if i >= 0), default=-1)
    if cut < 0:
        return name, None
    return name[:cut].strip(), clean(name[cut:].strip(_QUOTES + " "))


def fruit_variety(label: str, name: str | None) -> tuple[str | None, str | None]:
    """(species, cultivar) for a row under a fruit-variety pseudo-genus."""
    if name is None:
        return VARIETY_SPECIES.get(label) or GENUS_LABELS[label], None
    quoted = _FRUIT_QUOTED.match(name)
    if quoted and quoted.group(1).lower() in FRUIT_WORDS:
        return FRUIT_WORDS[quoted.group(1).lower()], clean(quoted.group(2))
    base, variety = name, name
    if _UNKNOWN_VARIETY.search(name):
        base, variety = _UNKNOWN_VARIETY.sub("", name).strip(), None
    first = base.split()[0].lower().rstrip(",") if base else ""
    if first in FRUIT_WORDS:
        return FRUIT_WORDS[first], variety
    if label in VARIETY_SPECIES:
        return VARIETY_SPECIES[label], variety
    # `Prunus Kultursorte`: a named variety, its fruit not in its name.
    return PRUNUS_VARIETIES.get(base.lower(), "Prunus"), variety


def parse_species(gattung, art, counts: dict[str, int]) -> tuple[str | None, str | None]:
    """(species, cultivar) from the Latin genus column and the German name."""
    genus = clean(gattung)
    if genus is None:
        counts["no_genus"] += 1
        return None, None
    label = genus.lower()
    name = clean(art)
    if label in VARIETY_SPECIES or label == "prunus kultursorte":
        return fruit_variety(label, name)
    latin_genus = GENUS_LABELS.get(label, genus)
    if name is None:
        return latin_genus, None
    base, cultivar = split_name(name)
    species = GERMAN_NAMES.get((label, base.lower()))
    if species is None:
        counts["unmapped"] += 1
        counts.setdefault("unmapped_names", set()).add(f"{genus} / {name}")
        return latin_genus, cultivar
    if cultivar is not None:
        species = CULTIVAR_SPECIES.get((label, cultivar.lower()), species)
    return species, cultivar


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------


def make_transform(counts: dict):
    def transform(features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float] = []
        longitude: list[float] = []
        planted_year: list[int] = []

        for feature in features:
            counts["read"] += 1
            attrs = feature.get("attributes") or {}
            number = attrs.get("Nummer")
            lat, lon = esri_point(feature.get("geometry"))
            if number is None or lat is None or lon is None:
                counts["unusable"] += 1
                continue
            year = attrs.get("Pflanzjahr")
            planted = parse_plant_date_year(year)
            if year not in (None, "") and planted is None:
                counts["bad_year"] += 1
            name, cv = parse_species(attrs.get("Gattung_beschrieb"), attrs.get("Art_beschrieb"), counts)
            tree_id.append(f"bern-{int(number)}")
            species.append(name)
            cultivar.append(cv)
            plant_date.append(planted)
            latitude.append(lat)
            longitude.append(lon)
            planted_year.append(planted.year if planted else -1)

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["CHBRN"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array([None] * len(tree_id), type=pa.float64()),
                "_planted_year": pa.array(planted_year, type=pa.int64()),
            }
        )

    return transform


def keep_latest_planting(table: pa.Table) -> tuple[pa.Table, int]:
    """One row per tree number: a reused number keeps its later planting."""
    ids = table.column("tree_id").to_pylist()
    years = table.column("_planted_year").to_pylist()
    best: dict[str, int] = {}
    for i, (key, year) in enumerate(zip(ids, years)):
        j = best.get(key)
        if j is None or year > years[j]:
            best[key] = i
    keep = sorted(best.values())
    dropped = len(ids) - len(keep)
    return table.take(pa.array(keep, type=pa.int64())).drop_columns(["_planted_year"]), dropped


def main() -> None:
    expected = feature_count(LAYER)
    counts: dict = {"read": 0, "unusable": 0, "bad_year": 0, "no_genus": 0, "unmapped": 0}
    table = stream_to_table(
        iter_features(
            LAYER,
            out_fields=OUT_FIELDS,
            return_geometry=True,
            out_sr=4326,
            order_by="Objectid",
        ),
        make_transform(counts),
        label="Bern OpenData",
    )
    if counts["read"] != expected:
        raise RuntimeError(
            f"Bern OpenData: read {counts['read']} feature(s) but the layer reports "
            f"{expected}; refusing to publish a partial cadastre"
        )
    if counts["bad_year"] > 0.01 * max(counts["read"], 1):
        raise RuntimeError(
            f"Bern OpenData: {counts['bad_year']} planting year(s) unreadable "
            f"(>1% of {counts['read']}); the field changed shape"
        )
    table, reused = keep_latest_planting(table)
    print(
        f"Bern OpenData: read {counts['read']} of {expected}; dropped "
        f"{counts['unusable']} row(s) with no tree number or no point and {reused} "
        f"earlier planting(s) under a reused tree number; {counts['bad_year']} "
        f"unreadable planting year(s) nulled; {counts['no_genus']} with no genus "
        f"(Unknown); leaving {table.num_rows}",
        file=sys.stderr,
    )
    if counts["unmapped"]:
        names = sorted(counts.get("unmapped_names", ()))
        print(
            f"Bern OpenData: {counts['unmapped']} tree(s) under {len(names)} German "
            f"name(s) not in GERMAN_NAMES published as their genus: {names[:20]}",
            file=sys.stderr,
        )
    table = validate_coordinates(table, city="Bern", city_code="CHBRN")
    table = enforce_tree_schema(table, city="Bern", data_source="BERN_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
