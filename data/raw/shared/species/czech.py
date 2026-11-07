"""Resolve Prague's Czech inventory name to an accepted binomial.

NOT a uv inline script -- a regular importable module, like `shared.ingest`.

Prague's shared green-space register (`Sdílená data o zeleni`, IPR Praha)
is assembled from the inventories of fifty-seven city districts and three
city-wide managers, each keeping its own software, and the species column
shows it.  `TAXON_DRUH` is a Latin binomial on the rows one supplier wrote
(`Acer platanoides`, with `TAXON_ROD` carrying the genus beside it) and a
Czech vernacular name on everyone else's (`javor mléč`, `lípa srdčitá`,
`trnovník akát`, with `TAXON_ROD` null): 332 distinct Czech strings over
roughly 90,000 of the 100,000 trees that name a species at all.  `species`
is the join key into the enrichment table, so without this the city's
most common trees arrive labelled `Unknown`.

This is `shared.species.spanish` applied to a fourth language, with the
same shape: a curated dict, a key normaliser, and a lookup that returns
`None` rather than guessing.  Czech earns its own module for the reason
Spanish did -- the keys fold their own diacritics (`ř`, `ů`, `ě`, `ď`)
and the register's own habits (` - neurčená` for "undetermined", a quoted
cultivar inside the name, a parenthetical gloss) are not another table's.

Usage:

    from shared.species.czech import species_from_czech_name

    species_from_czech_name("lípa srdčitá")        # 'Tilia cordata'
    species_from_czech_name("lípa - neurčená")     # 'Tilia'  -- a genus is an answer
    species_from_czech_name("trešen ptací")        # 'Prunus avium'  (diacritics lost upstream)
    species_from_czech_name("sakura ozdobná 'Kanzan'")  # 'Prunus serrulata'
    species_from_czech_name("03_neurčeno")         # None

**The whole string is the key**, with the quoted cultivar removed first.
Czech binomial vernaculars are genus-word plus epithet-word (`javor mléč`,
`javor klen`, `javor babyka` are three *Acer*), so the first word alone
would collapse every species in a genus; and the register's "undetermined"
forms (`javor neurčený`, `lípa - neurčená`) are entries in their own right
that resolve to the genus.  A cultivar quoted inside the name
(`třešeň křovitá 'Globosa'`) is not part of the taxon and is dropped
before lookup; the ingest keeps it on the tree row from `TAXON_KULTIVAR`
or `extract_cultivar`.

**The names are standard Czech dendrological vernaculars**, and most map
one-to-one onto a binomial the way the Czech botanical nomenclature
(Kubát et al., *Klíč ke květeně ČR*; the Dendrologická zahrada's
catalogue) fixes them: `lípa srdčitá` is *Tilia cordata* and nothing
else.  Judgement calls worth naming:

* `jeřáb obecný` and `jeřáb ptačí` are both *Sorbus aucuparia* -- the
  older and newer standard names for one species.
* `platan španělský` and `platan javorolistý` are both the London plane,
  *Platanus x hispanica*, the accepted name `SPECIES_SYNONYMS` folds
  `P. x acerifolia` onto.
* `slivoň mirabelka` and `slivoň slíva` are cultivated forms of *Prunus
  domestica* (POWO sinks *P. insititia* and the mirabelle into it), so
  they publish with `švestka domácí`.
* `javor ginnala` is *Acer tataricum*: POWO treats *A. ginnala* as the
  subspecies *A. tataricum* subsp. *ginnala*, and `sanitize_species`
  truncates to species rank anyway.
* `růže Hugova` is *Rosa xanthina*, of which *R. hugonis* is a form.
* `mahonie` is *Berberis*, where POWO has sunk *Mahonia*.
* `mochnovec křovitý` is *Dasiphora fruticosa*, the accepted name for the
  shrubby cinquefoil once in *Potentilla*.
* `jedle stříbrná` is *Abies procera* (the noble fir, "silver fir" in the
  trade); the Czech `jedle bělokorá` is the European *A. alba*.
* `borovice blatka` is *Pinus uncinata*, into which POWO sinks *P.
  rotundata*; `borovice zobanitá` is the same species by its other name.
* `jeřáb oskeruše` is *Cormus domestica*, the service tree under the genus
  POWO now accepts for it (`SPECIES_SYNONYMS` folds *Sorbus domestica* the
  same way); `jeřáb muk`, `jeřáb ptačí` and the rest stay in *Sorbus*.

**What is deliberately left unresolved** is in `UNRESOLVED`: `03_neurčeno`
(a placeholder), `jeřábník` (names no genus in standard use), `cypřišek
nutkajský` (the Nootka cypress, whose genus POWO has moved three times;
four trees) and `pivoňka keřovitá` (one tree).  They fall through to
`None` and publish as `Unknown`, which is honest; `UNRESOLVED` exists so
that `tests/test_czech_species.py` can tell a decision from an omission.

That test pins the mechanical half -- every value is a name
`sanitize_species` keeps as written, every key is in normalised form, no
value is also a key, no value is a sentinel, and every one of the 332
published Czech strings is either in the table or in `UNRESOLVED`.  The
taxonomy is reviewed by reading the file, the same way `SPECIES_SYNONYMS`
and the other three tables are; `tools/species_audit.py` puts the values
to POWO before the city's first publish.
"""

