"""Read an OGC WFS 2.0 layer (GeoServer) as GeoJSON, one page at a time.

NOT a uv inline script -- a regular importable module, like `_arcgis_shared`.

Copenhagen (`wfs-kbhkort.kk.dk`) and Helsinki (`kartta.hel.fi`) both publish
their tree register and their heritage register on a GeoServer WFS, which is
the third and fourth time this repo has read one (Berlin's `gdi.berlin.de`
came first and keeps its own paging loop).  The runbook's rule is to write the
shared module when the third city arrives, which is now.

Two things here are correctness rather than convenience:

* **Paging needs `sortBy`.**  `startIndex` over an unsorted result is not
  guaranteed stable between requests, so a row can repeat or vanish between
  pages -- the same rule `_arcgis_shared.iter_features` enforces for
  `orderByFields`.  The layer's primary key is the right column.
* **`numberMatched` is the termination test, not a short page.**  GeoServer
  reports the total on every page, so the loop stops when it has read that
  many rows; a short page on its own cannot distinguish "the data ended" from
  "the server capped the page" (a GeoServer `maxFeatures` cap is silent).

`wfs_max_property` is the freshness half: one row, sorted descending on a
timestamp column, so a probe reads the register's last edit without reading
the register.

    from _wfs_shared import WfsLayer, iter_wfs_features, wfs_max_property

    LAYER = WfsLayer("https://wfs-kbhkort.kk.dk/k101/ows", "k101:trae_basis")
    for page in iter_wfs_features(LAYER, properties=[...], sort_by="uuid"):
        ...
    stamp = wfs_max_property(LAYER, "opdateret_dato")
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

from _ingest_shared import get_json_with_retry


@dataclass(frozen=True)
class WfsLayer:
    """One feature type on a WFS 2.0 endpoint."""

    base_url: str
    type_name: str
    timeout: int = 180

    def params(self, **extra) -> dict[str, str]:
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": self.type_name,
            "outputFormat": "application/json",
        }
        params.update({k: str(v) for k, v in extra.items() if v is not None})
        return params


def iter_wfs_features(
    layer: WfsLayer,
    *,
    sort_by: str,
    properties: list[str] | None = None,
    srs: str = "EPSG:4326",
    page_size: int = 10000,
    cql_filter: str | None = None,
) -> Iterator[list[dict]]:
    """Pages of GeoJSON features, one HTTP request at a time.

    *properties* limits the columns the server serialises (the geometry column
    must be listed by name if it is wanted); a register with eighty columns is
    a fraction of the size with ten.  *sort_by* is required -- see the module
    docstring.
    """
    if not sort_by:
        raise ValueError(
            "iter_wfs_features needs sort_by: startIndex paging over an unsorted "
            "WFS result can repeat or skip rows, which truncates silently"
        )
    start = 0
    total: int | None = None
    while True:
        payload = get_json_with_retry(
            layer.base_url,
            params=layer.params(
                startIndex=start,
                count=page_size,
                sortBy=sort_by,
                srsName=srs,
                propertyName=",".join(properties) if properties else None,
                cql_filter=cql_filter,
            ),
            timeout=layer.timeout,
        )
        features = payload.get("features") or []
        if total is None:
            total = payload.get("numberMatched")
            if not isinstance(total, int):
                total = None
        if not features:
            return
        yield features
        start += len(features)
        if total is not None and start >= total:
            return
        if total is None and len(features) < page_size:
            return


def wfs_feature_count(layer: WfsLayer, *, cql_filter: str | None = None) -> int:
    """`numberMatched` from a `resultType=hits` request."""
    payload = get_json_with_retry(
        layer.base_url,
        params=layer.params(resultType="hits", cql_filter=cql_filter),
        timeout=layer.timeout,
    )
    total = payload.get("numberMatched")
    if not isinstance(total, int):
        raise RuntimeError(f"{layer.type_name}: resultType=hits returned no numberMatched")
    return total


def wfs_max_property(layer: WfsLayer, column: str) -> datetime:
    """The largest value of a date/timestamp *column*, as an aware datetime.

    One feature, sorted descending, so a freshness probe costs one small
    request.  Raises `RuntimeError` when the column is absent or empty -- that
    is the register changing shape, which must not degrade to "no new data"
    (see `emit_freshness`); a transport failure is raised as
    `UpstreamUnavailable` by `get_json_with_retry` and does degrade.
    """
    # Nulls sort *first* in a descending GeoServer sort (Copenhagen's
    # `opdateret_dato` answered `null` before the filter was added), so the
    # request excludes them rather than reading the top row and hoping.
    payload = get_json_with_retry(
        layer.base_url,
        params=layer.params(
            count=1,
            sortBy=f"{column} D",
            propertyName=column,
            cql_filter=f"{column} IS NOT NULL",
        ),
        timeout=layer.timeout,
    )
    features = payload.get("features") or []
    raw = (features[0].get("properties") or {}).get(column) if features else None
    if raw in (None, ""):
        raise RuntimeError(f"{layer.type_name}: MAX({column}) came back empty")
    stamp = parse_wfs_datetime(raw)
    if stamp is None:
        raise RuntimeError(f"{layer.type_name}: cannot read {column}={raw!r} as a datetime")
    return stamp


def _ring_wkt(ring: list) -> str:
    """`x y, x y, ...` for one GeoJSON ring, dropping any z ordinate."""
    return ", ".join(f"{pt[0]} {pt[1]}" for pt in ring)


def geojson_to_wkt(geometry: dict | None) -> str | None:
    """A GeoJSON geometry as 2-D WKT, or None when there is nothing usable.

    Point, MultiPoint (its first point -- Copenhagen's monuments are one-point
    multipoints), Polygon and MultiPolygon, which is what the two registers
    read through this module publish.  A z ordinate is dropped: Copenhagen's
    SAVE polygons and Helsinki's tree points carry one, and
    `landmark_common`'s `geo_from_text` wants plain 2-D WKT.
    """
    if not geometry:
        return None
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return None
    if kind == "Point":
        return f"POINT({coords[0]} {coords[1]})"
    if kind == "MultiPoint":
        first = coords[0]
        return f"POINT({first[0]} {first[1]})"
    if kind == "Polygon":
        return "POLYGON(" + ", ".join(f"({_ring_wkt(r)})" for r in coords) + ")"
    if kind == "MultiPolygon":
        polygons = [
            "(" + ", ".join(f"({_ring_wkt(r)})" for r in polygon) + ")"
            for polygon in coords
        ]
        return "MULTIPOLYGON(" + ", ".join(polygons) + ")"
    return None


def parse_wfs_datetime(value) -> datetime | None:
    """GeoServer's JSON spellings of a date: ISO-8601 with `Z`, or a bare date.

    A naive value is stamped UTC; a bare date is midnight UTC.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


if __name__ == "__main__":
    print(__doc__, file=sys.stderr)
