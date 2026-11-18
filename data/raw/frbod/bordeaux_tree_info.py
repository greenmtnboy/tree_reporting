#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Bordeaux's municipal tree inventory ("Patrimoine arboré de Bordeaux Métropole").

Source: OpenDataSoft dataset `ec_arbre_p` on Bordeaux Métropole's open-data
portal (https://opendata.bordeaux-metropole.fr/explore/dataset/ec_arbre_p/),
published by Bordeaux Métropole (Direction des Espaces Verts) under the
Licence Ouverte -- the dataset's `license` field says "Licence Ouverte" and
links Etalab's October 2011 text, version 1.0.  **85,306 rows** when this was
written, refreshed daily ("Quotidienne").  The parquet export returns the
whole layer as one ~6 MB file; the records API caps its offset at 10,000 and
is not used.

**Coverage is not the whole Métropole.**  The catalogue says the layer holds
the trees of the communes "ayant mutualisé la compétence espaces verts" --
the ones that pooled green-space management with the Métropole -- and that
completeness is "en cours".  18 of the 28 communes have rows: Bordeaux (41k),
Mérignac (14.6k), Bègles, Floirac, Cenon, Bruges, Carbon-Blanc, Ambarès,
Lormont, Bassens, Le Bouscat and a few hundred or fewer in the others; Pessac,
Talence (9 rows), Villenave-d'Ornon, Gradignan (146) and the western communes
are barely present.  705 rows sit in **Arcachon** (INSEE 33009), 50 km away
and not a member commune; they fall outside the city's territory box and
`validate_coordinates` drops them with its count.

**Keyed on `gid`**, the layer's primary key ("Clé primaire"), verified
non-null and unique over all 85,306 rows (1,003 to 241,477 with gaps).
Published as `bordeaux-12345`.

**Rows kept.**  `statut` is VIVANT (alive, 79.3k), MORT (dead, 889), SOUCHE
(stump, 5.1k) or PROJET (a planned planting, 28).  Stumps and planned sites
are not trees and are dropped with a count; a dead tree is a tree standing at
that spot and stays, as the shared rule keeps dead trees.  An unknown statut
raises: the portal's coded list changed.

**Species is `espece`**, a full botanical name with the cultivar quoted:
`Pyrus calleryana 'Chanticleer'`, `Malus 'Evereste'`, `Quercus sp.`.  The
shared hygiene does the rank truncation (`subsp.`, `var.`, `f.`), drops the
synonym notes (`Betula pendula (Syn. Betula verrucosa)`) and resolves the
synonyms it knows.  It is split here first because the register's cultivars
carry apostrophes (`'Westhof's Glorie'`, 612 rows; `'Prune d'Ente'`), which
the shared quote parser would cut at the first one.  Parentheticals go first
(`'Autumn Blaze' ('Jeffersred')`, `'Fastigiata' (Syn. 'Pyramidalis')`,
`'Nanguen' (Lutèce)`), then the name is what precedes the first quote and the
cultivar what lies between it and the last.  Otherwise:

  * `Indéterminé` (3.8k) is unknown and published as `Unknown`;
  * `Cedrus libani subsp. Atlantica` is the Atlas cedar, `Cedrus atlantica`
    (the shared truncation would make it the cedar of Lebanon);
  * a hybrid formula (`Lagerstroemia indica x l. fauriei 'Natchez'`,
    `Crataegus laevigata x media 'Paul's Scarlet'`) is the genus with its
    cultivar;
  * two one-offs are written out in `SPECIAL_NAMES`.

`nom_francais` is the French common name and is not used.

**Diameter**: `diametre_tronc`, the trunk diameter at 1.30 m in centimetres
(median 20, 99th percentile 102), else `circonference_tronc` (cm, at 1.30 m)
/ pi; on the 63.9k rows that carry both their ratio is pi throughout.  Zero
(107 rows) is left to the shared rule.  `diametre_souche` is the stump's
diameter, recorded mostly on SOUCHE rows, and is not a trunk measure.
`diametre_couronne` is the crown diameter in metres (median 7), published as
`crown_width_m`; zero is null.  `hauteur` (total height, m) is recorded but
the schema has no height column.

**Planting date**: `date_plantation`, a real date on 19.6k rows, mostly
2002-2026 with a 1920 cohort and a 1781 heritage tree.  Many are the first of
a month (planting seasons, written to the month); no single stamp dominates
(the largest, 2011-01-01, is 4.5%), so none is treated as a placeholder.
"""

import re
import sys
from collections import Counter
from datetime import date
from math import pi
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    download_parquet,
    emit,
    enforce_tree_schema,
    parse_wkb_point,
    validate_coordinates,
)

DATASET = "ec_arbre_p"
FIELDS = [
    "gid",
    "statut",
    "espece",
    "date_plantation",
    "diametre_tronc",
    "circonference_tronc",
    "diametre_couronne",
    "geo_point_2d",
]
SOURCE_URL = (
    f"https://opendata.bordeaux-metropole.fr/api/explore/v2.1/catalog/datasets/{DATASET}"
    f"/exports/parquet?select={','.join(FIELDS)}&timezone=UTC"
)

KEPT_STATUSES = frozenset({"VIVANT", "MORT"})
DROPPED_STATUSES = frozenset({"SOUCHE", "PROJET"})

UNKNOWN_NAMES = frozenset({"indéterminé", "indetermine", "inconnu"})

# Whole values the generic split cannot read.
SPECIAL_NAMES: dict[str, tuple[str, str | None]] = {
    # Prunus 'Pandora' is P. subhirtella 'Rosea' x P. x yedoensis.
    "Prunus subhirtella 'Rosea' x yedoensis Pandora": ("Prunus", "Pandora"),
    # Quercus x warei 'Nadler', sold as Kindred Spirit.
    "Quercus × warei Kindered Spirit ('Nadler')": ("Quercus x warei", "Kindred Spirit"),
}

# A subspecies the species key should not fold into its parent.
SUBSPECIES_AS_SPECIES = {
    "cedrus libani subsp. atlantica": "Cedrus atlantica",
}

_QUOTES = "'‘’\""
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")
# `Genus epithet x l. fauriei`, `Genus epithet x media`: a formula, not a name.
_HYBRID_FORMULA = re.compile(r"^([A-Z][a-z]+) [a-z-]+ [x×] \S")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_species(value) -> tuple[str | None, str | None]:
    """(species string for the shared hygiene, cultivar)."""
    text = clean(value)
    if text is None or text.lower() in UNKNOWN_NAMES:
        return None, None
    if text in SPECIAL_NAMES:
        return SPECIAL_NAMES[text]
    text = clean(_PARENTHETICAL.sub("", text)) or ""
    starts = [text.find(q) for q in _QUOTES if q in text]
    if starts:
        start = min(starts)
        name = text[:start].strip()
        cultivar = clean(text[start + 1:].strip().strip(_QUOTES))
    else:
        name, cultivar = text, None
    name = SUBSPECIES_AS_SPECIES.get(name.lower(), name)
    formula = _HYBRID_FORMULA.match(name)
    if formula:
        name = formula.group(1)
    return name or None, cultivar


def parse_dbh(diameter, circumference) -> float | None:
    """Inches, from the diameter at 1.30 m (cm) or else the circumference (cm)."""
    if diameter is not None:
        return float(diameter) / 2.54
    if circumference is not None:
        return float(circumference) / (pi * 2.54)
    return None


def transform(table: pa.Table) -> pa.Table:
    missing = set(FIELDS) - set(table.schema.names)
    if missing:
        raise RuntimeError(f"Bordeaux ingest: export is missing field(s) {sorted(missing)}")

    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []
    no_id = 0
    dropped: Counter[str] = Counter()

    for key, status, espece, planted, diameter, circumference, crown_m, point in zip(
        *(table[f].to_pylist() for f in FIELDS)
    ):
        if key is None:
            no_id += 1
            continue
        status = clean(status)
        if status in DROPPED_STATUSES:
            dropped[status] += 1
            continue
        if status not in KEPT_STATUSES:
            raise RuntimeError(
                f"Bordeaux ingest: unknown statut {status!r} on gid {key}; "
                "the portal's coded list changed"
            )
        lon, lat = parse_wkb_point(point)
        name, selection = parse_species(espece)
        tree_id.append(f"bordeaux-{key}")
        species.append(name)
        cultivar.append(selection)
        plant_date.append(planted)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(parse_dbh(diameter, circumference))
        crown.append(float(crown_m) if crown_m else None)

    print(
        f"Bordeaux OpenData: {table.num_rows} rows; dropped {no_id} with no gid, "
        f"{dropped['SOUCHE']} stumps (SOUCHE), {dropped['PROJET']} planned plantings "
        f"(PROJET)",
        file=sys.stderr,
    )
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["FRBOD"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


def main() -> None:
    table = transform(pq.read_table(download_parquet(SOURCE_URL)))
    table = validate_coordinates(table, city="Bordeaux", city_code="FRBOD")
    table = enforce_tree_schema(table, city="Bordeaux", data_source="BORDEAUX_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
