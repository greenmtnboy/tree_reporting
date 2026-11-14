#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Naples, FL's designated historic buildings and National Register listings.

Two official registers, both live ArcGIS layers read at refresh time, so there
is no Nominatim geocoding, no committed CSV and nothing to stage.

* **The city's historic buildings** (`Building/HistoricBuildings/MapServer/0`
  on g.naplesgov.com, the server that hosts the trees): "Building Footprints
  of historic buildings within the City of Naples", 61 footprints on
  2026-11-11, every one `Historic = 1` (Yes), all in the Naples Historic
  District around 10th-12th Avenue South.  `RuleID` is the building type
  through its coded-value domain (55 `Residential House`, 4 `Garage`, 2
  `Commercial Building`).  The layer carries no name or address, so the
  ingest gives each footprint the city's own address: the address point
  (`City/CityOfNaplesAddressAndUnitPoints`, layer 1) that falls inside the
  footprint where there is one (41 of 61), else the site address of the parcel
  (`City/Parcels`) holding the footprint's centroid (rear cottages and
  garages, 18 more).  The two commercial buildings stand on an unaddressed
  city parcel at the foot of 12th Avenue South by the pier and keep no
  address.  The name is "Historic house, 186 11th Ave S", and a lot with
  several historic buildings (149 10th Ave S has three) repeats it.  The
  layer has no GlobalID or register number, so `OBJECTID` is the key
  (`nap-hb-{OBJECTID}`).
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points and polygons) within
  `CITY_BOUNDS["USNAP"]`: Palm Cottage, the Seaboard Coast Line Railroad
  Depot and the Keewaydin Club (the last only as a polygon).  The key is the
  NRIS reference number (`nap-nr-{NRIS_Refnum}`), which is also published as
  `register_number`; `CertDate` (`1982-05-`) gives `year_designated`.  A
  listing in both layers keeps its point.  The Naples Historic District
  listing is an area, not a landmark, and is not published (Durham's rule).

The two registers do not overlap today (Palm Cottage, 137 12th Ave S, is not
among the city's footprints); were a building on both, each register's row
would be kept and `lm_type` says which it is, because the two designations
are separate facts.  The NPS points are NRIS's own coordinates, accurate to
about 12 m.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import (
    FeatureLayer,
    coded_value_domain,
    esri_geometry_to_wkt,
    iter_features,
)
from shared.ingest import CITY_BOUNDS, emit

NAPLES = "https://g.naplesgov.com/arcgis/rest/services"
HISTORIC_BUILDINGS = FeatureLayer(f"{NAPLES}/Building/HistoricBuildings/MapServer/0")
ADDRESS_POINTS = FeatureLayer(f"{NAPLES}/City/CityOfNaplesAddressAndUnitPoints/FeatureServer/1")
PARCELS = FeatureLayer(f"{NAPLES}/City/Parcels/FeatureServer/0")

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,CertDate,STATUS"

CITY_TYPE = "City of Naples historic building"
NATIONAL_TYPE = "National Register of Historic Places"

# RuleID's domain names, as the short word the landmark's name uses.
BUILDING_WORDS = {
    "Residential House": "house",
    "Commercial Building": "commercial building",
    "Garage": "garage",
    "Shed": "shed",
}


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def format_address(raw: str | None) -> str | None:
    """`186 11TH AVE S` -> `186 11th Ave S`."""
    text = clean(raw)
    if text is None:
        return None
    return " ".join(
        word.lower() if word[0].isdigit() else word.capitalize()
        for word in text.split()
    )


def envelope_params(xmin: float, ymin: float, xmax: float, ymax: float) -> dict[str, str]:
    return {
        "geometry": f"{xmin},{ymin},{xmax},{ymax}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def _in_ring(x: float, y: float, ring) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def in_polygon(x: float, y: float, rings) -> bool:
    """Even-odd over every ring, so a hole is outside."""
    return sum(_in_ring(x, y, ring) for ring in rings) % 2 == 1


def centroid(rings) -> tuple[float, float]:
    ring = rings[0][:-1] if rings[0][0] == rings[0][-1] else rings[0]
    return (sum(x for x, _ in ring) / len(ring), sum(y for _, y in ring) / len(ring))


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def city_buildings() -> dict[str, list]:
    kinds = coded_value_domain(HISTORIC_BUILDINGS, "RuleID")
    footprints = fetch_all(
        HISTORIC_BUILDINGS,
        out_fields="OBJECTID,RuleID,Historic",
        return_geometry=True,
        out_sr=4326,
    )
    footprints = [
        f for f in footprints
        if (f.get("attributes") or {}).get("Historic") == 1 and (f.get("geometry") or {}).get("rings")
    ]
    if not footprints:
        raise RuntimeError(f"{HISTORIC_BUILDINGS.base} returned no historic footprints")

    xs = [x for f in footprints for ring in f["geometry"]["rings"] for x, _ in ring]
    ys = [y for f in footprints for ring in f["geometry"]["rings"] for _, y in ring]
    around = envelope_params(min(xs), min(ys), max(xs), max(ys))
    points = [
        p for p in fetch_all(
            ADDRESS_POINTS, out_fields="OBJECTID,ADDRESS", return_geometry=True,
            out_sr=4326, extra_params=around,
        )
        if clean((p.get("attributes") or {}).get("ADDRESS")) and p.get("geometry")
    ]
    parcels = [
        p for p in fetch_all(
            PARCELS, out_fields="OBJECTID,STR_NUM,STR_NAME,STR_TYPE,STR_ORD",
            return_geometry=True, out_sr=4326, extra_params=around,
        )
        if (p.get("geometry") or {}).get("rings")
    ]

    def address_of(rings) -> str | None:
        inside = sorted(
            clean(p["attributes"]["ADDRESS"]) for p in points
            if in_polygon(p["geometry"]["x"], p["geometry"]["y"], rings)
        )
        if inside:
            return format_address(inside[0])
        cx, cy = centroid(rings)
        for parcel in parcels:
            if in_polygon(cx, cy, parcel["geometry"]["rings"]):
                a = parcel["attributes"]
                if clean(a.get("STR_NUM")) and clean(a.get("STR_NAME")):
                    parts = (a.get("STR_NUM"), a.get("STR_NAME"), a.get("STR_TYPE"), a.get("STR_ORD"))
                    return format_address(" ".join(clean(v) for v in parts if clean(v)))
        return None

    rows: dict[str, list] = {k: [] for k in COLUMNS}
    for feature in sorted(footprints, key=lambda f: f["attributes"]["OBJECTID"]):
        attrs = feature["attributes"]
        kind = kinds.get(str(attrs.get("RuleID")))
        if kind is None:
            raise RuntimeError(
                f"Naples historic building RuleID {attrs.get('RuleID')!r} is not in its domain"
            )
        word = BUILDING_WORDS.get(kind, kind.lower())
        address = address_of(feature["geometry"]["rings"])
        rows["landmark_id"].append(f"nap-hb-{attrs['OBJECTID']}")
        rows["name"].append(f"Historic {word}, {address}" if address else f"Historic {word}")
        rows["geometry_raw"].append(esri_geometry_to_wkt(feature["geometry"]))
        rows["address"].append(address)
        rows["property_type"].append(kind)
        rows["lm_type"].append(CITY_TYPE)
        rows["register_number"].append(None)
        rows["year_designated"].append(None)
    return rows


def national_register() -> dict[str, list]:
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS["USNAP"]
    box = envelope_params(lon_min, lat_min, lon_max, lat_max)
    seen: set[str] = set()
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    for layer in NRHP_LAYERS:
        for feature in fetch_all(
            layer, out_fields=NRHP_FIELDS, return_geometry=True, out_sr=4326, extra_params=box
        ):
            attrs = feature.get("attributes") or {}
            ref = clean(attrs.get("NRIS_Refnum"))
            name = clean(attrs.get("RESNAME"))
            wkt = esri_geometry_to_wkt(feature.get("geometry"))
            if not ref or not name or wkt is None or ref in seen:
                continue
            if clean(attrs.get("STATUS")) != "Listed":
                continue
            if (clean(attrs.get("ResType")) or "").lower() == "district":
                continue
            seen.add(ref)
            cert = clean(attrs.get("CertDate")) or ""
            rows["landmark_id"].append(f"nap-nr-{ref}")
            rows["name"].append(name)
            rows["geometry_raw"].append(wkt)
            rows["address"].append(clean(attrs.get("Address")))
            rows["property_type"].append((clean(attrs.get("ResType")) or "").capitalize() or None)
            rows["lm_type"].append(NATIONAL_TYPE)
            rows["register_number"].append(ref)
            rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    return rows


COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
    "property_type",
    "lm_type",
    "register_number",
    "year_designated",
)


def build() -> pa.Table:
    city = city_buildings()
    national = national_register()
    data = {k: city[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USNAP"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "property_type": pa.array(data["property_type"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
