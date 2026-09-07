"""Shared helpers for reading ArcGIS FeatureServer / MapServer layers.

NOT a uv inline script — a regular importable module, like `_ingest_shared`.

ArcGIS is the dominant platform for North American municipal open data, and by
the time Denver was wired six scripts across five cities were each carrying
their own copy of the same three operations: page a layer by offset, read a
freshness watermark out of it, and turn Esri's epoch-milliseconds into a
`datetime`.  They had drifted in exactly the ways copies do — two different
spellings of the watermark, hand-URL-encoded `outStatistics` JSON, and a
page-size constant that was right for one layer and a silent truncation risk
for the next.  This does for ArcGIS what `_osm_shared` does for Overpass:
one implementation, a thin shim per city.

Usage:

    from _arcgis_shared import FeatureLayer, iter_features, layer_last_edit

    LAYER = FeatureLayer(
        "https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/"
        "ODC_PARK_TREEINVENTORY_P/FeatureServer/241"
    )

    for page in iter_features(LAYER, out_fields="SITE_ID,SPECIES_BOTANIC"):
        ...
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from _ingest_shared import UpstreamUnavailable, get_json_with_retry

if TYPE_CHECKING:
    from collections.abc import Iterator


# ---------------------------------------------------------------------------
# Layer addressing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FeatureLayer:
    """One ArcGIS layer, addressed by its base URL with no trailing `/query`.

    Both server flavours work — `.../FeatureServer/0` and `.../MapServer/23` —
    because everything below is built from the same two endpoints: the layer
    resource itself (metadata) and `<layer>/query`.

    `timeout` travels with the layer rather than being passed at every call
    site: a layer is either the small metadata read or the big paged one, and
    the pages are what needs the generous budget.
    """

    url: str
    timeout: int = 120

    def __post_init__(self) -> None:
        if self.url.rstrip("/").endswith("/query"):
            raise ValueError(
                f"FeatureLayer takes the layer URL, not its /query endpoint: {self.url}"
            )

    @property
    def base(self) -> str:
        return self.url.rstrip("/")

    @property
    def query_url(self) -> str:
        return f"{self.base}/query"


# ---------------------------------------------------------------------------
# Esri time
# ---------------------------------------------------------------------------

def esri_ms_to_datetime(value) -> datetime | None:
    """Esri's epoch-milliseconds to an aware UTC datetime, or None.

    Every ArcGIS date field and every `editingInfo` entry is milliseconds since
    the Unix epoch.  Out-of-range values come back as None rather than raising:
    a portal that writes year 30827 into an edit date is publishing junk in one
    row, not signalling that our field mapping is wrong.
    """
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def esri_ms_to_date(value) -> date | None:
    """Esri's epoch-milliseconds to a `date`, or None.  See `esri_ms_to_datetime`."""
    stamp = esri_ms_to_datetime(value)
    return stamp.date() if stamp else None


# ---------------------------------------------------------------------------
# Metadata and freshness
# ---------------------------------------------------------------------------

def layer_metadata(layer: FeatureLayer) -> dict:
    """The layer resource as JSON (`?f=json`)."""
    return get_json_with_retry(f"{layer.base}?f=json", timeout=layer.timeout)


def layer_last_edit(layer: FeatureLayer) -> datetime:
    """The layer's own last-edit time, for a freshness probe.

    `editingInfo.dataLastEditDate` is when the *rows* last changed;
    `lastEditDate` also moves for a schema-only edit.  Prefer the former and
    fall back, which is what every hand-rolled copy of this did.

    Raises `RuntimeError` when the layer publishes no `editingInfo` at all —
    that is a schema question, not an availability one, so it must not degrade
    to "no new data" (see `emit_freshness`).  A layer without `editingInfo`
    wants `field_max` against its own edit-date column instead.
    """
    editing = layer_metadata(layer).get("editingInfo") or {}
    stamp = esri_ms_to_datetime(
        editing.get("dataLastEditDate") or editing.get("lastEditDate")
    )
    if stamp is None:
        raise RuntimeError(
            f"editingInfo.dataLastEditDate missing from ArcGIS layer metadata: {layer.base}"
        )
    return stamp


