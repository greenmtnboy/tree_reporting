#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Montreal's heritage buildings, from the Repertoire du patrimoine culturel.

Source: the RPCQ on Donnees Quebec -- the province's official register of
designated heritage property, published by the Ministere de la Culture et des
Communications under CC-BY.  This is the runbook's **first** preference, an
official designation registry, and it happens to arrive as two resources rather
than one because Quebec designates at two levels:

    classes  c6c20af9  621 province-wide, 120 in Montreal  -- classified by
                                                              the minister
    cites    ba6bed2e  730 province-wide,  46 in Montreal  -- cited by the
                                                              municipality

160 landmarks for Montreal, each with a name, a point and a designation date.
Both are read and concatenated on `bien_id`, the RPCQ's own per-property key,
which is **not** disjoint between them: a property can hold both designations,
and four Montreal ones do.  The classified row wins, since classement is the
stronger status.  Two more carry no coordinates and are dropped -- 120 + 46
resource rows come to 160 landmarks, not 166.

**The two resources do not share a schema**, which is the only awkward part
here and the reason each has its own field list below.  The geometry is
`latitude`/`longitude` columns on one and a `MULTIPOINT ((lon lat))` WKT string
on the other; the status and date columns are spelled differently; and the
`usage` column loses its `_princ` suffix between them.

Read straight through the datastore with a `municipalite` filter, so this pulls
166 rows rather than 1,351 and the province's other cities cost nothing.

Quebec City reads the same two resources with its own filter.  A third Quebec
city would be the point at which this is worth sharing the way `_ckan_shared`
shares the reads underneath it.
"""

import sys
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, iter_datastore_rows
from _ingest_shared import emit, make_point_wkt

HOST = "www.donneesquebec.ca/recherche"

# The municipality as the register spells it.  Not the city code, and not the
# accent-stripped form: this is a literal `filters` match on the portal.
MUNICIPALITY = "Montréal"

CLASSIFIED = CkanResource(HOST, "c6c20af9-504f-4848-9ff2-32c463c9b04c", timeout=180)
CLASSIFIED_FIELDS = (
    "bien_id,nom_bien,Wkt_Multipoint_XY,categorie,adresse,usage_princ,"
    "date_attri_stat_jurid_princ,statut_juridique_princ"
)

CITED = CkanResource(HOST, "ba6bed2e-2b87-47fa-be28-681be1b4b649", timeout=180)
CITED_FIELDS = (
    "bien_id,nom_bien,latitude,longitude,categorie,adresse,usage,"
    "date_attribution_stat_jurid_principal_actuel,statut_juridique"
)

# The register joins several civic addresses for one property with U+00A4 (the
# currency sign), not a comma: "2084 boulevard Gouin Est¤2086 boulevard Gouin
# Est".  Only the first is kept -- the column is a label on a map pin.
ADDRESS_SEPARATOR = "¤"


def clean(value) -> str | None:
    """Collapse whitespace; the register writes a missing value as "NULL"."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    return None if text in ("", "NULL") else text


def first_address(value) -> str | None:
    text = clean(value)
    if text is None:
        return None
    return clean(text.split(ADDRESS_SEPARATOR)[0])


def parse_designation_date(value) -> date | None:
    text = clean(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def multipoint_lon_lat(value) -> tuple[float | None, float | None]:
    """`(lon, lat)` from `MULTIPOINT ((-73.5677 45.5150))`.

    `point_lon_lat` handles a plain `POINT`; a multipoint carries a second set
    of parentheses, and a designated property may list several. The first is
    taken, which for these rows is the property itself -- the rest are
    additional civic addresses on the same site.
    """
    text = clean(value)
    if text is None or "((" not in text:
        return None, None
    inner = text.split("((", 1)[1].split(")", 1)[0]
    parts = inner.replace(",", " ").split()
    if len(parts) < 2:
        return None, None
    return _as_float(parts[0]), _as_float(parts[1])


def read_classified() -> list[dict]:
    rows: list[dict] = []
    for page in iter_datastore_rows(
        CLASSIFIED, filters={"municipalite": MUNICIPALITY}, fields=CLASSIFIED_FIELDS
    ):
        for rec in page:
            lon, lat = multipoint_lon_lat(rec.get("Wkt_Multipoint_XY"))
            rows.append(
                {
                    "bien_id": rec.get("bien_id"),
                    "nom_bien": rec.get("nom_bien"),
                    "lon": lon,
                    "lat": lat,
                    "categorie": rec.get("categorie"),
                    "adresse": rec.get("adresse"),
                    "usage": rec.get("usage_princ"),
                    "date": rec.get("date_attri_stat_jurid_princ"),
                    "statut": rec.get("statut_juridique_princ"),
                }
            )
    return rows


def read_cited() -> list[dict]:
    rows: list[dict] = []
    for page in iter_datastore_rows(
        CITED, filters={"municipalite": MUNICIPALITY}, fields=CITED_FIELDS
    ):
        for rec in page:
            rows.append(
                {
                    "bien_id": rec.get("bien_id"),
                    "nom_bien": rec.get("nom_bien"),
                    "lon": _as_float(rec.get("longitude")),
                    "lat": _as_float(rec.get("latitude")),
                    "categorie": rec.get("categorie"),
                    "adresse": rec.get("adresse"),
                    "usage": rec.get("usage"),
                    "date": rec.get("date_attribution_stat_jurid_principal_actuel"),
                    "statut": rec.get("statut_juridique"),
                }
            )
    return rows


def transform(rows: list[dict], *, city_code: str, prefix: str) -> pa.Table:
    landmark_id: list[str] = []
    city: list[str] = []
    name: list[str] = []
    geometry_raw: list[str | None] = []
    address: list[str | None] = []
    category: list[str | None] = []
    property_type: list[str | None] = []
    designation_date: list[date | None] = []

    seen: set[str] = set()
    for rec in rows:
        raw_id = clean(rec.get("bien_id"))
        label = clean(rec.get("nom_bien"))
        if raw_id is None or label is None or raw_id in seen:
            continue
        wkt = make_point_wkt(rec.get("lon"), rec.get("lat"))
        if wkt is None:
            continue
        seen.add(raw_id)

        landmark_id.append(f"{prefix}-{raw_id}")
        city.append(city_code)
        name.append(label)
        geometry_raw.append(wkt)
        address.append(first_address(rec.get("adresse")))
        # "Classement" or "Citation" -- which of the two registers designated
        # it, which is the distinction the two resources encode.
        category.append(clean(rec.get("statut")))
        property_type.append(first_address(rec.get("usage")))
        designation_date.append(parse_designation_date(rec.get("date")))

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(city, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "category": pa.array(category, type=pa.string()),
            "property_type": pa.array(property_type, type=pa.string()),
            "designation_date": pa.array(designation_date, type=pa.date32()),
        }
    )


if __name__ == "__main__":
    emit(
        transform(
            read_classified() + read_cited(), city_code="CAMTL", prefix="mtl"
        )
    )
