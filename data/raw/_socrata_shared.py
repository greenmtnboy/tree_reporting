"""Shared helpers for reading Socrata (Tyler Data & Insights) open data portals.

NOT a uv inline script — a regular importable module, like `_ingest_shared`.

Socrata is the second platform this repo talks to more than a couple of times:
San Francisco, New York and Los Angeles were each carrying their own copy of
the same three operations — build a `/resource/{id}` URL, read `rowsUpdatedAt`
out of `/api/views/{id}.json`, and page the rows by `$offset` — and the three
Canadian cities added alongside this module would have made six.  `EXTENDING.md`
sets the threshold at three ("write the module when the third city arrives"),
and this is what it asks for: one implementation, a thin shim per city, the way
`_arcgis_shared` did for ArcGIS and `_osm_shared` for Overpass.

Usage:

    from _socrata_shared import SocrataDataset, iter_rows, rows_updated_at

    DATASET = SocrataDataset("data.winnipeg.ca", "hfwk-jp4h")

    for page in iter_rows(DATASET, select="tree_id,botanical_name,point"):
        ...

Three things in here are correctness rather than convenience, and each was a
live defect in the copies this replaces:

* **`$order` is mandatory** — see `iter_rows`.  Offset paging over an unordered
  SoQL result may repeat or skip rows between requests.
* **`$limit` defaults to paging, not to one big number.**  SF asks for
  `$limit=500000` in a single request and stops there; nothing says so when the
  dataset outgrows it.  See `PAGE_SIZE`.
* **Every JSON value arrives as a string.**  Socrata encodes `11` as `"11"`, so
  a column left to inference lands in the Parquet as text — the same class of
  silent type drift `enforce_tree_schema` exists to stop.  Parse numerics in
  the ingest.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from _ingest_shared import UpstreamUnavailable, get_json_with_retry

if TYPE_CHECKING:
    from collections.abc import Iterator


# ---------------------------------------------------------------------------
# Dataset addressing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SocrataDataset:
    """One Socrata dataset, addressed by its host and four-four identifier.

    The identifier is the `xxxx-xxxx` in the dataset's URL — `tfs4-3wwa` for
    Calgary's Public Trees.  Everything below is built from the two endpoints
    that identifier reaches: `/resource/{id}` for rows and `/api/views/{id}`
    for metadata.

    `timeout` travels with the dataset rather than being passed at every call
    site: a dataset is either the small metadata read or the big paged one, and
    the pages are what needs the generous budget.
    """

    domain: str
    dataset_id: str
    timeout: int = 120

    @property
    def host(self) -> str:
        return self.domain.replace("https://", "").replace("http://", "").strip("/")

    @property
    def rows_url(self) -> str:
        """The JSON row endpoint.  See `rows_url_as` for CSV."""
        return self.rows_url_as("json")

    def rows_url_as(self, fmt: str) -> str:
        return f"https://{self.host}/resource/{self.dataset_id}.{fmt}"

    @property
    def metadata_url(self) -> str:
        return f"https://{self.host}/api/views/{self.dataset_id}.json"

    @property
    def landing_url(self) -> str:
        """Where a person should look — for a comment or an error message."""
        return f"https://{self.host}/d/{self.dataset_id}"


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------

APP_TOKEN_ENV = "SOCRATA_APP_TOKEN"


def app_token_headers() -> dict[str, str]:
    """`X-App-Token` when one is configured, otherwise no header at all.

    Socrata throttles anonymous traffic per IP and registered traffic per
    token.  Every request here works without one — the existing three cities
    have always run anonymous — so this is a pressure valve rather than a
    dependency: setting `SOCRATA_APP_TOKEN` in the job's environment raises the
    ceiling without any code change, and leaving it unset keeps a new city
    wirable from a workstation with no registration step.
    """
    token = os.environ.get(APP_TOKEN_ENV, "").strip()
    return {"X-App-Token": token} if token else {}


# ---------------------------------------------------------------------------
# Metadata and freshness
# ---------------------------------------------------------------------------

def dataset_metadata(dataset: SocrataDataset) -> dict:
    """The dataset's view metadata (`/api/views/{id}.json`)."""
    return get_json_with_retry(
        dataset.metadata_url,
        timeout=dataset.timeout,
        headers=app_token_headers() or None,
    )


