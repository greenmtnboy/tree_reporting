"""`shared.species.czech`, the Prague vernacular name -> binomial table.

The table is curated by hand, so what a test can add is the mechanical half:
that every value is a name the ingest would keep as written, that every key
is in normalised form (a key that is not can never be looked up), that the
normaliser folds the diacritics, case, punctuation and quoted cultivars
Prague's register actually publishes -- and that every one of the 332 Czech
strings the register published when the table was written is either
resolved or *deliberately* unresolved, so that a name the table forgot is a
red test rather than a quiet `Unknown`.

The taxonomy itself is not testable here and is not meant to be -- it is
reviewed by reading the file, the same way the other three tables and
`SPECIES_SYNONYMS` are.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from shared.ingest import (  # noqa: E402
    SPECIES_SENTINELS,
    sanitize_species,
)
from shared.species.czech import (  # noqa: E402
    CZECH_SPECIES,
    UNRESOLVED,
    czech_name_key,
    species_from_czech_name,
)

# Every distinct `TAXON_DRUH` the register published on a row with no
# `TAXON_ROD` in November 2026 -- 332 strings, verbatim, trailing spaces and
# the register's own accent-less typos included.  Regenerate from the layer
# (`groupByFieldsForStatistics=TAXON_ROD,TAXON_DRUH`) when the vocabulary
# changes; a new entry is a red test until the table decides it.
PRAGUE_PUBLISHED_NAMES: tuple[str, ...] = (
    'lípa srdčitá',
    'javor mléč',
    'javor neurčený',
    'lípa - neurčená',
    'trnovník akát',
    'bříza bílá',
    'javor klen',
    'borovice - neurčená',
    'jasan ztepilý',
    'borovice černá',
    'lípa velkolistá',
    'smrk neurčený',
    'třešeň ptačí',
    'bříza neurčená',
    'jabloň - neurčená',
    'jasan - neurčený',
    'borovice lesní',
    'smrk pichlavý',
    'jírovec maďal',
    'javor babyka',
    'habr obecný',
    'třešeň sakura',
    'javor jasanolistý',
    'jerlín japonský',
    'smrk ztepilý',
    'slivoň, třešeň - neurčená',
    'lípa plstnatá',
    'ořešák královský',
    'hrušeň obecná',
    'jeřáb neurčený',
    'platan španělský',
    'modřín opadavý',
    'slivoň myrobalán',
    'dub neurčený',
    'líska turecká',
    'douglaska tisolistá',
    'dřezovec trojtrný',
    'vrba - neurčená',
    'dub letní',
    'topol - neurčený',
    'trešen ptací',
    'jírovec neurčený',
    'zerav neurčený',
    'hloh jednosemenný',
    'smrk pančičův',
    'lípa evropská',
    'břestovec západní',
    'topol černý',
    'třešeň mahalebka',
    'hrušeň calleryova',
    'hloh neurčený',
    'javor stříbrný',
    'ořešák neurčený',
    'hloh obecný',
    'tavolník - neurčený',
    'buk lesní',
    'švestka domácí',
    'slivoň mirabelka',
    'lípa zelená',
    'pámelník bílý',
    'jeřáb obecný',
    'platan neurčený',
    'jinan dvoulaločný',
    'slivoň slíva',
    'hrušeň - neurčená ',
    'líska neurčená',
    "sakura ozdobná 'Kanzan'",
    'jedle neurčená',
    'jeřáb muk',
    'borovice vejmutovka',
    'pajasan žlaznatý',
    'bez černý',
    'jeřáb prostřední',
    'jírovec červený',
    'zerav západní',
    'jabloň domácí',
    'jasan pensylvánský',
    'jeřáb ptačí',
    'topol kanadský',
    'svitel latnatý',
    'platan javorolistý',
    'třešeň ptačí - ovocná',
    'višeň chloupkatá',
    'ambroň západní',
    'katalpa obecná',
    'topol osika',
    'katalpa neurčená',
    'růže šípková',
    'dub červený',
    'jilm vaz',
    'javor ginnala',
    'jilm - neurčený',
    'šeřík - neurčený',
    'šeřík obecný',
    'tis neurčený',
    'jalovec neurčený',
    'dub zimní',
    'javor mléč kultivary',
    'vrba jíva',
    'jeřáb duryňský',
    'jilm habrolistý',
    'vrba bílá',
    'jasan úzkolistý',
    'ptačí zob obecný',
    'břestovec jižní',
    'borovice těžká (žlutá)',
    'jilm lysý (horský)',
    'hlošina úzkolistá',
    'platan západní ',
    'střemcha obecná',
    'jilm hybrid',
    'cypřišek neurčený',
    'trešen ptací - ovocná',
    'tis červený',
    "třešeň křovitá 'Globosa'",
    'moruše bílá',
    'dřín obecný',
    'meruňka obecná',
    'topol bílý',
    'škumpa - neurčená',
    'jedle ojíněná',
    'štědřenec neurčený',
    'topol Šimonův',
    'zimolez pýřitý',
    'smrk stříbrný',
    'višeň křovitá',
    'jabloň lesní',
    'hlošina neurčená',
    'škumpa ocetná',
    "javor mléč 'Globosum'",
    'Jilm',
    'olše lepkavá',
    'hloh kuří noha',
    'borovice kleč',
    'pavlovnie plstnatá',
    'kaštanovník setý',
    'líska obecná',
    'jedle bělokorá',
    'zlatice neurčená',
    'růže Hugova',
    'ořešák černý',
    'višeň sargentova',
    'muchovník stromovitý',
    'višeň obecná',
    'vrba pokroucená',
    'hlohyně šarlatová',
    'olše neurčená',
    'vrba Matsudova',
    'svída - neurčená',
    'korkovník amurský',
    'smrk sivý',
    'břestovec Juliin',
    'jilm rebona',
    "dub letní 'Fastigiata'",
    'štědřenec odvislý',
    'vrba křehká',
    'trnovník margaretta',
    'javor červený',
    'tamarišek drobnokvětý',
    'liliovník tulipánokvětý',
    'topol vlašský',
    'aronie temnoplodá',
    'slivoň trnka',
    'zelkova habrolistá',
    'cypřišek Lawsonův',
    'rakytník řešetlákový',
    'zimolez - neurčený',
    'zerav řasnatý',
    'jalovec obecný',
    'šácholan - neurčený',
    'dub šarlatový',
    'jasan zimnář',
    'topol balzámový',
    'metasekvoje čínská',
    'borovice zobanitá',
    'broskvoň',
    'trnovník huňatý',
    'pámelník - neurčený',
    'jeřáb oskeruše',
    'dřišťál neurčený',
    'jalovec viržinský',
    'dub slovenský',
    'javor kříženec',
    'lyrovník tulipánokvětý',
    'olše šedá',
    'zlatice prostřední',
    'dub bahenní',
    'jeřáb širokolistý',
    'lípa americká',
    'trnovník neurčený',
    'tamarišek neurčený',
    "topol černý 'Italica'",
    'muchovník Lamarckův',
    'pustoryl neurčený',
    'ptačí zob - neurčený',
    'jeřáb',
    'růže neurčená - sadová',
    'jilm holandský',
    'Ořechovec srdcovitý',
    'pustoryl věncový',
    'borovice Jeffreyova',
    'svída krvavá',
    'smrk černý (severoamerický)',
    'habrovec habrolistý',
    'javor tatarský',
    'tavolník van Houteův',
    'jedle kavkazská',
    'tavolník vrbolistý',
    'cedr atlaský',
    'jedle korejská',
    'borovice pokroucená ',
    'jabloň purpurová',
    "smrk pichlavý 'Glauca'",
    'dub pýřitý',
    'smrk východní',
    'jinan dvoulalocný',
    'ibišek syrský',
    'javor kalinolistý',
    'zimostráz vždyzelený',
    'javor francouzský',
    'zimolez černý',
    'platan javorolistý ',
    'topol šedavý',
    'ruj vlasatá',
    'borovice Banksova',
    'jasan americký',
    'cedr libanonský',
    'zelkova ',
    'tavola kalinolistá',
    'mochnovec křovitý',
    'trnovník pražský',
    'dřišťál obecný',
    'vrba Matsudova "Tortuosa"',
    'dub dvoubarevný',
    'cedr himalájský',
    'břestovec',
    'janovec metlatý',
    'javor řídkokvětý',
    'střemcha pozdní',
    'parocie perská',
    'stremcha obecná',
    'zerav výhodní',
    'čimišník obecný',
    'dub balkánský',
    'cypřišek nutkajský',
    'meruzalka černá',
    'bříza papírová',
    'dub kaštanolistý',
    'jeřáb břek',
    'jalovec čínský',
    'šácholan kobus',
    'osika',
    'štedrenec odvislý',
    'bříza himalájská',
    'zmarlika kanadská',
    'rhododendron neurčený',
    'smrk liťiangský',
    '03_neurčeno',
    'jedle stříbrná',
    'borovice osinatá',
    "sakura ozdobná 'KIKU-SHIDARE-ZAKURA'",
    'trojpuk neurčený',
    'pazerav sbíhavý',
    "jasan ztepilý 'Pendula'",
    'Břestovec čínský',
    'jedlovec kanadský',
    'jedle obrovská',
    'bobkovišeň lékařská',
    'smrk Brewerův',
    'višeň křovitá - roub. na kmínek',
    'jalovec prostřední ',
    'borovice limba',
    "zerav západní 'Smaragd'",
    'borovice blatka ',
    'mahonie - neurčená',
    'muchovník neurčený',
    'mišpule německá',
    'cypřišek hrachonosný',
    'bríza himalájská',
    'zlatice převislá',
    'meruzalka červená',
    'skalník neurčený',
    'javor Zoeschensův',
    'kalina obecná',
    'jírovec žlutý',
    'trnovník honosný',
    'topol chlupatoplodý',
    'tisovec dvouřadý',
    'borovice bělokorá',
    'fíkovník',
    'brslen neurčený',
    'řešetlák počistivý',
    'jasmín nahokvětý',
    'přísavník pětilistý',
    'ostružiník neurčený',
    'Javor mleč sanset',
    'svída bílá',
    'kalina tušalaj',
    "líska největší 'Purpurea'",
    'čilimník neurčený',
    'meruzalka zlatá',
    'meruzalka alpská',
    'vrba babylónská',
    "jabloň mnohokvětá 'Atropurpurea'",
    'ořechovec pekanový',
    'hloh peřenoklaný',
    'kustovnice cizí',
    'jedle řecká',
    'zákula japonská',
    'Citronovník limonový',
    'dub velkoplodý',
    'juka vláknitá',
    "slivoň myrobalán 'Nigra'",
    'plamének vlašský',
    'třezalka kalíškatá',
    'buk lesní kultivar',
    'pivoňka keřovitá',
    'meruzalka krvavá',
    'kalina - neurčená',
    'idézia mnohoplodá',
    'platan východní',
    'brestovec',
    'hloh Lavallův',
    'mandloň nízká',
    'dub texaský',
    'jeřábník - neurčený',
    'cypřiš stálezelený',
    'papírovník čínský',
    'višeň jedoská',
    'kdouloň obecná',
    "javor mléč 'Atropurpurea'",
    'jalovec chvojka',
)


@pytest.mark.parametrize("key,value", sorted(CZECH_SPECIES.items()))
def test_every_value_is_a_name_the_ingest_keeps(key: str, value: str):
    """`sanitize_species` must return the value unchanged.

    A value it rewrites would publish as something other than what this
    table says; a value it rejects would publish as `Unknown`, so the table
    would look right and do nothing.
    """
    assert sanitize_species(value) == value, (
        f"{key!r} -> {value!r} is not a name sanitize_species keeps as written; "
        f"it emits {sanitize_species(value)!r}"
    )


@pytest.mark.parametrize("key", sorted(CZECH_SPECIES))
def test_every_key_is_in_normalised_form(key: str):
    assert czech_name_key(key) == key


@pytest.mark.parametrize("key", sorted(UNRESOLVED))
def test_every_unresolved_key_is_in_normalised_form(key: str):
    assert czech_name_key(key) == key
    assert key not in CZECH_SPECIES


def test_no_value_is_also_a_key():
    assert not set(CZECH_SPECIES) & set(CZECH_SPECIES.values())


def test_no_value_is_a_sentinel():
    assert not set(CZECH_SPECIES.values()) & SPECIES_SENTINELS


@pytest.mark.parametrize("published", PRAGUE_PUBLISHED_NAMES)
def test_every_published_name_is_decided(published: str):
    """Resolved, or in UNRESOLVED on purpose; never a quiet `Unknown`."""
    key = czech_name_key(published)
    if key in UNRESOLVED:
        assert species_from_czech_name(published) is None
    else:
        assert species_from_czech_name(published) is not None, (
            f"{published!r} (key {key!r}) is neither in CZECH_SPECIES nor in UNRESOLVED"
        )


def test_every_table_key_is_a_published_name():
    """No speculative entries: the table describes the register, not Czech."""
    published = {czech_name_key(n) for n in PRAGUE_PUBLISHED_NAMES}
    assert set(CZECH_SPECIES) <= published
    assert UNRESOLVED <= published


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("lípa srdčitá", "lipa srdcita"),
        ("lípa - neurčená", "lipa neurcena"),
        ("hrušeň - neurčená ", "hrusen neurcena"),
        ("borovice těžká (žlutá)", "borovice tezka zluta"),
        ("třešeň křovitá 'Globosa'", "tresen krovita"),
        ('vrba Matsudova "Tortuosa"', "vrba matsudova"),
        ("trešen ptací", "tresen ptaci"),
        ("03_neurčeno", "03 neurceno"),
        ("Ořechovec srdcovitý", "orechovec srdcovity"),
        ("", ""),
        (None, ""),
    ],
)
def test_key_normalisation(raw, expected):
    assert czech_name_key(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("lípa srdčitá", "Tilia cordata"),
        ("trešen ptací", "Prunus avium"),
        ("sakura ozdobná 'Kanzan'", "Prunus serrulata"),
        ("lípa - neurčená", "Tilia"),
        ("Jilm", "Ulmus"),
        ("03_neurčeno", None),
        ("rododendron obrovský", None),
        ("", None),
        (None, None),
    ],
)
def test_lookup(raw, expected):
    assert species_from_czech_name(raw) == expected
