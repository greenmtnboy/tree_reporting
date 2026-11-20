#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Spokane, WA's tree inventory, from the City of Spokane's ArcGIS Online.

LICENCE: no licence stated; displayed with attribution only, not licensed for
reuse.  The item's `licenseInfo` (and the Hub's "custom" licence) is only the
city GIS disclaimer, "THIS IS NOT A LEGAL DOCUMENT: This information is
compiled from various sources and is subject to constant revision ...".

Source: "Tree Inventory" (ArcGIS item d84eda620819425185d114a06a94d047, owner
`gisadmin_spokane`, "City of Spokane Parks Department tree inventory"): the
hosted layer `Tree_Inventory/FeatureServer/8`, the service's only layer,
stored in Washington State Plane North (ft, EPSG 2926) and reprojected
server-side.  112,444 rows on 2026-11-19.  It holds street trees (93,368 rows
with a blank `park`) and park trees (the rest, `park` naming the park, golf
course or facility) in one layer; there is no separate park or street layer.

**The id is `OBJECTID`, and it is a weak one.**  The layer has no GlobalID and
no asset id of its own: its fields are `park`, `species`, `diameter`,
`height`, `spread`, `trunks`, three genus/common-name helper columns and
`UpdatedDT`.  OBJECTID runs 1..112,444 without a gap and every row carries the
same `UpdatedDT` (2026-11-11), which is what a wholesale reload looks like, so
a future reload may renumber every tree.  Nothing better exists.

**`species` is a contraction of the inverted English common name**, cut to 17
characters -- `mapl,nwy` (Norway maple), `pine,ponderosa`, `hawth spp`,
`mapl,nwy crmsn kg` (Norway maple 'Crimson King'), `ch,weep higan` -- 669
distinct values.  The layer publishes no domain for it.  Its `Genus` and
`CommonName` columns are a partial and sometimes wrong decoding (`fir,douglas`
is filed under *Abies*; 'Unknown' on hundreds of plain codes) and are not
used.  The codes are decoded here by `SPECIES_BY_CODE`, read by hand against
`shared.species.english` (which is where most of the binomials come from, so a
Spokane Norway maple is named exactly as Mississauga's is):

* a code is looked up as `head,qualifier`, dropping trailing qualifier words
  until a key matches, so `mapl,nwy crmsn kg` resolves through `mapl,nwy`;
* a `head` key on its own answers the bare name, the `spp` form and every
  qualifier nothing finer matched -- the head names the genus (`crabappl,
  dorthea` is a *Malus*), except where every tree under it is one species
  (`honeyl`, `ginkgo`, `zelk`, `tupelo`);
* a head with no key (`cedar`, `locust`) resolves only through a qualifier,
  because the head alone does not name one genus.

Cultivars are not split out: the cultivar words are truncated past reading
(`linden,slv strl s`, `oak,ital fore gr`).  A code whose head is not in the
table publishes `Unknown`, and the run refuses when that is over 1% of rows,
so a new contraction in a republished layer is noticed rather than published
as unknown trees.

**Two kinds of row are not trees and are dropped.**  `class 1`, `class 2`,
`class 3` (and `class 2 upright`) are 1,053 planting-site size classes,
nearly all with zero diameter, height and trunks.  13,086 `Unknown` rows have
`trunks = 0` and zero diameter and height: no species, no trunk and no size is
an empty site rather than an unidentified tree.  `Unknown` rows with a
measured trunk stay (about 5,000), as do the 624 named-species rows with all
zeros (a named tree, likely a fresh planting).  The layer has no status or
removed-tree column.

**`diameter` is DBH in inches** (ponderosa pines at 21-33, zero on rows with
no measurement, nulled by the schema).  **`spread` is crown spread in feet**
(20 and 35 on mature pines) and is published as `crown_width_m`.  `height`
(feet) has no column in the schema; `trunks` is not mapped.  There is no
planting date.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services.arcgis.com/3PDwyTturHqnGCu0/arcgis/rest/services/"
    "Tree_Inventory/FeatureServer/8",
    timeout=300,
)

OUT_FIELDS = "OBJECTID,species,diameter,spread,height,trunks"

FEET_TO_M = 0.3048

# Refuse a run where more than this share of rows carries a code nobody has
# decoded (a new contraction would otherwise publish as unknown trees).
MAX_UNDECODED_SHARE = 0.01

