#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Madrid's municipal tree inventory, from datos.madrid.es.

Source: "Arbolado en parques y zonas verdes de Madrid (Detalle)", CKAN package
`300761-0-arbolado-especies` on https://datos.madrid.es, published by the
Ayuntamiento de Madrid (D. G. de Gestión del Agua y Zonas Verdes) under
CC BY 4.0.  One row per tree, inventoried "pie a pie": street trees, trees in
district parks and green spaces, and the historic, singular and forest parks.
Not included, per the publisher: palms, the municipal nurseries, trees in
forest masses counted as stands (most of the Casa de Campo), and trees kept by
other bodies (district boards, Calle 30, cemeteries).

The package is republished twice a year (mid January and mid July) by adding
new resources ("Arbolado 2026" next to "Arbolado 2025"), not by replacing the
old ones, so the CSV is found from `package_show` -- the CSV resource named
"Arbolado <year>" with the latest year -- rather than pinned.  `latest_csv_resource`
is shared with the freshness probe.

CSV (semicolon-separated, UTF-8 with a BOM, decimal commas):

  ASSETNUM       -> tree_id ("madrid-<n>"); unique and non-null over the whole
                    file (799,110 of 799,110 in the July 2026 release); the
                    publisher calls it "identificador numérico único de cada árbol"
  ESPECIE        -> species / cultivar.  Already the scientific name, in upper
                    case ("PYRUS CALLERYANA 'CHANTICLEER'"); the common name is
                    only in the CODIGOS_ESPECIES side table and is not mapped.
  X, Y           -> ETRS89 / UTM zone 30N metres (EPSG:25830), converted here
  PERIMETRO      -> trunk circumference at 1.3 m in cm (publisher's structure
                    document; median 45, p99 198) -> DBH inches
  ALTURA_TOTAL   -> total height in metres; not mapped (no recorded-height
                    column in the canonical schema)

