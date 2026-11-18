#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Rennes Métropole's public-space tree register, from its GeoServer WFS.

Source: `espub_esv:arbre` on Rennes Métropole's own GeoServer
(`public.sig.rennesmetropole.fr`), catalogued as *Arbres sur l'espace public
sur Rennes Métropole* -- every tree in the spaces the 43 communes of the
metropole manage, one point per tree, maintained by each commune and run by
the metropole's DTIG service.  **191,988 rows** when this was written.
Published under the **Licence ODbL 1.0** (Open Database License, a
share-alike licence), as both the GeoNetwork record
(https://public.sig.rennesmetropole.fr/geonetwork/srv/api/records/093590bd-9842-4138-af9a-7e7918b86292)
and the portal's dataset page
(https://data.rennesmetropole.fr/explore/dataset/arbre/) state.

Why the WFS and not the Opendatasoft copy (`data.rennesmetropole.fr`, dataset
`arbre`): the portal harvests this same layer, but its catalogue `modified`
is frozen at 2024-11-14 and its harvest reads every date column as null
(GeoServer writes a date as `2026-03-06Z`, which the portal does not parse),
so it has no planting dates and no edit stamp.  The WFS is the publisher's
live layer: `date_maj` (each row's last edit) runs to the day of reading.

Coverage: Rennes itself is half (95k), the rest spread over the metropole's
other 42 communes.  **108k rows have no genus** (`Non renseigné`): the
metadata says that where a commune does not maintain its own inventory, the
trees shown are those of the metropole's large-scale topographic reference
(RTGE), which records the point and nothing else.  They are trees, and
publish as `Unknown`.

**Keyed on `id`**, the layer's integer primary key (non-nullable in the
DescribeFeatureType; the GeoServer feature id is `arbre.{id}`), checked unique
and non-null over all 191,988 rows.  `id_gestion`, the communes' management
code, is null on 44% of rows and repeated on others, so it is not the grain.
Published as `rennes-207903`.

**Dropped**: `abattu` true (115 felled trees still in the layer), and trees
whose `type_taille` or `fonction` is `Totem` (~230): a *totem* is a trunk cut
high and left standing as wildlife habitat, a dead stem rather than a tree,
the counterpart of Toulouse's `Chandelle`.  `abattu` null (20 rows) is kept.

**Species is `genre` + `espece`, with `variete` as the cultivar**, Latin
throughout (`nom_commun` is the French common name and is not used):

  * `Non renseigné`, `Non reconnu`, `Autre` genus: unknown;
  * epithet empty, `sp.`, `autre`, `Non renseigné`: the genus alone;
  * epithet `x` alone is an unnamed hybrid; with a lower-case `variete` that
    is the hybrid epithet (`Populus x` `canadensis` -> `Populus x canadensis`),
    with a capitalised one a cultivar (`Prunus x` `Spire` -> Prunus 'Spire');
  * a capitalised epithet is a cultivar written in the wrong column
    (`Prunus Accolade`, `Ulmus Lobel`, `Prunus Kanzan`), except the four
    real epithets written capitalised (`Pinus Sylvestris`, ...);
  * `Ulmus resista` is the Resista elm series and `Malus perpetu` /
    `x perpetu` / `x evereste` the Evereste crab apple: the genus with the
    cultivar (the `variete` when there is one), as Toulouse and Lyon do;
    `Prunus pissardii` is Prunus cerasifera 'Pissardii'; `Malus golden homet`
    is Malus 'Golden Hornet';
  * old genus spellings are corrected here, per genus: `Albizzia`, `Thuya`,
    `Clerodendron`, `Cladastris`/`Cladrastris`, `Eleagnus`, `Camelia`,
    `Cinnamonum`, `Asiminia`, `Buddleia`; the nothogenus `X Chitalpa` is
    published as `Chitalpa`.

`variete` is a cultivar unless it is `Autre`, the pruning form `Tetard`, a
`subsp.`, the autonym (`Pinus nigra` `Nigra`), a `x ...` cross note, or one of
the lower-case botanical varieties the register writes there (`corsicana`,
`parvifolia`, ...); a lower-case cultivar (`fastigiata`) is capitalised.

**`circonference` is trunk circumference in centimetres**, as the metadata
says and the values agree (median 100, 95th percentile 275, max 900; a
diameter would be absurd at those values).  56k rows record 0 or nothing:
unmeasured, published as null.  Measured almost only in Rennes.

**`houppier` is the crown diameter in whole metres** (median 6): the
columnar cultivars give it away -- Populus nigra 'Italica' records a 4 m
houppier on a 23 m tree with a 20 m deep crown -- and becomes
`crown_width_m`, 0 as null.  `hauteur` is a recorded height; the canonical
schema has no column for one, so it is not read.

**`date_plantation`** is a date; 50k of its 53k values are 1 January, the
year-only convention, kept as written.  1900-01-01 (2,067 rows, all but four
in Rennes, against none in 1899 or 1901) is a placeholder and publishes as
null; a future date (2222) is nulled by `enforce_tree_schema`.
"""

import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    circumference_cm_to_dbh_inches,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.wfs import WfsLayer, iter_wfs_features

# Kept in step with `rennes_update_time.py`.
LAYER = WfsLayer(
    "https://public.sig.rennesmetropole.fr/geoserver/ows",
    "espub_esv:arbre",
    timeout=300,
)

PROPERTIES = [
    "id", "genre", "espece", "variete", "fonction", "type_taille", "abattu",
    "circonference", "houppier", "date_plantation", "geom",
]

# A genus nobody recorded.
UNKNOWN_GENERA = frozenset({"non renseigné", "non renseigne", "non reconnu", "autre"})
# Epithets that say "no species named".
GENUS_ONLY_EPITHETS = frozenset(
    {"sp", "sp.", "spp", "spp.", "autre", "non renseigné", "non renseigne"}
)
# Real epithets the register writes with a capital.
CAPITALISED_EPITHETS = frozenset({"sylvestris", "aquifolium", "ilex", "chenii"})
# The register's old spellings of a genus.
GENUS_SPELLINGS = {
    "Albizzia": "Albizia",
    "Thuya": "Thuja",
    "Clerodendron": "Clerodendrum",
    "Cladastris": "Cladrastis",
    "Cladrastris": "Cladrastis",
    "Eleagnus": "Elaeagnus",
    "Camelia": "Camellia",
    "Cinnamonum": "Cinnamomum",
    "Asiminia": "Asimina",
    "Buddleia": "Buddleja",
}
# (genus, epithet) pairs that are a cultivar or trade series, not a species:
# -> (species epithet to publish or None, the cultivar when `variete` is empty).
EPITHET_IS_CULTIVAR = {
    ("Ulmus", "resista"): (None, "Resista"),
    ("Malus", "perpetu"): (None, "Evereste"),
    ("Malus", "x perpetu"): (None, "Evereste"),
    ("Malus", "x evereste"): (None, "Evereste"),
    ("Malus", "golden homet"): (None, "Golden Hornet"),
    ("Prunus", "pissardii"): ("cerasifera", "Pissardii"),
}
# `variete` values that are not a cultivar.
NOT_A_CULTIVAR = frozenset({"autre", "tetard", "acacia"})
# Lower-case `variete` values that are a botanical rank below species, which
# the species key does not carry.
BOTANICAL_VARIETIES = frozenset(
    {"corsicana", "austriaca", "oxycarpa", "macrolepis", "parvifolia", "latifolia", "syriaca"}
)
# The pruning or habitat form that marks a dead standing trunk.
TOTEM = "totem"
# An estimated planting date the register stamps when it has none.
PLACEHOLDER_PLANT_DATES = {date(1900, 1, 1)}

# Refuse to publish when more than this share of a typed field is unreadable:
# that is the register changing shape, not noise.
MAX_UNREADABLE = 0.01

_TRADEMARK = re.compile(r"\s*(®|\(R\)|™)\s*", re.IGNORECASE)
_HYBRID = re.compile(r"^[x×]\s+", re.IGNORECASE)
_CULTIVAR_SMALL_WORDS = frozenset({"de", "des", "du", "la", "le", "of", "d"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("\xa0", " ").split())
    return text or None


def cultivar_name(value: str | None) -> str | None:
    """`'Pissardii'` -> `Pissardii`, `reine des reinettes` -> `Reine des Reinettes`,
    `Royal gala ®` -> `Royal Gala`, `RESISTA` -> `Resista`."""
    text = clean(value)
    if text is None:
        return None
    text = clean(_TRADEMARK.sub(" ", text).strip(" '\"‘’"))
    if text is None:
        return None
    if text.isupper() and len(text) > 3:
        text = text.lower()
    words = text.split(" ")
    out = []
    for i, word in enumerate(words):
        if i and word.lower() in _CULTIVAR_SMALL_WORDS:
            out.append(word.lower())
        else:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out)


def parse_genus(genre) -> str | None:
    genus = clean(genre)
    if genus is None or genus.lower() in UNKNOWN_GENERA:
        return None
    if genus.lower().startswith("x "):
        genus = genus[2:].strip()
    genus = genus.capitalize()
    return GENUS_SPELLINGS.get(genus, genus)


def parse_species(genre, espece, variete) -> tuple[str | None, str | None]:
    """(species string, cultivar)."""
    genus = parse_genus(genre)
    if genus is None:
        return None, None
    epithet = clean(espece)
    variety = clean(variete)
    if variety is not None and variety.lower() in NOT_A_CULTIVAR:
        variety = None

    if epithet is not None and epithet.lower() in GENUS_ONLY_EPITHETS:
        epithet = None
    if epithet is not None:
        epithet = _HYBRID.sub("x ", epithet)

    special = EPITHET_IS_CULTIVAR.get((genus, (epithet or "").lower()))
    if special is not None:
        species_epithet, series = special
        name = f"{genus} {species_epithet}" if species_epithet else genus
        return name, cultivar_name(variety) or series

    if epithet is not None and epithet.lower() == "x":
        # An unnamed hybrid: the variete is the hybrid epithet when it is
        # written lower-case, a cultivar when capitalised.
        if variety is not None and variety[:1].islower():
            return f"{genus} x {variety.lower()}", None
        return genus, cultivar_name(variety)

    if epithet is not None and epithet[:1].isupper():
        if epithet.lower() in CAPITALISED_EPITHETS:
            epithet = epithet.lower()
        else:
            # `Prunus Accolade`: a cultivar in the epithet column.
            return genus, cultivar_name(variety) or cultivar_name(epithet)

    name = genus if epithet is None else f"{genus} {epithet}"
    return name, parse_variety(variety, epithet)


def parse_variety(variety: str | None, epithet: str | None) -> str | None:
    """The cultivar in `variete`, or None for a rank or a note."""
    if variety is None:
        return None
    low = variety.lower()
    if low.startswith(("subsp.", "subsp ", "var.", "var ", "x ", "× ")):
        return None
    bare_epithet = (epithet or "").lower().removeprefix("x ").strip()
    if low == bare_epithet:
        # The autonym: `Pinus nigra` `Nigra` is subsp. nigra, not a cultivar.
        return None
    if variety[:1].islower() and low in BOTANICAL_VARIETIES:
        return None
    return cultivar_name(variety)


def parse_plant_date(value) -> tuple[date | None, bool]:
    """(the published date, whether the text was unreadable)."""
    text = clean(value)
    if text is None:
        return None, False
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        return None, True
    if parsed in PLACEHOLDER_PLANT_DATES:
        return None, False
    return parsed, False


def parse_number(value) -> tuple[float | None, bool]:
    if value is None or value == "":
        return None, False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, True
    return (number if number > 0 else None), False


def make_transform(seen: set[int], counts: Counter):
    def transform(features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float] = []
        longitude: list[float] = []
        dbh: list[float | None] = []
        crown: list[float | None] = []

        for feature in features:
            counts["read"] += 1
            props = feature.get("properties") or {}
            felled = props.get("abattu")
            if felled is True:
                counts["felled"] += 1
                continue
            if felled not in (False, None):
                counts["unreadable_flag"] += 1
            if TOTEM in {
                (clean(props.get("type_taille")) or "").lower(),
                (clean(props.get("fonction")) or "").lower(),
            }:
                counts["totem"] += 1
                continue
            key = props.get("id")
            geometry = feature.get("geometry") or {}
            coords = geometry.get("coordinates") if geometry.get("type") == "Point" else None
            if not isinstance(key, int) or not coords:
                counts["unidentified"] += 1
                continue
            if key in seen:
                counts["duplicate"] += 1
                continue
            seen.add(key)

            planted, bad_date = parse_plant_date(props.get("date_plantation"))
            circumference, bad_circ = parse_number(props.get("circonference"))
            spread, bad_crown = parse_number(props.get("houppier"))
            counts["unreadable_date"] += bad_date
            counts["unreadable_number"] += bad_circ + bad_crown
            if planted is None and clean(props.get("date_plantation")) and not bad_date:
                counts["placeholder_date"] += 1

            name, selection = parse_species(
                props.get("genre"), props.get("espece"), props.get("variete")
            )
            tree_id.append(f"rennes-{key}")
            species.append(name)
            cultivar.append(selection)
            plant_date.append(planted)
            longitude.append(float(coords[0]))
            latitude.append(float(coords[1]))
            dbh.append(circumference_cm_to_dbh_inches(circumference))
            crown.append(spread)

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["FRRNS"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
                "crown_width_m": pa.array(crown, type=pa.float64()),
            }
        )

    return transform


def check_readable(counts: Counter) -> None:
    read = max(counts["read"], 1)
    for key in ("unreadable_flag", "unreadable_date", "unreadable_number"):
        if counts[key] / read > MAX_UNREADABLE:
            raise RuntimeError(
                f"Rennes OpenData: {counts[key]} of {read} rows have {key.replace('_', ' ')} "
                f"(over {MAX_UNREADABLE:.0%}); the register has changed shape"
            )


def main() -> None:
    seen: set[int] = set()
    counts: Counter = Counter()
    table = stream_to_table(
        iter_wfs_features(LAYER, sort_by="id", properties=PROPERTIES, page_size=20000),
        make_transform(seen, counts),
        label="Rennes OpenData",
    )
    check_readable(counts)
    print(
        f"Rennes OpenData: read {counts['read']}; dropped {counts['felled']} felled "
        f"(abattu), {counts['totem']} totem trunk(s), {counts['unidentified']} with no "
        f"id or point, {counts['duplicate']} repeated id(s); nulled "
        f"{counts['placeholder_date']} placeholder plant date(s) (1900-01-01); "
        f"{counts['unreadable_date']} unreadable date(s), {counts['unreadable_number']} "
        f"unreadable measurement(s), {counts['unreadable_flag']} unreadable abattu; "
        f"kept {table.num_rows}",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Rennes", city_code="FRRNS")
    table = enforce_tree_schema(table, city="Rennes", data_source="RENNES_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