def rows_updated_at(dataset: SocrataDataset) -> datetime:
    """When the dataset's *rows* last changed, for a freshness probe.

    `rowsUpdatedAt` moves when the data changes; `viewLastModified` also moves
    for a description edit, which would rebuild a city because someone fixed a
    typo in the portal.  Prefer the former, exactly as the three hand-rolled
    copies of this did.

    Socrata's stamp is **seconds** since the epoch, not Esri's milliseconds —
    the one place these two platform modules disagree, and worth naming because
    a factor of 1000 in a watermark reads as "the portal published in 1970" and
    degrades silently into never rebuilding.

    Raises `RuntimeError` when the field is absent: that is the portal changing
    its metadata shape, which is a schema question rather than an availability
    one and must not degrade to "no new data" (see `emit_freshness`).  A real
    outage is caught by `get_json_with_retry` inside it and does degrade.
    """
    stamp = dataset_metadata(dataset).get("rowsUpdatedAt")
    if stamp is None:
        raise RuntimeError(
            f"rowsUpdatedAt missing from Socrata metadata: {dataset.metadata_url}"
        )
    return datetime.fromtimestamp(float(stamp), tz=timezone.utc)


def row_count(dataset: SocrataDataset, *, where: str | None = None) -> int:
    """`select count(*)`, optionally filtered.

    One row back, not the table — the cheap way to check a candidate id column
    for nulls before committing to it (`where="wam_id IS NULL"`), which is the
    check `EXTENDING.md` asks for under "`tree_id` is the grain".
    """
    params = {"$select": "count(*) as n"}
    if where:
        params["$where"] = where
    payload = get_json_with_retry(
        dataset.rows_url,
        params=params,
        timeout=dataset.timeout,
        headers=app_token_headers() or None,
    )
    if not payload:
        raise RuntimeError(f"count(*) returned no rows on {dataset.rows_url}")
    row = payload[0]
    value = row.get("n") or row.get("count") or row.get("count_1")
    if value is None:
        raise RuntimeError(f"count(*) gave no count on {dataset.rows_url}: {row}")
    return int(value)


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------

# One request's worth of rows.  A page is held as dicts while it is transformed,
# so this — not the dataset — is the ingest's memory footprint.  50k keeps a
# 580k-row city to twelve requests without the whole-city peak that OOM-killed
# Washington DC's 2 GiB container at 216k rows.
PAGE_SIZE = 50_000

# Socrata's system row identifier.  Always present, never null, and stable
# across a republish, which is what makes it the right thing to order by when a
# dataset has no natural key of its own.
SYSTEM_ID = ":id"


