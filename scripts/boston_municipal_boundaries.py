"""MassGIS municipal geometry for the Boston poster, cached with provenance."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import matplotlib.patheffects as path_effects
import numpy as np
from shapely.geometry import Point, box, shape
from shapely.ops import transform, unary_union

SERVICE = "https://services1.arcgis.com/hGdibHYSPO59RG1h/ArcGIS/rest/services/Massachusetts_Municipalities_Hosted/FeatureServer/0"


def load_boundaries(bounds, projection):
    lon0, lat0 = projection["central_longitude"], projection["central_latitude"]
    kx = 111.195 * np.cos(np.radians(lat0))
    envelope = [bounds[0]/kx+lon0, bounds[2]/111.195+lat0,
                bounds[1]/kx+lon0, bounds[3]/111.195+lat0]
    key = hashlib.sha256(json.dumps(envelope).encode()).hexdigest()[:16]
    cache = Path(__file__).resolve().parents[1] / ".tmp" / f"boston-municipalities-{key}.geojson"

    def query(**params):
        with urlopen(SERVICE + "/query?" + urlencode(params), timeout=90) as response:
            result = json.load(response)
        if "error" in result or result.get("exceededTransferLimit"):
            raise RuntimeError(f"Incomplete MassGIS boundary query: {result}")
        return result

    if not cache.exists():
        ids = query(f="json", where="1=1", geometry=",".join(map(str, envelope)),
                    geometryType="esriGeometryEnvelope", inSR=4326,
                    spatialRel="esriSpatialRelIntersects", returnIdsOnly="true")["objectIds"]
        if not ids:
            raise ValueError("MassGIS returned no municipalities for this frame.")
        features = []
        for start in range(0, len(ids), 50):
            features.extend(query(f="geojson", objectIds=",".join(map(str, ids[start:start+50])),
                                  outFields="OBJECTID,TOWN", outSR=4326,
                                  returnGeometry="true")["features"])
        if len(features) != len(ids):
            raise ValueError("MassGIS returned an incomplete municipality set.")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    collection = json.loads(cache.read_text(encoding="utf-8"))
    municipalities = []
    for feature in collection["features"]:
        geometry = shape(feature["geometry"])
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        projected = transform(lambda x, y, z=None: ((np.asarray(x)-lon0)*kx,
                                                   (np.asarray(y)-lat0)*111.195), geometry)
        municipalities.append((feature["properties"]["TOWN"], projected))
    return municipalities, {"service": SERVICE, "credit": "Municipal boundaries: MassGIS",
                            "source_documentation": "https://www.mass.gov/info-details/massgis-data-municipalities",
                            "cache": str(cache), "cache_sha256": hashlib.sha256(cache.read_bytes()).hexdigest(),
                            "outline_rule": "Shared municipal edges only; external coastlines and island perimeters omitted",
                            "municipalities": sorted(name for name, _ in municipalities)}


def shared_boundaries(municipalities):
    """Keep internal jurisdiction edges, including those crossing rivers.

    The union's external perimeter contains coastlines and island outlines.
    Removing it from all polygon edges leaves only shared municipal borders.
    """
    geometries = [geometry for _, geometry in municipalities]
    return unary_union([geometry.boundary for geometry in geometries]).difference(unary_union(geometries).boundary)


def draw_boundaries(ax, municipalities, bounds, palette, circle=None, scale=1):
    xmin, xmax, ymin, ymax = bounds
    frame = box(xmin, ymin, xmax, ymax)
    # Clip existing outlines, not polygons: clipping must not invent a frame boundary.
    outlines = shared_boundaries(municipalities).intersection(frame)
    lines = [outlines] if outlines.geom_type == "LineString" else list(outlines.geoms)
    ink = "#8b651e" if palette["bg"] == "#f7f5ed" else palette["muted"]
    label_ink = "#3e5b5c" if palette["bg"] == "#f7f5ed" else palette["muted"]
    halo = [path_effects.withStroke(linewidth=2.2*scale, foreground=palette["bg"])]
    for line in lines:
        if line.geom_type != "LineString":
            continue
        coordinates = np.asarray(line.coords)
        ax.plot(coordinates[:, 0], coordinates[:, 1], color=ink, lw=.8*scale,
                linestyle=(0, (4, 2)), path_effects=halo, zorder=3)
    label_area = frame.buffer(-.12)
    if circle:
        label_area = label_area.intersection(Point(*circle["centre_projected_km"]).buffer(circle["fade_start_km"]-.3))
    for name, geometry in municipalities:
        visible = geometry.intersection(label_area)
        if visible.is_empty:
            continue
        parts = [visible] if visible.geom_type == "Polygon" else list(visible.geoms)
        largest = max(parts, key=lambda part: part.area)
        if largest.area < (.15 if xmax-xmin < 5 else 1.5):
            continue
        anchor = largest.representative_point()
        ax.text(anchor.x, anchor.y, name.title(), ha="center", va="center",
                color=label_ink, fontsize=9*scale, path_effects=halo, zorder=4, clip_on=True)
