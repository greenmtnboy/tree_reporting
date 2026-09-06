"""Shared helpers for reading CKAN open data portals.

NOT a uv inline script — a regular importable module, like `_ingest_shared`.

CKAN is the third platform this repo talks to more than a couple of times.
Boston has read `data.boston.gov` since it was wired, and Toronto, Montreal and
Quebec City make four; `EXTENDING.md` sets the threshold at three ("write the
module when the third city arrives"), and this is what it asks for: one
implementation, a thin shim per city, the way `_arcgis_shared` did for ArcGIS,
`_socrata_shared` for Socrata and `_osm_shared` for Overpass.

Usage:

    from _ckan_shared import CkanResource, data_last_modified, iter_datastore_rows

    RESOURCE = CkanResource(
        "ckan0.cf.opendata.inter.prod-toronto.ca",
        "3dafa392-c6ab-4f37-9bf9-21ddf7308eaf",
    )

    for page in iter_datastore_rows(RESOURCE, fields="STRUCTID,BOTANICAL_NAME,geometry"):
        ...

Three things in here are correctness rather than convenience, and each was
measured against the four portals rather than assumed:

* **`limit` is silently capped at 32,000** and terminating on a short page
  truncates a city.  See `iter_datastore_rows`.
* **`sort` is mandatory.**  `datastore_search` pages by `offset` and promises
  nothing about the order of an unsorted result.
* **No single timestamp is the freshness watermark.**  See
  `data_last_modified`, which is the one place this module contradicts the
  handoff note in `CANADA_SOURCES.md` — and it contradicts it because Toronto
  would otherwise have been frozen in 2022 for ever.

**Every city wired onto this module is datastore-backed**, so the row reader
here is the datastore one only.  A portal with `datastore_active = False`
publishes its rows as a file (CSV, GeoJSON, shapefile) and needs a reader
written when the first such city arrives — deliberately not written in
advance, since the shape of that reader is not knowable until a source needs
it.  `resource_download_url` is the piece such a reader would start from, and
is used today by Boston's CSV ingest.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from _ingest_shared import UpstreamUnavailable, get_json_with_retry

if TYPE_CHECKING:
    from collections.abc import Iterator


# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------

def _host_of(domain: str) -> str:
    """`donnees.montreal.ca` from any spelling of it.

    Données Québec serves its API under a path (`/recherche`), not at the root,
    so the trailing slash is stripped but an inner path is kept:
    `www.donneesquebec.ca/recherche` is the host *and* base path for two of the
    four cities here.
    """
    return domain.replace("https://", "").replace("http://", "").strip("/")


@dataclass(frozen=True)
class CkanResource:
    """One CKAN resource — a single file or datastore table within a package.

    A resource id is the uuid in the portal's URL after `/resource/`.  It is
    what both halves of an ingest address: `iter_datastore_rows` reads its
    rows, and `data_last_modified` reads its publication time.

    The package is *not* passed in.  `resource_show` reports the resource's
    `package_id`, so one identifier reaches both, and a city's shim carries one
    constant instead of two that can drift apart.

    `timeout` travels with the resource rather than being passed at every call
    site: a resource is either the small metadata read or the big paged one,
    and the pages are what needs the generous budget.
    """

    domain: str
    resource_id: str
    timeout: int = 120

    @property
    def host(self) -> str:
        return _host_of(self.domain)

    @property
    def api(self) -> str:
        return f"https://{self.host}/api/3/action"

    @property
    def landing_url(self) -> str:
        """Where a person should look — for a comment or an error message."""
        return f"https://{self.host}/dataset/?res_id={self.resource_id}"


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def _action(resource: CkanResource, action: str, params: dict) -> dict:
    payload = get_json_with_retry(
        f"{resource.api}/{action}", params=params, timeout=resource.timeout
    )
    # CKAN wraps every answer in {"success": bool, "result": ...}.  A
    # `success: false` body is already treated as an outage by
    # `get_json_with_retry` (it classifies provider error envelopes), so
    # reaching here with no result is a shape change rather than a blip.
    result = payload.get("result")
    if result is None:
        raise RuntimeError(
            f"CKAN {action} returned no result for {resource.resource_id} "
            f"on {resource.host}: {str(payload)[:200]}"
        )
    return result


def resource_metadata(resource: CkanResource) -> dict:
    """`resource_show` — the resource's own record."""
    return _action(resource, "resource_show", {"id": resource.resource_id})


