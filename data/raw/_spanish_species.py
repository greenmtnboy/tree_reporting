"""Resolve Bogotá's Spanish inventory name to an accepted binomial.

NOT a uv inline script -- a regular importable module, like `_ingest_shared`.

Bogotá's Jardín Botánico publishes its 1.39M-tree inventory with a Spanish
common name in `Nombre_Esp` -- `Sauco`, `Jazmin del cabo, laurel huesito`,
`Acacia negra, gris`, `Chicala, chirlobirlo, flor amarillo` -- and a numeric
species code, and no binomial anywhere in the layer: 503 distinct strings
(419 once accents, case and spacing are folded) over 1,390,646 trees.
`species` is the join key into the enrichment table, so without this the whole
city arrives labelled `Unknown`.

This is `_common_name_species` applied to a third language, and the shape is
deliberately the same as `_japanese_species` -- a curated dict, a key
normaliser, and a lookup that returns `None` rather than guessing.  It is a
separate module for the reason that one is: the keys normalise by their own
rules (accents are stripped here, the English table has none to strip and the
Japanese one folds scripts), and the *whole* published string is the key.

Usage:

    from _spanish_species import species_from_spanish_name

    species_from_spanish_name("Sauco")                      # 'Sambucus nigra'
    species_from_spanish_name("Ciprés, Pino ciprés, Pino")  # 'Cupressus lusitanica'
    species_from_spanish_name("Eucalipto")                  # 'Eucalyptus'  -- a genus is an answer
    species_from_spanish_name("Palmera")                    # 'Palmera'  -- a Palm sentinel
    species_from_spanish_name("NN")                         # None

**The whole string is the key, and that is a correctness decision.**  A
published value is one catalogue entry, which may list several vernacular
names for one taxon: `Acacia baracatinga, acacia sabanera, acacia nigra` is
one species (*Paraserianthes lophantha*).  Keying on the first name would
have collapsed distinct entries: `Cedro, cedro andino, cedro clavel` is
*Cedrela montana* and `Nogal, cedro nogal, cedro negro` is *Juglans
neotropica*; `Cerezo, capuli` is *Prunus serotina* and `Cerezo, ciruelo` is a
pair of *Prunus* species; `Pino colombiano, pino de pacho, pino romerón` is
*Retrophyllum rospigliosii* and `Pino colombiano, chaquiro` is *Podocarpus
oleifolius*.  The numbered entries (`Cajeto 1`, `Cajeto 2`, `Mano de oso 2`)
are the publisher distinguishing species it did not name, which is why the
key keeps digits and why those resolve to the genus.

**The publisher's own name platform drafted this table, and POWO adjudicated
it.**  The Jardín Botánico publishes a "Plataforma de nombres comunes de las
plantas de Bogotá" (2,747 vernacular-to-scientific pairings, each with its
references, released on Datos Abiertos Bogotá), and the pairings whose
reference is the garden's own arbolado-urbano documentation are the ones the
inventory uses.  That resolved most of the table outright.  Three more
sources settled the rest: DANE's 2005-2007 census species dictionary, which
publishes the *same* common-name strings verbatim beside 232 binomials
(`Abutilon megapotamicum` for the red-and-yellow farolito, `Agonis flexuosa`
for `Arbol pipermint`, `Trichipteris frigida` for `Helecho palma`,
`Brunfelsia pauciflora` for `Ayer, hoy y mañana`); the garden's heritage-tree
layer, the only layer that carries a binomial beside the species code; and
UNAL's national common-names database for the names none of those had
(`Crucito` is three *Palicourea*, `Lembo` is *Dendropanax*).

Every value was then put to POWO the way `species_audit.py` does it, reading
**every** exact match rather than the first, with the genera checked the same
way.  The 306 distinct values stand at **208 accepted, 19 ambiguous with an
accepted reading, 74 genera, and 5 deliberate synonyms** (four species, one
genus):

* **A homonym is not a problem**, and that is what the 19 are: `Sambucus
  nigra`, `Prunus serotina`, `Pittosporum undulatum`, `Escallonia paniculata`,
  each published twice by different authors with the readings disagreeing.  A
  name Kew has published twice is still a name.
* **A genuine synonym was replaced.**  `Abutilon megapotamicum` is
  `Callianthe megapotamica`; `Piper bogotense` is `Piper barbatum`;
  `Tibouchina lepidota` (the sietecueros real, 4,109 trees) is `Andesanthus
  lepidotus` and `Tibouchina grossa` is `Chaetogastra grossa`; `Palicourea
  lineariflora` is `Palicourea paniculata`; `Caesalpinia spinosa` is `Tara
  spinosa`; both `Morella` are `Myrica`; `Ilex kunthiana` is `Ilex
  microphylla`; `Tecoma capensis` is `Tecomaria capensis`; `Diplostephium
  rosmarinifolium` is `Linochilus rosmarinifolius`; `Jasminum humile` is
  `Chrysojasminum humile`; `Polygonum punctatum` is `Persicaria punctata`;
  `Rosmarinus officinalis` is `Salvia rosmarinus`; and the genera `Hebe`,
  `Leandra` and `Lavatera` are `Veronica`, `Miconia` and `Malva`.
* **Two spellings POWO does not publish** were re-asked: `Cordia
  cylindrostachya` (the salvio negro, which the garden also writes
  `Varronia cylindrostachya`) is `Varronia cylindristachya` there, with an i;
  and `Nectandra laurel` is a name `sanitize_species` refuses (the epithet is
  an English noun), so `Aguacatillo` resolves to the genus.

**The 5 that are still synonyms are one deliberate rule, and nothing else:**
where POWO and the published table disagreed, the published table won -- the
argument `SPECIES_SYNONYMS` and `_japanese_species` make, that the same taxon
under two names is two enrichment rows and two entries in every rollup.
`Cupressus lusitanica` (36,969 trees; POWO says *Hesperocyparis*, and the
repo already folds *Hesperocyparis* onto *Cupressus*), `Citrus x sinensis` (POWO folds it
into a form of *Citrus x aurantium*), `Dypsis lutescens` (*Chrysalidocarpus*)
and the genus `Callistemon` (*Melaleuca*).  `Hibiscus rosa-sinensis` is the
same call in a different shape: POWO's only record is the hybrid spelling
`Hibiscus x rosa-sinensis`, and the table carries the plain one.
`Acca sellowiana` was the opposite case -- POWO says *Feijoa sellowiana* and
Melbourne already publishes that spelling beside San Francisco's *Acca* -- so
it is a `SPECIES_SYNONYMS` entry and `feijoa` here resolves to *Feijoa*.

**A genus is an answer, and 6.1% of these trees get one** (85,009 trees on
107 keys).  Most are the publisher's own category names -- `Eucalipto`,
`Acacia`, `Pino`, `Cipres`, `Caucho`, `Tuno`, `Citrus spp.`, `Duranta sp` --
and the rest are names the publisher itself pairs with more than one species:
`Mano de oso` is *Oreopanax incisus* on the name platform, *O. floribundus*
in the 2005 census and *O. albanense* on the heritage layer, over five
species codes, so it is `Oreopanax`; `Sangregado` is three *Croton*;
`Carbonero` is four *Calliandra*; `Arrayan` without a colour is two *Myrcia*
on the garden's own list (the white and black arrayanes are *Myrcianthes*);
the abutilones are `Abutilon`; a `Sietecueros` without a qualifier is
`Tibouchina`.

**Judgement calls worth naming**, each a place where sources disagreed:

* `Urapán, Fresno` (39,038 trees) is *Fraxinus uhdei*.  The heritage layer
  says *F. chinensis*, a misidentification Colombian sources have carried for
  decades; the name platform and every recent treatment say *uhdei*, and
  POWO accepts both, so the platform decides.
* `Falso pimiento` (22,076) is *Schinus molle*: the heritage layer's own
  name, the census's, and a published key, where the platform's *Schinus
  areira* is none of those.
* `Jazmin de la china` (17,535) is *Ligustrum lucidum*, not a *Jasminum* --
  the platform is explicit, and `Aligustre del Japon` and `Ligustrum` sit
  beside it.
* `Sauce lloron` (11,921) is *Salix humboldtiana*, the native willow, which
  is what the garden calls its weeping sauce; the Old World *S. babylonica*
  is what the name means elsewhere.
* `Tinto` is *Cestrum buxifolium* and `Cucharo` is *Myrsine guianensis*
  although each appears under five or six species codes: the code carrying
  seven or sixteen thousand trees is the one the garden's fichas name, and
  the minor codes share the string, so they share the answer.
* `Caucho sabanero` (23,334) is *Ficus soatensis*, which POWO accepts and
  the heritage layer and census both write; the name platform's *F.
  americana* is the wider species some treatments sink it into.
* `Palma de yuca, Palma de bayoneta` (4,902) is *Yucca aloifolia*, the
  Spanish bayonet, which the 2005 census carried and nothing else in the
  catalogue could be; the heritage layer says *Cordyline australis* for the
  same code.  The string itself says yucca, so it publishes as one.
* `Garrocho` is *Viburnum tinoides*, the pairing the garden's own references
  make twice (platform and census), rather than *V. triphyllum*.
* `Pino hayuelo` is *Prumnopitys montana* by elimination: the platform pairs
  the name with both it and *Podocarpus oleifolius*, and *P. oleifolius* is
  already `Pino colombiano, chaquiro`.
* `Lavanda` (1,106) is the genus *Lavandula*; the platform also offers a
  *Brunfelsia* under that name, and the inventory carries *Brunfelsia* as
  `Ayer, hoy y mañana`.
* `Barbasco` (433) is *Persicaria punctata*, a wetland herb: it is what the
  garden's platform pairs the name with, and it is documented in Bogotá's
  humedales, where the inventory records it.
* `Helecho palma` (814) is *Cyathea frigida*, the census's *Trichipteris
  frigida* under its current name.

**What is deliberately left unresolved** is 48 keys and 7,293 trees, 0.52%,
listed in `UNRESOLVED` below with their counts.  4,625 of those are `NN` and
`Otro`, which say nothing.  The rest are names none of the four sources
pairs with a taxon that could grow at 2,600 m (`Arbol de Fuego`, `Palma
Botella`, `Gallinazo` name lowland trees elsewhere in Colombia), or pairs
with several genera at once (`Canelo`, `Salvio`, `Romerillo`, `Uña de gato`,
`Azuceno, enebro`).  They fall through to `None` and publish as `Unknown`,
which is honest; a name not in the table is `None` already, and `UNRESOLVED`
exists so that the test can tell a *decision* from an omission.

`tests/test_spanish_species.py` pins the mechanical half -- every value is a
name `sanitize_species` keeps as written, every key is in normalised form, no
value is also a key, no value is a sentinel, and every one of the 503
published strings is either in the table or in `UNRESOLVED`.  The taxonomy is
reviewed by reading the file, the same way `SPECIES_SYNONYMS` and
`COMMON_NAME_SPECIES` are.
"""