from __future__ import annotations

import re
import unicodedata

from shared.ingest import form_sentinel_for, is_not_a_tree

# A cultivar quoted inside the name, in any of the quote marks the register
# uses.  Removed before keying: it is a selection within the taxon, not a
# different name for it.
_QUOTED = re.compile(r"[\"'‘’“”][^\"'‘’“”]*[\"'‘’“”]?")
# Anything that is not a lowercase ASCII letter or a digit, after the accents
# have been stripped: hyphens, commas, parentheses, underscores, stray spaces.
_NOT_A_WORD_CHAR = re.compile(r"[^a-z0-9]+")


def czech_name_key(value: str | None) -> str:
    """A published Czech name reduced to the form the table uses.

    The quoted cultivar is dropped, then NFKD-normalised with the combining
    marks removed (so `třešeň ptačí` and the register's own accent-less
    `trešen ptací` are one key), casefolded, with every run of punctuation
    or whitespace reduced to one space.  The ` - ` the register writes
    before `neurčená` becomes a plain word boundary.

    Returns `""` for a value with nothing left in it.

    Examples:
        "lípa srdčitá"                 -> "lipa srdcita"
        "lípa - neurčená"              -> "lipa neurcena"
        "borovice těžká (žlutá)"       -> "borovice tezka zluta"
        "třešeň křovitá 'Globosa'"     -> "tresen krovita"
        "hrušeň - neurčená "           -> "hrusen neurcena"
    """
    if value is None:
        return ""
    text = _QUOTED.sub(" ", str(value))
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _NOT_A_WORD_CHAR.sub(" ", text.casefold()).strip()