def package_metadata(resource: CkanResource) -> dict:
    """`package_show` for the package this resource belongs to.

    Two requests, because the caller holds only a resource id.  Both are small
    JSON reads and this runs once per probe, which is the cheap half of a
    refresh — the expensive half is the download it decides not to do.
    """
    package_id = resource_metadata(resource).get("package_id")
    if not package_id:
        raise RuntimeError(
            f"CKAN resource_show gave no package_id for {resource.resource_id} "
            f"on {resource.host}"
        )
    return _action(resource, "package_show", {"id": package_id})


def _parse_stamp(value) -> datetime | None:
    """CKAN's timestamps, which are naive ISO-8601 in UTC.

    `2026-06-04T20:41:24.911345` and `2026-06-04 20:41:43.434545` both occur —
    the space-separated spelling in Toronto's non-standard `last_refreshed` —
    and `fromisoformat` accepts both.  A naive value is stamped UTC, which is
    what CKAN documents and what every portal here publishes.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def data_last_modified(resource: CkanResource) -> datetime:
    """When this resource's *data* last changed, for a freshness probe.

    **The maximum of the data stamps, not the first one present.**  This is the
    one place the module contradicts the handoff note in `CANADA_SOURCES.md`,
    which proposed a preference order of resource `last_modified`, then the
    package's `last_refreshed`, then `metadata_modified`.  Measured against the
    four portals, that order freezes Toronto for ever:

    | portal    | resource `last_modified` | package `last_refreshed` |
    |-----------|--------------------------|--------------------------|
    | Toronto   | **2022-05-02**           | 2026-06-04               |
    | Boston    | 2026-09-06               | absent                   |
    | Montreal  | 2026-09-06               | absent                   |
    | Quebec    | 2026-09-04               | absent                   |

    Toronto's rows are current — the datastore holds all 688,335 of them — but
    its datastore is updated **in place**, so the resource record has not been
    edited since 2022 and its `last_modified` records that edit rather than the
    data.  Boston's datastore resource, on the same CKAN version, *does* move.
    So neither stamp is reliably the answer and neither is reliably wrong; the
    honest reading is that each one moves only when *something* real happened,
    and the latest of them is when the data was last published.

    Preferring the resource stamp would have stored 2022-05-02 on Toronto's
    first build and then compared fresh for ever — the city's Parquet silently
    never rebuilding, which is the exact failure mode `EXTENDING.md` warns a
    missing probe produces.

    `metadata_modified` is deliberately **not** in the maximum, only a
    fallback.  It moves when a description is edited, so including it would
    rebuild a city because somebody fixed a typo on the portal — Longueuil's
    package carries a 2026-02-09 `metadata_modified` over data last published
    2024-03-01.  It is used only when neither data stamp exists at all, which
    is better than having no watermark.

    Raises `RuntimeError` when none of the three is present: that is the portal
    changing its metadata shape, which is a schema question rather than an
    availability one and must not degrade to "no new data" (see
    `emit_freshness`).  A real outage is caught by `get_json_with_retry` inside
    the reads and does degrade.
    """
    resource_meta = resource_metadata(resource)
    package_id = resource_meta.get("package_id")
    package_meta = (
        _action(resource, "package_show", {"id": package_id}) if package_id else {}
    )

    candidates = [
        _parse_stamp(resource_meta.get("last_modified")),
        _parse_stamp(package_meta.get("last_refreshed")),
    ]
    stamps = [stamp for stamp in candidates if stamp is not None]
    if stamps:
        return max(stamps)

    fallback = _parse_stamp(
        resource_meta.get("created") or package_meta.get("metadata_modified")
    )
    if fallback is not None:
        return fallback

    raise RuntimeError(
        "CKAN metadata carries no usable timestamp for resource "
        f"{resource.resource_id} on {resource.host} — looked for the "
        "resource's last_modified/created and the package's "
        "last_refreshed/metadata_modified"
    )


def resource_download_url(resource: CkanResource) -> str:
    """The resource's own download URL, as the portal reports it.

    Preferable to a hardcoded one: CKAN's download path ends in the uploaded
    *filename*, so a city that re-uploads its export under a new name breaks a
    pasted URL and does not break this.
    """
    url = resource_metadata(resource).get("url")
    if not url:
        raise RuntimeError(
            f"CKAN resource_show gave no url for {resource.resource_id} "
            f"on {resource.host}"
        )
    return url


# ---------------------------------------------------------------------------
# Datastore paging
# ---------------------------------------------------------------------------

# What to ask for.  The server caps this (see `iter_datastore_rows`) and the
# effective size is read back off the first response, so this is a ceiling
# rather than a promise: raising it does not make a page bigger, and lowering
# it does bound memory, since a page is held as dicts while it is transformed.
PAGE_SIZE = 32_000

# The datastore's own row key: an auto-incrementing integer present on every
# row of every resource, never null, and the only column guaranteed to exist.
SYSTEM_ID = "_id"


def datastore_total(resource: CkanResource) -> int:
    """How many rows the datastore holds, from a one-row probe.

    The cheap way to check a candidate id column, together with `filters`:
    `datastore_total(R, filters={"STRUCTID": ""})` counts the blanks, which is
    the check `EXTENDING.md` asks for under "`tree_id` is the grain".
    """
    result = _action(
        resource,
        "datastore_search",
        {"resource_id": resource.resource_id, "limit": 0},
    )
    total = result.get("total")
    if total is None:
        raise RuntimeError(
            f"datastore_search reported no total for {resource.resource_id} "
            f"on {resource.host} — is the datastore active for this resource?"
        )
    return int(total)


def datastore_fields(resource: CkanResource) -> list[dict]:
    """The datastore's column list, as `{"id": name, "type": pg_type}`."""
    result = _action(
        resource,
        "datastore_search",
        {"resource_id": resource.resource_id, "limit": 0},
    )
    return result.get("fields") or []