def field_max(layer: FeatureLayer, field: str, *, where: str = "1=1") -> datetime:
    """MAX(*field*) as a datetime, via an `outStatistics` query.

    For layers that expose no `editingInfo` but do carry their own edit-date
    column (`EditDate`, `SDE_DT`, `LAST_EDITED_DATE`).  One statistics row, not
    the table.

    The `outStatistics` JSON is built here rather than pasted in pre-encoded:
    every hand-written copy of this URL in the repo was an unreadable
    percent-encoded blob, and one of them had drifted to a field the layer no
    longer had.
    """
    payload = get_json_with_retry(
        layer.query_url,
        params={
            "where": where,
            "outStatistics": json.dumps(
                [
                    {
                        "statisticType": "max",
                        "onStatisticField": field,
                        "outStatisticFieldName": "max_value",
                    }
                ]
            ),
            "f": "json",
        },
        timeout=layer.timeout,
    )
    features = payload.get("features") or []
    if not features:
        raise RuntimeError(
            f"no statistics row returned for MAX({field}) on {layer.base}"
        )
    attrs = features[0].get("attributes") or {}
    # ArcGIS is inconsistent about the case it echoes outStatisticFieldName in.
    raw = next(
        (v for k, v in attrs.items() if k.lower() == "max_value"),
        None,
    )
    stamp = esri_ms_to_datetime(raw)
    if stamp is None:
        raise RuntimeError(f"MAX({field}) came back empty on {layer.base}")
    return stamp


def feature_count(layer: FeatureLayer, *, where: str = "1=1") -> int:
    """`returnCountOnly` for *where*."""
    payload = get_json_with_retry(
        layer.query_url,
        params={"where": where, "returnCountOnly": "true", "f": "json"},
        timeout=layer.timeout,
    )
    count = payload.get("count")
    if count is None:
        raise RuntimeError(f"returnCountOnly gave no count on {layer.base}")
    return int(count)


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------

# Used only when the layer publishes no `maxRecordCount`.  The Esri default.
FALLBACK_PAGE_SIZE = 1000


def max_record_count(layer: FeatureLayer) -> int:
    """The layer's own `maxRecordCount`.

    Asking for more than this is *silently capped* rather than refused, so a
    hardcoded page size that happens to exceed it makes every page a short
    page — and a short page is the signal that the data ran out.  Reading it
    from the layer is one request that removes a whole class of
    silently-truncated city.
    """
    value = layer_metadata(layer).get("maxRecordCount")
    try:
        return int(value) if value else FALLBACK_PAGE_SIZE
    except (TypeError, ValueError):
        return FALLBACK_PAGE_SIZE


def iter_features(
    layer: FeatureLayer,
    *,
    out_fields: str = "*",
    where: str = "1=1",
    return_geometry: bool = False,
    out_sr: int | str = 4326,
    order_by: str = "OBJECTID",
    page_size: int | None = None,
    extra_params: dict[str, str] | None = None,
) -> Iterator[list[dict]]:
    """Pages of raw `f=json` features, one HTTP request at a time.

    Yields lists of Esri features (`{"attributes": {...}, "geometry": {...}}`)
    so a caller can feed `_ingest_shared.stream_to_table` and never hold the
    whole layer in memory.  DC's ingest OOM-killed its 2 GiB container reading
    216k features in one `response.json()`; Denver is 359k.

    Three things here are correctness, not tidiness:

    * **`order_by` is required.**  Offset paging over an unordered result may
      repeat or skip rows between requests, and a short page from that ends
      the loop early — a silently truncated city that looks exactly like a
      portal publishing less.  Pass the layer's OID field if it is not
      `OBJECTID`; passing an empty string is refused.
    * **`page_size` defaults to the layer's `maxRecordCount`**, read from the
      layer.  See `max_record_count`.
    * **Termination prefers `exceededTransferLimit`** — Esri's own "there is
      more" flag — and falls back to the short-page rule only for the older
      servers that omit it.  The flag is exact; the heuristic is not, since a
      full last page is indistinguishable from a truncated one.

    `out_sr` reprojects server-side, so a layer stored in a State Plane CRS
    still yields WGS84 geometry.  It only affects `geometry`; a layer that
    carries its own lat/lon *attributes* (Denver's `X_LONG`/`Y_LAT`) should be
    read with `return_geometry=False` and those fields in *out_fields*.
    """
    if not order_by:
        raise ValueError(
            "iter_features needs order_by: offset paging over an unordered "
            "ArcGIS result can repeat or skip rows, which truncates silently"
        )
    size = page_size or max_record_count(layer)
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "true" if return_geometry else "false",
            "orderByFields": order_by,
            "resultOffset": str(offset),
            "resultRecordCount": str(size),
            "f": "json",
        }
        if return_geometry:
            params["outSR"] = str(out_sr)
        if extra_params:
            params.update(extra_params)

        payload = get_json_with_retry(
            layer.query_url, params=params, timeout=layer.timeout
        )
        features = payload.get("features") or []
        if not features:
            return
        yield features

        more = payload.get("exceededTransferLimit")
        if more is None:
            # Older servers omit the flag; fall back to the short-page rule.
            if len(features) < size:
                return
        elif not more:
            return
        offset += len(features)


