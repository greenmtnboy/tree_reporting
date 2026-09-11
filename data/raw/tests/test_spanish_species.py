"""`_spanish_species`, the Bogotá vernacular name -> binomial table.

The table is curated by hand, so what a test can add is the mechanical half:
that every value is a name the ingest would keep as written, that every key is
in normalised form (a key that is not can never be looked up), that the
normaliser folds the accents, case, punctuation and spacing Bogotá's inventory
actually publishes -- and that every one of the 503 strings the inventory
published when the table was written is either resolved or *deliberately*
unresolved, so that a name the table forgot is a red test rather than a
quiet `Unknown`.

The taxonomy itself is not testable here and is not meant to be -- it is
reviewed by reading the file, the same way `COMMON_NAME_SPECIES`,
`JAPANESE_SPECIES` and `SPECIES_SYNONYMS` are.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from _ingest_shared import (  # noqa: E402
    SPECIES_SENTINELS,
    sanitize_species,
)
from _spanish_species import (  # noqa: E402
    SPANISH_SPECIES,
    UNRESOLVED,
    spanish_name_key,
    species_from_spanish_name,
)

# Every distinct `Nombre_Esp` the Jardín Botánico's inventory published in
# September 2026 -- 503 strings, verbatim, including the trailing spaces and
# the double space the portal ships.  Regenerate from the layer when the
# catalogue changes; a new entry is a red test until the table decides it.
BOGOTA_PUBLISHED_NAMES: tuple[str, ...] = (
    'Sauco',
    'Jazmin del cabo, laurel huesito',
    'Acacia negra, gris',
    'Chicala, chirlobirlo, flor amarillo',
    'Urapán, Fresno',
    'Acacia japonesa',
    'Eugenia',
    'Holly liso',
    'Ciprés, Pino ciprés, Pino',
    'Guayacan de Manizales',
    'Eucalipto común',
    'Hayuelo',
    'Acacia baracatinga, acacia sabanera, acacia nigra',
    'Caucho sabanero',
    'Palma yuca, palmiche',
    'Arrayan blanco',
    'Falso pimiento',
    'Cayeno',
    'Aliso, fresno, chaquiro',
    'Corono',
    'Jazmin de la china',
    'Caucho benjamin',
    'Roble',
    'Cucharo',
    'Caballero de la noche, Jazmin, Dama de noche',
    'Cerezo',
    'Cerezo, capuli',
    'Higuerillo',
    'Liquidambar, estoraque',
    'Tomatillo',
    'Sauce lloron',
    'Tuno esmeraldo',
    'Araucaria',
    'Abutilon rojo y amarillo (Farolito)',
    'Nogal, cedro nogal, cedro negro',
    'Cajeto, garagay, urapo',
    'Pino pátula',
    'Acacia morada',
    'Arboloco',
    'Cordoncillo',
    'Cedro, cedro andino, cedro clavel',
    'Eucalipto de flor, eucalipto lavabotella',
    'Pino colombiano, pino de pacho, pino romerón',
    'Fucsia boliviana',
    'Tinto',
    'Calistemo lloron',
    'Salvio negro',
    'Alcaparro enano',
    'Pino libro',
    'Mano de oso',
    'Cucubo',
    'Sangregao, drago, croto',
    'Alcaparro doble',
    'Durazno comun',
    'Ligustrum',
    'Mangle de tierra fria',
    'Chilco',
    'Duraznillo, velitas',
    'Mortillo',
    'Sietecueros nazareno',
    'Tuno roso',
    'Espino, Garbancillo',
    'Eucalipto pomarroso',
    'Palma de cera, Palma blanca',
    'Borrachero blanco',
    'Palma de yuca, Palma de bayoneta',
    'Schefflera, Pategallina hojipequeña',
    'Garrocho',
    'Holly espinoso',
    'Raque, San juanito',
    'Fucsia arbustiva',
    'Sietecueros real',
    'Caucho tequendama',
    'NN',
    'Magnolio',
    'Caballero de la noche',
    'Cajeto',
    'Pino candelabro',
    'Eucalipto',
    'Schefflera, Pategallina hojigrande',
    'Chicala rosado',
    'Pino romeron',
    'Abutilon blanco',
    'Feijoa',
    'Poligala',
    'Naranjo',
    'Milflores',
    'Palma payanesa',
    'Palma fenix',
    'Caucho',
    'Brevo',
    'Caucho de la india, caucho',
    'Pajarito',
    'Tominejero',
    'Carbonero rojo',
    'Aguacate',
    'Chiripique',
    'Ciro',
    'Chilco',
    'Papayuelo',
    'Dividivi de tierra fria',
    'Tibar, pagoda o rodamonte',
    'Laurel de cera',
    'Gaque',
    'Roble australiano',
    'Palma Alejandra',
    'Tabaquillo',
    'Tibar',
    'Cipres italiano',
    'Tuno roso',
    'Mano de oso',
    'Laurel de cera (hoja pequeña)',
    'Mermelada',
    'Acacia',
    'Arrayan negro',
    'Uva camarona',
    'Holly liso',
    'Cedrillo, Yuco',
    'Sombrilla japonesa',
    'Chocho',
    'Arrayan',
    'Araucaria crespa',
    'Rama negra',
    'Yarumo',
    'Eucalipto plateado',
    'Crucito',
    'Limon',
    'Cariseco',
    'Amarguero amarillo',
    'Curapin, Campanilla',
    'Amarrabollo',
    'Callistemo',
    'Garbancillo',
    'Mandarina',
    'Aligustre del Japon',
    'Pino colombiano, chaquiro',
    'Calistemo',
    'Garrocho',
    'Angelito',
    'Acacia de jardin',
    'Cariseco, Tres hojas',
    'Ayer, hoy y mañana',
    'Lavanda',
    'Arrayan',
    'Metrosideros',
    'Cajeto 1',
    'Tuno',
    'Guamo santafereño',
    'Nispero',
    'Palma coquito',
    'Tagua',
    'Cucharo',
    'Gurrubo',
    'Palma de cera',
    'Ciro',
    'Mimbre',
    'Venturosa',
    'Mirto',
    'Siete cueros',
    'Fucsia arbustiva',
    'Guayabo del peru',
    'Raphiolepys',
    'Cajeto',
    'Ocobo, Guayacan',
    'Tinto',
    'Pichuelo',
    'Tibar, Rodamonte, Pagoda',
    'Sangregado',
    'Palma de cera, Palma de ramo',
    'Endrino',
    'Trompeto',
    'Helecho palma',
    'Fucsia',
    'Sangregado',
    'Cucharo',
    'Cipres',
    'Yarumo  ',
    'Palma roebeleni',
    'Guayabo',
    'Abutilon  pequeño',
    'Té de Bogotá',
    'Arbol de hierro',
    'Amargoso',
    'Schefflera',
    'Otro',
    'Ciprés enano',
    'Baeckea',
    'Tinto',
    'Uva de Anis',
    'Caucho',
    'Palma washingtoniana',
    'Borrachero rojo',
    'Chilco de páramo',
    'Arbol de Te',
    'Cipres',
    'Tinto',
    'Siete Cueros peludo',
    'Eucalipto',
    'Sangregado',
    'Palma de datiles',
    'Acacia',
    'Acacia azul',
    'Gaque',
    'Cerezo, ciruelo',
    'Tibar extranjero',
    'Encenillo',
    'Algodon extranjero',
    'Mulato',
    'Alcaparro enano',
    'Barbasco',
    'Cipres Japones, criptomeria',
    'Cedrillo',
    'Gurrubo',
    'Pegamosco',
    'Leptospermun',
    'Tibar, tobo, rodamonte',
    'Citrus spp.',
    'Bonetero del Japon',
    'Salvia Tortuosa',
    'Pajarito',
    'Cipres',
    'Azara',
    'Azalea',
    'Algodoncillo',
    'Cerezo',
    'Ciruelo',
    'Pitosporo',
    'Carbonero',
    'Grevilea',
    'Manzano',
    'Arboloco',
    'Jarilla',
    'Gaque',
    'Pomarroso',
    'Cajeto de Bogota',
    'Palo blanco',
    'Abutilon quesito',
    'Niguito',
    'Cafe',
    'Guamo',
    'Alamo de lombardia',
    'Arbol de corcho',
    'Guamo',
    'Arbol de Fuego',
    'Palma cinta',
    'Brunelia',
    'Acacia blanca, leucaena',
    'Carbonero',
    'Gualanday',
    'Conejo',
    'Abelia',
    'Naranjillo',
    'Mano de oso',
    'Lantana boyacana',
    'Leandra',
    'Carbonero',
    'Acacia',
    'Guayabo brasilero',
    'Acacia',
    'Pino australiano',
    'Lupinus',
    'Espadero',
    'Cajeto sp',
    'Eucalipto',
    'Caucho sabanero',
    'Guayabo de mico',
    'Lulo de perro',
    'Malvavisco',
    'Amargoso',
    'Carbonero rosado',
    'Caucho Sabanero',
    'Boj',
    'Helecho arborecente',
    'Borrachero',
    'Corazon de pollo',
    'Fuscia arbórea',
    'Tuno',
    'Dalia',
    'Cestrum',
    'Pino hayuelo',
    'Pepero',
    'Mortiño ferrugineo',
    'Eucalipto plateado',
    'Cocobo azul',
    'Pino',
    'Caucho lira',
    'Moquillo',
    'Granado',
    'Guayacán amarillo',
    'Pino',
    'Tomate de arbol',
    'Escolin, Espadero',
    'Agracejo',
    'Sietecueros plateado',
    'Manzano de monte',
    'Laurel europeo',
    'Gaquillo',
    'Salvio morado',
    'Cerezó uche',
    'Guayabo',
    'Pino azul',
    'Fenix',
    'Chaguaca',
    'Palma Botella',
    'Blanquillo',
    'Eucalipto blanco',
    'Almanegra, quedo',
    'Salvia',
    'Salton o Charne',
    'Arrayan extranjero',
    'Schefflera, Tortolito',
    'Azuceno, enebro',
    'Mano de oso 2',
    'Arrocero',
    'Palma areca',
    'Tuno',
    'Tinto',
    'Encenillo',
    'Platano de tierra fria',
    'Cajeto 2',
    'Yuca, palma yuca',
    'Fotinia',
    'Ciro',
    'Duranta amarilla',
    'Chocho, balu, cambulo',
    'Carbonero',
    'Camelia',
    'Acebo',
    'Fucsia',
    'Tinto',
    'Cidron',
    'Higueron',
    'Gallinazo',
    'Guayabillo',
    'Jazmin amarillo',
    'Cucharo huesito',
    'Tecomaria',
    'Lavatera, Malvavisco morado',
    'Lechero',
    'Cucharo de Paramo',
    'Ceiba de tierra fria',
    'Fucsia',
    'Cestrum',
    'Olivo',
    'Cucharo',
    'Margariton',
    'Dalia',
    'Jazmin australiano',
    'Schefflera, Yuco blanco',
    'Romero',
    'Arañita',
    'Chirimoyo',
    'Aromo',
    'Palo blanco',
    'Azuceno de monte',
    'Ojo de perdiz',
    'Borracherito',
    'Gaque',
    'Hojarasco',
    'Motilon, chuguaca',
    'Pegamosco',
    'Añil',
    'Pero',
    'Romerillo',
    'Paulonia',
    'Mortiño',
    'Aralia japonesa',
    'Palma geonoma',
    'Cestrum',
    'Liberal o lechero',
    'Salvia',
    'Pepero',
    'Brevo',
    'Rosa',
    'Platano',
    'Hebe',
    'Salvio',
    'Palma prestoea',
    'Yolombo',
    'Garrapato',
    'Granado',
    'Pino Montezuma',
    'Mortiño',
    'Tabaquillo',
    'Arbol pipermint',
    'Eucalipto manchado',
    'Fucsia- naranja',
    'Diosme',
    'Totumito',
    'Cestrum',
    'Fique',
    'Aloe arboreo',
    'Motilón',
    'Palma de ramo',
    'Palma funeral',
    'Olmo de agua',
    'Bencenuco',
    'Canelo',
    'Pepas del diablo',
    'Palma Kenia',
    'Charné',
    'Tefrosia Purpurea',
    'Palma Senegal',
    'Quina',
    'Duranta sp',
    'Lembo, pategallo',
    'Espino barnadesia',
    'Pimiento',
    'Balso blanco',
    'Algodoncillo',
    'Mote',
    'Sandalo',
    'Chromolaena bullata',
    'Flor morado',
    'Garrocho sp',
    'Magnolia rosada',
    'Pimiento negro',
    'Moradilla',
    'Uña de gato 1',
    'Papayuela',
    'Coloradito',
    'Angelita',
    'Dulomoco',
    'Granizo',
    'Granizo',
    'Arupo',
    'Árbol de platano',
    'Berberis',
    'Palma sancona',
    'Hiperico, Corazoncillo',
    'Schefflera pategallina peludo',
    'Morera',
    'Tuno',
    'Manto de Maria',
    'Mango',
    'Salvio',
    'Ceiba',
    'Mano de oso',
    'Arrayan',
    'Uña de gato 2',
    'Ombu, Arbol de la bella sombra',
    'Espino blanco',
    'Tulipan africano',
    'Nacedero',
    'Trompo',
    'Sauco',
    'Arupo',
    'Guayabo de pava',
    'Laurel',
    'Gardenia',
    'Guarana, guacharo',
    'Jazmin Azul',
    'Chirriador',
    'Cigarrillo',
    'Balazo',
    'Acacia blanca, Cultriformes',
    'Siete Cueros',
    'Amarrabollo Longifolia',
    'Arupo',
    'Cucharo',
    'Arce',
    'Grosella',
    'Camaron',
    'Jomi, upacon',
    'Punta de lanza',
    'Cafetillo, crucito ',
    'Aguacatillo',
    'Caucho',
    'Secuoya',
    'Retamo',
    'Amarillo',
    'Roblehaya',
    'Aligustrina',
    'Romero de paramo',
    'Tachuelo',
    'Palmiche',
    'Sonajero',
    'Totumillo',
    'Crucero',
    'Tecuito',
    'Susque',
    'Guacimo',
    'Amargoso',
    'Oreja de Burro',
    'Mangostino',
    'Algarrobo',
    'Anon',
    'Anona',
    'Guayabo anselmo, Champo',
    'Pepero',
    'Ardicia',
    'Tuna de la sabana',
    'Aguacatillo',
    'Chilco negro',
    'Encenillo',
    'Tecuito',
    'Fique',
    'Copa de oro',
    'Tibar del jardin',
    'Mamey',
    'Arbol de neem',
    'Uña de gato',
    'Eugenia',
)


@pytest.mark.parametrize("key,value", sorted(SPANISH_SPECIES.items()))
def test_every_value_is_a_name_the_ingest_keeps(key: str, value: str):
    """`sanitize_species` must return the value unchanged.

    `species` is the join key into the enrichment table, and
    `enforce_tree_schema` puts every value through `sanitize_species` before
    publishing it.  A value that gets rewritten there (a rank below species, a
    U+00D7 hybrid mark, a lowercase genus, a synonym the repo folds) would
    publish as something other than what this table says, and a value that
    gets *rejected* would publish as `Unknown` -- so the table would look
    right and do nothing.
    """
    assert sanitize_species(value) == value, (
        f"{key!r} -> {value!r} is not a name sanitize_species keeps as written; "
        f"it emits {sanitize_species(value)!r}"
    )


@pytest.mark.parametrize("key", sorted(SPANISH_SPECIES))
def test_every_key_is_in_normalised_form(key: str):
    """A key not in `spanish_name_key` form is dead: nothing can match it."""
    assert spanish_name_key(key) == key


@pytest.mark.parametrize("key", sorted(UNRESOLVED))
def test_every_unresolved_key_is_in_normalised_form(key: str):
    """The same rule for the other set, or the coverage test could not use it."""
    assert spanish_name_key(key) == key


def test_no_value_is_also_a_key():
    """One lookup is enough -- the table is not chained.

    The same rule `SPECIES_SYNONYMS` and `COMMON_NAME_SPECIES` follow.  Here
    it is not a formality: the keys are lowercase and the values capitalised,
    but the portal publishes `Ligustrum`, `Baeckea`, `Berberis`, `Hebe` and
    `Lupinus` as common names, so a Latin key is one casefold away from a
    Latin value.
    """
    assert not set(SPANISH_SPECIES) & set(SPANISH_SPECIES.values())


def test_no_value_is_a_sentinel():
    """A sentinel is not a taxon and must not be reachable from this table.

    `species_from_spanish_name` returns a sentinel by asking
    `form_sentinel_for`, which is the one place that decision lives.  A
    sentinel hardcoded here would be a second, silent copy of it.
    """
    assert not set(SPANISH_SPECIES.values()) & SPECIES_SENTINELS


def test_resolved_and_unresolved_are_disjoint():
    """A key cannot be both a decision to resolve and a decision not to."""
    assert not set(SPANISH_SPECIES) & UNRESOLVED


@pytest.mark.parametrize("raw", BOGOTA_PUBLISHED_NAMES)
def test_every_published_name_is_decided(raw: str):
    """Every string the inventory publishes is in one set or the other.

    A name in neither would publish as `Unknown` without anyone having
    decided that it should, which is exactly the silent failure this table
    exists to prevent.  `UNRESOLVED` is where a deliberate `None` lives, so
    that this test can tell it from an omission.
    """
    key = spanish_name_key(raw)
    assert key in SPANISH_SPECIES or key in UNRESOLVED, (
        f"{raw!r} (key {key!r}) is neither resolved nor listed in UNRESOLVED"
    )


def test_unresolved_names_are_all_still_published():
    """A decision not to resolve a name should be about a name that exists.

    An entry in `UNRESOLVED` that no published string reaches is a leftover
    from a catalogue change and should be deleted, not carried.
    """
    published = {spanish_name_key(raw) for raw in BOGOTA_PUBLISHED_NAMES}
    assert UNRESOLVED <= published, sorted(UNRESOLVED - published)


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Case folds.
        ("Sauco", "sauco"),
        ("SAUCO", "sauco"),
        # Accents are stripped, so both spellings the portal uses are one key.
        ("Ciprés, Pino ciprés, Pino", "cipres pino cipres pino"),
        ("Cipres, Pino cipres, Pino", "cipres pino cipres pino"),
        ("Jazmín", "jazmin"),
        ("Té de Bogotá", "te de bogota"),
        # ñ folds to n with the other marks.
        ("Abutilon  pequeño", "abutilon pequeno"),
        # Punctuation becomes a word boundary and nothing more.
        ("Abutilon rojo y amarillo (Farolito)", "abutilon rojo y amarillo farolito"),
        ("Fucsia- naranja", "fucsia naranja"),
        ("Citrus spp.", "citrus spp"),
        # Every kind of stray space goes, including the trailing ones the
        # portal ships on `Yarumo  `.
        ("Yarumo  ", "yarumo"),
        ("  Mano   de oso ", "mano de oso"),
        # Digits stay: these are distinct published entries.
        ("Cajeto 1", "cajeto 1"),
        ("Mano de oso 2", "mano de oso 2"),
        (None, ""),
        ("   ", ""),
    ],
)
def test_spanish_name_key(raw, expected):
    assert spanish_name_key(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Sauco", "Sambucus nigra"),
        ("Jazmin del cabo, laurel huesito", "Pittosporum undulatum"),
        ("Chicala, chirlobirlo, flor amarillo", "Tecoma stans"),
        # An accented spelling reaches the same row as the plain one.
        ("Ciprés, Pino ciprés, Pino", "Cupressus lusitanica"),
        ("Cipres, Pino cipres, Pino", "Cupressus lusitanica"),
        ("Urapán, Fresno", "Fraxinus uhdei"),
        ("Urapan, Fresno", "Fraxinus uhdei"),
        # A genus is an answer: the publisher's own category names.
        ("Eucalipto", "Eucalyptus"),
        ("Acacia", "Acacia"),
        ("Citrus spp.", "Citrus"),
        ("Cipres", "Cupressus"),
        # ... and a name the publisher pairs with several species.
        ("Mano de oso", "Oreopanax"),
        ("Mano de oso 2", "Oreopanax"),
        # A growth form keeps its form rather than falling to Unknown; the
        # sentinel comes from form_sentinel_for, so this returns the raw value
        # for enforce_tree_schema to map.
        ("Palmera", "Palmera"),
        ("arbusto", "arbusto"),
        ("Árbol muerto", "Árbol muerto"),
        # An empty site is passed through for enforce_tree_schema to drop.
        ("Vacant", "Vacant"),
        # Unresolved is None -- never the raw value.
        ("NN", None),
        ("Otro", None),
        ("Arbol de Te", None),
        ("Palma cinta", None),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_species_from_spanish_name(raw, expected):
    assert species_from_spanish_name(raw) == expected


def test_the_whole_string_is_the_key():
    """Keying on the first name would collapse distinct catalogue entries.

    `Cedro` heads two entries that are two genera apart, and `Cerezo` heads
    one species and one pair.  The whole string is what the publisher wrote
    for one taxon, so the whole string is what is looked up -- and a bare
    first name that the inventory never publishes on its own is not a key.
    """
    assert species_from_spanish_name("Cedro, cedro andino, cedro clavel") == "Cedrela montana"
    assert species_from_spanish_name("Nogal, cedro nogal, cedro negro") == "Juglans neotropica"
    assert species_from_spanish_name("Cerezo, capuli") == "Prunus serotina"
    assert species_from_spanish_name("Cerezo, ciruelo") == "Prunus"
    assert species_from_spanish_name("Pino colombiano, pino de pacho, pino romerón") == "Retrophyllum rospigliosii"
    assert species_from_spanish_name("Pino colombiano, chaquiro") == "Podocarpus oleifolius"
    assert species_from_spanish_name("Cedro") is None


def test_an_unresolved_single_word_is_not_published_as_a_genus():
    """The failure this module exists to avoid, in its Spanish form.

    A single capitalised Spanish word is indistinguishable from a genus to
    `sanitize_species`: passed through, `Sauquito` would publish as an
    invented genus and join to an enrichment row for a plant that does not
    exist.  A name this table does not have must come back `None` rather
    than being guessed at from a prefix it shares with one that it does.
    """
    assert species_from_spanish_name("Sauco") == "Sambucus nigra"
    assert species_from_spanish_name("Sauquito") is None
    assert sanitize_species("Sauquito") == "Sauquito"  # which is why it must not pass through


def test_the_two_names_the_hints_got_wrong_are_curated():
    """Where the publisher's own layers disagree, the table records a choice.

    Each of these is a single row a regeneration from any one source would
    silently flip, so each is pinned with the reason it went the way it did.
    """
    # The heritage layer writes *Fraxinus chinensis*, a decades-old
    # misidentification; the name platform and POWO both carry *F. uhdei*.
    assert species_from_spanish_name("Urapán, Fresno") == "Fraxinus uhdei"
    # "Jazmín de la China" reads like a Jasminum and is a privet.
    assert species_from_spanish_name("Jazmin de la china") == "Ligustrum lucidum"
    assert species_from_spanish_name("Aligustre del Japon") == "Ligustrum japonicum"
    # The weeping willow of Bogotá is the native one, by the garden's own name.
    assert species_from_spanish_name("Sauce lloron") == "Salix humboldtiana"
