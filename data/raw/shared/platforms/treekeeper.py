"""Read a Davey TreeKeeper public map's site inventory.

NOT a uv inline script -- a regular importable module, like `shared.platforms.arcgis`.

Somerville and Belmont publish no tree dataset on an open-data portal; their
inventories live in TreeKeeper (`{town}.treekeepersoftware.com`), which each
town links as its public tree map.  TreeKeeper has no export API.  The public
map does two things a script can repeat without a login: select every site
inside a polygon into a server-side "map search" keyed by a client-chosen id,
then page the attribute grid over that selection.  The grid is the same one
the map's CSV export reads.  The GeoServer behind the map serves the points
over open WFS, but with only an id and a site type, so it cannot supply the
species.

Two things here are correctness rather than convenience:

* **The selection count is the termination test.**  The grid pages over the
  selection it was given, so a read shorter than the count is a truncated
  page, not a smaller town, and raises.
* **The scientific name is the last top-level parenthesised group**, not the
  text after the last `(`: Davey's do-not-plant marker is
  `Vacant (Do Not Plant) (Vacant (Do-Not-Plant))`.

What differs per town is the host, the two layer ids and the grid's column
names.  The layer ids come from the map's own layer listing, which needs the
session cookie the index page sets (`find_layers` below prints it).  The
columns are per-project `SITE_ATTR*` numbers, and which of them the public
grid exposes is the town's choice: Somerville's carries `SITE_INVENTORYDATE`,
Belmont's no date at all.

    from shared.platforms.treekeeper import TreeKeeperSite, fetch_sites, split_label

    SITE = TreeKeeperSite("https://somervillema.treekeepersoftware.com/",
                          map_layer="main_layer14", grid_layer=14, bbox=(...))
    for row in fetch_sites(SITE):
        common, scientific = split_label(row["SITE_ATTR41"])
"""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

# This module is also runnable (the layer listing at the bottom), and being
# run directly puts `shared/platforms` on the path rather than `data/raw`,
# so the package import has to be bootstrapped.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.ingest import normalize_tree_name, post_json_with_retry  # noqa: E402

PAGE_SIZE = 5000


@dataclass(frozen=True)
class TreeKeeperSite:
    """One town's TreeKeeper project.

    *map_layer* is the tree layer's `bmlayers_mapid` (the selection is made
    against it) and *grid_layer* its `bmlayers_facilitytype`, which is what
    the grid calls `layer_id`.  *bbox* is (lat_min, lat_max, lon_min, lon_max),
    drawn generously around the town: TreeKeeper selects by containment, and
    every site in a project is that town's.
    """

    url: str
    map_layer: str
    grid_layer: int
    bbox: tuple[float, float, float, float]

    def polygon(self) -> dict:
        lat_min, lat_max, lon_min, lon_max = self.bbox
        return {
            "type": "Polygon",
            "coordinates": [[
                [lon_min, lat_min], [lon_max, lat_min], [lon_max, lat_max],
                [lon_min, lat_max], [lon_min, lat_min],
            ]],
        }


def select_sites(site: TreeKeeperSite) -> tuple[str, int]:
    """Select every site into a fresh map search; return its id and size.

    The id is the client's to choose -- the map makes a nine-character random
    one -- and the selection lives on the server under it, so no session
    cookie is needed.
    """
    selection = uuid.uuid4().hex[:9]
    payload = post_json_with_retry(
        f"{site.url}cffiles/search.cfc?method=selectByGeometry"
        f"&uid={selection}&geometryType=polygon",
        data=json.dumps(
            {"geom": json.dumps(site.polygon()), "layersArray": [site.map_layer]}
        ),
        timeout=120,
        backoff=2.0,
    )
    return selection, int(payload["count"])