# Contraction -> species value.  Keys are normalised codes (`normalise_code`):
# `head` or `head,qualifier`.  Ordered by head, roughly by row count.
SPECIES_BY_CODE: dict[str, str] = {
    # maples
    "mapl": "Acer",
    "mapl,nwy": "Acer platanoides",
    "mapl,norway": "Acer platanoides",
    "mapl,red": "Acer rubrum",
    "mapl,rd": "Acer rubrum",
    # 'Armstrong' and 'Autumn Blaze' are sold as red maples and are Freeman
    # maples (A. rubrum x saccharinum).
    "mapl,rd armstrong": "Acer x freemanii",
    "mapl,rd aut blaze": "Acer x freemanii",
    "mapl,sycamore": "Acer pseudoplatanus",
    "mapl,syc": "Acer pseudoplatanus",
    "mapl,japanese": "Acer palmatum",
    "mapl,jap": "Acer palmatum",
    "mapl,sugar": "Acer saccharum",
    "mapl,sug": "Acer saccharum",
    "mapl,silver": "Acer saccharinum",
    "mapl,silv": "Acer saccharinum",
    "mapl,freeman": "Acer x freemanii",
    "mapl,frman": "Acer x freemanii",
    "mapl,boxelder": "Acer negundo",
    "mapl,box": "Acer negundo",
    "mapl,papbark": "Acer griseum",
    "mapl,hedge": "Acer campestre",
    "mapl,hed": "Acer campestre",
    "mapl,hdg": "Acer campestre",
    "mapl,vine": "Acer circinatum",
    "mapl,amur": "Acer ginnala",
    "mapl,tatarian": "Acer tataricum",
    "mapl,black": "Acer nigrum",
    "mapl,blk": "Acer nigrum",
    "mapl,rocky mtn": "Acer glabrum",
    "mapl,bigtooth": "Acer grandidentatum",
    "mapl,miyabe": "Acer miyabei",
    # pines (and the Japanese umbrella pine, which is not one)
    "pine": "Pinus",
    "pine,ponderosa": "Pinus ponderosa",
    "pine,austrian": "Pinus nigra",
    "pine,aust": "Pinus nigra",
    "pine,scotch": "Pinus sylvestris",
    "pine,east white": "Pinus strobus",
    "pine,east wh": "Pinus strobus",
    "pine,whit": "Pinus strobus",
    "pine,wt": "Pinus strobus",
    "pine,mugo": "Pinus mugo",
    "pine,lodgepole": "Pinus contorta",
    "pine,shore": "Pinus contorta",
    "pine,westrn white": "Pinus monticola",
    "pine,limb": "Pinus flexilis",
    "pine,limber": "Pinus flexilis",
    "pine,bristlecone": "Pinus aristata",
    "pine,japan black": "Pinus thunbergii",
    "pine,japan white": "Pinus parviflora",
    "pine,lacebark": "Pinus bungeana",
    "pine,jeffrey": "Pinus jeffreyi",
    "pine,jack": "Pinus banksiana",
    "pine,pinyon mexi": "Pinus cembroides",
    "pine,pitch": "Pinus rigida",
    "pine,red": "Pinus resinosa",
    "pine,swiss stone": "Pinus cembra",
    "pine,jap umbrela": "Sciadopitys verticillata",
    # spruces
    "spruce": "Picea",
    "spruce,colorado": "Picea pungens",
    "spruce,norway": "Picea abies",
    "spruce,nor": "Picea abies",
    "spruce,engelmann": "Picea engelmannii",
    "spruce,white": "Picea glauca",
    "spruce,wh": "Picea glauca",
    "spruce,serbian": "Picea omorika",
    "spruce,black": "Picea mariana",
    "spruce,red": "Picea rubens",
    "spruce,sitka": "Picea sitchensis",
    "spruce,oriental": "Picea orientalis",
    "spruce,ori": "Picea orientalis",
    # firs (and the Douglas fir, which is not one)
    "fir": "Abies",
    "fir,douglas": "Pseudotsuga menziesii",
    "fir,subalpine": "Abies lasiocarpa",
    "fir,sub": "Abies lasiocarpa",
    "fir,white": "Abies concolor",
    "fir,grand": "Abies grandis",
    "fir,noble": "Abies procera",
    "fir,fraser": "Abies fraseri",
    "fir,balsam": "Abies balsamea",
    "fir,manchurian": "Abies holophylla",
    "fir,silver": "Abies alba",
    "fir,blue spanish": "Abies pinsapo",
    "fir,greek": "Abies cephalonica",
    "fir,kor": "Abies koreana",
    "fir,momi": "Abies firma",
    # other conifers
    "hemlock": "Tsuga",
    "heml": "Tsuga",
    "hemlock,mountain": "Tsuga mertensiana",
    "hemlock,canadian": "Tsuga canadensis",
    "hemlock,ca": "Tsuga canadensis",
    "heml,can": "Tsuga canadensis",
    "hemlock,carolina": "Tsuga caroliniana",
    "heml,western": "Tsuga heterophylla",
    "hemlock,n jap": "Tsuga diversifolia",
    "hemlock,south jap": "Tsuga sieboldii",
    "larch": "Larix",
    "larch,western": "Larix occidentalis",
    "larch,east": "Larix laricina",
    "larch,european": "Larix decidua",
    "larch,japanese": "Larix kaempferi",
    "cedar,western red": "Thuja plicata",
    "cedar,atlas": "Cedrus atlantica",
    "cedar,deodar": "Cedrus deodara",
    "cedar of lebanon": "Cedrus libani",
    "redcedar,eastern": "Juniperus virginiana",
    "incensecedar,cal": "Calocedrus decurrens",
    "arb": "Thuja",
    "arb,americ": "Thuja occidentalis",
    "arb,amer": "Thuja occidentalis",
    "arb,japanese": "Thuja standishii",
    "arbor,hiba": "Thujopsis dolabrata",
    "junip": "Juniperus",
    "junip,rky mtn": "Juniperus scopulorum",
    "junip,rock mt": "Juniperus scopulorum",
    "junip,chinese": "Juniperus chinensis",
    "junip,common": "Juniperus communis",
    "junip,needle": "Juniperus rigida",
    "falsecy": "Chamaecyparis",
    "falsecypress": "Chamaecyparis",
    "falsecy,nootka": "Cupressus nootkatensis",
    "falsecy,not": "Cupressus nootkatensis",
    "falsecy,lawson": "Chamaecyparis lawsoniana",
    "falsecy,law": "Chamaecyparis lawsoniana",
    "falsecy,sawara": "Chamaecyparis pisifera",
    "falsecy,saw": "Chamaecyparis pisifera",
    "falsecy,hin": "Chamaecyparis obtusa",
    "cypress": "Cupressus",
    "cypress,arizonica": "Cupressus arizonica",
    "bcypres": "Taxodium distichum",
    "redwood,dawn": "Metasequoia glyptostroboides",
    "redwood,dwn": "Metasequoia glyptostroboides",
    "redwood,coast": "Sequoia sempervirens",
    "sequoia,giant": "Sequoiadendron giganteum",
    "yew": "Taxus",
    "yew,irish": "Taxus baccata",
    "yew,anjap": "Taxus x media",
    "yew,jap hill": "Taxus x media",
    "yew,western": "Taxus brevifolia",
    "ginkgo": "Ginkgo biloba",
    # locusts
    "locust,black": "Robinia pseudoacacia",
    "locust,idaho": "Robinia",
    "honeylocust": "Gleditsia triacanthos",
    "honeyl": "Gleditsia triacanthos",
    "honey,tibet": "Gleditsia",
    # Prunus
    "cherry": "Prunus",
    "ch": "Prunus",
    "ch,weep higan": "Prunus x subhirtella",
    "ch,higan": "Prunus x subhirtella",
    "ch,hig": "Prunus x subhirtella",
    "ch,sargent": "Prunus sargentii",
    "ch,sarg": "Prunus sargentii",
    "ch,jap": "Prunus serrulata",
    "ch,yoshino": "Prunus x yedoensis",
    "ch,yosh": "Prunus x yedoensis",
    "ch,black": "Prunus serotina",
    "ch,eur bird": "Prunus padus",
    "ch,mahaleb": "Prunus mahaleb",
    "ch,pin": "Prunus pensylvanica",
    "plum": "Prunus",
    "plum,cherry": "Prunus cerasifera",
    "plum,cher": "Prunus cerasifera",
    "chokchry": "Prunus virginiana",
    "chokchry,amur": "Prunus maackii",
    "peach": "Prunus persica",
    "apricot": "Prunus armeniaca",
    "almond,sweet": "Prunus dulcis",
    # Malus, Pyrus
    "crabapple": "Malus",
    "crabappl": "Malus",
    "crabappl,siberian": "Malus baccata",
    "crabappl,sib": "Malus baccata",
    "crabappl,sargent": "Malus toringo",
    "crabappl,toringo": "Malus toringo",
    "crabappl,plumleaf": "Malus prunifolia",
    "crabappl,yun": "Malus yunnanensis",
    "apple": "Malus",
    "pear": "Pyrus",
    "pear,flowering": "Pyrus calleryana",
    "pear,flwr": "Pyrus calleryana",
    "pear,prairie gem": "Pyrus ussuriensis",
    "pear,ussurian": "Pyrus ussuriensis",
    "pear,common": "Pyrus communis",
    "pear,anjou": "Pyrus communis",
    # hawthorns, serviceberries, mountain ashes
    "hawth": "Crataegus",
    "hawth,downy": "Crataegus mollis",
    "hawth,english": "Crataegus laevigata",
    "hawth,eng": "Crataegus laevigata",
    "hawth,lavalle": "Crataegus x lavallei",
    "hawth,green": "Crataegus viridis",
    "hawth,grn": "Crataegus viridis",
    "hawth,gr": "Crataegus viridis",
    "hawth,cockspur": "Crataegus crus-galli",
    "hawth,cocks": "Crataegus crus-galli",
    "hawth,washington": "Crataegus phaenopyrum",
    "hawth,fleshy": "Crataegus succulenta",
    "hawth,dotted": "Crataegus punctata",
    "hawth,douglas": "Crataegus douglasii",
    "hawth,singleseed": "Crataegus monogyna",
    "hawth,chinese": "Crataegus pinnatifida",
    "servbry": "Amelanchier",
    "servbry,canadian": "Amelanchier canadensis",
    "servbry,western": "Amelanchier alnifolia",
    "servbry,downy": "Amelanchier arborea",
    "servbry,al": "Amelanchier laevis",
    "mtn ash": "Sorbus",
    "mtn ash,european": "Sorbus aucuparia",
    "mtn ash,eur": "Sorbus aucuparia",
    "mtn ash,american": "Sorbus americana",
    "mtn ash,korean": "Sorbus alnifolia",
    "mtn ash,whtbeam": "Sorbus aria",
    # elms, lindens
    "elm": "Ulmus",
    "elm,siberian": "Ulmus pumila",
    "elm,american": "Ulmus americana",
    "elm,amer": "Ulmus americana",
    "elm,lace": "Ulmus parvifolia",
    "elm,lacebark": "Ulmus parvifolia",
    "elm,allee": "Ulmus parvifolia",
    "elm,red": "Ulmus rubra",
    "elm,david": "Ulmus davidiana",
    "elm,english": "Ulmus minor",
    "elm,rock": "Ulmus thomasii",
    "linden": "Tilia",
    "linden,littleleaf": "Tilia cordata",
    "linden,ltlf": "Tilia cordata",
    "linden,shamrock": "Tilia cordata",
    "linden,american": "Tilia americana",
    "linden,amer": "Tilia americana",
    "linden,slv": "Tilia tomentosa",
    "linden,silver": "Tilia tomentosa",
    "linden,hungarian": "Tilia tomentosa",
    "linden,european": "Tilia x europaea",
    "linden,bigleaf": "Tilia platyphyllos",
    "linden,crimean": "Tilia x euchlora",
    # oaks
    "oak": "Quercus",
    "oak,red": "Quercus rubra",
    "oak,english": "Quercus robur",
    "oak,eng": "Quercus robur",
    "oak,bur": "Quercus macrocarpa",
    "oak,urban pinnacl": "Quercus macrocarpa",
    "oak,pin": "Quercus palustris",
    "oak,scarlet": "Quercus coccinea",
    "oak,swamp white": "Quercus bicolor",
    "oak,white": "Quercus alba",
    "oak,shingle": "Quercus imbricaria",
    "oak,ital": "Quercus frainetto",
    "oak,sawtoth": "Quercus acutissima",
    "oak,chinquapin": "Quercus muehlenbergii",
    "oak,chestnut": "Quercus montana",
    "oak,black": "Quercus velutina",
    "oak,schumard": "Quercus shumardii",
    "oak,gambell": "Quercus gambelii",
    # ashes
    "ash": "Fraxinus",
    "ash,green": "Fraxinus pennsylvanica",
    "ash,gr": "Fraxinus pennsylvanica",
    "ash,red": "Fraxinus pennsylvanica",
    "ash,wh": "Fraxinus americana",
    "ash,manchurian": "Fraxinus mandshurica",
    "ash,euro": "Fraxinus excelsior",
    "ash,blue": "Fraxinus quadrangulata",
    "ash,raywood": "Fraxinus angustifolia",
    "ash,flowering": "Fraxinus ornus",
    # poplars, willows
    "aspen": "Populus",
    "aspen,quaking": "Populus tremuloides",
    "aspen,quak": "Populus tremuloides",
    "aspen,euro": "Populus tremula",
    "aspen,bigtooth": "Populus grandidentata",
    "poplar": "Populus",
    "poplar,lombardy": "Populus nigra",
    "poplar,balsam": "Populus balsamifera",
    "poplar,white": "Populus alba",
    "cottonwood": "Populus",
    "cottonwood,eastrn": "Populus deltoides",
    "cottonwood,black": "Populus trichocarpa",
    "willow": "Salix",
    "willow,corkscrew": "Salix matsudana",
    "willow,weeping": "Salix babylonica",
    "willow,pussy": "Salix discolor",
    "willow,golden": "Salix alba",
    # birches, alders, hornbeams, hazels, beeches
    "birch": "Betula",
    "birch,eur white": "Betula pendula",
    "birch,eur wht": "Betula pendula",
    "birch,paper": "Betula papyrifera",
    "birch,river": "Betula nigra",
    "birch,gray": "Betula populifolia",
    "birch,yellow": "Betula alleghaniensis",
    "birch,sweet": "Betula lenta",
    "birch,western": "Betula occidentalis",
    "alder": "Alnus",
    "alder,europ blck": "Alnus glutinosa",
    "alder,white": "Alnus rhombifolia",
    "alder,italian": "Alnus cordata",
    "hornbeam": "Carpinus",
    "hornbm": "Carpinus",
    "hornbeam,european": "Carpinus betulus",
    "hornbeam,eur": "Carpinus betulus",
    "hornbm,eur": "Carpinus betulus",
    "hornbeam,emer ave": "Carpinus betulus",
    "hornbeam,american": "Carpinus caroliniana",
    "hornbeam,jap": "Carpinus japonica",
    "hophornbeam": "Ostrya",
    "hophornbeam,am": "Ostrya virginiana",
    "hophornbeam,euro": "Ostrya carpinifolia",
    "filbert": "Corylus",
    "filbert,turkish": "Corylus colurna",
    "filbert,eur": "Corylus avellana",
    "hazelnut": "Corylus",
    "hazelnut,american": "Corylus americana",
    "beech": "Fagus",
    "beech,eur": "Fagus sylvatica",
    "beech,am": "Fagus grandifolia",
    "chestnut,american": "Castanea dentata",
    "chestnut,chinese": "Castanea mollissima",
    "walnut": "Juglans",
    "walnut,english": "Juglans regia",
    "walnut,black": "Juglans nigra",
    "butternut": "Juglans cinerea",
    "hickory": "Carya",
    "wingnut,caucasia": "Pterocarya fraxinifolia",
    # horse chestnuts, dogwoods, magnolias
    "horsechest": "Aesculus",
    "horsechest,com": "Aesculus hippocastanum",
    "horsechest,red": "Aesculus x carnea",
    "horsechest,rd": "Aesculus x carnea",
    "buckeye": "Aesculus",
    "buckeye,ohio": "Aesculus glabra",
    "buckeye,red": "Aesculus pavia",
    "buckeye,yellow": "Aesculus flava",
    "buckeye,btlebrush": "Aesculus parviflora",
    "dgwood": "Cornus",
    "dgwood,flowering": "Cornus florida",
    "dgwood,fl": "Cornus florida",
    "dgwood,flw": "Cornus florida",
    "dgwood,kou": "Cornus kousa",
    "dgwood,kousa": "Cornus kousa",
    "dgwood,crnlnchry": "Cornus mas",
    "dgwood,giant": "Cornus controversa",
    "dgwood,pagoda": "Cornus alternifolia",
    "magno": "Magnolia",
    "magno,saucer": "Magnolia x soulangeana",
    "magno,star": "Magnolia stellata",
    "magno,loeb": "Magnolia x loebneri",
    "magno,cucumbrtre": "Magnolia acuminata",
    "magno,cucumb": "Magnolia acuminata",
    "magno,swt bay": "Magnolia virginiana",
    "magno,yulan": "Magnolia denudata",
    # single-species heads and the rest of the broadleaves
    "planetree": "Platanus x hispanica",
    "sycamore,american": "Platanus occidentalis",
    "tupelo": "Nyssa sylvatica",
    "katsuratree": "Cercidiphyllum japonicum",
    "katsu": "Cercidiphyllum japonicum",
    "tuliptree": "Liriodendron tulipifera",
    "sweetg": "Liquidambar styraciflua",
    "zelk": "Zelkova serrata",
    "catalpa": "Catalpa",
    "catalpa,northern": "Catalpa speciosa",
    "catalpa,nor": "Catalpa speciosa",
    "tree of heaven": "Ailanthus altissima",
    "parrotia": "Parrotia persica",
    "yellwood": "Cladrastis kentukea",
    "coffetree": "Gymnocladus dioicus",
    "mackia": "Maackia amurensis",
    "corktr": "Phellodendron amurense",
    "pagt": "Styphnolobium japonicum",
    "redbud": "Cercis canadensis",
    "hackbry": "Celtis occidentalis",
    "olive,russian": "Elaeagnus angustifolia",
    "silverberry": "Elaeagnus commutata",
    "mulberry": "Morus",
    "mulberry,white": "Morus alba",
    "mulberry,red": "Morus rubra",
    "smoketree": "Cotinus coggygria",
    "gdenchaintree": "Laburnum x watereri",
    "raintree,golden": "Koelreuteria paniculata",
    "hardy rubber tree": "Eucommia ulmoides",
    "silvrbel,carolina": "Halesia carolina",
    "silvrbl,mountain": "Halesia carolina",
    "snowbell": "Styrax japonicus",
    "stewartia,japnese": "Stewartia pseudocamellia",
    "tetradium,korean": "Tetradium daniellii",
    "persimmon,common": "Diospyros virginiana",
    "sassafras": "Sassafras albidum",
    "fringetree,wht": "Chionanthus virginicus",
    "witchhazel,comm": "Hamamelis virginiana",
    "witchhaz,vern": "Hamamelis vernalis",
    "lilac": "Syringa",
    "lilac,jap tree": "Syringa reticulata",
    "lilac,jap tre": "Syringa reticulata",
    "lilac,peking": "Syringa pekinensis",
    "lilac,common": "Syringa vulgaris",
    "sumac": "Rhus",
    "sumac,staghorn": "Rhus typhina",
    "tamarisk": "Tamarix",
    "crapemyrtle": "Lagerstroemia",
    "holly": "Ilex",
    "rose of sharon": "Hibiscus syriacus",
    # shrubs recorded as inventory trees
    "elderberry": "Sambucus",
    "elderber,euro blk": "Sambucus nigra",
    "elder,blueberry": "Sambucus cerulea",
    "buckthorn": "Rhamnus",
    "buckthorn,cutleaf": "Frangula alnus",
    "euony": "Euonymus",
    "euony,winged": "Euonymus alatus",
    "euony,dwf winged": "Euonymus alatus",
    "barbry": "Berberis",
    "privet": "Ligustrum",
    "rose": "Rosa",
    "honeysuckle": "Lonicera",
    "viburnum": "Viburnum",
    "forsyt": "Forsythia",
    "boxwood": "Buxus",
    "cotoneastr": "Cotoneaster",
    "rhododen/azal": "Rhododendron",
    "mockora": "Philadelphus",
    "mockora,sweet": "Philadelphus coronarius",
    "mockora,wild": "Philadelphus lewisii",
    "ninbar,com": "Physocarpus opulifolius",
    "weig": "Weigela",
    "beauty bush": "Kolkwitzia amabilis",
    # the layer's own unknown
    "unknown": UNKNOWN_SPECIES,
}

