#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Brest's protected monuments, from the national Monuments Historiques register.

Source: `merimee.csv`, the Ministry of Culture's POP export of *Immeubles
protégés au titre des Monuments Historiques* -- the whole of France,
pipe-delimited, ~100 MB -- filtered here to the 8 communes of Brest
Métropole.  Catalogued on data.gouv.fr by the Ministère de la Culture under
the Licence Ouverte v2.0
(https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2).
It is the same file Paris, Toulouse, Bordeaux, Strasbourg and Nantes read
(`frsxb/strasbourg_landmarks.py` has the notes on its format: pipe-delimited,
and the coordinate a `"lat,lon"` string).

The metropole, not the commune: the tree inventory is Brest Métropole's (the
trees its Espaces Verts service maintains in all 8 communes), so the
landmarks are too.  A row is the metropole's when its department is 29 and
the first INSEE code in `COG_Insee_lors_de_la_protection` is one of the 8
below or, where that is blank, its indexed commune is one of their names.

Field mapping:

  Reference                          -> landmark_id (prefixed "frbes-")
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
import unicodedata
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

# The Ministry of Culture's POP bucket.  `brest_landmarks_probe.py` HEADs
# the same URL for its `Last-Modified`.
REGISTER_URL = "https://ministere-culture.s3.sbg.io.cloud.ovh.net/POP/merimee.csv"

DEPARTMENT = "29"
# Brest Métropole's communes (EPCI 242900314), INSEE code to name, from
# geo.api.gouv.fr/epcis/242900314/communes.
METROPOLE_COMMUNES = {
    "29011": "Bohars",
    "29019": "Brest",
    "29061": "Gouesnou",
    "29069": "Guilers",
    "29075": "Guipavas",
    "29189": "Plougastel-Daoulas",
    "29212": "Plouzané",
    "29235": "Le Relecq-Kerhuon",
}
BREST_INSEE = "29019"


def fold(text: str) -> str:
    """Case-, accent- and hyphen-insensitive commune name (`REZE` = `Rezé`)."""
    text = unicodedata.normalize("NFD", text.lower().replace("œ", "oe"))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).replace("-", " ")


_COMMUNE_NAMES = {fold(name) for name in METROPOLE_COMMUNES.values()}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_coordinates(value) -> tuple[float | None, float | None]:
    """`(lat, lon)` from the register's `"48.5818,7.7509"`, or `(None, None)`."""
    text = clean(value)
    if not text or "," not in text:
        return None, None
    lat_text, _, lon_text = text.partition(",")
    try:
        return float(lat_text), float(lon_text)
    except ValueError:
        return None, None


def is_metropole(record: dict) -> bool:
    if clean(record.get("Departement_format_numerique")) != DEPARTMENT:
        return False
    insee = clean(record.get("COG_Insee_lors_de_la_protection"))
    if insee:
        # Some notices list several communes ("29019 ; 29075"); the first is
        # the notice's own commune.
        return insee.replace(";", " ").replace(",", " ").split()[0] in METROPOLE_COMMUNES
    return fold(clean(record.get("Commune_forme_index")) or "") in _COMMUNE_NAMES


def read_rows() -> list[dict]:
    response = get_with_retry(REGISTER_URL, timeout=600)
    reader = csv.DictReader(
        io.StringIO(response.content.decode("utf-8-sig"), newline=""), delimiter="|"
    )
    rows: list[dict] = []
    national = 0
    unusable = 0
    for record in reader:
        national += 1
        if not is_metropole(record):
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
        f"Brest landmarks: {national} protected buildings nationally, "
        f"{len(rows)} in Brest Métropole kept, {unusable} dropped "
        f"(no reference, no title or no coordinate)",
        file=sys.stderr,
    )
    if not rows:
        # The request succeeded, so this is not an availability problem: the
        # department or INSEE columns changed, and zero rows would blank the
        # city silently.
        raise RuntimeError(
            f"the national register returned no rows for INSEE {BREST_INSEE} "
            f"and its metropole; check `COG_Insee_lors_de_la_protection` in {REGISTER_URL}"
        )
    return rows


def transform(rows: list[dict]) -> pa.Table:
    # A title shared by several designations ("Maison", "Église") is
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
            "landmark_id": pa.array([f"frbes-{r['reference']}" for r in rows], type=pa.string()),
            "city": pa.array(["FRBES"] * len(rows), type=pa.string()),
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
    table = transform(read_rows())
    table = validate_coordinates(table, city="Brest landmarks", city_code="FRBES")
    emit(table)