def iter_attributes(layer: FeatureLayer, **kwargs) -> Iterator[list[dict]]:
    """`iter_features` with the Esri envelope stripped, for attribute-only reads.

    The common case for a layer that publishes its coordinates as columns.
    """
    for page in iter_features(layer, **kwargs):
        yield [feature.get("attributes") or {} for feature in page]


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def _ring_is_clockwise(ring) -> bool:
    """Shoelace sign for an Esri ring.

    Esri's polygon convention is orientation-based rather than nested: every
    exterior ring is clockwise and every hole counter-clockwise, all of them in
    one flat `rings` list with no grouping.  This is what recovers the
    grouping, and getting it wrong turns a courtyard into a second building.
    """
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        area += (x2 - x1) * (y2 + y1)
    return area > 0


def _ring_to_wkt(ring) -> str | None:
    """One Esri ring as a WKT coordinate list, closed."""
    points = [p for p in ring if p and len(p) >= 2 and None not in p[:2]]
    if len(points) < 3:
        return None
    if points[0][:2] != points[-1][:2]:
        points.append(points[0])
    return "(" + ", ".join(f"{p[0]} {p[1]}" for p in points) + ")"


def esri_geometry_to_wkt(geometry: dict | None) -> str | None:
    """An Esri `f=json` geometry as WKT, or None when there is nothing usable.

    Handles the two shapes this repo's sources publish: a point
    (`{"x": ..., "y": ...}`) and a polygon (`{"rings": [...]}`).  Landmark
    registries are split about evenly between them -- Halifax, Kingston,
    Victoria and Kelowna publish the designated *parcel* as a polygon while
    Lethbridge and New Westminster publish a point -- and `landmark_common`
    takes either, deriving the centroid it needs with `geo_centroid`.

    Rings are grouped into polygons by orientation, so a designated property
    with a courtyard renders as one polygon with a hole rather than two
    buildings.  A leading hole with no exterior ring before it (which is
    malformed, but portals publish it) starts its own polygon rather than
    being dropped.
    """
    if not geometry:
        return None

    if geometry.get("x") is not None and geometry.get("y") is not None:
        return f"POINT({geometry['x']} {geometry['y']})"

    rings = geometry.get("rings")
    if not rings:
        return None

    polygons: list[list[str]] = []
    for ring in rings:
        rendered = _ring_to_wkt(ring)
        if rendered is None:
            continue
        if _ring_is_clockwise(ring) or not polygons:
            polygons.append([rendered])
        else:
            polygons[-1].append(rendered)

    if not polygons:
        return None
    parts = ["(" + ", ".join(rings_) + ")" for rings_ in polygons]
    if len(parts) == 1:
        return f"POLYGON{parts[0]}"
    return f"MULTIPOLYGON({', '.join(parts)})"


# ---------------------------------------------------------------------------
# Hub catalogues
# ---------------------------------------------------------------------------

def hub_datasets(hub_host: str, *, timeout: int = 120) -> list[dict]:
    """Every dataset an ArcGIS Hub site publishes, from its DCAT-US feed.

    `hub_datasets("opendata-geospatialdenver.hub.arcgis.com")` is how Denver's
    tree inventory was found: the feed names each dataset's title, its
    `modified` stamp and its GeoServices REST endpoint, which is everything
    needed to decide whether a city can be wired before writing any code.  The
    Hub search UI is JavaScript and its private search API is not; this feed is
    documented and stable.

    Returned verbatim, because what you want out of it differs per search.
    See `find_tree_layers` for the one this repo always runs.
    """
    host = hub_host.replace("https://", "").replace("http://", "").strip("/")
    payload = get_json_with_retry(
        f"https://{host}/api/feed/dcat-us/1.1.json", timeout=timeout
    )
    return payload.get("dataset") or []


