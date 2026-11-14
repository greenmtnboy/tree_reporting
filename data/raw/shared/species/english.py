"""Resolve a municipal inventory's *common* name to an accepted binomial.

NOT a uv inline script — a regular importable module, like `shared.ingest`.

Most North American portals publish a scientific name.  Some publish only an
English common name where the binomial should be, and `species` is the join key
into the enrichment table, so those cities arrive on the map with every tree
labelled "Unknown" unless something resolves the name.  Five arrived at once --
Mississauga, Ottawa, Burlington ON, Ajax and Moncton, 951,561 rows between
them -- which is well past the threshold `EXTENDING.md` sets for writing a
shared module.

Three of the five call this: Mississauga on `BOTDESC`, Burlington ON on
`SPECIES_COMMONNAME`, Ajax on the English name its `SPCODE` domain gives.  The
other two turned out to need something else and are worth knowing about before
reaching for this: Ottawa's `SPECIES` domain carries the binomial itself
(`coded_value_domain`), and Moncton's contraction is expanded by a table in its
own ingest.  **Check for a field domain first** -- it is one request, and it is
the publisher's answer rather than ours.

Usage:

    from shared.species.english import species_from_common_name

    species_from_common_name("MAPLE - NORWAY")   # 'Acer platanoides'
    species_from_common_name("ASH SPP.")         # 'Fraxinus'
    species_from_common_name("TO BE UPDATED")    # None

**The table is hardcoded and curated, and that is the whole design decision.**
The obvious alternative is to invert the enrichment table -- it is already a
scientific-name -> common-names map -- and it does not survive contact with the
data.  Measured against the published `tree_enrichment_v2.parquet`: of 11,070
distinct common names on species-rank rows, 2,328 are claimed by more than one
species, and the ambiguous ones are exactly the trees these cities are made of.
"Norway spruce" is claimed by `Picea abies`, `Picea excelsa`, `Pinus abies` and
`Picea x mariorika`; "white oak" by `Quercus alba` and `Quercus slba`; "tulip
tree" by `Liriodendron tulipifera`, `Lilirodendron tulipifera`, `Liriodrendron
tulipifera` and `Lirodendron tulipiforia`.  Those competitors are misspelled
binomials that some city published, so no amount of tie-breaking inside the
index removes them -- an automatic reverse index would have resolved about 60%
of Mississauga's rows and silently mislabelled some of the rest.  The reverse
index was still worth building as a *drafting aid*, and several entries below
came from it; what it could not be is the authority.

`SPECIES_MISSPELLINGS` has since folded the ones a *published city* still
carried, which shrinks that ambiguity without removing it: a misspelling only
earns an entry when POWO can be asked about both spellings (see
`species_audit.py`), and a name like `Quercus slba` sits in the enrichment
table with no tree behind it and nothing to compare it against.  So the
argument stands -- the index is a drafting aid and the table below is the
authority -- and the examples above are the ones that outlived the fold.

So this is a curated list in the same spirit as `_NON_TAXON_REWRITES` and
`SPECIES_SYNONYMS`: every key is a value some wired city actually publishes,
every value is a name `sanitize_species` keeps as written, and a reviewer can
read it.  `tests/test_common_name_species.py` pins the mechanical properties --
values are accepted species-rank names, keys are in normalised form, no key is
also a value.

It earned its keep a second time as an *audit*, once the table was written.
Of the 398 entries, 247 name a species the enrichment table also lists under
that common name, 125 name one it does not list at all, and 26 disagree -- and
reading all 26 is what confirmed the curation rather than undermining it.  Most
are the enrichment table offering something worse (`accolade elm` as
`Ulmus accolade`, `hemlock` as *Tecoma stans*, `copper beech` as
`Fagus moesiaca`); several are it offering a synonym where this table has the
accepted name (`Ulmus procera` for the English elm, `Gymnocladus dioica`,
`Halesia tetraptera`); and the rest are the two regional calls below, which is
exactly where a disagreement should show up.

Three rules keep it honest, and they are why the file is shorter than the
number of trees would suggest:

* **A genus is an answer.**  `ASH SPP.`, `MAPLE`, `SERVICEBERRY` and
  `HAWTHORN` name a genus and nothing finer, so they resolve to `Fraxinus`,
  `Acer`, `Amelanchier`, `Crataegus`.  `sanitize_species` keeps a bare genus,
  the enrichment table is keyed on it, and a genus is what the source recorded.
  Inventing a species for it would label every such tree with a species the source never recorded (docs/SPECIES_ENRICHMENT.md).
* **A cultivar resolves to its taxon, and a hybrid cultivar to its genus.**
  `HONEY LOCUST 'SKYLINE'` is a *Gleditsia triacanthos*; `ACCOLADE ELM` and
  `TRIUMPH ELM` are complex Asian elm hybrids with no binomial to give, so
  they are `Ulmus`.  The cultivar itself is not lost -- `enforce_tree_schema`
  lifts a quoted selection into the `cultivar` column before this module's
  answer is used.
* **Unresolved is `None`, never a guess.**  The caller publishes `Unknown` for
  a name that is not here, which is what the map already shows for a tree
  nobody identified.  A wrong binomial is worse than no binomial: it labels
  every tree carrying it, with a photo and a description of the wrong plant.

**Where a name is genuinely ambiguous the local usage wins, and it is written
down.**  `CEDAR` is *Thuja occidentalis* here, not `Cedrus`, because every city
in this batch is in Ontario or New Brunswick where "cedar" is the eastern white
cedar; `IRONWOOD` is *Ostrya virginiana* for the same reason.  Both are marked
below.  A city outside that range must not reuse those two keys without
checking; the rest of the table is not regional.
"""

from __future__ import annotations

import re
import unicodedata

from shared.ingest import form_sentinel_for, is_not_a_tree

# A quoted cultivar: `HONEY LOCUST 'SKYLINE'`, `Basswood 'Redmond'`.  Removed
# before the key is built so one entry covers every selection of a taxon.
_QUOTED = re.compile(r"[\"'‘’][^\"'‘’]*[\"'‘’]")

# A hyphen with whitespace on at least one side, which is how Burlington
# inverts a name (`MAPLE - NORWAY`, `BUCKEYE- OHIO`, `SWEETGUM -ROTUNDILOBA`).
# A bare hyphen is part of the word and must not invert: `HORSE-CHESTNUT`,
# `MOUNTAIN-ASH`, `BLUE-BEECH` are single names, and inverting them produced
# `chestnut horse` and `ash mountain`.
_INVERTING_HYPHEN = re.compile(r"\s-|-\s")

# Rank abbreviations that carry no information once the name is a key.
_RANK_WORDS = re.compile(r"\b(spp?|species|var|cv)\b\.?", re.IGNORECASE)