# `class 1/2/3` (and `class 2 upright`): planting-site size classes.
PLANTING_SITE_PREFIX = "class "


def normalise_code(value) -> tuple[str, list[str], bool]:
    """A `species` value as `(head, qualifier words, is_spp)`.

    Lower-cased, whitespace collapsed, quotes dropped; a `.` stands in for the
    comma in a few codes (`zelk.jap.wireless`, `hophornbeam.euro`).

    Examples:
        "mapl,nwy crmsn kg"    -> ("mapl", ["nwy", "crmsn", "kg"], False)
        "hawth spp"            -> ("hawth", [], True)
        "zelk.jap.wireless"    -> ("zelk", ["jap", "wireless"], False)
        "birch, western"       -> ("birch", ["western"], False)
        "fir,spp"              -> ("fir", [], True)
    """
    text = " ".join(str(value or "").replace("'", " ").replace('"', " ").lower().split())
    if "," not in text and "." in text:
        text = text.replace(".", ",", 1)
    text = text.replace(".", " ")
    head, _, tail = text.partition(",")
    head = head.strip()
    words = tail.split()
    is_spp = False
    if head.endswith(" spp"):
        head, is_spp = head[: -len(" spp")].strip(), True
    if words == ["spp"]:
        words, is_spp = [], True
    return head, words, is_spp


