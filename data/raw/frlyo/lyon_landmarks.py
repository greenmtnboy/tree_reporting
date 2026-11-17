#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""The Métropole de Lyon's protected monuments, from the national Monuments
Historiques register.

Source: `merimee.csv`, the Ministry of Culture's POP export of *Immeubles
protégés au titre des Monuments Historiques* -- the whole of France,
pipe-delimited, ~100 MB -- the same file Paris reads (`frpar/paris_landmarks.py`
says why it is the national file and not a regional portal).  Catalogued on
data.gouv.fr under the Licence Ouverte.

**Filtered to the Métropole by INSEE commune code, not by department.**  The
Rhône (69) also covers the Beaujolais and the Monts du Lyonnais, outside the
Métropole and outside this city's territory -- Villefranche-sur-Saône alone has
22 designations.  `COG_Insee_lors_de_la_protection` is matched against the
Métropole's 59 communes, the same 59 codes the tree register's `codeinsee`
carries (Lyon as 69123 and as its arrondissements 69381-69389).  Oullins and
Pierre-Bénite merged in 2024 and both old codes are kept, since the register
records the code at the time of protection.

Field mapping, as Paris's:

  Reference                          -> landmark_id (prefixed "frlyo-")
  Titre_editorial_de_la_notice       -> name
  coordonnees_au_format_WGS84        -> geometry_raw (WKT POINT), latitude, longitude
  Commune_forme_editoriale           -> arrondissement (here the commune)
  Date_et_typologie_de_la_protection -> protection_type
  Denomination_de_l_edifice          -> denomination

A designation with no coordinate cannot be placed and is dropped with a count;
a title shared by several designations ("Immeuble", "Château") is
disambiguated by its address, or its commune when it has none.
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

# Kept in step with `lyon_landmarks_probe.py`.
REGISTER_URL = "https://ministere-culture.s3.sbg.io.cloud.ovh.net/POP/merimee.csv"

# The Métropole de Lyon's communes (INSEE codes), Lyon whole and by arrondissement.
METROPOLE_INSEE = frozenset(
    {
        "69123", "69381", "69382", "69383", "69384", "69385", "69386", "69387",
        "69388", "69389",
        "69003", "69029", "69033", "69034", "69040", "69044", "69046", "69063",
        "69068", "69069", "69071", "69072", "69081", "69085", "69087", "69088",
        "69089", "69091", "69096", "69100", "69116", "69117", "69127", "69142",
        "69143", "69149", "69152", "69153", "69163", "69168", "69191", "69194",
        "69199", "69202", "69204", "69205", "69207", "69233", "69244", "69250",
        "69256", "69259", "69260", "69266", "69271", "69273", "69275", "69276",
        "69278", "69279", "69282", "69283", "69284", "69286", "69290", "69292",
        "69293", "69296",
    }
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_coordinates(value) -> tuple[float | None, float | None]:
    """`(lat, lon)` from the register's `"45.7623,4.8271"`, or `(None, None)`."""
    text = clean(value)
    if not text or "," not in text:
        return None, None
    lat_text, _, lon_text = text.partition(",")
    try:
        return float(lat_text), float(lon_text)
    except ValueError:
        return None, None


def read_lyon_rows() -> list[dict]:
    response = get_with_retry(REGISTER_URL, timeout=600)
    reader = csv.DictReader(
        io.StringIO(response.content.decode("utf-8-sig"), newline=""), delimiter="|"
    )
    rows: list[dict] = []
    national = 0
    unusable = 0
    seen: set[str] = set()
    for record in reader:
        national += 1
        if clean(record.get("COG_Insee_lors_de_la_protection")) not in METROPOLE_INSEE:
            continue
        reference = clean(record.get("Reference"))
        name = clean(record.get("Titre_editorial_de_la_notice"))
        latitude, longitude = parse_coordinates(record.get("coordonnees_au_format_WGS84"))
        if (
            reference is None or name is None or latitude is None
            or longitude is None or reference in seen
        ):
            unusable += 1
            continue
        seen.add(reference)
        rows.append(
            {
                "reference": reference,
                "name": name,
                "latitude": latitude,
                "longitude": longitude,
                "address": clean(record.get("Adresse_forme_editoriale")),
                "commune": clean(record.get("Commune_forme_editoriale")),
                "protection_type": clean(record.get("Date_et_typologie_de_la_protection")),
                "denomination": clean(record.get("Denomination_de_l_edifice")),
            }
        )
    print(
        f"Lyon landmarks: {national} protected buildings nationally, {len(rows)} in "
        f"the Métropole kept, {unusable} dropped (no reference, title or coordinate)",
        file=sys.stderr,
    )
    if not rows:
        # The request succeeded, so this is the file changing shape (the INSEE
        # column renamed), and publishing zero rows would blank the city.
        raise RuntimeError(
            "the national register returned no rows for the Métropole de Lyon; "
            f"check `COG_Insee_lors_de_la_protection` in {REGISTER_URL}"
        )
    return rows


def transform(rows: list[dict]) -> pa.Table:
    titles = Counter(row["name"] for row in rows)

    def label(row: dict) -> str:
        if titles[row["name"]] == 1:
            return row["name"]
        where = row["address"] or row["commune"]
        return f"{row['name']} ({where})" if where else row["name"]

    return pa.table(
        {
            "landmark_id": pa.array([f"frlyo-{r['reference']}" for r in rows], type=pa.string()),
            "city": pa.array(["FRLYO"] * len(rows), type=pa.string()),
            "name": pa.array([label(r) for r in rows], type=pa.string()),
            "geometry_raw": pa.array(
                [make_point_wkt(r["longitude"], r["latitude"]) for r in rows],
                type=pa.string(),
            ),
            "latitude": pa.array([r["latitude"] for r in rows], type=pa.float64()),
            "longitude": pa.array([r["longitude"] for r in rows], type=pa.float64()),
            "arrondissement": pa.array([r["commune"] for r in rows], type=pa.string()),
            "protection_type": pa.array([r["protection_type"] for r in rows], type=pa.string()),
            "denomination": pa.array([r["denomination"] for r in rows], type=pa.string()),
        }
    )


if __name__ == "__main__":
    table = transform(read_lyon_rows())
    table = validate_coordinates(table, city="Lyon landmarks", city_code="FRLYO")
    emit(table)