from __future__ import annotations

import re
import unicodedata

from _ingest_shared import form_sentinel_for, is_not_a_tree

# Anything that is not a lowercase ASCII letter or a digit, after accents have
# been stripped: commas, parentheses, hyphens, stray spaces.  Each run becomes
# one space, so the separators inside a multi-name entry are kept as word
# boundaries and nothing else about them matters.
_NOT_A_WORD_CHAR = re.compile(r"[^a-z0-9]+")


def spanish_name_key(value: str | None) -> str:
    """A published Spanish name reduced to the form the table uses.

    NFKD-normalised with the combining marks dropped (so `Ciprés` and
    `Cipres` are one key, `Jazmín` and `Jazmin` are one key, and `ñ` folds
    to `n`), casefolded, with every run of punctuation or whitespace reduced
    to a single space.  Digits are kept: `Cajeto 1` and `Cajeto 2` are two
    published entries.  The whole string is keyed -- see the module docstring
    for why the first name alone would be wrong.

    Returns `""` for a value with nothing left in it.

    Examples:
        "Sauco"                                -> "sauco"
        "Ciprés, Pino ciprés, Pino"            -> "cipres pino cipres pino"
        "Abutilon rojo y amarillo (Farolito)"  -> "abutilon rojo y amarillo farolito"
        "Abutilon  pequeño"                    -> "abutilon pequeno"
        "Fucsia- naranja"                      -> "fucsia naranja"
        "Yarumo  "                             -> "yarumo"
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _NOT_A_WORD_CHAR.sub(" ", text.casefold())
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------
#
# Keys are `spanish_name_key` output -- the whole published string; values are
# names `sanitize_species` keeps as written: accepted at POWO (or accepted
# under one of its readings), ASCII hybrid mark, genus capitalised, no rank
# below species.  Sorted by value, because that is how it was checked against
# POWO.  A trailing `# comment` is the value as the portal publishes it, shown
# where keying changed more than the case.

SPANISH_SPECIES: dict[str, str] = {
    "duraznillo velitas": "Abatia parviflora",  # Duraznillo, velitas
    "palo blanco": "Abatia parviflora",
    "abelia": "Abelia x grandiflora",
    "abutilon blanco": "Abutilon",
    "abutilon pequeno": "Abutilon",  # Abutilon  pequeño
    "abutilon quesito": "Abutilon",
    "acacia": "Acacia",
    "acacia azul": "Acacia",
    "acacia de jardin": "Acacia",
    "acacia morada": "Acacia baileyana",
    "acacia blanca cultriformes": "Acacia cultriformis",  # Acacia blanca, Cultriformes
    "acacia negra gris": "Acacia decurrens",  # Acacia negra, gris
    "acacia japonesa": "Acacia melanoxylon",
    "feijoa": "Feijoa sellowiana",
    "arce": "Acer",
    "amargoso": "Ageratina",
    "arbol pipermint": "Agonis flexuosa",
    "aliso fresno chaquiro": "Alnus acuminata",  # Aliso, fresno, chaquiro
    "aloe arboreo": "Aloe arborescens",
    "cidron": "Aloysia citrodora",
    "sietecueros real": "Andesanthus lepidotus",
    "anona": "Annona",
    "chirimoyo": "Annona cherimola",
    "anon": "Annona squamosa",
    "araucaria crespa": "Araucaria araucana",
    "araucaria": "Araucaria heterophylla",
    "palma alejandra": "Archontophoenix alexandrae",
    "palma payanesa": "Archontophoenix cunninghamiana",
    "ardicia": "Ardisia",
    "bencenuco": "Asclepias curassavica",
    "tuno roso": "Axinaea macrophylla",
    "arbol de neem": "Azadirachta indica",
    "azara": "Azara",
    "chilco de paramo": "Baccharis",  # Chilco de páramo
    "ciro": "Baccharis bogotensis",
    "chilco": "Baccharis latifolia",
    "baeckea": "Baeckea",
    "espino barnadesia": "Barnadesia spinosa",
    "pegamosco": "Bejaria",
    "berberis": "Berberis",
    "tachuelo": "Berberis rigidifolia",
    "cariseco": "Billia rosea",
    "cariseco tres hojas": "Billia rosea",  # Cariseco, Tres hojas
    "trompeto": "Bocconia frutescens",
    "borrachero": "Brugmansia",
    "borrachero rojo": "Brugmansia sanguinea",
    "borrachero blanco": "Brugmansia x candida",
    "brunelia": "Brunellia",
    "ayer hoy y manana": "Brunfelsia pauciflora",  # Ayer, hoy y mañana
    "charne": "Bucquetia glutinosa",  # Charné
    "salton o charne": "Bucquetia glutinosa",
    "salvio morado": "Buddleja davidii",
    "boj": "Buxus sempervirens",
    "carbonero": "Calliandra",
    "carbonero rosado": "Calliandra pittieri",
    "carbonero rojo": "Calliandra trinervia",
    "abutilon rojo y amarillo farolito": "Callianthe megapotamica",  # Abutilon rojo y amarillo (Farolito)
    "calistemo": "Callistemon",
    "callistemo": "Callistemon",
    "guayabillo": "Calycolpus moritzianus",
    "camelia": "Camellia japonica",
    "guayabo anselmo champo": "Campomanesia",  # Guayabo anselmo, Champo
    "pino australiano": "Casuarina equisetifolia",
    "uva de anis": "Cavendishia bracteata",
    "yarumo": "Cecropia telenitida",
    "cedro cedro andino cedro clavel": "Cedrela montana",  # Cedro, cedro andino, cedro clavel
    "ceiba": "Ceiba",
    "palma de cera": "Ceroxylon quindiuense",
    "palma de cera palma blanca": "Ceroxylon quindiuense",  # Palma de cera, Palma blanca
    "palma de cera palma de ramo": "Ceroxylon quindiuense",  # Palma de cera, Palma de ramo
    "palma de ramo": "Ceroxylon quindiuense",
    "cestrum": "Cestrum",
    "tinto": "Cestrum buxifolium",
    "caballero de la noche": "Cestrum nocturnum",
    "caballero de la noche jazmin dama de noche": "Cestrum nocturnum",  # Caballero de la noche, Jazmin, Dama de noche
    "sandalo": "Cestrum nocturnum",
    "flor morado": "Chaetogastra grossa",
    "cipres enano": "Chamaecyparis lawsoniana",  # Ciprés enano
    "arupo": "Chionanthus pubescens",
    "chromolaena bullata": "Chromolaena bullata",
    "jazmin amarillo": "Chrysojasminum humile",
    "quina": "Cinchona",
    "cajeto 1": "Citharexylum",
    "cajeto 2": "Citharexylum",
    "cajeto de bogota": "Citharexylum",
    "cajeto sp": "Citharexylum",
    "cajeto": "Citharexylum subflavescens",
    "cajeto garagay urapo": "Citharexylum subflavescens",  # Cajeto, garagay, urapo
    "citrus spp": "Citrus",  # Citrus spp.
    "mandarina": "Citrus reticulata",
    "limon": "Citrus x limon",
    "naranjo": "Citrus x sinensis",
    "dulomoco": "Clethra",
    "manzano de monte": "Clethra",
    "gaquillo": "Clusia",
    "gaque": "Clusia multiflora",
    "cafe": "Coffea arabica",
    "diosme": "Coleonema album",
    "eucalipto pomarroso": "Corymbia ficifolia",
    "eucalipto manchado": "Corymbia maculata",
    "holly liso": "Cotoneaster pannosus",
    "amarguero amarillo": "Critoniopsis bogotana",
    "pajarito": "Crotalaria agatiflora",
    "sangregado": "Croton",
    "sangregao drago croto": "Croton",  # Sangregao, drago, croto
    "cipres japones criptomeria": "Cryptomeria japonica",  # Cipres Japones, criptomeria
    "cigarrillo": "Cuphea",
    "cipres": "Cupressus",
    "cipres pino cipres pino": "Cupressus lusitanica",  # Ciprés, Pino ciprés, Pino
    "cipres italiano": "Cupressus sempervirens",
    "helecho arborecente": "Cyathea",
    "helecho palma": "Cyathea frigida",
    "naranjillo": "Cybianthus iteoides",
    "palma funeral": "Cycas revoluta",
    "dalia": "Dahlia imperialis",
    "chiripique": "Dalea coerulea",
    "mote": "Daphnopsis caracasana",
    "chicala rosado": "Delostoma integrifolium",
    "curapin campanilla": "Delostoma integrifolium",  # Curapin, Campanilla
    "nacedero": "Delostoma integrifolium",
    "lembo pategallo": "Dendropanax arboreus",  # Lembo, pategallo
    "hayuelo": "Dodonaea viscosa",
    "duranta sp": "Duranta",
    "duranta amarilla": "Duranta erecta",
    "espino garbancillo": "Duranta mutisii",  # Espino, Garbancillo
    "garbancillo": "Duranta mutisii",
    "palma areca": "Dypsis lutescens",
    "platano de tierra fria": "Ensete ventricosum",
    "nispero": "Eriobotrya japonica",
    "chocho balu cambulo": "Erythrina",  # Chocho, balu, cambulo
    "chocho": "Erythrina rubrinervia",
    "tibar del jardin": "Escallonia",
    "tibar extranjero": "Escallonia",
    "tibar": "Escallonia paniculata",
    "tibar pagoda o rodamonte": "Escallonia paniculata",  # Tibar, pagoda o rodamonte
    "tibar rodamonte pagoda": "Escallonia paniculata",  # Tibar, Rodamonte, Pagoda
    "tibar tobo rodamonte": "Escallonia paniculata",  # Tibar, tobo, rodamonte
    "mangle de tierra fria": "Escallonia pendula",
    "eucalipto": "Eucalyptus",
    "eucalipto blanco": "Eucalyptus",
    "eucalipto plateado": "Eucalyptus cinerea",
    "eucalipto comun": "Eucalyptus globulus",  # Eucalipto común
    "guayabo brasilero": "Eugenia brasiliensis",
    "bonetero del japon": "Euonymus japonicus",
    "lechero": "Euphorbia",
    "liberal o lechero": "Euphorbia cotinifolia",
    "sombrilla japonesa": "Euphorbia pulcherrima",
    "aralia japonesa": "Fatsia japonica",
    "caucho": "Ficus",
    "higueron": "Ficus",
    "caucho benjamin": "Ficus benjamina",
    "brevo": "Ficus carica",
    "caucho de la india caucho": "Ficus elastica",  # Caucho de la india, caucho
    "caucho lira": "Ficus lyrata",
    "caucho sabanero": "Ficus soatensis",
    "caucho tequendama": "Ficus tequendamae",
    "ojo de perdiz": "Frangula goudotiana",
    "urapan fresno": "Fraxinus uhdei",  # Urapán, Fresno
    "fucsia": "Fuchsia",
    "fucsia arbustiva": "Fuchsia",
    "fucsia naranja": "Fuchsia",  # Fucsia- naranja
    "fuscia arborea": "Fuchsia",  # Fuscia arbórea
    "fucsia boliviana": "Fuchsia boliviana",
    "fique": "Furcraea",
    "tagua": "Gaiadendron punctatum",
    "mangostino": "Garcinia mangostana",
    "gardenia": "Gardenia jasminoides",
    "palma geonoma": "Geonoma",
    "grevilea": "Grevillea",
    "roble australiano": "Grevillea robusta",
    "granizo": "Hedyosmum",
    "balso blanco": "Heliocarpus americanus",
    "mortino ferrugineo": "Hesperomeles ferruginea",  # Mortiño ferrugineo
    "mortillo": "Hesperomeles goudotiana",
    "mortino": "Hesperomeles goudotiana",  # Mortiño
    "cayeno": "Hibiscus rosa-sinensis",
    "chaguaca": "Hieronyma",
    "motilon": "Hieronyma macrocarpa",  # Motilón
    "motilon chuguaca": "Hieronyma macrocarpa",  # Motilon, chuguaca
    "palma kenia": "Howea forsteriana",
    "hiperico corazoncillo": "Hypericum patulum",  # Hiperico, Corazoncillo
    "acebo": "Ilex aquifolium",
    "mulato": "Ilex microphylla",
    "anil": "Indigofera",  # Añil
    "guamo": "Inga",
    "guamo santafereno": "Inga",  # Guamo santafereño
    "corazon de pollo": "Iochroma gesnerioides",
    "gualanday": "Jacaranda mimosifolia",
    "nogal cedro nogal cedro negro": "Juglans neotropica",  # Nogal, cedro nogal, cedro negro
    "guayacan amarillo": "Lafoensia acuminata",  # Guayacán amarillo
    "guayacan de manizales": "Lafoensia acuminata",
    "lantana boyacana": "Lantana boyacana",
    "venturosa": "Lantana camara",
    "laurel europeo": "Laurus nobilis",
    "lavanda": "Lavandula",
    "milflores": "Ledenbergia seguierioides",
    "leptospermun": "Leptospermum",
    "acacia blanca leucaena": "Leucaena leucocephala",  # Acacia blanca, leucaena
    "aligustrina": "Ligustrum",
    "ligustrum": "Ligustrum",
    "aligustre del japon": "Ligustrum japonicum",
    "jazmin de la china": "Ligustrum lucidum",
    "romero de paramo": "Linochilus rosmarinifolius",
    "liquidambar estoraque": "Liquidambar styraciflua",  # Liquidambar, estoraque
    "lupinus": "Lupinus",
    "gurrubo": "Lycianthes lycioides",
    "uva camarona": "Macleania rupestris",
    "almanegra quedo": "Magnolia",  # Almanegra, quedo
    "magnolia rosada": "Magnolia",
    "hojarasco": "Magnolia caricifragrans",
    "magnolio": "Magnolia grandiflora",
    "manzano": "Malus domestica",
    "lavatera malvavisco morado": "Malva",  # Lavatera, Malvavisco morado
    "malvavisco": "Malvaviscus arboreus",
    "mamey": "Mammea americana",
    "mango": "Mangifera indica",
    "eucalipto de flor eucalipto lavabotella": "Melaleuca citrina",  # Eucalipto de flor, eucalipto lavabotella
    "calistemo lloron": "Melaleuca viminalis",
    "amarrabollo longifolia": "Meriania",
    "amarrabollo": "Meriania nobilis",
    "metrosideros": "Metrosideros",
    "arbol de hierro": "Metrosideros excelsa",
    "leandra": "Miconia",
    "niguito": "Miconia",
    "tuno": "Miconia",
    "conejo": "Miconia salicifolia",
    "tuno esmeraldo": "Miconia squamulosa",
    "angelito": "Monochaetum myrtoideum",
    "balazo": "Monstera deliciosa",
    "morera": "Morus alba",
    "platano": "Musa x paradisiaca",
    "arrayan": "Myrcia",
    "endrino": "Myrcia popayanensis",
    "arrayan blanco": "Myrcianthes leucoxyla",
    "arrayan negro": "Myrcianthes rhopaloides",
    "laurel de cera hoja pequena": "Myrica parvifolia",  # Laurel de cera (hoja pequeña)
    "laurel de cera": "Myrica pubescens",
    "cucharo de paramo": "Myrsine",
    "cucharo huesito": "Myrsine",
    "escolin espadero": "Myrsine coriacea",  # Escolin, Espadero
    "espadero": "Myrsine coriacea",
    "cucharo": "Myrsine guianensis",
    "aguacatillo": "Nectandra",
    "olivo": "Olea europaea",
    "tuna de la sabana": "Opuntia",
    "mano de oso": "Oreopanax",
    "mano de oso 2": "Oreopanax",
    "cafetillo crucito": "Palicourea",  # Cafetillo, crucito
    "crucito": "Palicourea",
    "tominejero": "Palicourea paniculata",
    "yolombo": "Panopsis suaveolens",
    "palma coquito": "Parajubaea cocoides",
    "acacia baracatinga acacia sabanera acacia nigra": "Paraserianthes lophantha",  # Acacia baracatinga, acacia sabanera, acacia nigra
    "paulonia": "Paulownia",
    "aguacate": "Persea americana",
    "barbasco": "Persicaria punctata",
    "fenix": "Phoenix",
    "palma fenix": "Phoenix canariensis",
    "palma de datiles": "Phoenix dactylifera",
    "palma senegal": "Phoenix reclinata",
    "palma roebeleni": "Phoenix roebelenii",
    "fotinia": "Photinia x fraseri",
    "cedrillo": "Phyllanthus salviifolius",
    "cedrillo yuco": "Phyllanthus salviifolius",  # Cedrillo, Yuco
    "ombu arbol de la bella sombra": "Phytolacca dioica",  # Ombu, Arbol de la bella sombra
    "pino": "Pinus",
    "pino montezuma": "Pinus montezumae",
    "pino patula": "Pinus patula",  # Pino pátula
    "pino candelabro": "Pinus radiata",
    "cordoncillo": "Piper barbatum",
    "pitosporo": "Pittosporum tobira",
    "blanquillo": "Pittosporum undulatum",
    "jazmin australiano": "Pittosporum undulatum",
    "jazmin del cabo laurel huesito": "Pittosporum undulatum",  # Jazmin del cabo, laurel huesito
    "arbol de platano": "Platanus occidentalis",  # Árbol de platano
    "pino libro": "Platycladus orientalis",
    "sietecueros nazareno": "Pleroma urvilleanum",
    "jazmin azul": "Plumbago auriculata",
    "pino colombiano chaquiro": "Podocarpus oleifolius",  # Pino colombiano, chaquiro
    "poligala": "Polygala x dalmaisiana",
    "alamo de lombardia": "Populus",
    "azuceno de monte": "Posoqueria latifolia",
    "guayabo de mico": "Posoqueria latifolia",
    "palma prestoea": "Prestoea",
    "pino hayuelo": "Prumnopitys montana",
    "cerezo ciruelo": "Prunus",  # Cerezo, ciruelo
    "cerezo uche": "Prunus",  # Cerezó uche
    "ciruelo": "Prunus domestica",
    "durazno comun": "Prunus persica",
    "cerezo": "Prunus serotina",
    "cerezo capuli": "Prunus serotina",  # Cerezo, capuli
    "guayabo del peru": "Psidium",
    "guayabo": "Psidium guajava",
    "pino azul": "Psoralea pinnata",
    "granado": "Punica granatum",
    "holly espinoso": "Pyracantha angustifolia",
    "pero": "Pyrus communis",
    "roble": "Quercus humboldtii",
    "pino colombiano pino de pacho pino romeron": "Retrophyllum rospigliosii",  # Pino colombiano, pino de pacho, pino romerón
    "pino romeron": "Retrophyllum rospigliosii",
    "raphiolepys": "Rhaphiolepis",
    "azalea": "Rhododendron",
    "higuerillo": "Ricinus communis",
    "rosa": "Rosa",
    "sauce lloron": "Salix humboldtiana",
    "mimbre": "Salix viminalis",
    "salvia": "Salvia",
    "salvia tortuosa": "Salvia",
    "romero": "Salvia rosmarinus",
    "oreja de burro": "Salvia scutellarioides",
    "sauco": "Sambucus nigra",
    "moquillo": "Saurauia scabra",
    "schefflera": "Schefflera",
    "schefflera pategallina hojigrande": "Schefflera",  # Schefflera, Pategallina hojigrande
    "schefflera pategallina hojipequena": "Schefflera",  # Schefflera, Pategallina hojipequeña
    "schefflera pategallina peludo": "Schefflera",
    "schefflera tortolito": "Schefflera",  # Schefflera, Tortolito
    "schefflera yuco blanco": "Schefflera",  # Schefflera, Yuco blanco
    "pimiento": "Schinus",
    "falso pimiento": "Schinus molle",
    "pimiento negro": "Schinus terebinthifolia",
    "rama negra": "Senna corymbosa",
    "alcaparro enano": "Senna multiglandulosa",
    "pichuelo": "Senna pistaciifolia",
    "alcaparro doble": "Senna viarum",
    "secuoya": "Sequoia sempervirens",
    "arboloco": "Smallanthus pyramidalis",
    "jomi upacon": "Smallanthus pyramidalis",  # Jomi, upacon
    "cucubo": "Solanum asperolanatum",
    "tomate de arbol": "Solanum betaceum",
    "manto de maria": "Solanum laxum",
    "lulo de perro": "Solanum marginatum",
    "tomatillo": "Solanum ovalifolium",
    "mirto": "Solanum pseudocapsicum",
    "algodon extranjero": "Sparrmannia africana",
    "algodoncillo": "Sparrmannia africana",
    "tulipan africano": "Spathodea campanulata",
    "jarilla": "Stevia lucida",
    "mermelada": "Streptosolen jamesonii",
    "palma sancona": "Syagrus sancona",
    "te de bogota": "Symplocos theiformis",  # Té de Bogotá
    "pomarroso": "Syzygium jambos",
    "arrayan extranjero": "Syzygium paniculatum",
    "eugenia": "Syzygium paniculatum",
    "ocobo guayacan": "Tabebuia rosea",  # Ocobo, Guayacan
    "dividivi de tierra fria": "Tara spinosa",
    "chicala chirlobirlo flor amarillo": "Tecoma stans",  # Chicala, chirlobirlo, flor amarillo
    "tecomaria": "Tecomaria capensis",
    "tefrosia purpurea": "Tephrosia purpurea",
    "siete cueros": "Tibouchina",
    "siete cueros peludo": "Tibouchina",
    "sietecueros plateado": "Tibouchina",
    "retamo": "Ulex europaeus",
    "agracejo": "Vaccinium",
    "aromo": "Vachellia farnesiana",
    "raque san juanito": "Vallea stipularis",  # Raque, San juanito
    "guacimo": "Varronia cylindristachya",
    "salvio negro": "Varronia cylindristachya",
    "papayuela": "Vasconcellea pubescens",
    "papayuelo": "Vasconcellea pubescens",
    "tabaquillo": "Verbesina crassiramea",
    "hebe": "Veronica",
    "garrocho sp": "Viburnum",
    "garrocho": "Viburnum tinoides",
    "palma washingtoniana": "Washingtonia",
    "encenillo": "Weinmannia tomentosa",
    "corono": "Xylosma spiculifera",
    "palma de yuca palma de bayoneta": "Yucca aloifolia",  # Palma de yuca, Palma de bayoneta
    "palma yuca palmiche": "Yucca gigantea",  # Palma yuca, palmiche
    "palmiche": "Yucca gigantea",
    "yuca palma yuca": "Yucca gigantea",  # Yuca, palma yuca
}


# Published strings this table deliberately does not resolve, as keys, with the
# raw string and the tree count when the table was written.  A key here
# publishes as `Unknown`.  The test walks every published string and requires
# it to be in one of the two sets, so a name that is *missing* is a red test
# and a name that is here is a decision.
UNRESOLVED: frozenset[str] = frozenset({
    "nn",  # NN (3994)
    "otro",  # Otro (631)
    "arbol de te",  # Arbol de Te (542)
    "arbol de corcho",  # Arbol de corcho (263)
    "arbol de fuego",  # Arbol de Fuego (257)
    "palma cinta",  # Palma cinta (256)
    "pepero",  # Pepero (190)
    "cocobo azul",  # Cocobo azul (146)
    "palma botella",  # Palma Botella (108)
    "azuceno enebro",  # Azuceno, enebro (100)
    "arrocero",  # Arrocero (98)
    "gallinazo",  # Gallinazo (79)
    "ceiba de tierra fria",  # Ceiba de tierra fria (72)
    "margariton",  # Margariton (65)
    "aranita",  # Arañita (56)
    "borracherito",  # Borracherito (52)
    "romerillo",  # Romerillo (46)
    "salvio",  # Salvio (45)
    "garrapato",  # Garrapato (34)
    "totumito",  # Totumito (29)
    "olmo de agua",  # Olmo de agua (25)
    "canelo",  # Canelo (24)
    "pepas del diablo",  # Pepas del diablo (24)
    "moradilla",  # Moradilla (18)
    "una de gato 1",  # Uña de gato 1 (18)
    "coloradito",  # Coloradito (16)
    "angelita",  # Angelita (15)
    "espino blanco",  # Espino blanco (8)
    "una de gato 2",  # Uña de gato 2 (8)
    "guarana guacharo",  # Guarana, guacharo (7)
    "guayabo de pava",  # Guayabo de pava (7)
    "laurel",  # Laurel (7)
    "trompo",  # Trompo (7)
    "chirriador",  # Chirriador (6)
    "camaron",  # Camaron (5)
    "grosella",  # Grosella (5)
    "punta de lanza",  # Punta de lanza (5)
    "amarillo",  # Amarillo (3)
    "crucero",  # Crucero (3)
    "roblehaya",  # Roblehaya (3)
    "sonajero",  # Sonajero (3)
    "tecuito",  # Tecuito (3)
    "totumillo",  # Totumillo (3)
    "algarrobo",  # Algarrobo (2)
    "susque",  # Susque (2)
    "chilco negro",  # Chilco negro (1)
    "copa de oro",  # Copa de oro (1)
    "una de gato",  # Uña de gato (1)
})


def species_from_spanish_name(value: str | None) -> str | None:
    """A published Spanish tree name as a species value, or ``None``.

    The return value is what belongs in the ``species`` column, ready for
    `enforce_tree_schema`:

    * a name from `SPANISH_SPECIES` when the value is one we resolve;
    * the value **unchanged** when the shared hygiene already understands it --
      a growth form (`form_sentinel_for`, which is what keeps `palmera` as
      `Palm` and `arbusto` as `Shrub` rather than losing both to `Unknown`) or
      an empty planting site (`is_not_a_tree`, which makes
      `enforce_tree_schema` drop the row; no Bogotá value triggers either
      today, and the branch is here so that one would not need this module
      changed);
    * ``None`` otherwise, which publishes as ``Unknown``.

    Passing an *unresolved* value through is refused, as it is in the other
    two tables, and Spanish makes the case for it sharper than Japanese did:
    a single capitalised Spanish word is indistinguishable from a genus to
    `sanitize_species` -- `Sauco`, `Corono`, `Tinto` would each publish as an
    invented genus and join to an enrichment row for a plant that does not
    exist.  Unresolved is `Unknown`, and `Unknown` is honest.
    """
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    resolved = SPANISH_SPECIES.get(spanish_name_key(text))
    if resolved is not None:
        return resolved
    if is_not_a_tree(text) or form_sentinel_for(text) is not None:
        return text
    return None