def decode_species(value) -> str | None:
    """The species value a Spokane contraction names, or None if undecoded.

    Examples:
        "mapl,nwy crmsn kg"   -> "Acer platanoides"
        "fir,douglas"         -> "Pseudotsuga menziesii"
        "crabappl,dorthea"    -> "Malus"
        "hawth spp"           -> "Crataegus"
        "honeyl,thornl 'sh"   -> "Gleditsia triacanthos"
        "Unknown"             -> "Unknown"
        "frobnitz,blue"       -> None
    """
    head, words, is_spp = normalise_code(value)
    if not head:
        return None
    if not is_spp:
        for k in range(len(words), 0, -1):
            hit = SPECIES_BY_CODE.get(f"{head},{' '.join(words[:k])}")
            if hit is not None:
                return hit
    return SPECIES_BY_CODE.get(head)


def is_planting_site(rec: dict) -> bool:
    return str(rec.get("species") or "").strip().lower().startswith(PLANTING_SITE_PREFIX)


def is_empty_site(rec: dict) -> bool:
    """`Unknown`, no trunk, and no diameter or height: an empty site."""
    return (
        str(rec.get("species") or "").strip().lower() in ("", "unknown")
        and not rec.get("trunks")
        and not rec.get("diameter")
        and not rec.get("height")
    )