def iter_datastore_rows(
    resource: CkanResource,
    *,
    fields: str | None = None,
    filters: dict | None = None,
    sort: str = SYSTEM_ID,
    page_size: int = PAGE_SIZE,
) -> Iterator[list[dict]]:
    """Pages of raw records, one HTTP request at a time.

    Yields lists of dicts so a caller can feed `_ingest_shared.stream_to_table`
    and never hold the whole dataset in memory.

    Three things here are correctness, not tidiness:

    * **`limit` is silently capped, so a short page cannot end the loop.**
      CKAN clamps `limit` to `ckan.datastore.search.rows_max`, which is 32,000
      on all four portals here, and answers a request for more with 32,000 rows
      and no error — the same silent cap `_arcgis_shared` hit with
      `maxRecordCount`.  A loop that stops on `len(page) < requested` therefore
      stops after **one** page whenever the caller asks for more than the cap:
      Toronto would publish 32,000 of its 688,335 trees and look exactly like a
      city whose portal shrank.  Two things prevent it — the effective page
      size is taken from the server's echoed `limit`, and termination is on
      `total` (which CKAN reports on every response) or an empty page, never on
      a short one.

    * **`sort` is required and defaults to `_id`.**  `datastore_search` pages
      by `offset` over a result CKAN makes no ordering promise about, so
      paging an unsorted result may repeat or skip rows between requests.
      Passing an empty string is refused, the same way
      `_socrata_shared.iter_rows` refuses an empty `$order` and
      `_arcgis_shared.iter_features` an empty `order_by`.

    * **`datastore_search_sql` is not used anywhere in this module**, however
      convenient it looks: Toronto's portal answers it with a 404.  It is
      disabled per-site and cannot be relied on, so the paged search — which
      every CKAN with an active datastore serves — is the only row reader here.

    Values arrive **typed** on this endpoint, unlike Socrata's all-strings: a
    column CKAN typed `int4` comes back as an int and a `text` one as a string.
    That is a property of the datastore, not of the portal's CSV, so an ingest
    still parses defensively.
    """
    if not sort:
        raise ValueError(
            "iter_datastore_rows needs a sort: offset paging over an unsorted "
            "datastore result can repeat or skip rows, which truncates a city "
            "silently"
        )
    offset = 0
    total: int | None = None
    while True:
        params: dict = {
            "resource_id": resource.resource_id,
            "limit": page_size,
            "offset": offset,
            "sort": sort,
        }
        if fields:
            params["fields"] = fields
        if filters:
            params["filters"] = _json_filters(filters)

        result = _action(resource, "datastore_search", params)
        records = result.get("records") or []
        if not records:
            return

        if total is None:
            total = int(result.get("total") or 0)
            # The cap, read back rather than assumed.  CKAN echoes the limit it
            # actually applied; trusting the requested one is what turns the
            # silent clamp above into a truncated city.
            applied = result.get("limit")
            if applied and int(applied) < page_size:
                page_size = int(applied)

        yield records
        offset += len(records)
        if total and offset >= total:
            return


