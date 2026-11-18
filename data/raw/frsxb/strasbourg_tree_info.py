#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Strasbourg's tree inventory ("Patrimoine arboré"), from the Ville et
Eurométropole de Strasbourg.

Source: OpenDataSoft dataset `patrimoine_arbore` on data.strasbourg.eu
(https://data.strasbourg.eu/explore/dataset/patrimoine_arbore/), published by
the Ville et Eurométropole de Strasbourg under the Licence Ouverte v2.0
(Etalab), as the dataset's `license` field states.  **83,832 rows** when this
was written, refreshed monthly.  The parquet export returns the whole layer as
one ~4 MB file.

**Coverage is the Eurométropole, not the commune.**  The dataset is every tree
the city and metropole services track individually: street trees and
metropolitan facilities across all 33 communes, plus parks and municipal
facilities in Strasbourg itself.  A point-in-polygon against the communes'
outlines (geo.api.gouv.fr, EPCI 246700488) put 64.3k in Strasbourg, 19.4k in
the other 32 communes (Illkirch-Graffenstaden 3.0k, Schiltigheim 2.5k,
Eckbolsheim 2.3k, ...) and 97 just outside the metropole's edge.  There is no
commune column.  Woodland, forest and private trees are not in it.

**Keyed on `num_arbre`**, the tree number, verified non-null and unique over
all 83,832 rows.  Published as `strasbourg-14374`.  `num_pt_vert` is the
"point vert" (green-space site) the tree belongs to, 3,081 of them, not an id.

**Species is `si_essence`**, a Latin name with the register's habits:

  * the hybrid mark written after the epithet (`Platanus acerifolia x`,
    `Tilia euchlora x`, `Aesculus carnea x Briotii`), published before it
    (`Platanus x acerifolia`, cultivar Briotii);
  * cultivars mostly unquoted and capitalised (`Tilia cordata Green Spire`,
    `Pyrus calleryana Chanticleer`, `Malus Beauty`), sometimes quoted
    (`Carpinus betulus 'Fastigiata'`); both go to `cultivar`;
  * ranks below species (`ssp`, `var`, `f.`) and the name after them are
    dropped (`Pinus nigra ssp nigra` -> `Pinus nigra`);
  * ` - ` used as a separator (`Styphnolobium - japonicum - Pendula`);
  * `Essence non définie` (404 rows) or no value (106): unknown.

`genre` is the genus of `si_essence` and is not used.  There is no
common-name column.

**DBH is `si_diametre_fut`**, "Diamètre du tronc en centimètres".  The
distribution confirms a diameter (median 25 cm, p90 58 cm; trees recorded at
30 m tall have a median of 80 cm, which a circumference would not give).  Some
values use a decimal comma (`7,5`); 0 is "not measured" and the shared guard
nulls it, as it does the few values over 5 m.

**Crown width is `si_largeur_couronne`**, "Largeur de la couronne en mètres":
a measured spread in metres (median 5 m, decimal comma), mapped to
`crown_width_m`.  `Non renseigné` ("not given") is null.  `si_hauteur` is a
height in metres, but the canonical schema has no recorded-height column, so
it is not mapped.

There is no planting date, and no status column: the dataset carries no
stumps, planting sites or removed trees to drop.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    cm_to_inches,
    download_parquet,
    emit,
    enforce_tree_schema,
    parse_wkb_point,
    validate_coordinates,
)

DATASET = "patrimoine_arbore"
FIELDS = ["num_arbre", "si_essence", "si_diametre_fut", "si_largeur_couronne", "geo_point_2d"]
SOURCE_URL = (
    f"https://data.strasbourg.eu/api/explore/v2.1/catalog/datasets/{DATASET}"
    f"/exports/parquet?select={','.join(FIELDS)}&timezone=UTC"
)

# Placeholders the register writes for "no value".
UNKNOWN_SPECIES = frozenset({"essence non définie", "essence non definie"})
NOT_GIVEN = frozenset({"non renseigné", "non renseigne"})
# A measurement column that stops parsing is a format change, not noise.
MAX_UNREADABLE_SHARE = 0.01

# Ranks below species: the rank word and the name after it are dropped.
INFRASPECIFIC_RANKS = frozenset({"ssp", "ssp.", "subsp", "subsp.", "var", "var.", "f", "f."})
# Lowercase words after the epithet that are a subspecies or variety written
# without its rank, not a cultivar.  `Pinus - mugo - Mughus` is var. mughus.
UNRANKED_INFRASPECIFIC = frozenset({"divergens", "macrolepis", "mughus"})
# A lowercase "epithet" that is a cultivar: `Malus evereste  Perpetue` is the
# crab apple sold as Evereste (registered 'Perpetu').
EPITHET_IS_CULTIVAR = {("malus", "evereste"): "Evereste"}
# `Quercus variabilis x Acutissima`: a cross of two species, not a hybrid
# epithet with a cultivar, so it publishes as the genus.
SPECIES_CROSSES = frozenset({("quercus", "variabilis", "acutissima")})
# The register's typos in cultivar names.
CULTIVAR_FIXES = {
    "Merrrill": "Merrill",
    "Sunbrust": "Sunburst",
    "Skyroket": "Skyrocket",
    "Princeton Upricht": "Princeton Upright",
}