DROPPED = {"planting site class": 0, "unknown with no trunk or size": 0}
UNDECODED: dict[str, int] = {}


def keep(feature: dict) -> bool:
    rec = feature.get("attributes") or {}
    if is_planting_site(rec):
        DROPPED["planting site class"] += 1
        return False
    if is_empty_site(rec):
        DROPPED["unknown with no trunk or size"] += 1
        return False
    return True


def iter_row_chunks():
    """One ArcGIS page at a time, geometry reprojected to WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for feature in features:
        rec = feature.get("attributes") or {}
        lat, lon = esri_point(feature.get("geometry"))
        code = rec.get("species")
        name = decode_species(code)
        if name is None:
            key = str(code)
            UNDECODED[key] = UNDECODED.get(key, 0) + 1

        tree_id.append(f"spokane-{int(rec['OBJECTID'])}")
        species.append(name)
        latitude.append(lat)
        longitude.append(lon)
        raw_dbh = rec.get("diameter")
        dbh.append(float(raw_dbh) if raw_dbh is not None else None)
        raw_spread = rec.get("spread")
        crown.append(float(raw_spread) * FEET_TO_M if raw_spread is not None else None)

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USGEG"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


def check_decoding(table: pa.Table) -> None:
    undecoded = sum(UNDECODED.values())
    if undecoded:
        top = sorted(UNDECODED.items(), key=lambda kv: -kv[1])[:20]
        print(f"Spokane: {undecoded} rows with an undecoded species code: {top}", file=sys.stderr)
    if table.num_rows and undecoded / table.num_rows > MAX_UNDECODED_SHARE:
        raise RuntimeError(
            f"Spokane: {undecoded} of {table.num_rows} rows carry a species code "
            f"SPECIES_BY_CODE does not decode (> {MAX_UNDECODED_SHARE:.0%}); "
            "read the new codes and add them"
        )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, keep=keep, label="Spokane OpenData")
    print(f"Spokane: dropped non-tree rows {DROPPED}", file=sys.stderr)
    check_decoding(table)
    table = validate_coordinates(table, city="Spokane", city_code="USGEG")
    table = enforce_tree_schema(table, city="Spokane", data_source="SPOKANE_OPENDATA")
    emit(table)