# Keys are in `czech_name_key` form; values are names `sanitize_species`
# keeps as written -- a binomial, a hybrid with the ASCII `x`, or a genus.
CZECH_SPECIES: dict[str, str] = {
    # --- Acer ---
    "javor mlec": "Acer platanoides",
    "javor mlec kultivary": "Acer platanoides",
    "javor mlec sanset": "Acer platanoides",
    "javor klen": "Acer pseudoplatanus",
    "javor babyka": "Acer campestre",
    "javor jasanolisty": "Acer negundo",
    "javor stribrny": "Acer saccharinum",
    "javor cerveny": "Acer rubrum",
    "javor ginnala": "Acer tataricum",
    "javor tatarsky": "Acer tataricum",
    "javor kalinolisty": "Acer opalus",
    "javor francouzsky": "Acer monspessulanum",
    "javor ridkokvety": "Acer pycnanthum",
    "javor zoeschensuv": "Acer x zoeschense",
    "javor krizenec": "Acer",
    "javor neurceny": "Acer",
    # --- Tilia ---
    "lipa srdcita": "Tilia cordata",
    "lipa velkolista": "Tilia platyphyllos",
    "lipa plstnata": "Tilia tomentosa",
    "lipa evropska": "Tilia x europaea",
    "lipa zelena": "Tilia x euchlora",
    "lipa americka": "Tilia americana",
    "lipa neurcena": "Tilia",
    # --- Robinia ---
    "trnovnik akat": "Robinia pseudoacacia",
    "trnovnik hunaty": "Robinia hispida",
    "trnovnik margaretta": "Robinia x margaretta",
    "trnovnik prazsky": "Robinia",
    "trnovnik honosny": "Robinia",
    "trnovnik neurceny": "Robinia",
    # --- Betula ---
    "briza bila": "Betula pendula",
    "briza papirova": "Betula papyrifera",
    "briza himalajska": "Betula utilis",
    "briza neurcena": "Betula",
    # --- Pinus ---
    "borovice cerna": "Pinus nigra",
    "borovice lesni": "Pinus sylvestris",
    "borovice vejmutovka": "Pinus strobus",
    "borovice tezka zluta": "Pinus ponderosa",
    "borovice klec": "Pinus mugo",
    "borovice zobanita": "Pinus uncinata",
    "borovice blatka": "Pinus uncinata",
    "borovice jeffreyova": "Pinus jeffreyi",
    "borovice pokroucena": "Pinus contorta",
    "borovice banksova": "Pinus banksiana",
    "borovice limba": "Pinus cembra",
    "borovice osinata": "Pinus aristata",
    "borovice belokora": "Pinus heldreichii",
    "borovice neurcena": "Pinus",
    # --- Fraxinus ---
    "jasan ztepily": "Fraxinus excelsior",
    "jasan pensylvansky": "Fraxinus pennsylvanica",
    "jasan uzkolisty": "Fraxinus angustifolia",
    "jasan zimnar": "Fraxinus ornus",
    "jasan americky": "Fraxinus americana",
    "jasan neurceny": "Fraxinus",
    # --- Picea ---
    "smrk pichlavy": "Picea pungens",
    "smrk stribrny": "Picea pungens",
    "smrk ztepily": "Picea abies",
    "smrk pancicuv": "Picea omorika",
    "smrk sivy": "Picea glauca",
    "smrk cerny severoamericky": "Picea mariana",
    "smrk vychodni": "Picea orientalis",
    "smrk litiangsky": "Picea likiangensis",
    "smrk breweruv": "Picea breweriana",
    "smrk neurceny": "Picea",
    # --- Prunus (třešeň, višeň, slivoň, střemcha, ...) ---
    "tresen ptaci": "Prunus avium",
    "tresen ptaci ovocna": "Prunus avium",
    "tresen sakura": "Prunus serrulata",
    "sakura ozdobna": "Prunus serrulata",
    "tresen mahalebka": "Prunus mahaleb",
    "tresen krovita": "Prunus fruticosa",
    "visen krovita": "Prunus fruticosa",
    "visen krovita roub na kminek": "Prunus fruticosa",
    "visen chloupkata": "Prunus x subhirtella",
    "visen sargentova": "Prunus sargentii",
    "visen obecna": "Prunus cerasus",
    "visen jedoska": "Prunus x yedoensis",
    "slivon myrobalan": "Prunus cerasifera",
    "slivon mirabelka": "Prunus domestica",
    "slivon sliva": "Prunus domestica",
    "svestka domaci": "Prunus domestica",
    "slivon trnka": "Prunus spinosa",
    "slivon tresen neurcena": "Prunus",
    "stremcha obecna": "Prunus padus",
    "stremcha pozdni": "Prunus serotina",
    "merunka obecna": "Prunus armeniaca",
    "broskvon": "Prunus persica",
    "mandlon nizka": "Prunus tenella",
    "bobkovisen lekarska": "Prunus laurocerasus",
    # --- Malus ---
    "jablon domaci": "Malus domestica",
    "jablon lesni": "Malus sylvestris",
    "jablon purpurova": "Malus x purpurea",
    "jablon mnohokveta": "Malus floribunda",
    "jablon neurcena": "Malus",
    # --- Aesculus ---
    "jirovec madal": "Aesculus hippocastanum",
    "jirovec cerveny": "Aesculus x carnea",
    "jirovec zluty": "Aesculus flava",
    "jirovec neurceny": "Aesculus",
    # --- Carpinus, Ostrya ---
    "habr obecny": "Carpinus betulus",
    "habrovec habrolisty": "Ostrya carpinifolia",
    # --- Styphnolobium ---
    "jerlin japonsky": "Styphnolobium japonicum",
    # --- Juglans, Carya ---
    "oresak kralovsky": "Juglans regia",
    "oresak cerny": "Juglans nigra",
    "oresak neurceny": "Juglans",
    "orechovec srdcovity": "Carya cordiformis",
    "orechovec pekanovy": "Carya illinoinensis",
    # --- Pyrus ---
    "hrusen obecna": "Pyrus communis",
    "hrusen calleryova": "Pyrus calleryana",
    "hrusen neurcena": "Pyrus",
    # --- Sorbus ---
    "jerab obecny": "Sorbus aucuparia",
    "jerab ptaci": "Sorbus aucuparia",
    "jerab muk": "Sorbus aria",
    "jerab prostredni": "Sorbus intermedia",
    "jerab durynsky": "Sorbus x thuringiaca",
    "jerab oskeruse": "Cormus domestica",
    "jerab sirokolisty": "Sorbus latifolia",
    "jerab brek": "Sorbus torminalis",
    "jerab": "Sorbus",
    "jerab neurceny": "Sorbus",
    # --- Platanus ---
    "platan spanelsky": "Platanus x hispanica",
    "platan javorolisty": "Platanus x hispanica",
    "platan zapadni": "Platanus occidentalis",
    "platan vychodni": "Platanus orientalis",
    "platan neurceny": "Platanus",
    # --- Larix ---
    "modrin opadavy": "Larix decidua",
    # --- Quercus ---
    "dub letni": "Quercus robur",
    "dub zimni": "Quercus petraea",
    "dub cerveny": "Quercus rubra",
    "dub sarlatovy": "Quercus coccinea",
    "dub slovensky": "Quercus frainetto",
    "dub bahenni": "Quercus palustris",
    "dub pyrity": "Quercus pubescens",
    "dub dvoubarevny": "Quercus bicolor",
    "dub balkansky": "Quercus trojana",
    "dub kastanolisty": "Quercus castaneifolia",
    "dub velkoplody": "Quercus macrocarpa",
    "dub texasky": "Quercus texana",
    "dub neurceny": "Quercus",
    # --- Corylus ---
    "liska turecka": "Corylus colurna",
    "liska obecna": "Corylus avellana",
    "liska nejvetsi": "Corylus maxima",
    "liska neurcena": "Corylus",
    # --- Pseudotsuga, Gleditsia ---
    "douglaska tisolista": "Pseudotsuga menziesii",
    "drezovec trojtrny": "Gleditsia triacanthos",
    # --- Salix ---
    "vrba jiva": "Salix caprea",
    "vrba bila": "Salix alba",
    "vrba pokroucena": "Salix matsudana",
    "vrba matsudova": "Salix matsudana",
    "vrba krehka": "Salix fragilis",
    "vrba babylonska": "Salix babylonica",
    "vrba neurcena": "Salix",
    # --- Populus ---
    "topol cerny": "Populus nigra",
    "topol vlassky": "Populus nigra",
    "topol kanadsky": "Populus x canadensis",
    "topol osika": "Populus tremula",
    "osika": "Populus tremula",
    "topol bily": "Populus alba",
    "topol simonuv": "Populus simonii",
    "topol balzamovy": "Populus balsamifera",
    "topol sedavy": "Populus x canescens",
    "topol chlupatoplody": "Populus trichocarpa",
    "topol neurceny": "Populus",
    # --- Thuja, Platycladus ---
    "zerav zapadni": "Thuja occidentalis",
    "zerav rasnaty": "Thuja plicata",
    "zerav vyhodni": "Platycladus orientalis",
    "zerav neurceny": "Thuja",
    # --- Crataegus ---
    "hloh jednosemenny": "Crataegus monogyna",
    "hloh obecny": "Crataegus laevigata",
    "hloh kuri noha": "Crataegus crus-galli",
    "hloh perenoklany": "Crataegus pinnatifida",
    "hloh lavalluv": "Crataegus x lavalleei",
    "hloh neurceny": "Crataegus",
    # --- Celtis ---
    "brestovec zapadni": "Celtis occidentalis",
    "brestovec jizni": "Celtis australis",
    "brestovec juliin": "Celtis julianae",
    "brestovec cinsky": "Celtis sinensis",
    "brestovec": "Celtis",
    # --- Spiraea ---
    "tavolnik van houteuv": "Spiraea x vanhouttei",
    "tavolnik vrbolisty": "Spiraea salicifolia",
    "tavolnik neurceny": "Spiraea",
    # --- Fagus ---
    "buk lesni": "Fagus sylvatica",
    "buk lesni kultivar": "Fagus sylvatica",
    # --- Symphoricarpos ---
    "pamelnik bily": "Symphoricarpos albus",
    "pamelnik neurceny": "Symphoricarpos",
    # --- Ginkgo ---
    "jinan dvoulalocny": "Ginkgo biloba",
    # --- Abies ---
    "jedle ojinena": "Abies concolor",
    "jedle belokora": "Abies alba",
    "jedle kavkazska": "Abies nordmanniana",
    "jedle korejska": "Abies koreana",
    "jedle stribrna": "Abies procera",
    "jedle obrovska": "Abies grandis",
    "jedle recka": "Abies cephalonica",
    "jedle neurcena": "Abies",
    # --- Ailanthus, Sambucus, Koelreuteria, Liquidambar ---
    "pajasan zlaznaty": "Ailanthus altissima",
    "bez cerny": "Sambucus nigra",
    "svitel latnaty": "Koelreuteria paniculata",
    "ambron zapadni": "Liquidambar styraciflua",
    # --- Catalpa ---
    "katalpa obecna": "Catalpa bignonioides",
    "katalpa neurcena": "Catalpa",
    # --- Rosa ---
    "ruze sipkova": "Rosa canina",
    "ruze hugova": "Rosa xanthina",
    "ruze neurcena sadova": "Rosa",
    # --- Ulmus ---
    "jilm vaz": "Ulmus laevis",
    "jilm habrolisty": "Ulmus minor",
    "jilm lysy horsky": "Ulmus glabra",
    "jilm holandsky": "Ulmus x hollandica",
    "jilm hybrid": "Ulmus",
    "jilm rebona": "Ulmus",
    "jilm": "Ulmus",
    "jilm neurceny": "Ulmus",
    # --- Syringa, Taxus, Juniperus ---
    "serik obecny": "Syringa vulgaris",
    "serik neurceny": "Syringa",
    "tis cerveny": "Taxus baccata",
    "tis neurceny": "Taxus",
    "jalovec obecny": "Juniperus communis",
    "jalovec virzinsky": "Juniperus virginiana",
    "jalovec cinsky": "Juniperus chinensis",
    "jalovec chvojka": "Juniperus sabina",
    "jalovec prostredni": "Juniperus x pfitzeriana",
    "jalovec neurceny": "Juniperus",
    # --- Ligustrum, Elaeagnus ---
    "ptaci zob obecny": "Ligustrum vulgare",
    "ptaci zob neurceny": "Ligustrum",
    "hlosina uzkolista": "Elaeagnus angustifolia",
    "hlosina neurcena": "Elaeagnus",
    # --- Chamaecyparis, Cupressus, Calocedrus, Metasequoia, Taxodium ---
    "cyprisek lawsonuv": "Chamaecyparis lawsoniana",
    "cyprisek hrachonosny": "Chamaecyparis pisifera",
    "cyprisek neurceny": "Chamaecyparis",
    "cypris stalezeleny": "Cupressus sempervirens",
    "pazerav sbihavy": "Calocedrus decurrens",
    "metasekvoje cinska": "Metasequoia glyptostroboides",
    "tisovec dvourady": "Taxodium distichum",
    # --- Morus, Cornus ---
    "moruse bila": "Morus alba",
    "drin obecny": "Cornus mas",
    "svida krvava": "Cornus sanguinea",
    "svida bila": "Cornus alba",
    "svida neurcena": "Cornus",
    # --- Rhus, Cotinus ---
    "skumpa ocetna": "Rhus typhina",
    "skumpa neurcena": "Rhus",
    "ruj vlasata": "Cotinus coggygria",
    # --- Laburnum, Cytisus, Caragana ---
    "stedrenec odvisly": "Laburnum anagyroides",
    "stedrenec neurceny": "Laburnum",
    "janovec metlaty": "Cytisus scoparius",
    "cilimnik neurceny": "Cytisus",
    "cimisnik obecny": "Caragana arborescens",
    # --- Lonicera, Viburnum ---
    "zimolez pyrity": "Lonicera xylosteum",
    "zimolez cerny": "Lonicera nigra",
    "zimolez neurceny": "Lonicera",
    "kalina obecna": "Viburnum opulus",
    "kalina tusalaj": "Viburnum lantana",
    "kalina neurcena": "Viburnum",
    # --- Alnus ---
    "olse lepkava": "Alnus glutinosa",
    "olse seda": "Alnus incana",
    "olse neurcena": "Alnus",
    # --- Paulownia, Castanea, Phellodendron, Parrotia, Cercis ---
    "pavlovnie plstnata": "Paulownia tomentosa",
    "kastanovnik sety": "Castanea sativa",
    "korkovnik amursky": "Phellodendron amurense",
    "parocie perska": "Parrotia persica",
    "zmarlika kanadska": "Cercis canadensis",
    # --- Forsythia ---
    "zlatice prostredni": "Forsythia x intermedia",
    "zlatice previsla": "Forsythia suspensa",
    "zlatice neurcena": "Forsythia",
    # --- Amelanchier, Pyracantha, Aronia, Cotoneaster, Mespilus, Cydonia ---
    "muchovnik stromovity": "Amelanchier arborea",
    "muchovnik lamarckuv": "Amelanchier x lamarckii",
    "muchovnik neurceny": "Amelanchier",
    "hlohyne sarlatova": "Pyracantha coccinea",
    "aronie temnoploda": "Aronia melanocarpa",
    "skalnik neurceny": "Cotoneaster",
    "mispule nemecka": "Mespilus germanica",
    "kdoulon obecna": "Cydonia oblonga",
    # --- Tamarix, Liriodendron, Zelkova, Hippophae, Magnolia ---
    "tamarisek drobnokvety": "Tamarix parviflora",
    "tamarisek neurceny": "Tamarix",
    "liliovnik tulipanokvety": "Liriodendron tulipifera",
    "lyrovnik tulipanokvety": "Liriodendron tulipifera",
    "zelkova habrolista": "Zelkova carpinifolia",
    "zelkova": "Zelkova",
    "rakytnik resetlakovy": "Hippophae rhamnoides",
    "sacholan kobus": "Magnolia kobus",
    "sacholan neurceny": "Magnolia",
    # --- Berberis (and Mahonia, sunk into it) ---
    "dristal obecny": "Berberis vulgaris",
    "dristal neurceny": "Berberis",
    "mahonie neurcena": "Berberis",
    # --- Philadelphus, Deutzia, Hibiscus, Buxus, Kerria ---
    "pustoryl vencovy": "Philadelphus coronarius",
    "pustoryl neurceny": "Philadelphus",
    "trojpuk neurceny": "Deutzia",
    "ibisek syrsky": "Hibiscus syriacus",
    "zimostraz vzdyzeleny": "Buxus sempervirens",
    "zakula japonska": "Kerria japonica",
    # --- Cedrus ---
    "cedr atlasky": "Cedrus atlantica",
    "cedr libanonsky": "Cedrus libani",
    "cedr himalajsky": "Cedrus deodara",
    # --- Physocarpus, Dasiphora ---
    "tavola kalinolista": "Physocarpus opulifolius",
    "mochnovec krovity": "Dasiphora fruticosa",
    # --- Ribes ---
    "meruzalka cerna": "Ribes nigrum",
    "meruzalka cervena": "Ribes rubrum",
    "meruzalka zlata": "Ribes aureum",
    "meruzalka alpska": "Ribes alpinum",
    "meruzalka krvava": "Ribes sanguineum",
    # --- Rhododendron, Tsuga ---
    "rhododendron neurceny": "Rhododendron",
    "jedlovec kanadsky": "Tsuga canadensis",
    # --- Ficus, Euonymus, Rhamnus, Jasminum, Parthenocissus, Rubus ---
    "fikovnik": "Ficus carica",
    "brslen neurceny": "Euonymus",
    "resetlak pocistivy": "Rhamnus cathartica",
    "jasmin nahokvety": "Jasminum nudiflorum",
    "prisavnik petilisty": "Parthenocissus quinquefolia",
    "ostruzinik neurceny": "Rubus",
    # --- Lycium, Citrus, Yucca, Clematis, Hypericum, Idesia, Broussonetia ---
    "kustovnice cizi": "Lycium barbarum",
    "citronovnik limonovy": "Citrus x limon",
    "juka vlaknita": "Yucca filamentosa",
    "plamenek vlassky": "Clematis viticella",
    "trezalka kaliskata": "Hypericum calycinum",
    "idezia mnohoploda": "Idesia polycarpa",
    "papirovnik cinsky": "Broussonetia papyrifera",
}

