#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""The Métropole de Lyon's street-tree register, from its GeoServer WFS.

Source: `metropole-de-lyon:abr_arbres_alignement.abrarbre` on
`data.grandlyon.com/geoserver`, catalogued as *Arbres d'alignement de la
Métropole de Lyon* -- every tree the Métropole manages on public roads and
squares across its 59 communes (Lyon's nine arrondissements, Villeurbanne,
Saint-Priest, Vénissieux, ...), **112,508 rows** when this was written, of
which about 6,200 are not trees (below).  The data.gouv.fr record of the same
layer, published by the Métropole, states Licence Ouverte v2.0.

**Keyed on `identifiant`**, the register's asset number, which was checked
unique and non-null over the whole layer (112,508 distinct values).  `gid`,
the table's primary key and the GeoServer feature id, is unique too and is
what the WFS pages are sorted by; the asset number is preferred because it is
the register's own and survives a reload of the table.

**`codegenre` says whether the row is a tree**: 2 is a tree, 0 an empty pit
(`genre` = `Emplacement libre`), 1 a stump (`Souche`).  Only 2 is kept.  A
further ~460 rows carry `codegenre` 2 and a species but `architecture` =
`Sans arbre` ("no tree"): the pit's last tree, felled, with its record left
in place.  They are dropped too, with a count.  `Non défini` (216 trees whose
species nobody recorded) is a tree and publishes as `Unknown`.