_QUOTED = re.compile(r"['‘’\"]([^'‘’\"]+)['‘’\"]?")
_TRADEMARK = re.compile(r"\s*(®|\(R\)|™)\s*", re.IGNORECASE)
_EPITHET = re.compile(r"^[a-zà-ÿ][a-zà-ÿ-]*$")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def cultivar_name(words: list[str]) -> str | None:
    text = clean(" ".join(words))
    if text is None:
        return None
    quoted = _QUOTED.search(text)
    if quoted:
        # `Crimson Spire 'Crimschidt'`, `Vada 'Wanoux'`: the trade name before
        # the registered one; the quoted name is the cultivar.
        text = quoted.group(1)
    text = clean(_TRADEMARK.sub(" ", text).strip(" '‘’\""))
    if text is None:
        return None
    text = text[:1].upper() + text[1:]
    return CULTIVAR_FIXES.get(text, text)


def parse_species(value) -> tuple[str | None, str | None]:
    """(species, cultivar) from `si_essence`."""
    text = clean(value)
    if text is None or text.lower() in UNKNOWN_SPECIES:
        return None, None
    # `Styphnolobium - japonicum - Pendula`: a dash used as a separator.
    text = clean(text.replace(" - ", " "))
    words = text.split()
    genus = words[0].capitalize()
    rest = words[1:]
    hybrid = False
    if rest and rest[0].lower() in {"x", "×"}:
        hybrid = True
        rest = rest[1:]
    if not rest:
        return genus, None
    first = rest[0]
    if (genus.lower(), first.lower()) in EPITHET_IS_CULTIVAR:
        return genus, EPITHET_IS_CULTIVAR[(genus.lower(), first.lower())]
    if not _EPITHET.match(first):
        # `Malus Beauty`, `Prunus 'Umineko'`, `Ulmus Resista (R) Sapporo Gold2`.
        return genus, cultivar_name(rest)
    epithet = first
    rest = rest[1:]
    # `Platanus acerifolia x`, `Aesculus carnea x Briotii`: the mark after.
    if rest and rest[0].lower() in {"x", "×"}:
        rest = rest[1:]
        if rest and (genus.lower(), epithet.lower(), rest[0].lower()) in SPECIES_CROSSES:
            return genus, None
        hybrid = True
    if rest and rest[0].lower() in INFRASPECIFIC_RANKS:
        rest = rest[2:]
    elif rest and rest[0].lower() in UNRANKED_INFRASPECIFIC:
        rest = rest[1:]
    name = f"{genus} x {epithet.lower()}" if hybrid else f"{genus} {epithet.lower()}"
    return name, cultivar_name(rest) if rest else None


class MeasureParser:
    """A decimal-comma number, counting values that are present but unreadable."""

    def __init__(self, field: str) -> None:
        self.field = field
        self.present = 0
        self.unreadable = 0
        self.examples: set[str] = set()

    def __call__(self, value) -> float | None:
        text = clean(value)
        if text is None or text.lower() in NOT_GIVEN:
            return None
        self.present += 1
        try:
            return float(text.replace(",", ".").replace(" ", ""))
        except ValueError:
            self.unreadable += 1
            if len(self.examples) < 5:
                self.examples.add(text)
            return None

    def check(self) -> None:
        if self.present and self.unreadable / self.present > MAX_UNREADABLE_SHARE:
            raise RuntimeError(
                f"Strasbourg ingest: {self.unreadable} of {self.present} `{self.field}` "
                f"values are not numbers (e.g. {sorted(self.examples)}); the format changed"
            )
        if self.unreadable:
            print(
                f"Strasbourg OpenData: {self.unreadable} unreadable `{self.field}` values "
                f"nulled (e.g. {sorted(self.examples)})",
                file=sys.stderr,
            )


def transform(table: pa.Table) -> pa.Table:
    missing = set(FIELDS) - set(table.schema.names)
    if missing:
        raise RuntimeError(f"Strasbourg ingest: export is missing field(s) {sorted(missing)}")

    diameter_cm = MeasureParser("si_diametre_fut")
    crown_m = MeasureParser("si_largeur_couronne")
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []
    no_id = 0

    for key, essence, diameter, spread, point in zip(*(table[f].to_pylist() for f in FIELDS)):
        key = clean(key)
        if key is None:
            no_id += 1
            continue
        lon, lat = parse_wkb_point(point)
        name, selection = parse_species(essence)
        tree_id.append(f"strasbourg-{key}")
        species.append(name)
        cultivar.append(selection)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(cm_to_inches(diameter_cm(diameter)))
        crown.append(crown_m(spread))

    diameter_cm.check()
    crown_m.check()
    print(
        f"Strasbourg OpenData: {table.num_rows} rows; dropped {no_id} with no num_arbre",
        file=sys.stderr,
    )
    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["FRSXB"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.nulls(n, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


def main() -> None:
    table = transform(pq.read_table(download_parquet(SOURCE_URL)))
    table = validate_coordinates(table, city="Strasbourg", city_code="FRSXB")
    table = enforce_tree_schema(table, city="Strasbourg", data_source="STRASBOURG_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
