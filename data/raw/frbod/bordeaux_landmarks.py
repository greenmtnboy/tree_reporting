#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Bordeaux's protected monuments, from the national Monuments Historiques register.

Source: `merimee.csv`, the Ministry of Culture's POP export of *Immeubles
protégés au titre des Monuments Historiques* -- the whole of France,
pipe-delimited, ~100 MB -- filtered here to the 28 communes of Bordeaux
Métropole (Gironde, department 33).  Catalogued on data.gouv.fr by the
Ministère de la Culture under the Licence Ouverte v2.0
(https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2).
It is the same file Paris and Toulouse read (`frpar/paris_landmarks.py`,
`frtls/toulouse_landmarks.py`), which have the notes on its format:
pipe-delimited, and the coordinate a `"lat,lon"` string.

The Métropole, not the commune: the tree inventory is the Métropole's, and
half of it lies outside Bordeaux proper (Mérignac, Bègles, Floirac, Cenon,
Bruges, ...).  A row is the Métropole's when its department is 33 and the
first INSEE code of `COG_Insee_lors_de_la_protection` is a member commune or,
where that is blank, its indexed commune (`Commune_forme_index`) is one by
name.  Arcachon, which the tree layer's coded list mentions, is not a member
and is not included.

Field mapping:

  Reference                          -> landmark_id (prefixed "frbod-")
  Titre_editorial_de_la_notice       -> name
  coordonnees_au_format_WGS84        -> geometry_raw (WKT POINT), latitude, longitude
  Adresse_forme_editoriale           -> address
  Date_et_typologie_de_la_protection -> protection_type
  Denomination_de_l_edifice          -> denomination

Rows with no reference, title or coordinate are dropped with a count; a
coordinate whose longitude is zero is a stub, not a position (62 notices).
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

# The Ministry of Culture's POP bucket.  `bordeaux_landmarks_probe.py` HEADs
# the same URL for its `Last-Modified`.
REGISTER_URL = "https://ministere-culture.s3.sbg.io.cloud.ovh.net/POP/merimee.csv"

DEPARTMENT = "33"
# Bordeaux Métropole's 28 member communes, INSEE code -> name.
METROPOLE_COMMUNES = {
    "33003": "Ambarès-et-Lagrave",
    "33004": "Ambès",
    "33013": "Artigues-près-Bordeaux",
    "33032": "Bassens",
    "33039": "Bègles",
    "33056": "Blanquefort",
    "33063": "Bordeaux",
    "33065": "Bouliac",
    "33069": "Le Bouscat",
    "33075": "Bruges",
    "33096": "Carbon-Blanc",
    "33119": "Cenon",
    "33162": "Eysines",
    "33167": "Floirac",
    "33192": "Gradignan",
    "33200": "Le Haillan",
    "33249": "Lormont",
    "33273": "Martignas-sur-Jalle",
    "33281": "Mérignac",
    "33312": "Parempuyre",
    "33318": "Pessac",
    "33376": "Saint-Aubin-de-Médoc",
    "33434": "Saint-Louis-de-Montferrand",
    "33449": "Saint-Médard-en-Jalles",
    "33487": "Saint-Vincent-de-Paul",
    "33519": "Le Taillan-Médoc",
    "33522": "Talence",
    "33550": "Villenave-d'Ornon",
}


def commune_key(name: str) -> str:
    """Accent-, case- and punctuation-insensitive commune name."""
    text = unicodedata.normalize("NFD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return "".join(ch for ch in text if ch.isalnum())


METROPOLE_NAMES = frozenset(commune_key(n) for n in METROPOLE_COMMUNES.values())


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_coordinates(value) -> tuple[float | None, float | None]:
    """`(lat, lon)` from the register's `"44.8412,-0.5800"`, or `(None, None)`."""
    text = clean(value)
    if not text or "," not in text:
        return None, None
    lat_text, _, lon_text = text.partition(",")
    try:
        latitude, longitude = float(lat_text), float(lon_text)
    except ValueError:
        return None, None
    # 62 Bordeaux notices (place de la Bourse, the Cité Frugès in Pessac, ...)
    # carry a stub coordinate with the longitude zeroed ("44.0,0.0",
    # "44.841031,0.0"), 45 km east of the city.  Gironde lies wholly west of
    # the Greenwich meridian, so a zero longitude is no coordinate.
    if longitude == 0:
        return None, None
    return latitude, longitude


def is_metropole(record: dict) -> bool:
    if clean(record.get("Departement_format_numerique")) != DEPARTMENT:
        return False
    insee = clean(record.get("COG_Insee_lors_de_la_protection"))
    if insee:
        # Some notices list several communes ("33063 ; 33039"); the first is
        # the notice's own commune.
        return insee.replace(";", " ").replace(",", " ").split()[0] in METROPOLE_COMMUNES
    commune = clean(record.get("Commune_forme_index"))
    return commune is not None and commune_key(commune) in METROPOLE_NAMES


def read_metropole_rows() -> list[dict]:
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
        f"Bordeaux landmarks: {national} protected buildings nationally, "
        f"{len(rows)} in Bordeaux Métropole kept, {unusable} dropped "
        f"(no reference, no title or no coordinate)",
        file=sys.stderr,
    )
    if not rows:
        # The request succeeded, so this is not an availability problem: the
        # department or INSEE columns changed, and zero rows would blank the
        # city silently.
        raise RuntimeError(
            "the national register returned no rows for Bordeaux Métropole's communes; "
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
            "landmark_id": pa.array([f"frbod-{r['reference']}" for r in rows], type=pa.string()),
            "city": pa.array(["FRBOD"] * len(rows), type=pa.string()),
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
    table = transform(read_metropole_rows())
    table = validate_coordinates(table, city="Bordeaux landmarks", city_code="FRBOD")
    emit(table)
