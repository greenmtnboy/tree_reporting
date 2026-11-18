#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Toulouse's protected monuments, from the national Monuments Historiques register.

Source: `merimee.csv`, the Ministry of Culture's POP export of *Immeubles
protégés au titre des Monuments Historiques* -- the whole of France,
pipe-delimited, ~100 MB -- filtered here to the commune of Toulouse (INSEE
31555).  Catalogued on data.gouv.fr by the Ministère de la Culture under the
Licence Ouverte v2.0
(https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2).
It is the same file Paris reads (`frpar/paris_landmarks.py`), which has the
notes on its format: pipe-delimited, and the coordinate a `"lat,lon"` string.

Toulouse Métropole's own portal publishes `base-merimee`, but that is the
Inventaire général's survey of the city's architecture -- mostly buildings
"non protégé MH" -- not a designation register, so it is not used.

The commune, not the metropole: the tree inventory is 95% Toulouse proper,
and the designations of the other communes it touches would be a handful of
outlying pins.  A row is Toulouse's when its department is 31 and its INSEE
code (`COG_Insee_lors_de_la_protection`) is 31555 or, where that is blank,
its indexed commune is `Toulouse`.

Field mapping:

  Reference                          -> landmark_id (prefixed "frtls-")
  Titre_editorial_de_la_notice       -> name
  coordonnees_au_format_WGS84        -> geometry_raw (WKT POINT), latitude, longitude
  Adresse_forme_editoriale           -> address
  Date_et_typologie_de_la_protection -> protection_type
  Denomination_de_l_edifice          -> denomination

Rows with no reference, title or coordinate are dropped with a count.
"""

import csv
import io
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    emit,
    get_with_retry,
    make_point_wkt,
    validate_coordinates,
)

# The Ministry of Culture's POP bucket.  `toulouse_landmarks_probe.py` HEADs
# the same URL for its `Last-Modified`.
REGISTER_URL = "https://ministere-culture.s3.sbg.io.cloud.ovh.net/POP/merimee.csv"

DEPARTMENT = "31"
TOULOUSE_INSEE = "31555"
TOULOUSE_COMMUNE = "toulouse"


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_coordinates(value) -> tuple[float | None, float | None]:
    """`(lat, lon)` from the register's `"43.6008,1.4423"`, or `(None, None)`."""
    text = clean(value)
    if not text or "," not in text:
        return None, None
    lat_text, _, lon_text = text.partition(",")
    try:
        return float(lat_text), float(lon_text)
    except ValueError:
        return None, None


def is_toulouse(record: dict) -> bool:
    if clean(record.get("Departement_format_numerique")) != DEPARTMENT:
        return False
    insee = clean(record.get("COG_Insee_lors_de_la_protection"))
    if insee:
        # Some notices list several communes ("31555 ; 31069"); one is enough
        # only when it is the first, the notice's own commune.
        return insee.replace(";", " ").replace(",", " ").split()[0] == TOULOUSE_INSEE
    commune = (clean(record.get("Commune_forme_index")) or "").lower()
    return commune == TOULOUSE_COMMUNE


def read_toulouse_rows() -> list[dict]:
    response = get_with_retry(REGISTER_URL, timeout=600)
    reader = csv.DictReader(
        io.StringIO(response.content.decode("utf-8-sig"), newline=""), delimiter="|"
    )
    rows: list[dict] = []
    national = 0
    unusable = 0
    for record in reader:
        national += 1
        if not is_toulouse(record):
            continue
        reference = clean(record.get("Reference"))
        name = clean(record.get("Titre_editorial_de_la_notice"))
        latitude, longitude = parse_coordinates(record.get("coordonnees_au_format_WGS84"))
        if reference is None or name is None or latitude is None or longitude is None:
            unusable += 1
            continue
        rows.append(
            {
                "reference": reference,
                "name": name,
                "latitude": latitude,
                "longitude": longitude,
                "address": clean(record.get("Adresse_forme_editoriale")),
                "protection_type": clean(record.get("Date_et_typologie_de_la_protection")),
                "denomination": clean(record.get("Denomination_de_l_edifice")),
            }
        )
    print(
        f"Toulouse landmarks: {national} protected buildings nationally, "
        f"{len(rows)} in Toulouse kept, {unusable} dropped "
        f"(no reference, no title or no coordinate)",
        file=sys.stderr,
    )
    if not rows:
        # The request succeeded, so this is not an availability problem: the
        # department or INSEE columns changed, and zero rows would blank the
        # city silently.
        raise RuntimeError(
            f"the national register returned no rows for INSEE {TOULOUSE_INSEE}; "
            f"check `COG_Insee_lors_de_la_protection` in {REGISTER_URL}"
        )
    return rows


def transform(rows: list[dict]) -> pa.Table:
    # A title shared by several designations ("Hôtel", "Maison") is
    # disambiguated by address, as Paris does.
    titles = Counter(row["name"] for row in rows)
    names = [
        f"{row['name']} ({row['address']})"
        if titles[row["name"]] > 1 and row["address"]
        else row["name"]
        for row in rows
    ]
    return pa.table(
        {
            "landmark_id": pa.array([f"frtls-{r['reference']}" for r in rows], type=pa.string()),
            "city": pa.array(["FRTLS"] * len(rows), type=pa.string()),
            "name": pa.array(names, type=pa.string()),
            "geometry_raw": pa.array(
                [make_point_wkt(r["longitude"], r["latitude"]) for r in rows],
                type=pa.string(),
            ),
            "latitude": pa.array([r["latitude"] for r in rows], type=pa.float64()),
            "longitude": pa.array([r["longitude"] for r in rows], type=pa.float64()),
            "address": pa.array([r["address"] for r in rows], type=pa.string()),
            "protection_type": pa.array([r["protection_type"] for r in rows], type=pa.string()),
            "denomination": pa.array([r["denomination"] for r in rows], type=pa.string()),
        }
    )


if __name__ == "__main__":
    table = transform(read_toulouse_rows())
    table = validate_coordinates(table, city="Toulouse landmarks", city_code="FRTLS")
    emit(table)
