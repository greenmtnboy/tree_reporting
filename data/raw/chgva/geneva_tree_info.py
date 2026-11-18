#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Geneva's cantonal tree inventory (Inventaire cantonal des arbres, ICA).

Source: `SIPV_ICA_ARBRE_ISOLE` on SITG's ArcGIS server (vector.sitg.ge.ch),
catalogued at https://sitg.ge.ch/donnees/sipv-ica-arbre-isole -- "les relevés
d'arbres isolés situés hors forêts dans le canton de Genève", kept by the
Conservatoire et Jardin botaniques (Ville de Genève) for the canton and the
communes, updated weekly.  Diffusion level "A - Accès libre" (SITG open data:
private and commercial use, the source must be credited, "Données SITG").
The inventory covers the **whole canton**, public and private trees alike,
outside forests: 241,012 rows when this was written.  The sibling layer
`SIPV_ICA_WEB_PUBLIC_TOT` (the Ville de Genève's web-map copy, same 241,012
trees) carries a "sur autorisation de l'OCAN et du CJB" use restriction in its
catalogue record; this layer carries none, which is why it is the one read.

**Keyed on `ID_ARBRE`**, "identifiant unique et permanent de l'arbre" in the
catalogue.  Checked over the whole layer: never null, but 241,006 distinct
values over 241,012 rows -- three ids are shared by two different trees and
one by four copies of the same tree, all entered in 2026.  Every row of a
repeated id is dropped with a count (an arbitrary pick could swap a tree's
species between builds), and a build where more than 0.1% of rows repeat is
refused.  `OBJECTID` is a row number the catalogue says not to use as an id.

**Rows read and rows kept.**

* `STATUT = 'Historique'` (96,518 rows, 40% of the layer) are the 1976 paper
  inventory digitised, never resurveyed: positioned to "environ 25 mètres"
  by the publisher's own account, nearly all named to the genus only, with
  no measurement.  They say a tree stood near there fifty years ago, which is
  not what a dot on this map means, and at 25 m they would neither dedup
  against OSM nor sit on the right side of a street.  Dropped, with a count.
  `Relevé` (a recent field survey, ~1 m), `Positionné` and `Nommé` are kept.
* `SOUCHE = 'Oui'` is a stump; dropped.  `STADE_DEVELOPPEMENT = 'Mort'` is a
  dead tree, and stays, as the shared rule keeps dead trees.

**Species is `NOM_COMPLET`**, a full botanical name with rank qualifiers and
the cultivar in single quotes (`Quercus robur subsp. robur 'Fastigiata'`);
the shared hygiene truncates to species rank and moves the cultivar.  The
register glues the hybrid sign to the next word (`Tilia ×europaea`,
`×Cupressocyparis leylandii`), which the hygiene would read as one token, so
it is spaced out here; a bare `×Ulmus` (a hybrid elm cultivar such as
'Resista') is the genus.  Genus-only names (`Betula`) publish as the genus.

**Diameter**: `DIAMETRE_1M`, the trunk diameter at 1 m in centimetres
(median 28, 99th percentile 140), else `CIRCONFERENCE_1M` (cm) / pi; on the
99k rows that have both their ratio is pi to within 3%.  Zero means not
measured and is left to the shared rule.  `DIAMETRE_COURONNE` is the crown
diameter in metres (median 6), published as `crown_width_m`; zero is null.
`HAUTEUR_TOTALE` is recorded but the schema has no height column.

**Planting date**: `DATE_PLANTATION`, a date, flagged estimated by
`DATE_PLANTATION_ESTIMEE` on most rows.  The estimates are age classes a
surveyor wrote down relative to the survey (2012 - 50 = 1962-01-01 on 7,000
rows, 2012 - 10 = 2002-01-01 on 6,700, 2012 - 100 = 1912), and their median
diameters rise with the class -- the register's stated estimate, not a
placeholder, so it is kept.  Dates before 1500 or in the future are nulled by
the shared schema.

Coordinates: the layer is stored in CH1903+/LV95 (EPSG:2056); the server
reprojects to WGS84 with its datum transformation (checked against the
swisstopo approximation to within 2 m).
"""

import math
import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.arcgis import (
    FeatureLayer,
    esri_ms_to_date,
    esri_point,
    feature_count,
    iter_features,
)

SOURCE_URL = "https://vector.sitg.ge.ch/arcgis/rest/services/SIPV_ICA_ARBRE_ISOLE/FeatureServer/0"
LAYER = FeatureLayer(SOURCE_URL, timeout=300)

OUT_FIELDS = ",".join(
    [
        "OBJECTID",
        "ID_ARBRE",
        "NOM_COMPLET",
        "STATUT",
        "SOUCHE",
        "DIAMETRE_1M",
        "CIRCONFERENCE_1M",
        "DIAMETRE_COURONNE",
        "DATE_PLANTATION",
    ]
)

# The 1976 inventory, positioned to ~25 m; see the module docstring.
DROPPED_STATUSES = frozenset({"Historique"})
KNOWN_STATUSES = frozenset({"Historique", "Relevé", "Positionné", "Nommé"})
STUMP = "Oui"

# A build where more rows than this share an id is refused, not deduplicated.
MAX_REPEATED_ID_SHARE = 0.001

_HYBRID_GLUED = re.compile(r"×\s*")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def tree_number(value) -> str | None:
    """`ID_ARBRE` (a double on the wire) as an integer string."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not number.is_integer() or number <= 0:
        return None
    return str(int(number))