No planting date, crown spread, or status column is published; the shapefile
form carries vitality and health classes, but a dead tree is still a tree.
"""

import math
import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    circumference_cm_to_dbh_inches,
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    iter_csv_row_chunks,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)

PORTAL = "https://datos.madrid.es"
PACKAGE_ID = "300761-0-arbolado-especies"
PACKAGE_URL = f"{PORTAL}/api/3/action/package_show"
LANDING_URL = f"{PORTAL}/dataset/{PACKAGE_ID}"

_RESOURCE_NAME = re.compile(r"^\s*Arbolado\s+(\d{4})\s*$", re.IGNORECASE)

ID_PREFIX = "madrid-"

# Above this share of unreadable coordinates or circumferences the export's
# format has changed (a different separator or decimal mark), and publishing
# would silently drop trees or diameters.
MAX_UNREADABLE = 0.01


def latest_csv_resource() -> dict:
    """The CSV resource of the newest "Arbolado <year>" release.

    Raises RuntimeError when the package holds none: that is the publisher
    renaming or reshaping the release, which must not degrade to "no new data".
    An outage is raised by `get_json_with_retry` as UpstreamUnavailable.
    """
    payload = get_json_with_retry(PACKAGE_URL, params={"id": PACKAGE_ID}, timeout=120)
    resources = (payload.get("result") or {}).get("resources") or []
    candidates = []
    for res in resources:
        match = _RESOURCE_NAME.match(res.get("description") or res.get("name") or "")
        fmt = (res.get("format") or "").strip().upper()
        url = res.get("url") or ""
        if match and fmt == "CSV" and url:
            stamp = res.get("last_modified") or res.get("created") or ""
            candidates.append((int(match.group(1)), stamp, res))
    if not candidates:
        raise RuntimeError(
            f"Madrid: no 'Arbolado <year>' CSV resource in {LANDING_URL}; "
            f"found {[(r.get('name'), r.get('description'), r.get('format')) for r in resources]}"
        )
    candidates.sort(key=lambda c: (c[0], c[1]))
    return candidates[-1][2]


# ---------------------------------------------------------------------------
# ETRS89 / UTM 30N -> geographic.  ETRS89 and WGS84 agree to well under a metre
# in Spain, so the GRS80 inverse is the answer.  Krüger's series to n^4
# (Karney 2011), sub-millimetre inside the zone; no pyproj dependency.
# ---------------------------------------------------------------------------

_A = 6378137.0
_F = 1 / 298.257222101
_K0 = 0.9996
_E0 = 500000.0
_LON0 = math.radians(-3.0)  # zone 30
_N = _F / (2 - _F)
_A_RECT = _A / (1 + _N) * (1 + _N**2 / 4 + _N**4 / 64)
_BETA = (
    _N / 2 - 2 * _N**2 / 3 + 37 * _N**3 / 96 - _N**4 / 360,
    _N**2 / 48 + _N**3 / 15 - 437 * _N**4 / 1440,
    17 * _N**3 / 480 - 37 * _N**4 / 840,
    4397 * _N**4 / 161280,
)
_DELTA = (
    2 * _N - 2 * _N**2 / 3 - 2 * _N**3 + 116 * _N**4 / 45,
    7 * _N**2 / 3 - 8 * _N**3 / 5 - 227 * _N**4 / 45,
    56 * _N**3 / 15 - 136 * _N**4 / 35,
    4279 * _N**4 / 630,
)


def utm30_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """(latitude, longitude) in degrees for an ETRS89 UTM 30N point."""
    xi = northing / (_K0 * _A_RECT)
    eta = (easting - _E0) / (_K0 * _A_RECT)
    xi_p, eta_p = xi, eta
    for j, b in enumerate(_BETA, start=1):
        xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    lat = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(_DELTA, start=1))
    lon = _LON0 + math.atan2(math.sinh(eta_p), math.cos(xi_p))
    return math.degrees(lat), math.degrees(lon)


def _number(value: str | None) -> float | None:
    """A Spanish-formatted number ("0,5", "4474049.950"), or None when blank.

    Raises ValueError on a non-blank value that is not a number, so the caller
    can count it.
    """
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    return float(s.replace(",", "."))


def split_species(value: str | None) -> tuple[str | None, str | None]:
    """("Pyrus calleryana", "Chanticleer") from "PYRUS CALLERYANA 'CHANTICLEER'".

    The register writes everything in capitals, so the cultivar is title-cased
    here (`extract_cultivar` keeps an all-caps selection as written) and taken
    between the first and the *last* quote: "CRATAEGUS LAEVIGATA 'PAUL'S
    SCARLET'" holds an apostrophe inside its selection.
    """
    if value is None or not value.strip():
        return None, None
    s = " ".join(value.split())
    first = s.find("'")
    if first == -1:
        return normalize_species(s), None
    last = s.rfind("'")
    inner = s[first + 1 : last] if last > first else s[first + 1 :]
    inner = inner.strip()
    cultivar = (
        " ".join(w[:1].upper() + w[1:].lower() for w in inner.split(" ")) if inner else None
    )
    return normalize_species(s[:first]), cultivar


def iter_row_chunks():
    """Chunks of CSV rows from the newest release."""
    resource = latest_csv_resource()
    print(
        f"Madrid: reading {resource.get('description') or resource.get('name')} "
        f"({resource['url']})",
        file=sys.stderr,
    )
    yield from iter_csv_row_chunks(resource["url"], delimiter=";")


_counts = {"rows": 0, "no_id": 0, "bad_xy": 0, "bad_perimeter": 0}


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for rec in rows:
        _counts["rows"] += 1
        asset = (rec.get("ASSETNUM") or "").strip()
        if not asset:
            _counts["no_id"] += 1
            continue
        try:
            x, y = _number(rec.get("X")), _number(rec.get("Y"))
        except ValueError:
            x = y = None
        if x is None or y is None:
            _counts["bad_xy"] += 1
            lat = lon = None
        else:
            lat, lon = utm30_to_wgs84(x, y)
        try:
            perimeter = _number(rec.get("PERIMETRO"))
        except ValueError:
            _counts["bad_perimeter"] += 1
            perimeter = None

        name, selection = split_species(rec.get("ESPECIE"))
        tree_id.append(f"{ID_PREFIX}{asset}")
        species.append(name)
        cultivar.append(selection)
        plant_date.append(None)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(circumference_cm_to_dbh_inches(perimeter))

    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["ESMAD"] * len(tree_id), type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "cultivar": pa.array(cultivar, type=pa.string()),
        "plant_date": pa.array(plant_date, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })


def check_counts() -> None:
    rows = _counts["rows"] or 1
    if _counts["no_id"]:
        print(f"Madrid: dropped {_counts['no_id']} row(s) with no ASSETNUM", file=sys.stderr)
    for key in ("bad_xy", "bad_perimeter"):
        if _counts[key]:
            print(f"Madrid: {_counts[key]} row(s) with {key}", file=sys.stderr)
        if _counts[key] / rows > MAX_UNREADABLE:
            raise RuntimeError(
                f"Madrid: {_counts[key]} of {rows} rows unreadable ({key}); "
                "the CSV's number format has probably changed"
            )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, label="Madrid OpenData")
    check_counts()
    table = validate_coordinates(table, city="Madrid", city_code="ESMAD")
    table = enforce_tree_schema(table, city="Madrid", data_source="MADRID_OPENDATA")
    emit(table)