def fetch_grid(
    site: TreeKeeperSite,
    selection: str,
    *,
    offset: int = 0,
    limit: int = PAGE_SIZE,
    sorts: list | None = None,
) -> dict:
    query = (
        f"{site.url}cffiles/grids.cfc?method=getTreeKeeperGridOptionsData"
        f"&session_id={selection}&layer_id={site.grid_layer}&gridType=sites&limit={limit}"
    )
    if offset:
        query += f"&offset={offset}"
    return post_json_with_retry(
        query,
        data=json.dumps({"filters": "[]", "sorts": json.dumps(sorts or [])}),
        timeout=120,
        backoff=2.0,
    )


def fetch_sites(site: TreeKeeperSite) -> list[dict]:
    """Every site's grid row, raising on a read shorter than the selection."""
    selection, expected = select_sites(site)
    rows: list[dict] = []
    while True:
        page = fetch_grid(site, selection, offset=len(rows)).get("data", [])
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
    if len(rows) != expected:
        raise RuntimeError(
            f"TreeKeeper {site.url}: selected {expected} sites but the grid returned {len(rows)}"
        )
    return rows


def newest_value(site: TreeKeeperSite, column: str, sort_type: int = 6):
    """The largest *column* on any site, read as one sorted grid row.

    *sort_type* is the grid's own column type code; 6 is a date.  A column the
    public grid does not expose sorts nothing and comes back missing, so the
    caller must check for None.
    """
    selection, _ = select_sites(site)
    page = fetch_grid(
        site,
        selection,
        limit=1,
        sorts=[{"name": column, "desc": True, "priority": 0, "type": sort_type}],
    ).get("data", [])
    return page[0].get(column) if page else None


def split_label(label: str | None) -> tuple[str | None, str | None]:
    """`common (Scientific)` -> (common, scientific).

    The scientific name is the LAST top-level parenthesised group, which is
    not the last `(`: Davey's do-not-plant marker is
    `Vacant (Do Not Plant) (Vacant (Do-Not-Plant))`.
    """
    if not label:
        return None, None
    text = label.strip()
    if not text.endswith(")"):
        return text, None
    depth = 0
    for i in range(len(text) - 1, -1, -1):
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            depth -= 1
            if depth == 0:
                return text[:i].strip() or None, text[i + 1 : -1].strip() or None
    return text, None


def tree_name_for(common: str | None) -> str | None:
    """TreeKeeper common names come genus-first, with a colon (`maple:
    Norway`, `cherry/plum: spp.`) or a comma (`Maple, Norway`), which is the
    inversion `normalize_tree_name` already undoes; `spp.` is not a name."""
    if not common:
        return None
    head, sep, qualifier = common.partition(":")
    qualifier = qualifier.strip()
    if sep and qualifier.lower() in ("spp.", "spp"):
        return normalize_tree_name(head)
    if sep:
        return normalize_tree_name(f"{head.strip()}, {qualifier}")
    return normalize_tree_name(common)


def find_layers(url: str) -> list[dict]:
    """The map's layer listing, through the app's API proxy.

    Unlike the selection and the grid, the proxy answers only with the
    session cookie the index page sets.
    """
    import requests

    session = requests.Session()
    session.get(f"{url}index.cfm?deviceWidth=1920", timeout=60).raise_for_status()
    response = session.post(
        f"{url}application/APIRequests.cfc?method=makeAPIRequest",
        data=json.dumps(
            {
                "type": "GET",
                "microserviceName": "core",
                "endpoint": "/map/facilities?allLayers=true",
                "dataParams": {},
                "optionalHTTP": {},
            }
        ),
        headers={"Content-Type": "application/json"},
        timeout=60,
    )
    response.raise_for_status()
    body = {key.lower(): value for key, value in response.json().items()}
    return json.loads(body["filecontent"])["data"]


if __name__ == "__main__":
    # python shared/platforms/treekeeper.py https://belmontma.treekeepersoftware.com/
    for layer in find_layers(sys.argv[1]):
        print(
            f"map_layer={layer.get('bmlayers_mapid')!r} "
            f"grid_layer={layer.get('bmlayers_facilitytype')} "
            f"title={layer.get('bmlayers_title')!r}"
        )
