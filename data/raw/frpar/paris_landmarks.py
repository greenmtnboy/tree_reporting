#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Paris's protected monuments, from the national Monuments Historiques register.

Source: `merimee.csv`, the Ministry of Culture's POP export of *Immeubles
protégés au titre des Monuments Historiques* -- the whole of France,
pipe-delimited, ~100 MB, filtered here to department 75. Catalogued on
data.gouv.fr under the Licence Ouverte.

**This replaced data.iledefrance.fr, which closed its API.** The Île-de-France
portal was republishing this same national register filtered to dept 75, and on
2026-09-12 it began answering 200 on the dataset's metadata and **403 on
`/records` and every `/exports/*`**, with `export` gone from the dataset's own
`features` list -- the publisher withdrew the data API, not a rate limit and not
a User-Agent check (four were tried). Because a landmark script's failure used
to abort the cross-city union, that one portal took the whole landmark lane down;
`full_landmark_publish.preql` is the structural half of the fix and this file is
the other.

**The ids do not churn.** `Reference` here is the same Mérimée reference the
Île-de-France extract published (`PA00085796`), so every `frpar-PA…` landmark_id
is preserved and any check-in recorded against one still resolves. The register
carries 1,893 Paris rows where the old filtered export had 1,885; the difference
is designations added since.

Field mapping, unchanged from the old source but for the column spellings (the
national export is Capitalised_With_Underscores where the portal's API was
lowercase):

  Reference                          -> landmark_id (prefixed "frpar-")
  Titre_editorial_de_la_notice       -> name
  coordonnees_au_format_WGS84        -> geometry_raw (WKT POINT), latitude, longitude
  Commune_forme_editoriale           -> arrondissement
  Date_et_typologie_de_la_protection -> protection_type
  Denomination_de_l_edifice          -> denomination

Two details of the national file that the filtered one hid:

**It is pipe-delimited**, not comma or semicolon, across 78 columns -- several of
which are free prose containing commas and semicolons, which is presumably why.
`csv.DictReader(delimiter="|")` and nothing cleverer.

**The coordinate is a `"lat,lon"` string**, not the WKB point the parquet export
served, so it is split here rather than read with `parse_wkb_point`. Three of the
1,893 Paris rows carry no coordinate and are dropped with a count: a landmark
with no position cannot be placed on the map, and keeping it would publish a
null-geometry row.
"""

import csv
import io
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    emit,
    get_with_retry,
    make_point_wkt,
    validate_coordinates,
)

# The Ministry of Culture's POP bucket. `paris_landmarks_probe.py` HEADs the
# same URL for its `Last-Modified`, which is the register's publication time.
REGISTER_URL = "https://ministere-culture.s3.sbg.io.cloud.ovh.net/POP/merimee.csv"

# `Departement_format_numerique` for Paris.
PARIS_DEPARTMENT = "75"


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_coordinates(value) -> tuple[float | None, float | None]:
    """`(lat, lon)` from the register's `"48.8595,2.3413"`, or `(None, None)`."""
    text = clean(value)
    if not text or "," not in text:
        return None, None
    lat_text, _, lon_text = text.partition(",")
    try:
        return float(lat_text), float(lon_text)
    except ValueError:
        return None, None


def read_paris_rows() -> list[dict]:
    response = get_with_retry(REGISTER_URL, timeout=600)
    reader = csv.DictReader(
        io.StringIO(response.content.decode("utf-8-sig"), newline=""), delimiter="|"
    )
    rows: list[dict] = []
    national = 0
    unusable = 0
    for record in reader:
        national += 1
        if clean(record.get("Departement_format_numerique")) != PARIS_DEPARTMENT:
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
                "arrondissement": clean(record.get("Commune_forme_editoriale")),
                "protection_type": clean(record.get("Date_et_typologie_de_la_protection")),
                "denomination": clean(record.get("Denomination_de_l_edifice")),
            }
        )
    print(
        f"Paris landmarks: {national} protected buildings nationally, "
        f"{len(rows)} in department {PARIS_DEPARTMENT} kept, {unusable} dropped "
        f"(no reference, no title or no coordinate)",
        file=sys.stderr,
    )
    if not rows:
        # Not an availability problem -- the request succeeded. Either the
        # department column changed spelling or the register stopped covering
        # Paris, and publishing zero rows would blank the city silently.
        raise RuntimeError(
            "the national register returned no rows for department "
            f"{PARIS_DEPARTMENT}; check `Departement_format_numerique` in {REGISTER_URL}"
        )
    return rows


def transform(rows: list[dict]) -> pa.Table:
    # A title shared by several designations is disambiguated by address, as the
    # old source's output was -- "Immeuble" alone names dozens of buildings.
    titles = Counter(row["name"] for row in rows)
    names = [
        f"{row['name']} ({row['address']})"
        if titles[row["name"]] > 1 and row["address"]
        else row["name"]
        for row in rows
    ]
    return pa.table(
        {
            "landmark_id": pa.array(
                [f"frpar-{r['reference']}" for r in rows], type=pa.string()
            ),
            "city": pa.array(["FRPAR"] * len(rows), type=pa.string()),
            "name": pa.array(names, type=pa.string()),
            "geometry_raw": pa.array(
                [make_point_wkt(r["longitude"], r["latitude"]) for r in rows],
                type=pa.string(),
            ),
            "latitude": pa.array([r["latitude"] for r in rows], type=pa.float64()),
            "longitude": pa.array([r["longitude"] for r in rows], type=pa.float64()),
            "arrondissement": pa.array(
                [r["arrondissement"] for r in rows], type=pa.string()
            ),
            "protection_type": pa.array(
                [r["protection_type"] for r in rows], type=pa.string()
            ),
            "denomination": pa.array([r["denomination"] for r in rows], type=pa.string()),
        }
    )


if __name__ == "__main__":
    table = transform(read_paris_rows())
    # `city_code` is what turns on the bounding-box filter, and the old script
    # did not pass it: the register carries one Paris designation at exactly
    # (48.0, 2.0), a placeholder coordinate 90 km south-west of the city, and
    # the published parquet has been placing the Temple du Foyer de l'âme there
    # ever since. One row, dropped with a logged count.
    table = validate_coordinates(table, city="Paris landmarks", city_code="FRPAR")
    emit(table)
