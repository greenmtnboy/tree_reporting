#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Prince George's Heritage Register, placed on the City's own address points.

The register: the City of Prince George's Heritage Register, "an official
list of buildings and sites" with heritage value, added to by Council
resolution on the Heritage Commission's recommendation, published as a web
page with a Statement of Significance per place
(https://www.princegeorge.ca/community-culture/heritage/heritage-register-properties,
17 places on 2026-11-22).  It has no data layer: the City's open-data
ArcGIS organisation carries only the OCP's Heritage Conservation Area
parcels (six unnamed parcels) and a downtown heritage-*sign* layer, and the
BC Register of Historic Places is "Access Only".  The page states no
licence; what is taken from it is each place's name, civic address and the
build year its statement gives (no licence stated; displayed with
attribution only, not licensed for reuse).

The places: each register address is looked up live in the City's civic
address points (`OpenData_Address/FeatureServer/4`, "Civic Address Labels",
Open Government License - City of Prince George,
https://pgmapinfo.princegeorge.ca/opendata/CityofPrinceGeorge_Open_Government_License_Open_Data.pdf),
by house number, street name, type and directional prefix, so nothing is
geocoded by a third party or staged.  A register address that matches no
active point, or more than one, refuses the build: the list below was
checked against the layer when the city was wired, so a miss means the
address layer changed.

Three register entries have no civic address and are not published: the
Dogwood and Elm Street trees (the Millar Addition between 15th and 17th
Avenues), the Vancouver Street elms, and Nechako Crossing.  Knox United
Church is listed "at the corner of Fifth Avenue and Brunswick Street"; its
civic address is 1448 5th Ave.  Quinson Elementary is "251 South Ogilvie
Street": the address layer carries the south block's 251 without a prefix
(its north counterpart is `N`).

**The id is the register place** (`pg-hr-{slug}`): the register has no
numbers of its own, and a place's name is what the City's page keys it by.
"""

import re
import sys
import unicodedata
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import emit

ADDRESSES = FeatureLayer(
    "https://services2.arcgis.com/CnkB6jCzAsyli34z/arcgis/rest/services/"
    "OpenData_Address/FeatureServer/4",
    timeout=120,
)

# (name, house number, StrName, StrType, StrPrefix or None, year built or None)
REGISTER: tuple[tuple[str, str, str, str, str | None, int | None], ...] = (
    ("George Buchan House", "2755", "Hammond", "Ave", None, None),
    ("John Nielsen House", "2280", "McBride", "Cres", None, None),
    ("Howieson Residence", "2688", "Inlander", "St", None, 1912),
    ("Munro/Moffat House", "153", "Moffat", "St", "N", 1914),
    ("Pitman House", "2387", "McBride", "Cres", None, None),
    ("Taylor House", "1872", "10th", "Ave", None, 1920),
    ("House by Alan Greenwell", "2299", "Laurier", "Cres", None, None),
    ("Sixth Avenue Liquor Store", "1188", "6th", "Ave", None, None),
    ("Federal Government Building (Old Post Office)", "1294", "3rd", "Ave", None, 1939),
    ("Hilliard Clare Masonic Hall", "480", "Vancouver", "St", None, 1955),
    ("Professional Building", "1705", "3rd", "Ave", None, 1953),
    ("Knox United Church and Performance Centre", "1448", "5th", "Ave", None, 1922),
    ("South Fort George School House", "755", "20th", "Ave", None, 1910),
    ("Quinson Elementary School", "251", "Ogilvie", "St", None, None),
)


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def locate(number: str, street: str, kind: str, prefix: str | None) -> tuple[float, float, str]:
    where = (
        f"AddrNumber = {quote(number)} AND StrName = {quote(street)} "
        f"AND StrType = {quote(kind)} AND LifeCycleStatus = 'ACT' AND AddrUnitID IS NULL AND "
        + (f"StrPrefix = {quote(prefix)}" if prefix else "StrPrefix IS NULL")
    )
    rows = [
        f
        for page in iter_features(
            ADDRESSES, out_fields="OBJECTID,FullName", where=where, return_geometry=True
        )
        for f in page
    ]
    if len(rows) != 1:
        raise RuntimeError(
            f"Prince George landmarks: {len(rows)} address points for "
            f"{prefix or ''} {number} {street} {kind}; expected exactly one"
        )
    lat, lon = esri_point(rows[0].get("geometry"))
    if lat is None or lon is None:
        raise RuntimeError(f"Prince George landmarks: no geometry for {number} {street} {kind}")
    full = rows[0]["attributes"].get("FullName") or f"{number} {street} {kind}"
    if prefix:
        number_part, _, rest = full.partition(" ")
        full = f"{number_part} {prefix} {rest}"
    return lat, lon, full


def build() -> pa.Table:
    landmark_id: list[str] = []
    name: list[str] = []
    geometry_raw: list[str] = []
    address: list[str] = []
    year_built: list[int | None] = []
    for label, number, street, kind, prefix, built in REGISTER:
        lat, lon, full = locate(number, street, kind, prefix)
        landmark_id.append(f"pg-hr-{slug(label)}")
        name.append(label)
        geometry_raw.append(f"POINT({lon} {lat})")
        address.append(full)
        year_built.append(built)
    print(f"Prince George landmarks: {len(name)} register places placed", file=sys.stderr)
    n = len(name)
    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["CAPRG"] * n, type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "year_built": pa.array(year_built, type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