def parse_species(value) -> str | None:
    """`NOM_COMPLET` with the hybrid sign spaced out, as the shared hygiene reads it."""
    text = clean(value)
    if text is None:
        return None
    text = clean(_HYBRID_GLUED.sub("x ", text))
    tokens = text.split()
    # `×Ulmus` alone, or `×Ulmus 'Resista'`: a hybrid cultivar of the genus.
    if tokens[0] == "x" and (len(tokens) == 2 or tokens[2].startswith("'")):
        return " ".join(tokens[1:])
    return text


def positive(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def dbh_inches(diameter_cm, circumference_cm) -> float | None:
    diameter = positive(diameter_cm)
    if diameter is not None:
        return diameter / 2.54
    circumference = positive(circumference_cm)
    if circumference is not None:
        return circumference / (math.pi * 2.54)
    return None


def read_layer(counts: Counter) -> list[dict]:
    """Every kept feature, as a slim dict; ids are checked over the whole layer."""
    kept: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    ):
        for feature in page:
            counts["read"] += 1
            attrs = feature.get("attributes") or {}
            status = clean(attrs.get("STATUT"))
            if status not in KNOWN_STATUSES:
                raise RuntimeError(
                    f"Geneva SITG: unknown STATUT {status!r}; the inventory changed its codes"
                )
            if status in DROPPED_STATUSES:
                counts["historique"] += 1
                continue
            if clean(attrs.get("SOUCHE")) == STUMP:
                counts["stump"] += 1
                continue
            number = tree_number(attrs.get("ID_ARBRE"))
            latitude, longitude = esri_point(feature.get("geometry"))
            if number is None or latitude is None or longitude is None:
                counts["unusable"] += 1
                continue
            kept.append(
                {
                    "number": number,
                    "species": parse_species(attrs.get("NOM_COMPLET")),
                    "plant_date": esri_ms_to_date(attrs.get("DATE_PLANTATION")),
                    "latitude": latitude,
                    "longitude": longitude,
                    "dbh": dbh_inches(attrs.get("DIAMETRE_1M"), attrs.get("CIRCONFERENCE_1M")),
                    "crown": positive(attrs.get("DIAMETRE_COURONNE")),
                }
            )
    return kept


def drop_repeated_ids(rows: list[dict], counts: Counter) -> list[dict]:
    seen = Counter(row["number"] for row in rows)
    repeated = {number for number, n in seen.items() if n > 1}
    if not repeated:
        return rows
    dropped = sum(seen[number] for number in repeated)
    if dropped > MAX_REPEATED_ID_SHARE * len(rows):
        raise RuntimeError(
            f"Geneva SITG: {dropped} row(s) share {len(repeated)} ID_ARBRE value(s) "
            f"(>{MAX_REPEATED_ID_SHARE:.1%} of {len(rows)}); ID_ARBRE is no longer the grain"
        )
    counts["repeated_id"] += dropped
    return [row for row in rows if row["number"] not in repeated]


def chunks(rows: list[dict], size: int = 50000):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def transform(rows: list[dict]) -> pa.Table:
    n = len(rows)
    return pa.table(
        {
            "tree_id": pa.array([f"geneva-{r['number']}" for r in rows], type=pa.string()),
            "city": pa.array(["CHGVA"] * n, type=pa.string()),
            "species": pa.array([r["species"] for r in rows], type=pa.string()),
            "plant_date": pa.array([r["plant_date"] for r in rows], type=pa.date32()),
            "latitude": pa.array([r["latitude"] for r in rows], type=pa.float64()),
            "longitude": pa.array([r["longitude"] for r in rows], type=pa.float64()),
            "diameter_at_breast_height": pa.array([r["dbh"] for r in rows], type=pa.float64()),
            "crown_width_m": pa.array([r["crown"] for r in rows], type=pa.float64()),
        }
    )


def main() -> None:
    expected = feature_count(LAYER)
    counts: Counter = Counter()
    rows = read_layer(counts)
    if counts["read"] != expected:
        raise RuntimeError(
            f"Geneva SITG: read {counts['read']} feature(s) but the layer reports "
            f"{expected}; refusing to publish a partial inventory"
        )
    rows = drop_repeated_ids(rows, counts)
    table = stream_to_table(chunks(rows), transform, label="Geneva SITG")
    print(
        f"Geneva SITG: read {counts['read']} of {expected}; dropped "
        f"{counts['historique']} 1976-inventory (Historique) row(s), "
        f"{counts['stump']} stump(s), {counts['unusable']} row(s) with no id or "
        f"no point, {counts['repeated_id']} row(s) sharing an ID_ARBRE; "
        f"leaving {table.num_rows}",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Geneva", city_code="CHGVA")
    table = enforce_tree_schema(table, city="Geneva", data_source="GENEVA_SITG")
    emit(table)


if __name__ == "__main__":
    main()
