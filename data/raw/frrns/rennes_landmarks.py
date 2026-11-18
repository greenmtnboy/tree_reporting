#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Rennes Métropole's protected monuments, from the national Monuments Historiques register.

Source: `merimee.csv`, the Ministry of Culture's POP export of *Immeubles
protégés au titre des Monuments Historiques* -- the whole of France,
pipe-delimited, ~100 MB -- filtered here to the 43 communes of Rennes
Métropole in Ille-et-Vilaine (department 35).  Catalogued on data.gouv.fr by
the Ministère de la Culture under the Licence Ouverte v2.0
(https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2).
It is the file Paris and Toulouse read (`frpar/paris_landmarks.py`,
`frtls/toulouse_landmarks.py`), which have the notes on its format.

The metropole, not the commune: the tree register covers all 43 communes
(Rennes is half of it), so the designations of the whole territory are the
city's landmarks.  A row is the metropole's when its department is 35 and the
first INSEE code of `COG_Insee_lors_de_la_protection` is one of the 43 or,
where that is blank, its indexed commune is one of their names.

Field mapping:

  Reference                          -> landmark_id (prefixed "frrns-")
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

# The Ministry of Culture's POP bucket.  `rennes_landmarks_probe.py` HEADs
# the same URL for its `Last-Modified`.
REGISTER_URL = "https://ministere-culture.s3.sbg.io.cloud.ovh.net/POP/merimee.csv"

DEPARTMENT = "35"
# The 43 communes of Rennes Métropole (EPCI 243500139), every one of which
# appears in the tree register.
METROPOLE_COMMUNES = {
    "35001": "Acigné",
    "35022": "Bécherel",
    "35024": "Betton",
    "35032": "Bourgbarré",
    "35039": "Brécé",
    "35047": "Bruz",
    "35051": "Cesson-Sévigné",
    "35055": "Chantepie",
    "35058": "La Chapelle-Chaussée",
    "35059": "La Chapelle-des-Fougeretz",
    "35065": "La Chapelle-Thouarault",
    "35066": "Chartres-de-Bretagne",
    "35076": "Chavagne",
    "35079": "Chevaigné",
    "35080": "Cintré",
    "35081": "Clayes",
    "35088": "Corps-Nuds",
    "35120": "Gévezé",
    "35131": "L'Hermitage",
    "35139": "Laillé",
    "35144": "Langan",
    "35180": "Miniac-sous-Bécherel",
    "35189": "Montgermont",
    "35196": "Mordelles",
    "35204": "Nouvoitou",
    "35206": "Noyal-Châtillon-sur-Seiche",
    "35208": "Orgères",
    "35210": "Pacé",
    "35216": "Parthenay-de-Bretagne",
    "35238": "Rennes",
    "35240": "Le Rheu",
    "35245": "Romillé",
    "35250": "Saint-Armel",
    "35266": "Saint-Erblon",
    "35275": "Saint-Gilles",
    "35278": "Saint-Grégoire",
    "35281": "Saint-Jacques-de-la-Lande",
    "35315": "Saint-Sulpice-la-Forêt",
    "35334": "Thorigné-Fouillard",
    "35351": "Le Verger",
    "35352": "Vern-sur-Seiche",
    "35353": "Vezin-le-Coquet",
    "35363": "Pont-Péan",
}


def commune_key(value: str | None) -> str:
    """`Saint-Grégoire` and `saint gregoire` compare equal."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().replace("-", " ").replace("'", " ").split())


COMMUNE_KEYS = {commune_key(name) for name in METROPOLE_COMMUNES.values()}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_coordinates(value) -> tuple[float | None, float | None]:
    """`(lat, lon)` from the register's `"48.1113,-1.6800"`, or `(None, None)`."""
    text = clean(value)
    if not text or "," not in text:
        return None, None
    lat_text, _, lon_text = text.partition(",")
    try:
        return float(lat_text), float(lon_text)
    except ValueError:
        return None, None


def in_metropole(record: dict) -> bool:
    if clean(record.get("Departement_format_numerique")) != DEPARTMENT:
        return False
    insee = clean(record.get("COG_Insee_lors_de_la_protection"))
    if insee:
        # Some notices list several communes ("35238 ; 35051"); the first is
        # the notice's own commune.
        return insee.replace(";", " ").replace(",", " ").split()[0] in METROPOLE_COMMUNES
    return commune_key(clean(record.get("Commune_forme_index"))) in COMMUNE_KEYS


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
        if not in_metropole(record):
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
        f"Rennes landmarks: {national} protected buildings nationally, "
        f"{len(rows)} in Rennes Métropole kept, {unusable} dropped "
        f"(no reference, no title or no coordinate)",
        file=sys.stderr,
    )
    if not rows:
        # The request succeeded, so this is not an availability problem: the
        # department or INSEE columns changed, and zero rows would blank the
        # city silently.
        raise RuntimeError(
            "the national register returned no rows for Rennes Métropole's INSEE "
            f"codes; check `COG_Insee_lors_de_la_protection` in {REGISTER_URL}"
        )
    return rows


def transform(rows: list[dict]) -> pa.Table:
    # A title shared by several designations ("Maison", "Hôtel") is
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
            "landmark_id": pa.array([f"frrns-{r['reference']}" for r in rows], type=pa.string()),
            "city": pa.array(["FRRNS"] * len(rows), type=pa.string()),
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
    table = validate_coordinates(table, city="Rennes landmarks", city_code="FRRNS")
    emit(table)