def common_name_key(value: str | None) -> str:
    """A published common name reduced to the form `COMMON_NAME_SPECIES` uses.

    Casefolded, accent-free, cultivar-free, un-inverted, punctuation-free.
    Returns `""` for a value with nothing left in it.

    Examples:
        "MAPLE - NORWAY"                   -> "norway maple"
        "Spruce, Colorado"                 -> "colorado spruce"
        "JAPANESE TREE LILAC 'IVORY SILK'" -> "japanese tree lilac"
        "ASH SPP."                         -> "ash"
        "HORSE-CHESTNUT"                   -> "horse chestnut"
        "SHRUB / HEDGE"                    -> "shrub hedge"
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _QUOTED.sub(" ", text)

    match = _INVERTING_HYPHEN.search(text)
    if match:
        head, tail = text[: match.start()], text[match.end() :]
        text = f"{tail} {head}"
    elif text.count(",") == 1:
        head, _, tail = text.partition(",")
        text = f"{tail} {head}"

    text = _RANK_WORDS.sub(" ", text)
    text = re.sub(r"[^a-z ]", " ", text.lower())
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------
#
# Keys are `common_name_key` output; values are names `sanitize_species` keeps
# as written -- accepted at POWO, ASCII hybrid mark, genus capitalised, no rank
# below species.  Grouped by genus, because that is how it was checked.

COMMON_NAME_SPECIES: dict[str, str] = {
    # --- Acer ------------------------------------------------------------
    "maple": "Acer",
    "norway maple": "Acer platanoides",
    "columnar norway maple": "Acer platanoides",
    "norway maple columnar": "Acer platanoides",
    "red norway maple": "Acer platanoides",
    "crimson king maple": "Acer platanoides",
    "columnare maple": "Acer platanoides",
    "silver maple": "Acer saccharinum",
    "silver queen maple": "Acer saccharinum",
    "sugar maple": "Acer saccharum",
    "sugar maple columnar": "Acer saccharum",
    "red maple": "Acer rubrum",
    "red maple columnar": "Acer rubrum",
    "red maple armstrong columnar": "Acer rubrum",
    "armstrong maple": "Acer rubrum",
    "redpointe maple": "Acer rubrum",
    "bowhall maple": "Acer rubrum",
    # 'Autumn Blaze' and 'Sienna Glen' are the Freeman maple, a natural
    # A. rubrum x A. saccharinum hybrid the trade sells under both names.
    "freeman maple": "Acer x freemanii",
    "freemanni maple": "Acer x freemanii",
    "autumn blaze maple": "Acer x freemanii",
    "manitoba maple": "Acer negundo",
    "amur maple": "Acer ginnala",
    "hedge maple": "Acer campestre",
    "japanese maple": "Acer palmatum",
    "black maple": "Acer nigrum",
    "sycamore maple": "Acer pseudoplatanus",
    "tatarian maple": "Acer tataricum",
    "tartarian maple": "Acer tataricum",
    "paperbark maple": "Acer griseum",
    "bigleaf maple": "Acer macrophyllum",
    "state street maple": "Acer miyabei",
    # 'Norwegian Sunset' is A. truncatum x A. platanoides; the cross has no
    # published binomial, so the genus is the honest answer.
    "norwegian sunset maple": "Acer",

    # --- Aesculus --------------------------------------------------------
    "buckeye": "Aesculus",
    "ohio buckeye": "Aesculus glabra",
    "yellow buckeye": "Aesculus flava",
    "horsechestnut": "Aesculus hippocastanum",
    "horse chestnut": "Aesculus hippocastanum",
    "horse chestnut bauman": "Aesculus hippocastanum",
    "red horsechestnut": "Aesculus x carnea",
    "ruby red horsechestnut": "Aesculus x carnea",

    # --- Ailanthus, Alnus, Amelanchier, Aralia, Asimina ------------------
    "tree of heaven": "Ailanthus altissima",
    "alder": "Alnus",
    "speckled alder": "Alnus incana",
    "speckled adler": "Alnus incana",
    "european alder": "Alnus glutinosa",
    "european black alder": "Alnus glutinosa",
    "serviceberry": "Amelanchier",
    "eastern serviceberry": "Amelanchier canadensis",
    "devil s walking stick": "Aralia spinosa",
    "pawpaw": "Asimina triloba",

    # --- Betula ----------------------------------------------------------
    "birch": "Betula",
    "white birch": "Betula papyrifera",
    "european white birch": "Betula pendula",
    "weeping birch": "Betula pendula",
    "yellow birch": "Betula alleghaniensis",
    "river birch": "Betula nigra",
    "heritage river birch": "Betula nigra",
    "sweet birch": "Betula lenta",
    "cherry birch": "Betula lenta",

    # --- Caragana, Carpinus, Carya, Castanea -----------------------------
    "siberian pea shrub": "Caragana arborescens",
    "american hornbeam": "Carpinus caroliniana",
    "blue beech": "Carpinus caroliniana",
    "native blue beech": "Carpinus caroliniana",
    "blue beech hornbeam": "Carpinus caroliniana",
    "european hornbeam": "Carpinus betulus",
    "pyramidal european hornbeam": "Carpinus betulus",
    "european columnar hornbeam": "Carpinus betulus",
    "hickory": "Carya",
    "shagbark hickory": "Carya ovata",
    "bitternut hickory": "Carya cordiformis",
    "pignut hickory": "Carya glabra",
    "black hickory": "Carya texana",
    "pecan": "Carya illinoinensis",
    "american chestnut": "Castanea dentata",
    "chinese chestnut": "Castanea mollissima",
    "alleghany chinkapin": "Castanea pumila",

    # --- Catalpa, Celtis, Cercidiphyllum, Cercis -------------------------
    "northern catalpa": "Catalpa speciosa",
    "southern catalpa": "Catalpa bignonioides",
    "chinese catalpa": "Catalpa ovata",
    "japanese catalpa": "Catalpa ovata",
    "catalpa mop head": "Catalpa bignonioides",
    "hackberry": "Celtis occidentalis",
    "common hackberry": "Celtis occidentalis",
    "northern hackberry": "Celtis occidentalis",
    "prairie sentinel hackberry": "Celtis occidentalis",
    "katsura": "Cercidiphyllum japonicum",
    "katsura tree": "Cercidiphyllum japonicum",
    "redbud": "Cercis canadensis",
    "eastern redbud": "Cercis canadensis",

    # --- Chamaecyparis, Cladrastis, Cornus, Corylus, Cotinus -------------
    "false cypress": "Chamaecyparis",
    "weeping false cyprus": "Chamaecyparis",
    "yellow wood": "Cladrastis kentukea",
    "yellowwood": "Cladrastis kentukea",
    "dogwood": "Cornus",
    "flowering dogwood": "Cornus florida",
    "eastern flowering dogwood": "Cornus florida",
    "kousa dogwood": "Cornus kousa",
    "grey dogwood": "Cornus racemosa",
    "red osier dogwood": "Cornus sericea",
    "pagoda dogwood": "Cornus alternifolia",
    "alternate leaf dogwood": "Cornus alternifolia",
    "hazelnut": "Corylus",
    "american hazel": "Corylus americana",
    "european hazel": "Corylus avellana",
    "european filbert": "Corylus avellana",
    "turkish hazel": "Corylus colurna",
    "turkish hazelnut": "Corylus colurna",
    "smoke tree": "Cotinus coggygria",

    # --- Crataegus -------------------------------------------------------
    "hawthorn": "Crataegus",
    "english hawthorn": "Crataegus monogyna",
    "green hawthorn": "Crataegus viridis",
    "paul s scarlet hawthorn": "Crataegus laevigata",
    "toba hawthorn": "Crataegus x mordenensis",
    # Burlington's "HAWTHORN INTERMIS" is the thornless cockspur hawthorn,
    # Crataegus crus-galli 'Inermis' -- Ottawa spells the same tree out.
    "hawthorn intermis": "Crataegus crus-galli",
    "thornless hawthorn": "Crataegus crus-galli",

    # --- Diospyros, Elaeagnus, Eucommia, Euonymus ------------------------
    "common persimmon": "Diospyros virginiana",
    "russian olive": "Elaeagnus angustifolia",
    "silverberry": "Elaeagnus commutata",
    "hardy rubber tree": "Eucommia ulmoides",
    "burning bush": "Euonymus alatus",

    # --- Fagus -----------------------------------------------------------
    "beech": "Fagus",
    "american beech": "Fagus grandifolia",
    "european beech": "Fagus sylvatica",
    "copper beech": "Fagus sylvatica",
    "purple beech": "Fagus sylvatica",

    # --- Fraxinus --------------------------------------------------------
    "ash": "Fraxinus",
    "green ash": "Fraxinus pennsylvanica",
    "red ash": "Fraxinus pennsylvanica",
    "summit ash": "Fraxinus pennsylvanica",
    "summit green ash": "Fraxinus pennsylvanica",
    "patmore ash": "Fraxinus pennsylvanica",
    "cimmaron ash": "Fraxinus pennsylvanica",
    "white ash": "Fraxinus americana",
    "manitou white ash": "Fraxinus americana",
    "autumn purple white ash": "Fraxinus americana",
    "white ash kleinburg": "Fraxinus americana",
    "black ash": "Fraxinus nigra",
    "european ash": "Fraxinus excelsior",
    "blue ash": "Fraxinus quadrangulata",
    # 'Leprechaun' is sold as both a green and a Manchurian ash selection.
    "leprechaun ash": "Fraxinus",

    # --- Ginkgo, Gleditsia, Gymnocladus ----------------------------------
    "ginkgo": "Ginkgo biloba",
    "gingko": "Ginkgo biloba",
    "ginkgo biloba": "Ginkgo biloba",
    "autumn gold ginkgo": "Ginkgo biloba",
    "autumn gold gingko": "Ginkgo biloba",
    "princeton sentry ginkgo": "Ginkgo biloba",
    "princeton sentry gingko": "Ginkgo biloba",
    "magyar ginkgo": "Ginkgo biloba",
    "female gingko": "Ginkgo biloba",
    "honey locust": "Gleditsia triacanthos",
    "honeylocust": "Gleditsia triacanthos",
    "skyline honeylocust": "Gleditsia triacanthos",
    "shademaster": "Gleditsia triacanthos",
    "kentucky coffeetree": "Gymnocladus dioicus",
    "kentucky coffee tree": "Gymnocladus dioicus",
    "skinny latte kentucky coffee tree": "Gymnocladus dioicus",

    # --- Halesia, Hamamelis, Hibiscus ------------------------------------
    "carolina silverbell": "Halesia carolina",
    "mountain silverbell": "Halesia carolina",
    "witch hazel": "Hamamelis virginiana",
    "rose of sharon": "Hibiscus syriacus",

    # --- Ilex, Juglans ---------------------------------------------------
    "holly": "Ilex",
    "walnut": "Juglans",
    "black walnut": "Juglans nigra",
    "butternut": "Juglans cinerea",
    "butternut walnut": "Juglans cinerea",
    "english walnut": "Juglans regia",
    "little walnut": "Juglans microcarpa",

    # --- Juniperus, Koelreuteria, Laburnum, Larix ------------------------
    "juniper": "Juniperus",
    "eastern red cedar": "Juniperus virginiana",
    "eastern red juniper": "Juniperus virginiana",
    "golden rain tree": "Koelreuteria paniculata",
    "goldenrain tree": "Koelreuteria paniculata",
    "laburnum": "Laburnum",
    "golden chain tree": "Laburnum x watereri",
    "goldenchain tree": "Laburnum x watereri",
    "larch": "Larix",
    "tamarack": "Larix laricina",
    "european larch": "Larix decidua",

    # --- Liquidambar, Liriodendron, Maackia, Maclura ---------------------
    "sweet gum": "Liquidambar styraciflua",
    "sweetgum": "Liquidambar styraciflua",
    "rotundiloba sweetgum": "Liquidambar styraciflua",
    "moraine sweetgum": "Liquidambar styraciflua",
    "slender silhouette sweetgum": "Liquidambar styraciflua",
    "tulip tree": "Liriodendron tulipifera",
    "amur maackia": "Maackia amurensis",
    "maacnificent amur maackia": "Maackia amurensis",
    "osage orange": "Maclura pomifera",

    # --- Magnolia --------------------------------------------------------
    "magnolia": "Magnolia",
    "saucer magnolia": "Magnolia x soulangeana",
    "star magnolia": "Magnolia stellata",
    "cucumber tree": "Magnolia acuminata",
    "cucumber tree magnolia": "Magnolia acuminata",

    # --- Malus -----------------------------------------------------------
    # Every flowering crab in the trade is a hybrid selection; the species is
    # not recoverable from the trade name, so the genus is the answer.
    "apple": "Malus",
    "crab apple": "Malus",
    "crabapple": "Malus",
    "apple crab": "Malus",
    "apple crab flowering": "Malus",
    "flowering crab apple": "Malus",
    "snowdrift crabapple": "Malus",
    "red splendor crab apple": "Malus",
    "common apple": "Malus domestica",

    # --- Metasequoia, Morus, Nyssa, Ostrya, Parrotia ---------------------
    "dawn redwood": "Metasequoia glyptostroboides",
    "mulberry": "Morus",
    "mulberry mophead": "Morus",
    "white mulberry": "Morus alba",
    "red mulberry": "Morus rubra",
    "black gum": "Nyssa sylvatica",
    # REGIONAL: in Ontario and the Maritimes "ironwood" is the hop-hornbeam.
    # Elsewhere the same word is Casuarina, Olneya or Cyrilla -- do not reuse
    # this key for a city outside eastern North America without checking.
    "ironwood": "Ostrya virginiana",
    "iron wood": "Ostrya virginiana",
    "persian ironwood": "Parrotia persica",

    # --- Phellodendron, Philadelphus, Picea ------------------------------
    "amur corktree": "Phellodendron amurense",
    "amur cork": "Phellodendron amurense",
    "cork tree": "Phellodendron amurense",
    "mock orange": "Philadelphus",
    "spruce": "Picea",
    "dwarf spruce": "Picea",
    "colorado spruce": "Picea pungens",
    "colorado blue spruce": "Picea pungens",
    "colorado green spruce": "Picea pungens",
    "blue spruce": "Picea pungens",
    "white spruce": "Picea glauca",
    "norway spruce": "Picea abies",
    "serbian spruce": "Picea omorika",
    "black spruce": "Picea mariana",

    # --- Pinus -----------------------------------------------------------
    "pine": "Pinus",
    "austrian pine": "Pinus nigra",
    "white pine": "Pinus strobus",
    "eastern white pine": "Pinus strobus",
    "scotch pine": "Pinus sylvestris",
    "red pine": "Pinus resinosa",
    "jack pine": "Pinus banksiana",
    "mugho pine": "Pinus mugo",
    "pitch pine": "Pinus rigida",

    # --- Platanus --------------------------------------------------------
    # REGIONAL, and the reverse of the London usage: in Ontario "sycamore"
    # unqualified is the native American plane, and the hybrid is always
    # spelled out as a plane.
    "sycamore": "Platanus occidentalis",
    "american sycamore": "Platanus occidentalis",
    "london plane": "Platanus x hispanica",
    "london planetree": "Platanus x hispanica",
    "london plane sycamore": "Platanus x hispanica",
    "sycamore plane": "Platanus x hispanica",
    "bloodgood london plane": "Platanus x hispanica",
    "exclamation london plane": "Platanus x hispanica",

    # --- Populus ---------------------------------------------------------
    "poplar": "Populus",
    "trembling poplar": "Populus tremuloides",
    "trembling aspen": "Populus tremuloides",
    "trembling aspen poplar": "Populus tremuloides",
    "largetooth aspen": "Populus grandidentata",
    "balsam poplar": "Populus balsamifera",
    "eastern cottonwood": "Populus deltoides",
    "cottonwood poplar": "Populus deltoides",
    "lombardy poplar": "Populus nigra",
    "carolina poplar": "Populus x canadensis",
    "white poplar": "Populus alba",
    "european white poplar": "Populus alba",

    # --- Prunus ----------------------------------------------------------
    "cherry": "Prunus",
    "plum": "Prunus",
    "flowering cherry": "Prunus",
    "black cherry": "Prunus serotina",
    "choke cherry": "Prunus virginiana",
    "common chokecherry": "Prunus virginiana",
    "pin cherry": "Prunus pensylvanica",
    "kwanzan cherry": "Prunus serrulata",
    "sargent s cherry": "Prunus sargentii",
    "yoshino cherry": "Prunus x yedoensis",
    "bird cherry": "Prunus padus",
    "purple sand cherry": "Prunus x cistena",
    "purple plum": "Prunus cerasifera",
    "flowering plum": "Prunus cerasifera",
    "cherry plum": "Prunus cerasifera",
    "canada plum": "Prunus nigra",
    "flowering almond": "Prunus glandulosa",

    # --- Pseudotsuga, Ptelea, Pyrus --------------------------------------
    "douglas fir": "Pseudotsuga menziesii",
    "hoptree": "Ptelea trifoliata",
    "common hoptree": "Ptelea trifoliata",
    "pear": "Pyrus",
    "common pear": "Pyrus communis",
    "pear fruiting": "Pyrus communis",
    "callery pear": "Pyrus calleryana",
    "ornamental pear": "Pyrus calleryana",
    "capital ornamental pear": "Pyrus calleryana",
    "pear brad": "Pyrus calleryana",
    "redspire ornamental pear": "Pyrus calleryana",

    # --- Quercus ---------------------------------------------------------
    "oak": "Quercus",
    "red oak": "Quercus rubra",
    "northern red oak": "Quercus rubra",
    "northern rad oak": "Quercus rubra",
    "white oak": "Quercus alba",
    "bur oak": "Quercus macrocarpa",
    "burr oak": "Quercus macrocarpa",
    "english oak": "Quercus robur",
    "columnar oak": "Quercus robur",
    "pin oak": "Quercus palustris",
    "northern pin oak": "Quercus ellipsoidalis",
    "swamp white oak": "Quercus bicolor",
    "black oak": "Quercus velutina",
    "scarlet oak": "Quercus coccinea",
    "chinkapin oak": "Quercus muehlenbergii",
    "chinquapin oak": "Quercus muehlenbergii",
    "chestnut oak": "Quercus montana",
    "shumard oak": "Quercus shumardii",
    "shingle oak": "Quercus imbricaria",
    "live oak": "Quercus virginiana",
    # 'Regal Prince', 'Streetspire' and 'Skinny Genes' are all selections of
    # the same Q. robur x Q. bicolor cross, which POWO names.
    "regal prince oak": "Quercus x warei",
    "streetspire oak": "Quercus x warei",
    "skinny genes oak": "Quercus x warei",
    "swamp red oak": "Quercus",

    # --- Rhamnus, Rhus, Robinia ------------------------------------------
    # Bare "buckthorn" stays at the genus: common buckthorn is the one that
    # matters in southern Ontario, but the native alder-leaved buckthorn is
    # also a Rhamnus and the value does not say which.
    "buckthorn": "Rhamnus",
    "european buckthorn": "Rhamnus cathartica",
    "sumac": "Rhus",
    "staghorn sumac": "Rhus typhina",
    "black locust": "Robinia pseudoacacia",

    # --- Salix -----------------------------------------------------------
    "willow": "Salix",
    "common willow": "Salix",
    "weeping willow": "Salix babylonica",
    "corkscrew willow": "Salix matsudana",
    "corckscrew willow": "Salix matsudana",
    "black willow": "Salix nigra",
    "white willow": "Salix alba",
    "crack willow": "Salix x fragilis",
    "pussy willow": "Salix discolor",
    "bebb willow": "Salix bebbiana",
    "peachleaf willow": "Salix amygdaloides",

    # --- Sassafras, Sorbus -----------------------------------------------
    "sassafras": "Sassafras albidum",
    "mountain ash": "Sorbus",
    "european mountain ash": "Sorbus aucuparia",
    "european mountan ash": "Sorbus aucuparia",
    "american mountain ash": "Sorbus americana",
    "oakleaf mountain ash": "Sorbus x thuringiaca",
    "white beam": "Sorbus aria",

    # --- Styphnolobium, Syringa ------------------------------------------
    "japanese pagoda tree": "Styphnolobium japonicum",
    "pagoda tree": "Styphnolobium japonicum",
    "lilac": "Syringa",
    "japanese tree lilac": "Syringa reticulata",
    "japanaes tree lilac": "Syringa reticulata",
    "japanse tree lilac": "Syringa reticulata",
    "ivory silk lilac": "Syringa reticulata",
    "ivory silk tree lilac": "Syringa reticulata",
    "common lilac": "Syringa vulgaris",
    "korean lilac": "Syringa meyeri",
    "chinese lilac": "Syringa x chinensis",
    "beijing gold tree lilac": "Syringa pekinensis",
    # Mississauga's "GLENLEVEN LILAC" is a data-entry crossing: 'Glenleven' is
    # a Tilia cordata selection and the same file spells that one out.  The
    # genus is all the row honestly supports.
    "glenleven lilac": "Syringa",

    # --- Taxodium, Taxus, Thuja ------------------------------------------
    "bald cypress": "Taxodium distichum",
    "yew": "Taxus",
    "english yew": "Taxus baccata",
    "japanese yew": "Taxus cuspidata",
    "canada yew": "Taxus canadensis",
    # REGIONAL: "cedar" unqualified is the eastern white cedar throughout
    # eastern Canada.  A city where Cedrus or Juniperus is the local referent
    # must not reuse this key.
    "cedar": "Thuja occidentalis",
    "eastern white cedar": "Thuja occidentalis",
    "northern white cedar": "Thuja occidentalis",
    "norther white cedar": "Thuja occidentalis",

    # --- Tilia -----------------------------------------------------------
    "linden": "Tilia",
    "unique linden": "Tilia",
    "littleleaf linden": "Tilia cordata",
    "greenspire": "Tilia cordata",
    "greenspire linden": "Tilia cordata",
    "greenspire littleleaf linden": "Tilia cordata",
    "basswood": "Tilia americana",
    "american basswood": "Tilia americana",
    "basswood linden": "Tilia americana",
    "redman linden": "Tilia americana",
    "silver linden": "Tilia tomentosa",
    "sterling silver linden": "Tilia tomentosa",

    # --- Tsuga, Ulmus ----------------------------------------------------
    "hemlock": "Tsuga canadensis",
    "eastern hemlock": "Tsuga canadensis",
    "carloina hemlock": "Tsuga caroliniana",
    "elm": "Ulmus",
    "american elm": "Ulmus americana",
    "white elm": "Ulmus americana",
    "siberian elm": "Ulmus pumila",
    "chinese elm": "Ulmus parvifolia",
    "lacebark elm": "Ulmus parvifolia",
    "elm lacebark everclear": "Ulmus parvifolia",
    "english elm": "Ulmus minor",
    "wych elm": "Ulmus glabra",
    "rock elm": "Ulmus thomasii",
    # The disease-resistant street elms are complex Asian hybrids sold under a
    # trade name; none of them has a binomial below the genus.
    "accolade elm": "Ulmus",
    "triumph elm": "Ulmus",
    "pioneer elm": "Ulmus",
    "homestead elm": "Ulmus",
    "patriot elm": "Ulmus",
    "elm prospector": "Ulmus",
    "elm commendation": "Ulmus",
    "valley forge elm": "Ulmus",

    # --- Viburnum, Zelkova -----------------------------------------------
    "viburnum": "Viburnum",
    "nannyberry": "Viburnum lentago",
    "nannyberry viburnum": "Viburnum lentago",
    "highbush cranberry": "Viburnum opulus",
    "zelkova": "Zelkova serrata",
    "japanese zelkova": "Zelkova serrata",
    "musashino zelkova": "Zelkova serrata",
    "wireless zelkova": "Zelkova serrata",

    # --- Arlington VA (DPR Trees): USDA PLANTS spellings, closed compounds -
    # Arlington writes the one-word USDA forms ("Tuliptree", "Blackgum") and
    # inverts on the comma.  Cultivar crosses with no binomial ('Okame',
    # 'Pacific Sunset') and generic forms resolve to the genus.
    "pacific sunset maple": "Acer",
    "trident maple": "Acer buergerianum",
    "boxelder": "Acer negundo",
    "red buckeye": "Aesculus pavia",
    "european horsechestnut": "Aesculus hippocastanum",
    "mimosa": "Albizia julibrissin",
    "downy serviceberry": "Amelanchier arborea",
    "american shadbush": "Amelanchier canadensis",
    "allegheny serviceberry": "Amelanchier laevis",
    "paper birch": "Betula papyrifera",
    "mockernut hickory": "Carya tomentosa",
    "shellbark hickory": "Carya laciniosa",
    "allegheny chinkapin": "Castanea pumila",
    "catalpa": "Catalpa",
    "deodar cedar": "Cedrus deodara",
    "blue atlas cedar": "Cedrus atlantica",
    "dwarf hackberry": "Celtis tenuifolia",
    "katsuratree": "Cercidiphyllum japonicum",
    "hinoki cypress": "Chamaecyparis obtusa",
    "sawara false cypress": "Chamaecyparis pisifera",
    "white fringetree": "Chionanthus virginicus",
    "chinese fringetree": "Chionanthus retusus",
    "silky dogwood": "Cornus amomum",
    "american hazelnut": "Corylus americana",
    "smoketree": "Cotinus coggygria",
    "european smoketree": "Cotinus coggygria",
    "washington hawthorn": "Crataegus phaenopyrum",
    "cockspur hawthorn": "Crataegus crus-galli",
    "thornless cockspur hawthorn": "Crataegus crus-galli",
    "japanese cedar": "Cryptomeria japonica",
    "common chinafir": "Cunninghamia lanceolata",
    "leyland cypress": "Cupressus x leylandii",
    "dove tree": "Davidia involucrata",
    "american persimmon": "Diospyros virginiana",
    "japanese persimmon": "Diospyros kaki",
    "autumn olive": "Elaeagnus umbellata",
    "eastern wahoo": "Euonymus atropurpureus",
    "franklinia": "Franklinia alatamaha",
    "thornless honeylocust": "Gleditsia triacanthos",
    "silverbell": "Halesia",
    "american witchhazel": "Hamamelis virginiana",
    "american holly": "Ilex opaca",
    "foster s holly": "Ilex x attenuata",
    "eastern redcedar": "Juniperus virginiana",
    "common juniper": "Juniperus communis",
    "goldenraintree": "Koelreuteria paniculata",
    "crapemyrtle": "Lagerstroemia indica",
    "common crapemyrtle": "Lagerstroemia indica",
    "common larch": "Larix decidua",
    "spicebush": "Lindera benzoin",
    "american sweetgum": "Liquidambar styraciflua",
    "tuliptree": "Liriodendron tulipifera",
    "sweetbay magnolia": "Magnolia virginiana",
    "southern magnolia": "Magnolia grandiflora",
    "bigleaf magnolia": "Magnolia macrophylla",
    "umbrella magnolia": "Magnolia tripetala",
    "southern crabapple": "Malus angustifolia",
    "sweet crabapple": "Malus coronaria",
    "blackgum": "Nyssa sylvatica",
    "eastern hophornbeam": "Ostrya virginiana",
    "sourwood": "Oxydendrum arboreum",
    "persian parrotia": "Parrotia persica",
    "royal paulownia": "Paulownia tomentosa",
    "photinia": "Photinia",
    "japanese black pine": "Pinus thunbergii",
    "virginia pine": "Pinus virginiana",
    "loblolly pine": "Pinus taeda",
    "shortleaf pine": "Pinus echinata",
    "longleaf pine": "Pinus palustris",
    "pistache": "Pistacia",
    "okame cherry": "Prunus",
    "purpleleaf plum": "Prunus cerasifera",
    "chokecherry": "Prunus virginiana",
    "american plum": "Prunus americana",
    "chickasaw plum": "Prunus angustifolia",
    "sweet cherry": "Prunus avium",
    "carolina laurel cherry": "Prunus caroliniana",
    "willow oak": "Quercus phellos",
    "southern red oak": "Quercus falcata",
    "overcup oak": "Quercus lyrata",
    "post oak": "Quercus stellata",
    "sawtooth oak": "Quercus acutissima",
    "blackjack oak": "Quercus marilandica",
    "swamp chestnut oak": "Quercus michauxii",
    "darlington oak": "Quercus hemisphaerica",
    "water oak": "Quercus nigra",
    "laurel oak": "Quercus laurifolia",
    "smooth sumac": "Rhus glabra",
    "shining sumac": "Rhus copallinum",
    "american sassafras": "Sassafras albidum",
    "japanese umbrella pine": "Sciadopitys verticillata",
    "giant redwood": "Sequoiadendron giganteum",
    "american snowbell": "Styrax americanus",
    "japanese snowbell": "Styrax japonicus",
    "baldcypress": "Taxodium distichum",
    "american arborvitae": "Thuja occidentalis",
    "american linden": "Tilia americana",
    "little leaf linden": "Tilia cordata",
    "poison sumac": "Toxicodendron vernix",
    "slippery elm": "Ulmus rubra",
    "blackhaw viburnum": "Viburnum prunifolium",

    # --- Charlotte NC (Landscape Management Trees) -----------------------
    # Charlotte writes `GENUS: EPITHET` or `GENUS EPITHET` in capitals; its
    # ingest un-inverts both before the key is built, so these keys are in
    # natural order.  Southeastern street and landscape trees, each checked
    # against POWO for the accepted name.  `painted maple` is the genus: the
    # name is GRIN's for *A. pictum*, which 623 Charlotte street trees are
    # unlikely to be, and a maple is all it certainly says.  'Nellie R.
    # Stevens' (I. aquifolium x I. cornuta), 'Okame', 'Snow Goose' and the
    # trade elms have no binomial; their genus is the answer.  Nuttall oak is
    # *Q. texana* (POWO sinks *Q. nuttallii* into it); southern wax myrtle is
    # *Morella cerifera* and devilwood *Cartrema americana* under POWO's
    # current generic placement; pondcypress is *T. distichum* var.
    # *imbricarium*, so the species is *T. distichum*.  Leatherleaf mahonia is
    # the genus: POWO has sunk *Mahonia* into *Berberis*, and the trade name
    # covers both *B. bealei* and *B. japonica*.
    "painted maple": "Acer",
    "boxelder maple": "Acer negundo",
    "state maple": "Acer miyabei",
    "florida maple": "Acer floridanum",
    "southern sugar maple": "Acer floridanum",
    "shantung maple": "Acer truncatum",
    "legacy sugar maple": "Acer saccharum",
    "apple serviceberry": "Amelanchier x grandiflora",
    "common pawpaw": "Asimina triloba",
    "aucuba": "Aucuba",
    "eastern baccharis": "Baccharis halimifolia",
    "paper mulberry": "Broussonetia papyrifera",
    "butterfly bush": "Buddleja",
    "jelly palm": "Butia",
    "leatherleaf mahonia": "Berberis",
    "boxwood": "Buxus",
    "american beautyberry": "Callicarpa americana",
    "camellia": "Camellia",
    "japanese camellia": "Camellia japonica",
    "devilwood": "Cartrema americana",
    "atlas cedar": "Cedrus atlantica",
    "cedar of lebanon": "Cedrus libani",
    "sugar hackberry": "Celtis laevigata",
    "lawson cypress": "Chamaecyparis lawsoniana",
    "american yellowwood": "Cladrastis kentukea",
    "harlequin glorybower": "Clerodendrum trichotomum",
    "forest pansy redbud": "Cercis canadensis",
    "appalachian spring dogwood": "Cornus florida",
    "american filbert": "Corylus americana",
    "american smoketree": "Cotinus obovatus",
    "japanese cryptomeria": "Cryptomeria japonica",
    "blue china fir": "Cunninghamia lanceolata",
    "arizona cypress": "Cupressus arizonica",
    "italian cypress": "Cupressus sempervirens",
    "japanese medlar": "Eriobotrya japonica",
    "eucalyptus": "Eucalyptus",
    "euonymus": "Euonymus",
    "silver dollar tree": "Eucalyptus cinerea",
    "common fig": "Ficus carica",
    "commom fig": "Ficus carica",
    "indian laurel fig": "Ficus microcarpa",
    "chinese parasol tree": "Firmiana simplex",
    "forsythia": "Forsythia",
    "common witchhazel": "Hamamelis virginiana",
    "hydrangea": "Hydrangea",
    "english holly": "Ilex aquifolium",
    "chinese holly": "Ilex cornuta",
    "dahoon holly": "Ilex cassine",
    "yaupon holly": "Ilex vomitoria",
    "nellie holly": "Ilex",
    "chinese juniper": "Juniperus chinensis",
    "rocky mountain juniper": "Juniperus scopulorum",
    "golden raintree": "Koelreuteria paniculata",
    "crape myrtle": "Lagerstroemia indica",
    "bay laurel": "Laurus nobilis",
    "ligustrum": "Ligustrum",
    "privet": "Ligustrum",
    "glossy privet": "Ligustrum lucidum",
    "fruitless sweetgum": "Liquidambar styraciflua",
    "honeysuckle": "Lonicera",
    "chinese fringe flower": "Loropetalum chinense",
    "lily magnolia": "Magnolia liliiflora",
    "big leaf magnolia": "Magnolia macrophylla",
    "sweetbay": "Magnolia virginiana",
    "green mile magnolia": "Magnolia virginiana",
    "green mile sweetbay": "Magnolia virginiana",
    "green mile sweetbay magnolia": "Magnolia virginiana",
    "tschonoskii crabapple": "Malus tschonoskii",
    "european crabapple": "Malus sylvestris",
    "chinaberry": "Melia azedarach",
    "southern waxmyrtle": "Morella cerifera",
    "southern wax myrtle": "Morella cerifera",
    "banana": "Musa",
    "banana tree": "Musa",
    "oleander": "Nerium oleander",
    "common blackgum": "Nyssa sylvatica",
    "hophornbeam": "Ostrya virginiana",
    "princess tree": "Paulownia tomentosa",
    "avocado": "Persea americana",
    "fraser s photinia": "Photinia x fraseri",
    "frasers photinia": "Photinia x fraseri",
    "alberta spruce": "Picea glauca",
    "mugo pine": "Pinus mugo",
    "slash pine": "Pinus elliottii",
    "turkish pine": "Pinus brutia",
    "chinese pistache": "Pistacia chinensis",
    "oriental arborvitae": "Platycladus orientalis",
    "podocarpus": "Podocarpus",
    "cherry plum peach": "Prunus",
    "peach": "Prunus persica",
    "carolina cherrylaurel": "Prunus caroliniana",
    "thundercloud purple plum": "Prunus cerasifera",
    "oriental cherry": "Prunus serrulata",
    "okame cherrry": "Prunus",
    "snowgoose cherry": "Prunus",
    "higan cherry": "Prunus x subhirtella",
    "autumnalis cherry": "Prunus x subhirtella",
    "nanking cherry": "Prunus tomentosa",
    "sargent cherry": "Prunus sargentii",
    "chinese quince": "Pseudocydonia sinensis",
    "pomegranate": "Punica granatum",
    "pyracantha": "Pyracantha",
    "nuttall oak": "Quercus texana",
    "nutall oak": "Quercus texana",
    "chinese evergreen oak": "Quercus myrsinifolia",
    "bluff oak": "Quercus austrina",
    "rhododendron": "Rhododendron",
    "white lady banks rose": "Rosa banksiae",
    "cabbage palm": "Sabal palmetto",
    "saw palmetto": "Serenoa repens",
    "japanese pagodatree": "Styphnolobium japonicum",
    "snowbell": "Styrax",
    "common baldcypress": "Taxodium distichum",
    "pondcypress": "Taxodium distichum",
    "western red cedar": "Thuja plicata",
    "florida torreya": "Torreya taxifolia",
    "windmill palm": "Trachycarpus fortunei",
    "chinese tallow tree": "Triadica sebifera",
    "winged elm": "Ulmus alata",
    "red elm": "Ulmus rubra",
    "field elm": "Ulmus minor",
    "cedar elm": "Ulmus crassifolia",
    "dutch elm": "Ulmus x hollandica",
    "frontier elm": "Ulmus",
    "emerald elm": "Ulmus",
    "emerald sunshine elm": "Ulmus",
    "arrowwood": "Viburnum dentatum",
    "chastetree": "Vitex agnus-castus",
    "lilac chaste tree": "Vitex agnus-castus",
    "mexican fan palm": "Washingtonia robusta",
    "wisteria": "Wisteria",
    "yucca": "Yucca",
    "common jujube": "Ziziphus jujuba",

    # --- Abies, and the firs, kept last because "fir" is not one genus ----
    "fir": "Abies",
    "balsam fir": "Abies balsamea",
    "white fir": "Abies concolor",
    "silver fir": "Abies alba",
    "silver fit": "Abies alba",
    "fraser fir": "Abies fraseri",
    "korean fir": "Abies koreana",
    "momi fir": "Abies firma",
    # --- Texas and the Gulf: Austin, Houston, Durham, New Orleans ---------
    # Austin's Tree Inventory and Houston's street-tree layer publish common
    # names only.  Values were checked against GBIF's backbone (exact, accepted)
    # and follow the table's POWO conventions where they differ: Pistacia
    # mexicana for Texas pistache (P. texana is its synonym), Quercus canbyi
    # for chisos oak, Eriobotrya and Cinnamomum as the rest of the table spells
    # them.  Regional readings that would be wrong elsewhere stay in the city
    # ingests, not here: bare `cedar` and `live oak` (Austin, Houston), `cherry
    # laurel` (Prunus laurocerasus in Europe), `japanese plum` (Houston's
    # loquat), `mountain laurel` (Kalmia in the north-east), `black pine`.
    "afghan pine": "Pinus brutia",
    "ailanthus": "Ailanthus altissima",
    "althea": "Hibiscus syriacus",
    "american live oak": "Quercus virginiana",
    "american sweet gum": "Liquidambar styraciflua",
    "anacacho orchid": "Bauhinia lunarioides",
    "anacacho orchid tree": "Bauhinia lunarioides",
    "anacua": "Ehretia anacua",
    "anaqua": "Ehretia anacua",
    "arizona ash": "Fraxinus velutina",
    "arroyo sweetwood": "Myrospermum sousanum",
    "ashe juniper": "Juniperus ashei",
    "bank catclaw": "Senegalia roemeriana",
    "bastard oak": "Quercus sinuata",
    "bauhinia": "Bauhinia",
    "big bend yucca": "Yucca rostrata",
    "bigtooth maple": "Acer grandidentatum",
    "bois d arc": "Maclura pomifera",
    "box elder": "Acer negundo",
    "bradford pear": "Pyrus calleryana",
    "bronze loquat": "Eriobotrya deflexa",
    "buckley oak": "Quercus buckleyi",
    "bumelia": "Sideroxylon",
    "burford holly": "Ilex cornuta",
    "california fan palm": "Washingtonia filifera",
    "camphor": "Cinnamomum camphora",
    "canary island date palm": "Phoenix canariensis",
    "canby oak": "Quercus canbyi",
    "carolina buckthorn": "Frangula caroliniana",
    "carolina laurelcherry": "Prunus caroliniana",
    "chaste tree": "Vitex agnus-castus",
    "chasteberry": "Vitex agnus-castus",
    "cherrybark oak": "Quercus pagoda",
    "chile yew": "Podocarpus",
    "chilean mesquite": "Prosopis chilensis",
    "chinaberrytree": "Melia azedarach",
    "chinese flame tree": "Koelreuteria bipinnata",
    "chinese fringe tree": "Chionanthus retusus",
    "chinese parasol": "Firmiana simplex",
    "chinese photinia": "Photinia serratifolia",
    "chinese privet": "Ligustrum sinense",
    "chinese tallow": "Triadica sebifera",
    "chiso s red oak": "Quercus gravesii",
    "chisos oak": "Quercus canbyi",
    "chisos red oak": "Quercus gravesii",
    "citrus": "Citrus",
    "common loquat": "Eriobotrya japonica",
    "common pricklyash": "Zanthoxylum americanum",
    "common winterberry": "Ilex verticillata",
    "condalia": "Condalia",
    "cork oak": "Quercus suber",
    "cottonwood": "Populus",
    "desert willow": "Chilopsis linearis",
    "devils walkingstick": "Aralia spinosa",
    "drake elm": "Ulmus parvifolia",
    "durand oak": "Quercus sinuata",
    "eastern black cherry": "Prunus serotina",
    "eastern catalpa": "Catalpa bignonioides",
    "eastern hophornbean": "Ostrya virginiana",
    "eastern wax myrtle": "Morella cerifera",
    "edible fig": "Ficus carica",
    "edible loquat": "Eriobotrya japonica",
    "edible pear": "Pyrus communis",
    "emory oak": "Quercus emoryi",
    "escarpment black cherry": "Prunus serotina",
    "escarpment live oak": "Quercus fusiformis",
    "eve s necklace": "Styphnolobium affine",
    "evergreen sumac": "Rhus virens",
    "eves necklacepod": "Styphnolobium affine",
    "ficus": "Ficus",
    "fig": "Ficus",
    "firethorn": "Pyracantha",
    "flamegold": "Koelreuteria elegans",
    "formosa flamegold": "Koelreuteria elegans",
    "fraser photinia": "Photinia x fraseri",
    "frasier photinia": "Photinia x fraseri",
    "fringe tree": "Chionanthus virginicus",
    "golden leadball": "Leucaena retusa",
    "goldenball leadtree": "Leucaena retusa",
    "guadalupe palm": "Brahea edulis",
    "gum bully": "Sideroxylon lanuginosum",
    "gum bumelia": "Sideroxylon lanuginosum",
    "holly yaupon": "Ilex vomitoria",
    "honey mesquite": "Prosopis glandulosa",
    "huisache": "Vachellia farnesiana",
    "indian hawthorne": "Rhaphiolepis indica",
    "italian stone pine": "Pinus pinea",
    "japanese blueberry": "Elaeocarpus decipiens",
    "japanese blueberry tree": "Elaeocarpus decipiens",
    "japanese privet": "Ligustrum japonicum",
    "jeffery pine": "Pinus jeffreyi",
    "jerusalem thorn": "Parkinsonia aculeata",
    "kidneywood": "Eysenhardtia",
    "knockaway": "Ehretia anacua",
    "lacey oak": "Quercus laceyi",
    "leadball tree": "Leucaena retusa",
    "lilac chasetree": "Vitex agnus-castus",
    "lindheimer hackberry": "Celtis lindheimeri",
    "littleleaf leadtree": "Leucaena retusa",
    "loquat": "Eriobotrya japonica",
    "maidenhair tree": "Ginkgo biloba",
    "mediterranean fan palm": "Chamaerops humilis",
    "mescal bean": "Dermatophyllum secundiflorum",
    "mesquite": "Prosopis",
    "mexican ash": "Fraxinus berlandieriana",
    "mexican buckeye": "Ungnadia speciosa",
    "mexican plum": "Prunus mexicana",
    "mexican redbud": "Cercis canadensis",
    "mexican sabal palm": "Sabal mexicana",
    "mexican sycamore": "Platanus mexicana",
    "mexican whie oak": "Quercus polymorpha",
    "mexican white oak": "Quercus polymorpha",
    "monterey cypress": "Cupressus macrocarpa",
    "monterey oak": "Quercus polymorpha",
    "monterey white oak": "Quercus polymorpha",
    "monterrey oak": "Quercus polymorpha",
    "montezuma bald cypress": "Taxodium mucronatum",
    "montezuma cypress": "Taxodium mucronatum",
    "netleaf hackberry": "Celtis reticulata",
    "norfolk island pine": "Araucaria heterophylla",
    "oklahoma redbud": "Cercis canadensis",
    "palmetto": "Sabal",
    "palo verde": "Parkinsonia",
    "pindo palm": "Butia capitata",
    "pistachio": "Pistacia vera",
    "pond cypress": "Taxodium distichum",
    "possum haw": "Ilex decidua",
    "possum haw holly": "Ilex decidua",
    "possumhaw": "Ilex decidua",
    "prairie sumac": "Rhus lanceolata",
    "purple leaf plum": "Prunus cerasifera",
    "purple leafed plum": "Prunus cerasifera",
    "red tip photenia": "Photinia x fraseri",
    "red tip photinia": "Photinia x fraseri",
    "redbay": "Persea borbonia",
    "retama": "Parkinsonia aculeata",
    "rio grande palmetto": "Sabal mexicana",
    "roughleaf dogwood": "Cornus drummondii",
    "rusty blackhaw": "Viburnum rufidulum",
    "shoestring acacia": "Acacia stenophylla",
    "silk tree": "Albizia julibrissin",
    "silk tree mimosa": "Albizia julibrissin",
    "silktree": "Albizia julibrissin",
    "soapberry": "Sapindus",
    "southern live oak": "Quercus virginiana",
    "southern pecan": "Carya illinoinensis",
    "stone fruit": "Prunus",
    "sugarberry": "Celtis laevigata",
    "sweet acacia": "Vachellia farnesiana",
    "texas ash": "Fraxinus albicans",
    "texas ebony": "Ebenopsis ebano",
    "texas elm": "Ulmus crassifolia",
    "texas kidneywood": "Eysenhardtia texana",
    "texas live oak": "Quercus fusiformis",
    "texas madrone": "Arbutus xalapensis",
    "texas mountain laurel": "Dermatophyllum secundiflorum",
    "texas mulberry": "Morus microphylla",
    "texas olive": "Cordia boissieri",
    "texas paloverde": "Parkinsonia texana",
    "texas persimmon": "Diospyros texana",
    "texas pistache": "Pistacia mexicana",
    "texas red oak": "Quercus buckleyi",
    "texas redbud": "Cercis canadensis",
    "texas sumac": "Rhus",
    "thornless chilean mesquite": "Prosopis chilensis",
    "velvet ash": "Fraxinus velutina",
    "vitex": "Vitex",
    "wax myrtle": "Morella cerifera",
    "waxleaf ligustrum": "Ligustrum japonicum",
    "western catalpa": "Catalpa speciosa",
    "western soapberry": "Sapindus saponaria",
    "white shin oak": "Quercus sinuata",
    "wingleaf soapberry": "Sapindus saponaria",
    "woolly bumelia": "Sideroxylon lanuginosum",
    "yaupon": "Ilex vomitoria",
    "yellow poplar": "Liriodendron tulipifera",
    "yew pine": "Podocarpus macrophyllus",
}


def species_from_common_name(value: str | None) -> str | None:
    """A published common name as a species value, or ``None``.

    The return value is what belongs in the ``species`` column, ready for
    `enforce_tree_schema`:

    * a name from `COMMON_NAME_SPECIES` when the value is one we resolve;
    * the value **unchanged** when the shared hygiene already understands it --
      an empty planting site or stump (`is_not_a_tree`, which makes
      `enforce_tree_schema` drop the row) or a growth form (`form_sentinel_for`,
      which keeps "Shrub" rather than losing it to "Unknown");
    * ``None`` otherwise, which publishes as ``Unknown``.

    Passing an *unresolved* common name through would be the tempting third
    option and is deliberately refused.  `sanitize_species` rejects most of
    them, but not all: a single capitalised word is indistinguishable from a
    genus, so "Katsura" and "Hackberry" would publish as invented genera, and
    every tree carrying one would join to an enrichment row describing a plant
    that does not exist.  Unresolved is `Unknown`, and `Unknown` is honest.
    """
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    resolved = COMMON_NAME_SPECIES.get(common_name_key(text))
    if resolved is not None:
        return resolved
    if is_not_a_tree(text) or form_sentinel_for(text) is not None:
        return text
    return None
