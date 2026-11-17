#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Toulouse's municipal tree inventory ("Arbres urbains"), from Toulouse Métropole.

Source: OpenDataSoft dataset `arbres-urbains` on the metropole's open-data
portal (https://data.toulouse-metropole.fr/explore/dataset/arbres-urbains/),
published by Toulouse Métropole under the Licence Ouverte v2.0 (Etalab), as
the dataset's `license` field states.  **124,214 rows** when this was written,
refreshed monthly ("Mensuelle").  The parquet export returns the whole layer
as one ~6 MB file; the records API caps its offset at 10,000 and is not used.
The inventory is the metropole's managed trees: 117.7k in Toulouse itself,
the rest in Tournefeuille, Cornebarrieu, Beauzelle, Blagnac and a handful in
Balma, Fenouillet and Launaguet -- all inside the city's territory box.

**Keyed on `id`**, the dataset's integer tree id ("un identifiant unique de
l'objet arbre"), verified non-null and unique over all 124,214 rows (1 to
132,405 with gaps, so removed trees leave holes rather than renumbering).
Published as `toulouse-71513`.

**Species is `genre` + `espece`**, Latin, with the register's own habits:

  * hybrids written `x.hispanica`, published `Platanus x hispanica`;
  * `sp.`, `0_sp.`, `0_espece_hors_liste` or no epithet: the genus alone;
  * `0_Genre.inconnu` (3k rows): unknown, published as `Unknown`;
  * a family in the genus column (`Paulowniacées tomentosa`, 8 rows) is the
    genus of that family with the epithet (`Paulownia tomentosa`);
  * an epithet that is really a cultivar (`Malus Everest`, `Ulmus lutece®`,
    `Sorbus 'Dodong'`, `Malus x.Coccinella(R)`) is the genus with that
    cultivar; `Ulmus minor VADA®` keeps its epithet;
  * the nothogenus `x Chitalpa` is published as `Chitalpa`, without the mark;
  * `agnus_castus` is `agnus-castus`; `x.hybride` ("a hybrid") is the genus
    alone; `Malus perpetu` is Malus 'Perpetu' and `Ulmus x.resista` is
    Ulmus 'Resista' (a cultivar series, not a hybrid epithet).

`cultivar_variete` is prefixed by rank: `cv.Chanticleer` is a cultivar,
written in CamelCase (`cv.SchwedleriNigra` -> `Schwedleri Nigra`); `var.` with
a capital is a cultivar too (`var.Italica` is Populus nigra 'Italica'), while
a lowercase `var.`/`subsp.` (`var.fastigiata`, `subsp.austriaca`) is a rank
below species that the species key does not carry, and is dropped.  `famille`
is not used.  There is no common-name column.

**No stem size.**  The dataset publishes no trunk circumference or diameter
(its description mentions dimensions, but the field is not in the export), so
`diameter_at_breast_height` is null throughout.  `hauteur_approx_forestry` is
a height band (`2 - 7 à 12m`), not a measured height, and is not mapped.

**Planting date** is `date_plantation`, a real date on 5.1k rows (1922 and
2013-2025), with no single placeholder stamp dominating.

**Not trees.**  The genus column records `Souche` (stump) and `Chandelle` (a
dead trunk left standing) on a few rows; they are dropped with a count.
`stade_dev`/`forme` `0 - Sans objet` ("not applicable") rows carry ordinary
species and are kept.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    download_parquet,
    emit,
    enforce_tree_schema,
    validate_coordinates,
)

DATASET = "arbres-urbains"
FIELDS = [
    "id",
    "genre",
    "espece",
    "cultivar_variete",
    "date_plantation",
    "latitude",
    "longitude",
]
SOURCE_URL = (
    f"https://data.toulouse-metropole.fr/api/explore/v2.1/catalog/datasets/{DATASET}"
    f"/exports/parquet?select={','.join(FIELDS)}&timezone=UTC"
)

# The genus column's values that are not a tree at all.
NOT_A_TREE_GENERA = frozenset({"souche", "chandelle"})
# Placeholders the register writes for an unknown genus or epithet.
UNKNOWN_GENUS_PREFIX = "0_"
# `x.hybride` is French for "a hybrid", not an epithet (Magnolia x.hybride).
GENUS_ONLY_EPITHETS = frozenset(
    {"sp", "sp.", "spp", "spp.", "0_sp.", "0_espece_hors_liste", "x.hybride"}
)
# Lowercase epithets that are cultivars: `Malus perpetu` is Malus 'Perpetu',
# sold as Evereste (the register writes the trade name in `cultivar_variete`).
# `Ulmus x.resista` is the Resista series of elm cultivars, not a hybrid
# epithet (unknown to WCVP), so it is Ulmus 'Resista'.
EPITHET_IS_CULTIVAR = frozenset({("malus", "perpetu"), ("ulmus", "resista")})
# A family written in the genus column, mapped to its only genus in the register.
FAMILY_AS_GENUS = {"paulowniacees": "Paulownia", "paulowniacées": "Paulownia"}