**Species is `genre` + `espece`, with `variete` as the cultivar.**  The
register writes a hybrid as the word `hybride` in the epithet: before a
named epithet it is the hybrid mark (`Quercus hybride turneri` ->
`Quercus x turneri`); on its own it means "a hybrid of this genus", so the
tree is the genus with its selection (`Ulmus hybride` 'Lobel' -> `Ulmus
'Lobel'`, as Copenhagen's `hybr.` cultivars are).  `Platanus hybride` 'clone
Vallis Clausa' is the London plane clone the register elsewhere files under
`Platanus acerifolia`, so it is filed there.  Four genera are written in an
old spelling and corrected here, because the fix is per-genus and the shared
misspelling map is per-binomial: `Pirus` (4,293 trees) is *Pyrus*,
`Albizzia` *Albizia*, `Thuya` *Thuja*, `Clerodendron` *Clerodendrum*.
Two elm epithets are nursery trade series, not species: `Ulmus resista`
(367 trees, 'Sapporo Gold', 'New Horizon') and `Ulmus lutece` (146, all
'Nanguen', sold as LUTÈCE).  They publish as the genus with the cultivar,
or with the series name when no cultivar is recorded.

**`circonference_cm` is trunk circumference in centimetres** (median 46,
95th percentile 190, max 606 -- a diameter would be absurd at those values)
and is converted to a diameter in inches.  8,102 rows record 0, most of them
pits and stumps; on a tree it means unmeasured and publishes as null.
`diametrecouronne_m` is the crown spread in whole metres (`rayoncouronne_m`
is exactly half of it on every row) and becomes `crown_width_m`, 0 as null.
`hauteurtotale_m` is a recorded height; the canonical schema has no column
for one, so it is not read.

**`dateplantation` is a date, but many are a year written as a holiday.**
Of 96,160 dated rows, 12,891 fall on 1 January, 9,412 on 14 July and 4,151 on
11 November, and two thirds of the latter two are on years ending in 0 or 5
(1980-07-14, 2000-07-14, 1950-11-11): an estimated planting year stamped on
a national holiday.  Those three days publish as 1 January of the year --
the year-only convention every other city uses -- and every other date as
written.  The year is kept: it is the register's estimate, and
`anneeplantation` agrees with it on every row.

`commune` rides the shared `borough` column.  `datereleve` (survey date) is
read by the freshness probe, not here.
"""

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

# Kept in step with `lyon_update_time.py`.
LAYER = WfsLayer(
    "https://data.grandlyon.com/geoserver/metropole-de-lyon/ows",
    "metropole-de-lyon:abr_arbres_alignement.abrarbre",
    timeout=300,
)

PROPERTIES = [
    "identifiant", "codegenre", "genre", "espece", "variete", "architecture",
    "circonference_cm", "diametrecouronne_m", "dateplantation", "commune",
    "the_geom",
]

# `codegenre`: 2 tree, 0 empty pit, 1 stump.
TREE_CODE = 2
KNOWN_CODES = {0, 1, 2}

# An estimated planting year is stamped on one of these days.
YEAR_ONLY_DAYS = {(1, 1), (7, 14), (11, 11)}

# The register's old spellings of four genera.
GENUS_SPELLINGS = {
    "Pirus": "Pyrus",
    "Albizzia": "Albizia",
    "Thuya": "Thuja",
    "Clerodendron": "Clerodendrum",
}

# Epithets that are a nursery trade series of hybrid elms, not a species.
TRADE_SERIES = {("Ulmus", "resista"): "Resista", ("Ulmus", "lutece"): "Lutèce"}

# A tree whose species nobody recorded; the shared hygiene does not read French.
UNIDENTIFIED = {"Non défini"}

# Refuse to publish when more than this share of a coded or typed field is
# unreadable: that is the register changing shape, not noise.
MAX_UNREADABLE = 0.01


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_species(genre, espece, variete) -> str | None:
    """`Genus epithet 'Cultivar'` in the form `enforce_tree_schema` splits."""
    genus = clean(genre)
    if genus is None or genus in UNIDENTIFIED:
        return None
    genus = GENUS_SPELLINGS.get(genus, genus)
    epithet = clean(espece)
    cultivar = clean(variete)
    if cultivar is not None and cultivar.lower().startswith("clone "):
        cultivar = cultivar[len("clone "):].strip() or None
    series = TRADE_SERIES.get((genus, (epithet or "").lower()))
    if series is not None:
        epithet, cultivar = None, cultivar or series
    if epithet is not None:
        words = epithet.split()
        if words[0].lower() == "hybride":
            if len(words) > 1:
                epithet = "x " + " ".join(words[1:])
            elif genus == "Platanus":
                epithet = "acerifolia"
            else:
                epithet = None
    name = genus if epithet is None else f"{genus} {epithet}"
    if cultivar is not None:
        name = f"{name} '{cultivar}'"
    return name


def parse_plant_date(value) -> tuple[date | None, bool]:
    """(the published date, whether the text was unreadable)."""
    text = clean(value)
    if text is None:
        return None, False
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        return None, True
    if (parsed.month, parsed.day) in YEAR_ONLY_DAYS:
        return date(parsed.year, 1, 1), False
    return parsed, False


def parse_number(value) -> tuple[float | None, bool]:
    if value is None or value == "":
        return None, False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, True
    return (number if number > 0 else None), False


def make_transform(seen: set[str], counts: Counter):
    def transform(features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        borough: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float] = []
        longitude: list[float] = []
        dbh: list[float | None] = []
        crown: list[float | None] = []

        for feature in features:
            counts["read"] += 1
            props = feature.get("properties") or {}
            code = props.get("codegenre")
            if code not in KNOWN_CODES:
                counts["unreadable_code"] += 1
                continue
            if code != TREE_CODE:
                counts["pit" if code == 0 else "stump"] += 1
                continue
            if clean(props.get("architecture")) == "Sans arbre":
                counts["sans_arbre"] += 1
                continue
            ident = clean(props.get("identifiant"))
            geometry = feature.get("geometry") or {}
            coords = geometry.get("coordinates") if geometry.get("type") == "Point" else None
            if ident is None or not coords:
                counts["unidentified"] += 1
                continue
            if ident in seen:
                counts["duplicate"] += 1
                continue
            seen.add(ident)

            planted, bad_date = parse_plant_date(props.get("dateplantation"))
            circumference, bad_circ = parse_number(props.get("circonference_cm"))
            spread, bad_crown = parse_number(props.get("diametrecouronne_m"))
            counts["unreadable_date"] += bad_date
            counts["unreadable_number"] += bad_circ + bad_crown

            tree_id.append(f"lyo-{ident}")
            species.append(
                parse_species(props.get("genre"), props.get("espece"), props.get("variete"))
            )
            borough.append(clean(props.get("commune")))
            plant_date.append(planted)
            longitude.append(float(coords[0]))
            latitude.append(float(coords[1]))
            dbh.append(circumference_cm_to_dbh_inches(circumference))
            crown.append(spread)

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["FRLYO"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "borough": pa.array(borough, type=pa.string()),
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
    for key in ("unreadable_code", "unreadable_date", "unreadable_number"):
        if counts[key] / read > MAX_UNREADABLE:
            raise RuntimeError(
                f"Lyon OpenData: {counts[key]} of {read} rows have {key.replace('_', ' ')} "
                f"(over {MAX_UNREADABLE:.0%}); the register has changed shape"
            )


def main() -> None:
    seen: set[str] = set()
    counts: Counter = Counter()
    table = stream_to_table(
        iter_wfs_features(LAYER, sort_by="gid", properties=PROPERTIES, page_size=20000),
        make_transform(seen, counts),
        label="Lyon OpenData",
    )
    check_readable(counts)
    print(
        f"Lyon OpenData: read {counts['read']}; dropped {counts['pit']} empty pit(s), "
        f"{counts['stump']} stump(s), {counts['sans_arbre']} felled-tree record(s) "
        f"(`Sans arbre`), {counts['unreadable_code']} unknown codegenre, "
        f"{counts['unidentified']} with no id or point, {counts['duplicate']} repeated "
        f"id(s); {counts['unreadable_date']} unreadable date(s), "
        f"{counts['unreadable_number']} unreadable measurement(s); kept {table.num_rows}",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Lyon", city_code="FRLYO")
    table = enforce_tree_schema(table, city="Lyon", data_source="LYON_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