# Published strings the table decides *not* to resolve.  See the module
# docstring; each falls through to `None` and publishes as `Unknown`.
UNRESOLVED: frozenset[str] = frozenset(
    {
        "03 neurceno",
        "jerabnik neurceny",
        "cyprisek nutkajsky",
        "pivonka kerovita",
    }
)


def species_from_czech_name(value: str | None) -> str | None:
    """A published Czech tree name as a species value, or ``None``.

    The return value is what belongs in the ``species`` column, ready for
    `enforce_tree_schema`:

    * a name from `CZECH_SPECIES` when the value is one we resolve;
    * the value **unchanged** when the shared hygiene already understands
      it -- a growth form (`form_sentinel_for`) or an empty planting site
      (`is_not_a_tree`); no Prague value triggers either today, and the
      branch is here so that one would not need this module changed;
    * ``None`` otherwise, which publishes as ``Unknown``.

    An unresolved value is never passed through: a capitalised Czech word
    (`Jilm`, `Břestovec`) is indistinguishable from a genus to
    `sanitize_species` and would publish as an invented one.
    """
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    resolved = CZECH_SPECIES.get(czech_name_key(text))
    if resolved is not None:
        return resolved
    if is_not_a_tree(text) or form_sentinel_for(text) is not None:
        return text
    return None