def _json_filters(filters: dict) -> str:
    """CKAN takes `filters` as a JSON object in the query string."""
    return json.dumps(filters)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def point_lon_lat(value) -> tuple[float | None, float | None]:
    """`(lon, lat)` from whichever point shape a CKAN column carries.

    A datastore column holding geometry is `text`, so a GeoJSON point arrives
    as the *string* `{"type": "Point", "coordinates": [lon, lat]}` (Toronto's
    `geometry`) rather than as an object — which is the one shape
    `_socrata_shared.point_lon_lat` does not accept, and the reason this is not
    simply imported from there.  A dict, a `{lat, lon}` pair and WKT text are
    all accepted too.

    Anything unrecognised or unparseable returns `(None, None)` rather than
    raising: a single row with a broken coordinate is a row
    `validate_coordinates` will drop, not a reason to fail the city's refresh.
    """
    if value is None:
        return None, None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, None
        if text.startswith("{"):
            try:
                value = json.loads(text)
            except ValueError:
                return None, None
        elif text.upper().startswith("POINT"):
            inner = text[text.find("(") + 1 : text.rfind(")")] if "(" in text else ""
            parts = inner.replace(",", " ").split()
            if len(parts) < 2:
                return None, None
            return _as_float(parts[0]), _as_float(parts[1])
        else:
            return None, None

    if isinstance(value, dict):
        coords = value.get("coordinates")
        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            return _as_float(coords[0]), _as_float(coords[1])
        if "latitude" in value or "longitude" in value:
            return _as_float(value.get("longitude")), _as_float(value.get("latitude"))

    return None, None


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Catalogue discovery
# ---------------------------------------------------------------------------

# The francophone portals index in French, so an English-only search finds
# nothing on donnees.montreal.ca or donneesquebec.ca.  Both terms are tried and
# the hits merged.
TREE_QUERIES = ("tree", "arbres")


def package_search(
    domain: str, *, query: str, rows: int = 50, timeout: int = 120
) -> list[dict]:
    """Packages on *domain* matching *query*, via `package_search`."""
    payload = get_json_with_retry(
        f"https://{_host_of(domain)}/api/3/action/package_search",
        params={"q": query, "rows": str(rows)},
        timeout=timeout,
    )
    return (payload.get("result") or {}).get("results") or []


def find_tree_datasets(
    domain: str, *, queries: tuple[str, ...] = TREE_QUERIES, timeout: int = 120
) -> list[dict]:
    """Candidate tree-inventory packages on a CKAN portal.

    The counterpart of `_arcgis_shared.find_tree_layers` and
    `_socrata_shared.find_tree_datasets`, and it needs the same canopy
    exclusion for the same reason: a search for "tree" returns the canopy-cover
    and planting-plan datasets alongside the inventory.

    Returns one entry per package with its datastore-backed resources listed,
    because on CKAN the *resource* is what an ingest addresses and whether it
    is datastore-backed decides how it can be read at all.
    """
    seen: dict[str, dict] = {}
    for query in queries:
        for package in package_search(domain, query=query, timeout=timeout):
            title = (package.get("title") or "").strip()
            low = title.lower()
            if not any(word in low for word in ("tree", "arbre")):
                continue
            if "canopy" in low or "canopee" in low or "canopée" in low:
                continue
            seen[package.get("id") or title] = {
                "title": title,
                "package_id": package.get("id"),
                "name": package.get("name"),
                "license": package.get("license_title"),
                "modified": package.get("metadata_modified"),
                "resources": [
                    {
                        "name": (res.get("name") or "").strip(),
                        "format": res.get("format"),
                        "resource_id": res.get("id"),
                        "datastore": bool(res.get("datastore_active")),
                        "last_modified": res.get("last_modified"),
                    }
                    for res in package.get("resources") or []
                ],
            }
    return sorted(seen.values(), key=lambda h: h.get("modified") or "", reverse=True)


if __name__ == "__main__":
    # `uv run _ckan_shared.py <host>` — the CKAN half of the runbook's first
    # step, so finding a portal's tree dataset is not a manual browse.  The
    # host may carry a path: donneesquebec.ca serves its API under /recherche.
    if len(sys.argv) != 2:
        print("usage: _ckan_shared.py <ckan-host>", file=sys.stderr)
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
        print(f"{hit['modified']}  {hit['title']}  [{hit['license']}]")
        for res in hit["resources"]:
            flag = "datastore" if res["datastore"] else "file only"
            print(
                f"    {res['format'] or '?':>8}  {flag:>9}  {res['resource_id']}"
                f"  {res['name']}"
            )
