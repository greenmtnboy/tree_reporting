"""The National Register of Historic Places, as a city's landmark source.

The National Park Service's `cultural_resources/nrhp_locations` MapServer
(points, then polygons for the listings that have no point), read live within
`CITY_BOUNDS[code]` and kept where the listing's `City` is the city -- the box
also holds the towns around it.  Listed buildings, sites and structures only:
districts are areas, and a listing whose `STATUS` is not `Listed` (removed,
pending) is not a landmark.  Keyed `{prefix}-nr-{NRIS_Refnum}`, with
`CertDate` as `year_designated`.  NPS data is a US federal work.

Many US cities publish no register of their own, and many that do publish
one beside this; both read it through here.  A city that needs a different
filter (a city line rather than the `City` field, dedup against its own
register) still writes its own loop over the same layers (`NRHP_LAYERS`).
"""

from __future__ import annotations

from datetime import datetime

import pyarrow as pa

from shared.ingest import CITY_BOUNDS
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, esri_ms_to_datetime, iter_features

NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
NRHP_LAYERS = (FeatureLayer(f"{NRHP}/0"), FeatureLayer(f"{NRHP}/1"))  # points first
NRHP_FIELDS = "OBJECTID,NRIS_Refnum,RESNAME,ResType,Address,City,CertDate,STATUS"

NATIONAL_TYPE = "National Register of Historic Places"

COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
    "lm_type",
    "register_number",
    "year_designated",
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def bounds_query(city_code: str) -> dict[str, str]:
    """The envelope query over `CITY_BOUNDS[city_code]`."""
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS[city_code]
    return {
        "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def national_register(
    city_code: str,
    city_name: str,
    prefix: str,
    columns: tuple[str, ...] = COLUMNS,
    *,
    required: bool = True,
) -> dict[str, list]:
    """The city's listings as column lists keyed by *columns*.

    *city_name* is matched against the listing's `City`, case-insensitively.
    A column in *columns* this does not fill stays empty, as the per-city
    copies this replaced left it.  Raises when nothing is found unless
    *required* is False: a city whose only source is the register has
    listings, so none means the query or the name broke; a city with a
    register of its own may have none here.
    """
    seen: set[str] = set()
    rows: dict[str, list] = {k: [] for k in columns}
    for layer in NRHP_LAYERS:
        for page in iter_features(
            layer, out_fields=NRHP_FIELDS, return_geometry=True, out_sr=4326,
            extra_params=bounds_query(city_code),
        ):
            for feature in page:
                attrs = feature.get("attributes") or {}
                ref = clean(attrs.get("NRIS_Refnum"))
                name = clean(attrs.get("RESNAME"))
                wkt = esri_geometry_to_wkt(feature.get("geometry"))
                if not ref or not name or wkt is None or ref in seen:
                    continue
                if (clean(attrs.get("City")) or "").lower() != city_name.lower():
                    continue
                if clean(attrs.get("STATUS")) != "Listed":
                    continue
                if (clean(attrs.get("ResType")) or "").lower() == "district":
                    continue
                seen.add(ref)
                cert = clean(attrs.get("CertDate")) or ""
                rows["landmark_id"].append(f"{prefix}-nr-{ref}")
                rows["name"].append(name)
                rows["geometry_raw"].append(wkt)
                rows["address"].append(clean(attrs.get("Address")))
                rows["lm_type"].append(NATIONAL_TYPE)
                rows["register_number"].append(ref)
                rows["year_designated"].append(int(cert[:4]) if cert[:4].isdigit() else None)
    if required and not rows["landmark_id"]:
        raise RuntimeError(f"no National Register listings found in {city_name}")
    return rows


def national_register_table(
    city_code: str, city_name: str, prefix: str, *, required: bool = True
) -> pa.Table:
    """The landmark table of a city whose only source is the register.

    *required* False is for a city the register has no listing in (Everett,
    MA): it publishes an empty table instead of failing its landmark job."""
    data = national_register(city_code, city_name, prefix, required=required)
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array([city_code] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


def national_register_modified_at(city_code: str, city_name: str) -> datetime:
    """The newest `EDIT_DATE` over the register rows inside the city's box --
    the whole box, not only the city's rows, so a listing whose `City` is
    corrected into the city moves it too."""
    stamps = [
        esri_ms_to_datetime((feature.get("attributes") or {}).get("EDIT_DATE"))
        for layer in NRHP_LAYERS
        for page in iter_features(layer, out_fields="OBJECTID,EDIT_DATE", extra_params=bounds_query(city_code))
        for feature in page
    ]
    stamps = [s for s in stamps if s is not None]
    if not stamps:
        raise RuntimeError(f"no National Register EDIT_DATE inside {city_name}'s bounds")
    return max(stamps)
