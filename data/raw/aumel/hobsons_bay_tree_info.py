#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Hobsons Bay City Council street and park trees, from data.gov.au.

Source: "Street and Park Trees in Hobsons Bay City Council"
(`hbcc-street-and-park-trees`) on data.gov.au's CKAN, published by Hobsons
Bay City Council under CC BY 2.5 AU.  The CSV resource `339238b6` has an
active datastore: 82,100 rows, last modified 2019-03-01 (a GeoJSON, a
shapefile, a MapInfo TAB and a geoserver WFS of the same rows sit beside it).
Hobsons Bay (Williamstown, Newport, Altona, Laverton) is south-west of the
City of Melbourne across the Maribyrnong and the West Gate, shares no boundary
with it, and is inside AUMEL's territory, so it is a partition of Melbourne
rather than a city.

**The id is the datastore's own row key `_id`, because the source has none.**
No resource carries an asset number or anything that sounds like one: the
columns are `type`, `suburb`, `ward_name`, `easting`, `northing`,
`melway_ref`, `dbh_mm`, `Genus`, `Species`.  `_id` is assigned by CKAN when
the CSV is loaded into the datastore, non-null and distinct on every row (the
same footing as Austin's Socrata `:id`, `usaus/austin_tree_info.py`).  The
rows have not been reloaded since 2019; if the council ever re-uploads the
file, every `_id` is reassigned with it, a full re-key the `@source_kept`
guard does not see because the row count is unchanged.  The GeoJSON's feature
order and the WFS's feature ids are positions in a file, which is worse.
Prefixed `hob-`.

**Coordinates are projected here, from `easting`/`northing`** (MGA zone 55 on
GDA94, EPSG:28355), because the datastore -- the only copy with an id -- has no
latitude or longitude.  The inverse is the standard Transverse Mercator series
on GRS80 with no datum shift: over all 82,091 rows the GeoJSON resource
geolocates, it lands within 4 cm of the council's own WGS84 point for 99% of
rows and within 5 mm at the median.  Nine rows have no position and one has a
northing that puts it in Bass Strait; `validate_coordinates` drops them.

* `type` records site status: `Vacant` (3,470), `Stump` (221) and
  `Do Not Plant` (53) are not trees and are dropped here, because the flag is
  the source's own and the shared rule reads only `species`.
  `Street Tree`, `Park Tree` and `Council Property Tree` stay.
* `Genus` + `Species` are the scientific name; `Unknown` in either is the
  sentinel (9,645 rows, mostly the dropped vacant sites), and `sp.` leaves the
  genus.
* **`dbh_mm` is a band in millimetres** (`0-150` ... `1200-1500`), read as the
  band's midpoint, the same reconstruction Helsinki and Halifax get.  A
  midpoint is wrong by up to 75 mm per tree and right in aggregate.
  `Not Applicable` and `Unknown` are null.  An unrecognised band raises: the
  coding has changed.
* 16 tree rows repeat another row exactly (point, genus, species, band and
  type): the same tree entered twice.  The cross-source dedup never merges two
  municipal rows, so the repeat with the higher `_id` is dropped here.
"""

import math
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.ckan import SYSTEM_ID, CkanResource, iter_datastore_rows
from shared.ingest import (
    emit,
    enforce_tree_schema,
    normalize_species_parts,
    stream_to_table,
    validate_coordinates,
)

RESOURCE = CkanResource(
    "data.gov.au/data", "339238b6-3c2c-4649-aa49-501ee12a6c84", timeout=180
)
FIELDS = f"{SYSTEM_ID},type,easting,northing,dbh_mm,Genus,Species"

NOT_A_TREE = {"vacant", "stump", "do not plant"}

# `dbh_mm` band -> its midpoint in centimetres.
DBH_BAND_MIDPOINT_CM: dict[str, float] = {
    "0-150": 7.5,
    "150-300": 22.5,
    "300-450": 37.5,
    "450-600": 52.5,
    "600-750": 67.5,
    "750-900": 82.5,
    "900-1050": 97.5,
    "1050-1200": 112.5,
    "1200-1500": 135.0,
}
DBH_NOT_RECORDED = {"not applicable", "unknown"}


def clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def as_float(value) -> float | None:
    text = clean(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def mga55_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """MGA zone 55 (EPSG:28355) easting, northing to (lat, lon).

    Transverse Mercator on GRS80, central meridian 147 E, scale 0.9996, false
    easting 500 km, false northing 10,000 km.  GDA94 is WGS84 to well under the
    accuracy of a street-tree survey, so the inverse is the whole conversion.
    Snyder 1987, eq. 8-17 to 8-25 (as `shared.ingest.twd97_to_wgs84`).
    """
    a = 6378137.0
    f = 1 / 298.257222101
    k0 = 0.9996
    lon0 = math.radians(147.0)

    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))

    x = easting - 500000.0
    m = (northing - 10000000.0) / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    n1 = a / math.sqrt(1 - e2 * sin1 * sin1)
    t1 = tan1 * tan1
    c1 = ep2 * cos1 * cos1
    r1 = a * (1 - e2) / (1 - e2 * sin1 * sin1) ** 1.5
    d = x / (n1 * k0)

    lat = phi1 - (n1 * tan1 / r1) * (
        d * d / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * ep2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * ep2 - 3 * c1 * c1) * d**6 / 720
    )
    lon = lon0 + (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * ep2 + 24 * t1 * t1) * d**5 / 120
    ) / cos1
    return math.degrees(lat), math.degrees(lon)


def position(rec: dict) -> tuple[float | None, float | None]:
    easting, northing = as_float(rec.get("easting")), as_float(rec.get("northing"))
    if easting is None or northing is None:
        return None, None
    return mga55_to_wgs84(easting, northing)


def dbh_cm(value) -> float | None:
    text = clean(value)
    if text is None or text.lower() in DBH_NOT_RECORDED:
        return None
    if text not in DBH_BAND_MIDPOINT_CM:
        raise RuntimeError(
            f"Hobsons Bay: unrecognised dbh_mm band {text!r}; the field's coding has changed"
        )
    return DBH_BAND_MIDPOINT_CM[text]


def species_part(value) -> str | None:
    text = clean(value)
    if text is None or text.lower() in {"unknown", "sp.", "sp", "spp.", "spp"}:
        return None
    return text


def scientific_name(genus, species) -> str | None:
    g = clean(genus)
    if g is None or g.lower() == "unknown":
        return "Unknown"
    return normalize_species_parts(g, species_part(species))


def is_tree(rec: dict) -> bool:
    return (clean(rec.get("type")) or "").lower() not in NOT_A_TREE


class ExactRepeats:
    """Drops a row identical to an earlier one in every published attribute."""

    def __init__(self) -> None:
        self.seen: set[tuple] = set()
        self.dropped = 0

    def __call__(self, rec: dict) -> bool:
        key = tuple(clean(rec.get(k)) for k in ("easting", "northing", "Genus", "Species", "dbh_mm", "type"))
        if key in self.seen:
            self.dropped += 1
            return False
        self.seen.add(key)
        return True


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for rec in rows:
        lat, lon = position(rec)
        tree_id.append(f"hob-{rec[SYSTEM_ID]}")
        species.append(scientific_name(rec.get("Genus"), rec.get("Species")))
        latitude.append(lat)
        longitude.append(lon)
        cm = dbh_cm(rec.get("dbh_mm"))
        dbh.append(cm / 2.54 if cm is not None else None)

    n = len(tree_id)
    plant_date: list[date | None] = [None] * n
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["AUMEL"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


if __name__ == "__main__":
    repeats = ExactRepeats()

    def keep(rec: dict) -> bool:
        # Sorted by `_id`, so the first of a repeated pair is the one kept.
        return rec.get(SYSTEM_ID) is not None and is_tree(rec) and repeats(rec)

    pages = iter_datastore_rows(RESOURCE, fields=FIELDS)
    table = stream_to_table(pages, transform, keep=keep, label="Hobsons Bay trees")
    if repeats.dropped:
        print(f"Hobsons Bay: dropped {repeats.dropped} exact repeat row(s)", file=sys.stderr)
    table = validate_coordinates(table, city="Melbourne", city_code="AUMEL")
    table = enforce_tree_schema(table, city="Melbourne", data_source="HOBSONS_BAY")
    emit(table)