def find_tree_layers(hub_host: str, *, timeout: int = 120) -> list[dict]:
    """Candidate tree-inventory layers on a Hub site.

    Filters `hub_datasets` to titles mentioning trees and returns
    `{"title", "modified", "rest_url"}` for each, newest metadata first.
    Canopy-polygon datasets are excluded — they are the most common false
    positive on a tree search and are not an inventory.
    """
    hits: list[dict] = []
    for entry in hub_datasets(hub_host, timeout=timeout):
        title = (entry.get("title") or "").strip()
        low = title.lower()
        if "tree" not in low or "canopy" in low:
            continue
        rest = next(
            (
                d.get("accessURL")
                for d in entry.get("distribution") or []
                if d.get("format") == "ArcGIS GeoServices REST API"
            ),
            None,
        )
        if not rest:
            continue
        hits.append(
            {"title": title, "modified": entry.get("modified"), "rest_url": rest}
        )
    return sorted(hits, key=lambda h: h.get("modified") or "", reverse=True)


def hub_last_modified(
    hub_host: str, rest_url: str, *, timeout: int = 120
) -> datetime:
    """The Hub catalogue's `modified` stamp for the dataset served at *rest_url*.

    The **third** freshness watermark, and the last resort of the three.
    `layer_last_edit` reads the layer's own `editingInfo` and `field_max` reads
    an edit-date column; a layer that publishes neither has nothing inside it
    to read, and four of the cities wired on this module are in exactly that
    position — Kingston, Lethbridge, Victoria and Kelowna all serve their tree
    inventory from an on-prem ArcGIS Server whose layer resource carries no
    `editingInfo` and whose schema carries no edit date.

    What their Hub site does publish is a per-dataset `modified` in the same
    DCAT-US feed `find_tree_layers` reads, and it moves at a believable
    cadence (Lethbridge 2025-09-15, Kingston 2026-06-08, Kelowna 2025-12-03).

    **Know what it is before you reach for it.**  This is the *catalogue's*
    stamp, not the data's, so it has both failure modes: it can move when only
    a description changed, and it can fail to move when the publisher
    overwrites the service in place.  The second is the dangerous one — it is
    how Toronto's CKAN resource stamp would have frozen that city on its first
    build (see `_ckan_shared.data_last_modified`).  So prefer a real edit
    stamp wherever one exists, and where a city has both, take the **maximum**
    of the two rather than picking one: that is the rule `data_last_modified`
    arrived at, and it is the only one that cannot freeze a city.

    Matching is on the REST endpoint rather than the dataset title, because a
    title is prose someone re-words and the endpoint is the thing the ingest
    already hardcodes.  Raises `RuntimeError` when the feed names no such
    dataset — that is our wiring being wrong, not the portal being down, and
    `emit_freshness` must not turn it into "no new data".
    """
    wanted = _normalise_rest_url(rest_url)
    for entry in hub_datasets(hub_host, timeout=timeout):
        for dist in entry.get("distribution") or []:
            if _normalise_rest_url(dist.get("accessURL") or "") != wanted:
                continue
            stamp = _parse_dcat_datetime(entry.get("modified"))
            if stamp is None:
                raise RuntimeError(
                    f"{hub_host} lists {rest_url} with an unreadable "
                    f"modified stamp: {entry.get('modified')!r}"
                )
            return stamp
    raise RuntimeError(f"{hub_host} publishes no dataset served at {rest_url}")


def _normalise_rest_url(url: str) -> str:
    """A REST endpoint reduced to what identifies it, for comparison."""
    return url.split("?")[0].rstrip("/").casefold()


def _parse_dcat_datetime(value) -> datetime | None:
    """A DCAT `modified` string to an aware UTC datetime, or None.

    The feed writes RFC 3339 with a `Z` (`2025-09-15T23:36:08.000Z`), which
    `fromisoformat` did not accept before 3.11 and which some Hub sites emit
    as a bare date instead.
    """
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    # `uv run _arcgis_shared.py <hub-host>` — the first step of the city
    # runbook, so finding a portal's tree layer is not a manual browse.
    if len(sys.argv) != 2:
        print("usage: _arcgis_shared.py <arcgis-hub-host>", file=sys.stderr)
        raise SystemExit(2)
    try:
        found = find_tree_layers(sys.argv[1])
    except UpstreamUnavailable as exc:
        print(f"hub unavailable: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if not found:
        print("no tree layers found", file=sys.stderr)
        raise SystemExit(1)
    for hit in found:
        print(f"{hit['modified']}  {hit['title']}\n    {hit['rest_url']}")