_TRADEMARK = re.compile(r"\s*(®|\(R\)|™)\s*", re.IGNORECASE)
_HYBRID_PREFIX = re.compile(r"^x[.\s]\s*", re.IGNORECASE)
_EPITHET = re.compile(r"^[a-z][a-z-]*$")
_CAMEL_BREAK = re.compile(r"(?<=[a-zà-ÿ])(?=[A-Z])")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def cultivar_name(value: str | None) -> str | None:
    """`SchwedleriNigra` -> `Schwedleri Nigra`, `Kagayamae_Fruitless` ->
    `Kagayamae Fruitless`, `'Dodong'` -> `Dodong`, `lutece®` -> `Lutece`."""
    text = clean(value)
    if text is None:
        return None
    text = _TRADEMARK.sub(" ", text).strip(" '\"‘’")
    text = text.replace("_", " ").replace("-", " ")
    text = _CAMEL_BREAK.sub(" ", text)
    words = text.split()
    if not words:
        return None
    return " ".join(w[:1].upper() + w[1:] for w in words)


def parse_cultivar_field(value) -> str | None:
    """The cultivar in `cultivar_variete`, or None for a botanical rank."""
    text = clean(value)
    if text is None:
        return None
    rank, dot, name = text.partition(".")
    if not dot:
        return cultivar_name(text)
    rank = rank.lower()
    if rank == "cv":
        return cultivar_name(name)
    if rank == "var" and name[:1].isupper():
        return cultivar_name(name)
    return None


def parse_species(genre, espece) -> tuple[str | None, str | None]:
    """(species string, cultivar found in the epithet column)."""
    genus = clean(genre)
    if genus is None or genus.startswith(UNKNOWN_GENUS_PREFIX):
        return None, None
    genus = FAMILY_AS_GENUS.get(genus.lower(), genus)
    # `x Chitalpa`: a nothogenus.  Published without the mark, as every other
    # city spells it -- the shared hygiene lower-cases the genus after a
    # leading mark ("X chitalpa"), which would be a key of its own.
    if genus.lower().startswith("x "):
        genus = genus[2:].strip()
    genus = genus.capitalize()

    epithet = clean(espece)
    if epithet is None or epithet.lower() in GENUS_ONLY_EPITHETS:
        return genus, None
    hybrid = bool(_HYBRID_PREFIX.match(epithet))
    if hybrid:
        epithet = _HYBRID_PREFIX.sub("", epithet)
    epithet = epithet.replace("_", "-")
    if (genus.lower(), epithet.lower()) in EPITHET_IS_CULTIVAR:
        return genus, cultivar_name(epithet)
    first, _, rest = epithet.partition(" ")
    if _EPITHET.match(first) and not _TRADEMARK.search(first):
        # `minor VADA®`: an epithet followed by a cultivar.
        name = f"{genus} x {first}" if hybrid else f"{genus} {first}"
        return name, cultivar_name(rest) if rest else None
    # The whole epithet is a cultivar (`Everest`, `lutece®`, `'Dodong'`,
    # `Coccinella(R)`, `Golden-hornet`).
    return genus, cultivar_name(epithet)


def transform(table: pa.Table) -> pa.Table:
    missing = set(FIELDS) - set(table.schema.names)
    if missing:
        raise RuntimeError(f"Toulouse ingest: export is missing field(s) {sorted(missing)}")

    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    no_id = not_a_tree = 0

    for key, genre, espece, variete, planted, lat, lon in zip(
        *(table[f].to_pylist() for f in FIELDS)
    ):
        if key is None:
            no_id += 1
            continue
        if (clean(genre) or "").lower() in NOT_A_TREE_GENERA:
            not_a_tree += 1
            continue
        name, from_epithet = parse_species(genre, espece)
        tree_id.append(f"toulouse-{key}")
        species.append(name)
        cultivar.append(parse_cultivar_field(variete) or from_epithet)
        plant_date.append(planted)
        latitude.append(lat)
        longitude.append(lon)

    print(
        f"Toulouse OpenData: {table.num_rows} rows; dropped {no_id} with no id, "
        f"{not_a_tree} stumps or standing dead trunks (genre Souche/Chandelle)",
        file=sys.stderr,
    )
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["FRTLS"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array([None] * len(tree_id), type=pa.float64()),
        }
    )


def main() -> None:
    table = transform(pq.read_table(download_parquet(SOURCE_URL)))
    table = validate_coordinates(table, city="Toulouse", city_code="FRTLS")
    table = enforce_tree_schema(table, city="Toulouse", data_source="TOULOUSE_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