def iter_rows(
    dataset: SocrataDataset,
    *,
    select: str | None = None,
    where: str | None = None,
    order: str = SYSTEM_ID,
    page_size: int = PAGE_SIZE,
    extra_params: dict[str, str] | None = None,
) -> Iterator[list[dict]]:
    """Pages of raw records, one HTTP request at a time.

    Yields lists of dicts so a caller can feed `_ingest_shared.stream_to_table`
    and never hold the whole dataset in memory.

    Two things here are correctness, not tidiness:

    * **`order` is required and defaults to `:id`.**  SoQL makes no promise
      about the order of an unordered result, so `$offset` paging over one may
      repeat or skip rows between requests — and a short page from that ends
      the loop early, which is a silently truncated city that looks exactly
      like a portal publishing less.  New York's ingest pages by `$offset`
      today with no `$order` at all.  Passing an empty string is refused, the
      same way `_arcgis_shared.iter_features` refuses an empty `order_by`.
    * **Termination is on a short page**, which is exact here in a way it is
      not on ArcGIS: `$limit` is a promise Socrata keeps unless the data ran
      out, and there is no server-side cap quietly rewriting it (a request for
      more than the dataset holds returns the dataset, not an error).

    Every value in a returned dict is a **string** — see the module docstring.
    """
    if not order:
        raise ValueError(
            "iter_rows needs an order: $offset paging over an unordered SoQL "
            "result can repeat or skip rows, which truncates a city silently"
        )
    headers = app_token_headers() or None
    offset = 0
    while True:
        params = {
            "$limit": str(page_size),
            "$offset": str(offset),
            "$order": order,
        }
        if select:
            params["$select"] = select
        if where:
            params["$where"] = where
        if extra_params:
            params.update(extra_params)

        page = get_json_with_retry(
            dataset.rows_url,
            params=params,
            timeout=dataset.timeout,
            headers=headers,
        )
        if not page:
            return
        yield page
        if len(page) < page_size:
            return
        offset += len(page)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def point_lon_lat(value) -> tuple[float | None, float | None]:
    """`(lon, lat)` from whichever point shape a Socrata column carries.

    Three spellings are in use across the portals this repo reads, and a city
    does not get to choose which one it is handed:

    * a GeoJSON object — `{"type": "Point", "coordinates": [lon, lat]}`
      (Calgary's `point`, Winnipeg's `point`);
    * a location object — `{"latitude": "...", "longitude": "..."}`
      (Edmonton's `location`);
    * WKT text — `"POINT (lon lat)"` (New York's `location` in CSV form).

    Anything unrecognised or unparseable returns `(None, None)` rather than
    raising: a single row with a broken coordinate is a row
    `validate_coordinates` will drop, not a reason to fail the city's refresh.
    """
    if value is None:
        return None, None

    if isinstance(value, dict):
        coords = value.get("coordinates")
        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            return _as_float(coords[0]), _as_float(coords[1])
        if "latitude" in value or "longitude" in value:
            return _as_float(value.get("longitude")), _as_float(value.get("latitude"))
        return None, None

    if isinstance(value, str):
        text = value.strip()
        if not text.upper().startswith("POINT"):
            return None, None
        inner = text[text.find("(") + 1 : text.rfind(")")] if "(" in text else ""
        parts = inner.replace(",", " ").split()
        if len(parts) < 2:
            return None, None
        return _as_float(parts[0]), _as_float(parts[1])

    return None, None


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Catalogue discovery
# ---------------------------------------------------------------------------

DISCOVERY_URL = "https://api.us.socrata.com/api/catalog/v1"


def catalog_search(
    domain: str, *, query: str, limit: int = 100, timeout: int = 120
) -> list[dict]:
    """Datasets on *domain* matching *query*, via the Socrata discovery API.

    Scoped to the one domain on purpose.  The discovery API federates every
    Socrata portal, so an unscoped search for "tree" against a Winnipeg host
    cheerfully returns New York's street tree census — which is a confusing
    way to wire the wrong city.
    """
    host = domain.replace("https://", "").replace("http://", "").strip("/")
    payload = get_json_with_retry(
        DISCOVERY_URL,
        params={
            "q": query,
            "domains": host,
            "search_context": host,
            "only": "dataset",
            "limit": str(limit),
        },
        timeout=timeout,
        headers=app_token_headers() or None,
    )
    return payload.get("results") or []


def find_tree_datasets(domain: str, *, timeout: int = 120) -> list[dict]:
    """Candidate tree-inventory datasets on a Socrata portal.

    The counterpart of `_arcgis_shared.find_tree_layers`, and it needs the same
    canopy exclusion for the same reason: of the eight top hits for "tree" on
    `data.calgary.ca`, seven are canopy-cover rasters and one is the inventory.

    Returns `{"title", "dataset_id", "updated", "link"}` for each, newest
    first.
    """
    hits: list[dict] = []
    for entry in catalog_search(domain, query="tree", timeout=timeout):
        resource = entry.get("resource") or {}
        title = (resource.get("name") or "").strip()
        low = title.lower()
        if "tree" not in low or "canopy" in low:
            continue
        hits.append(
            {
                "title": title,
                "dataset_id": resource.get("id"),
                "updated": resource.get("updatedAt"),
                "link": entry.get("link"),
            }
        )
    return sorted(hits, key=lambda h: h.get("updated") or "", reverse=True)


if __name__ == "__main__":
    # `uv run _socrata_shared.py <domain>` — the Socrata half of the runbook's
    # first step, so finding a portal's tree dataset is not a manual browse.
    if len(sys.argv) != 2:
        print("usage: _socrata_shared.py <socrata-domain>", file=sys.stderr)
        raise SystemExit(2)
    try:
        found = find_tree_datasets(sys.argv[1])
    except UpstreamUnavailable as exc:
        print(f"portal unavailable: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if not found:
        print("no tree datasets found", file=sys.stderr)
        raise SystemExit(1)
    for hit in found:
        print(f"{hit['updated']}  {hit['title']}\n    {hit['link']}")
