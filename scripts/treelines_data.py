"""City-independent published-tree filtering, projection, and water loading."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import duckdb
from matplotlib.path import Path as MplPath
import numpy as np
from shapely.geometry import box, shape
from shapely.geometry.polygon import orient
from shapely.ops import unary_union


def project_lonlat(lon, lat, projection):
    kx = 111.195 * np.cos(np.radians(projection["central_latitude"]))
    return ((np.asarray(lon)-projection["central_longitude"])*kx,
            (np.asarray(lat)-projection["central_latitude"])*111.195)


def source_groups(sources, inventory_sources):
    """Partition surviving source names; imagery merged into an inventory stays inventory."""
    names = sorted(set(str(s) for s in sources))
    inventory = tuple(s for s in names if s in inventory_sources)
    aerial = tuple(s for s in names if s not in inventory and s.startswith(("PREDICTED_", "SATELLITE_")))
    other = tuple(s for s in names if s not in inventory + aerial)
    return inventory, aerial, other


def load_city_points(location, city, inventory_sources, source_mode):
    with duckdb.connect() as con:
        con.execute("SET TimeZone='UTC'")
        con.read_parquet(location).create_view("trees")
        columns = {r[0] for r in con.execute("DESCRIBE trees").fetchall()}
        required = {"tree_id", "city", "data_source", "latitude", "longitude"}
        if required - columns:
            raise ValueError(f"Missing columns: {sorted(required-columns)}")
        filters = ["city = ?", "isfinite(latitude)", "isfinite(longitude)",
                   "latitude BETWEEN -90 AND 90", "longitude BETWEEN -180 AND 180"]
        for flag in ("reported_missing", "is_duplicate", f"{city.lower()}_border_duplicate"):
            if flag in columns:
                filters.append(f"NOT coalesce({flag}, false)")
        if "cluster_id" in columns:
            filters.append("(cluster_id IS NULL OR tree_id = cluster_id)")
        stamp = f"{city.lower()}_published_data_updated_through"
        stamp = stamp if stamp in columns else "NULL"
        crown = "crown_width_m" if "crown_width_m" in columns else "NULL::DOUBLE"
        con.execute(f"CREATE TEMP TABLE visible AS SELECT tree_id, data_source, latitude, longitude, {stamp} AS updated, {crown} AS crown_width_m FROM trees WHERE {' AND '.join(filters)}", [city])
        available = dict(con.execute("SELECT data_source,count(*) FROM visible GROUP BY 1 ORDER BY 1").fetchall())
        if source_mode == "inventory":
            selection, params = "data_source IN (SELECT unnest(?))", [list(inventory_sources)]
        elif source_mode == "non-aerial":
            selection, params = "NOT starts_with(data_source, 'PREDICTED_') AND NOT starts_with(data_source, 'SATELLITE_')", []
        else:
            selection, params = "true", []
        con.execute(f"CREATE TEMP TABLE selected AS SELECT * FROM visible WHERE {selection}", params)
        rows, unique = con.execute("SELECT count(*), count(DISTINCT tree_id) FROM selected").fetchone()
        if not rows or rows != unique:
            raise ValueError(f"Expected nonempty unique records; got {rows} rows, {unique} IDs.")
        dates = con.execute("SELECT min(updated)::VARCHAR,max(updated)::VARCHAR FROM selected").fetchone()
        points = con.execute("SELECT tree_id,data_source,latitude,longitude,coalesce(crown_width_m,'NaN'::DOUBLE) AS crown_width_m FROM selected ORDER BY tree_id").fetchnumpy()
    selected_counts = {str(s): int(np.sum(points["data_source"] == s)) for s in np.unique(points["data_source"])}
    return points, {"input": location, "city": city, "source_mode": source_mode,
                    "visibility_filters": filters, "source_selection": selection,
                    "inventory_sources": list(inventory_sources), "available_counts_by_source": available,
                    "selected_counts_by_source": selected_counts,
                    "excluded_counts_by_source": {s: n-selected_counts.get(s, 0) for s, n in available.items() if n != selected_counts.get(s, 0)},
                    "published_updated_through_range": dates}


def _request(url, params):
    with urlopen(url + "?" + urlencode(params), timeout=120) as response:
        result = json.load(response)
    if "error" in result or result.get("exceededTransferLimit"):
        raise RuntimeError(f"Incomplete water response from {url}: {result}")
    return result


def load_city_water(config, projection):
    envelope = config["viewport_lonlat"]
    clip = box(*envelope)
    geometries, provenance = [], []
    cache_dir = Path(__file__).resolve().parents[1] / ".tmp"
    cache_dir.mkdir(exist_ok=True)
    for source in config["water_sources"]:
        key = hashlib.sha256(json.dumps([source, envelope], sort_keys=True).encode()).hexdigest()[:16]
        cache = cache_dir / f"treelines-water-{key}.geojson"
        if not cache.exists():
            features = []
            if source["kind"] == "arcgis":
                query_url = source["url"] + "/query"
                ids = _request(query_url, {"f": "json", "where": source.get("where", "1=1"),
                    "geometry": ",".join(map(str, envelope)), "geometryType": "esriGeometryEnvelope",
                    "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "returnIdsOnly": "true"}).get("objectIds") or []
                for start in range(0, len(ids), 50):
                    features.extend(_request(query_url, {"f": "geojson", "objectIds": ",".join(map(str, ids[start:start+50])),
                        "outFields": "*", "outSR": 4326, "returnGeometry": "true",
                        "maxAllowableOffset": .000015})["features"])
                if len(features) != len(ids):
                    raise ValueError("Incomplete water geometry response.")
            elif source["kind"] == "socrata":
                offset = 0
                while True:
                    batch = _request(source["url"], {"$limit": 1000, "$offset": offset, "$order": ":id"})["features"]
                    features.extend(batch)
                    if len(batch) < 1000:
                        break
                    offset += len(batch)
            else:
                raise ValueError(f"Unknown water provider: {source['kind']}")
            if not features:
                raise ValueError(f"No water geometry returned by {source['url']}")
            cache.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
        collection = json.loads(cache.read_text(encoding="utf-8"))
        for feature in collection["features"]:
            geometry = shape(feature["geometry"])
            if not geometry.is_valid:
                geometry = geometry.buffer(0)
            geometries.append(geometry.intersection(clip))
        provenance.append({**source, "cache": str(cache), "features": len(collection["features"]),
                           "sha256": hashlib.sha256(cache.read_bytes()).hexdigest()})
    water = unary_union(geometries)
    paths = []
    parts = [water] if water.geom_type == "Polygon" else list(water.geoms)
    for polygon in parts:
        if polygon.geom_type != "Polygon" or polygon.is_empty:
            continue
        vertices, codes = [], []
        rings = orient(polygon, sign=1)
        for ring in [rings.exterior, *rings.interiors]:
            coords = np.asarray(ring.coords)
            px, py = project_lonlat(coords[:, 0], coords[:, 1], projection)
            vertices.extend(np.column_stack([px, py]))
            codes.extend([MplPath.MOVETO] + [MplPath.LINETO]*(len(coords)-2) + [MplPath.CLOSEPOLY])
        paths.append(MplPath(vertices, codes))
    if not paths:
        raise ValueError("No water polygons intersect the viewport.")
    return paths, provenance
