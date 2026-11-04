#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb", "matplotlib", "numpy", "scipy", "shapely"]
# ///
"""Boston's canopy as a night map, coloured by published source.

    uv run scripts/render_boston_sources_map.py
    uv run scripts/render_boston_sources_map.py --layout comparison
    uv run scripts/render_boston_sources_map.py --dpi 400 --width 20
    uv run scripts/render_boston_sources_map.py --palette forest
    uv run scripts/render_boston_sources_map.py --palette forest --inset brookline
    uv run scripts/render_boston_sources_map.py --palette forest --inset brookline --inset-canopy
    uv run scripts/render_boston_sources_map.py --palette forest --inset coolidge_corner --inset-canopy
    uv run scripts/render_boston_sources_map.py --palette forest --inset harvard_square --inset-canopy
    uv run scripts/render_boston_sources_map.py --palette forest --inset arboretum --inset-canopy
    uv run scripts/render_boston_sources_map.py --palette forest --inset back_bay --inset-canopy
    uv run scripts/render_boston_sources_map.py --palette forest --inset boston_common --inset-canopy
    uv run scripts/render_boston_sources_map.py --palette forest --inset back_bay_public_garden --inset-canopy
    uv run scripts/render_boston_sources_map.py --palette forest --inset mount_auburn --inset-canopy
    uv run scripts/render_boston_sources_map.py --palette forest --inset mount_auburn --inset-canopy --north-shift-km 1.5
    uv run scripts/render_boston_sources_map.py --palette light --inset mount_auburn --inset-canopy --north-shift-km 1.5

Outputs default to this script's directory. Each image has a JSON provenance
sidecar. --input accepts a local snapshot or a published parquet URL.

Imagery means PREDICTED_USBOS plus reviewed SATELLITE_USBOS, both aerial
imagery, not necessarily spaceborne. These are surviving source categories:
an imagery detection absorbed into an inventory record stays inventory.
The two counts are not independent surveys or a measure of completeness.
All non-imagery sources, including OSM/community, share the warm colour.
Default metro framing follows inventory/imagery coverage; --extent all includes
outlying OSM records. Counts on the image describe only points within the frame.
Water is cached from MassGIS; --no-water reproduces the tree-only treatment.
The default circular fade follows the actual bulk-imagery planning circle and
fades water, glow and points together. --no-circle-fade restores the rectangular
view (also use it with --extent all to reveal every outlying record).
--north-shift-km moves the main viewport and artistic vignette north without
moving the data or inset, or extending the actual imagery coverage.
--frame-from reuses a previous JSON sidecar's projection and final viewport
so newly published coverage does not change the composition. The vignette
still follows --north-shift-km; the saved viewport is not shifted again.
--inset adds a square zoom with municipal and institutional inventory records
divided by all mapped records in that same square. OSM/community are separate;
the denominator is mapped records after merging, not verified ground truth.
"""
from __future__ import annotations

import argparse
from copy import copy
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as path_effects
from matplotlib.path import Path as MplPath
from matplotlib.patches import Rectangle, ConnectionPatch
import numpy as np
from shapely.geometry import shape, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from boston_municipal_boundaries import load_boundaries, draw_boundaries
from treelines_core import (BG, PALETTES, draw_map, draw_circle_fade,
                            draw_cartographic_details, draw_centered_text_runs)

DEFAULT_INPUT = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/usbos_tree_info_v2.parquet"
DEFAULT_CROWNS_INPUT = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/usbos_tree_predictions_v2.parquet"
MUNICIPAL = ("CITY_OF_BOSTON", "CAMBRIDGE", "BROOKLINE", "SOMERVILLE", "BELMONT", "WATERTOWN")
INVENTORY = MUNICIPAL + ("ARNOLD_ARBORETUM",)
IMAGERY = ("PREDICTED_USBOS", "SATELLITE_USBOS")
INSET_CENTRES = {
    "brookline": (-71.127, 42.337), "downtown": (-71.061, 42.356),
    "coolidge_corner": (-71.1213, 42.3422),
    "boston_common": (-71.0656, 42.3550),
    "back_bay_public_garden": (-71.0738, 42.3525),
    "mount_auburn": (-71.1450, 42.3711),
    "harvard_square": (-71.119, 42.3736),
    "arboretum": (-71.1266, 42.299), "back_bay": (-71.082, 42.35),
}
INSET_LABELS = {"brookline": "Brookline", "downtown": "Downtown Boston",
                "coolidge_corner": "Coolidge Corner",
                "boston_common": "Boston Common",
                "back_bay_public_garden": "Back Bay & Public Garden",
                "mount_auburn": "Mount Auburn Cemetery",
                "harvard_square": "Harvard Square", "arboretum": "Arnold Arboretum",
                "back_bay": "Back Bay"}
INSET_SUMMARY_BOTTOM = .100
WATER_SERVICE = "https://services1.arcgis.com/hGdibHYSPO59RG1h/ArcGIS/rest/services/Massachusetts_Water_Features/FeatureServer/8"
WATER_CREDIT = "Water: USGS / MassDEP / MassGIS"
CIRCLE_PATH = Path(__file__).resolve().parents[1] / "imagery_model/bulk_areas/usbos-metro-circle.geojson"


def circle_fade_config(projection, feather, north_shift_km=0):
    """Use the planning footprint as the base for an optional artistic shift."""
    boundary = json.loads(CIRCLE_PATH.read_text(encoding="utf-8"))
    props = boundary["features"][0]["properties"]
    lon, lat = props["centre"]
    coverage_centre = [lon, lat]
    lat += north_shift_km / 111.195
    lat0 = projection["central_latitude"]
    cx = (lon - projection["central_longitude"]) * 111.195 * np.cos(np.radians(lat0))
    cy = (lat - lat0) * 111.195
    radius = props["radius_m"] / 1000
    return {"source": str(CIRCLE_PATH), "centre_lonlat": [lon, lat],
            "coverage_centre_lonlat": coverage_centre, "north_shift_km": north_shift_km,
            "centre_projected_km": [cx, cy], "radius_km": radius,
            "fade_start_km": radius * (1.03 - feather), "fade_end_km": radius * 1.03}


def load_water(bounds, projection):
    """Fetch actual open-water polygons, keeping islands and excluding wetlands."""
    lon0, lat0 = projection["central_longitude"], projection["central_latitude"]
    kx = 111.195 * np.cos(np.radians(lat0))
    west, east = (bounds[0] / kx + lon0, bounds[1] / kx + lon0)
    south, north = (bounds[2] / 111.195 + lat0, bounds[3] / 111.195 + lat0)
    envelope = [west, south, east, north]
    key = hashlib.sha256(json.dumps(envelope).encode()).hexdigest()[:16]
    cache = Path(__file__).resolve().parents[1] / ".tmp" / f"boston-water-{key}.geojson"
    def query(params):
        with urlopen(WATER_SERVICE + "/query?" + urlencode(params), timeout=90) as response:
            data = json.load(response)
        if "error" in data:
            raise RuntimeError(f"Water service: {data['error']}")
        return data
    if cache.exists():
        collection = json.loads(cache.read_text(encoding="utf-8"))
    else:
        print("Fetching MassGIS open-water polygons ...", flush=True)
        ids = query({"f": "json", "where": "POLY_CODE IN (1,6,8)",
                     "geometry": ",".join(map(str, envelope)), "geometryType": "esriGeometryEnvelope",
                     "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "returnIdsOnly": "true"})
        features = []
        object_ids = sorted(ids.get("objectIds") or [])
        if not object_ids:
            raise ValueError("Water query returned no polygons; use --no-water to omit the layer explicitly.")
        for start in range(0, len(object_ids), 100):
            data = query({"f": "geojson", "objectIds": ",".join(map(str, object_ids[start:start+100])),
                          "outFields": "OBJECTID,NAME,POLY_CODE", "outSR": 4326,
                          "returnGeometry": "true", "maxAllowableOffset": .000015})
            if data.get("exceededTransferLimit"):
                raise RuntimeError("Water query was truncated; refusing an incomplete shoreline.")
            features.extend(data["features"])
        if len(features) != len(object_ids):
            raise RuntimeError("Water service returned an incomplete set of polygons.")
        collection = {"type": "FeatureCollection", "features": features}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(collection), encoding="utf-8")
    clip = box(*envelope)
    polygons = []
    for feature in collection["features"]:
        geom = shape(feature["geometry"])
        if not geom.is_valid:
            geom = geom.buffer(0)
        polygons.append(geom.intersection(clip))
    water = unary_union(polygons)
    parts = [water] if water.geom_type == "Polygon" else list(water.geoms)
    paths = []
    for polygon in parts:
        if polygon.geom_type != "Polygon" or polygon.is_empty:
            continue
        rings = orient(polygon, sign=1)
        vertices, codes = [], []
        for ring in [rings.exterior, *rings.interiors]:
            coords = np.array(ring.coords)
            coords[:, 0] = (coords[:, 0] - lon0) * kx
            coords[:, 1] = (coords[:, 1] - lat0) * 111.195
            vertices.extend(coords)
            codes.extend([MplPath.MOVETO] + [MplPath.LINETO] * (len(coords) - 2) + [MplPath.CLOSEPOLY])
        paths.append(MplPath(vertices, codes))
    return paths, {"service": WATER_SERVICE, "credit": WATER_CREDIT,
                   "filter": "POLY_CODE IN (1,6,8): reservoirs and open water; no wetlands",
                   "cache": str(cache), "features": len(collection["features"]),
                   "cache_sha256": hashlib.sha256(cache.read_bytes()).hexdigest()}


def load_points(location: str):
    """Read once, respect published visibility flags, and report exclusions."""
    with duckdb.connect() as con:
        con.execute("SET TimeZone='UTC'")
        con.read_parquet(location).create_view("trees")
        columns = {r[0] for r in con.execute("DESCRIBE trees").fetchall()}
        required = {"tree_id", "city", "data_source", "latitude", "longitude"}
        if required - columns:
            raise ValueError(f"Missing columns: {sorted(required - columns)}")
        predicates = ["city = 'USBOS'"]
        for flag in ("reported_missing", "is_duplicate", "usbos_border_duplicate"):
            if flag in columns:
                predicates.append(f"NOT coalesce({flag}, false)")
        if "cluster_id" in columns:
            predicates.append("(cluster_id IS NULL OR tree_id = cluster_id)")
        predicates += ["isfinite(latitude)", "isfinite(longitude)",
                       "latitude BETWEEN -90 AND 90", "longitude BETWEEN -180 AND 180"]
        stamp = "usbos_published_data_updated_through"
        stamp_expr = stamp if stamp in columns else "NULL"
        crown_expr = "crown_width_m" if "crown_width_m" in columns else "NULL::DOUBLE"
        con.execute(f"""CREATE TEMP TABLE visible AS
            SELECT tree_id, data_source, latitude, longitude, {stamp_expr} AS updated,
                   {crown_expr} AS crown_width_m
            FROM trees WHERE {' AND '.join(predicates)}""")
        counts = dict(con.execute("SELECT data_source, count(*) FROM visible GROUP BY 1 ORDER BY 1").fetchall())
        rows, unique = con.execute("SELECT count(*), count(DISTINCT tree_id) FROM visible").fetchone()
        if rows != unique:
            raise ValueError("Repeated tree IDs in input; use the published city parquet.")
        dates = con.execute("SELECT min(updated)::VARCHAR, max(updated)::VARCHAR FROM visible").fetchone()
        points = con.execute("""SELECT tree_id, data_source, latitude, longitude,
            coalesce(crown_width_m, 'NaN'::DOUBLE) AS crown_width_m
            FROM visible ORDER BY tree_id""").fetchnumpy()
    if not len(points["latitude"]):
        raise ValueError("No Boston inventory or imagery points in this input.")
    for names in (INVENTORY, IMAGERY):
        if not any(counts.get(name, 0) for name in names):
            raise ValueError(f"No points for comparison category: {names}")
    return points, {"visible_counts_by_source": counts, "visibility_filters": predicates,
                    "published_updated_through_range": [str(d) if d else None for d in dates],
                    "excluded_other_sources": {}}


def project(points, extent="metro"):
    """Local equirectangular kilometres; equal x/y scale preserves Boston's shape."""
    lon, lat = points["longitude"], points["latitude"]
    lon0, lat0 = float(np.mean(lon)), float(np.mean(lat))
    x = (lon - lon0) * 111.195 * np.cos(np.radians(lat0))
    y = (lat - lat0) * 111.195
    framing = np.isin(points["data_source"], INVENTORY + IMAGERY) if extent == "metro" else np.ones(len(x), dtype=bool)
    xmin, xmax, ymin, ymax = float(x[framing].min()), float(x[framing].max()), float(y[framing].min()), float(y[framing].max())
    pad = max(xmax - xmin, ymax - ymin) * .035
    bounds = (xmin - pad, xmax + pad, ymin - pad, ymax + pad)
    return x, y, bounds, {"name": "local equirectangular", "central_longitude": lon0,
                           "central_latitude": lat0, "units": "km"}


def load_canopy_widths(points, mask, prediction_input):
    """Prefer source widths, then join model estimates by ID without adding rows."""
    ids = points["tree_id"][mask]
    widths = np.array(points["crown_width_m"][mask], dtype=float, copy=True)
    native = np.isfinite(widths) & (widths > 0)
    widths[~native] = np.nan
    missing_ids = ids[~native].tolist()
    if missing_ids:
        with duckdb.connect() as con:
            con.read_parquet(prediction_input).create_view("predictions")
            rows = con.execute("""SELECT tree_id, max(predicted_crown_width_m) FROM predictions
                WHERE city='USBOS' AND tree_id IN (SELECT unnest(?))
                  AND isfinite(predicted_crown_width_m) AND predicted_crown_width_m > 0
                GROUP BY tree_id""", [missing_ids]).fetchall()
        estimates = dict(rows)
        for index in np.flatnonzero(~native):
            widths[index] = estimates.get(ids[index], np.nan)
    valid = np.isfinite(widths) & (widths > 0)
    summary = {"predictions_input": prediction_input, "source_width_count": int(native.sum()),
               "estimated_width_count": int((valid & ~native).sum()),
               "missing_width_count": int((~valid).sum()),
               "diameter_range_m": [float(widths[valid].min()), float(widths[valid].max())] if valid.any() else None,
               "radius_rule": "crown_width_m / 2, converted to kilometres; no enlargement or clamping",
               "missing_width_rendering": "center dot only",
               "semantics": "Source crown widths may themselves be model outputs. Circles approximate crowns, not measured crown outlines."}
    print(f"Canopy: {summary['source_width_count']:,} source widths, {summary['estimated_width_count']:,} estimates, {summary['missing_width_count']:,} dots only", flush=True)
    return widths, summary


def draw_inset(fig, main_ax, points, x, y, projection, water_paths, palette, args, scale, municipalities=(), stacked_position=None):
    """Square in projected kilometres; count the same selected rows we draw."""
    lon, lat = INSET_CENTRES[args.inset]
    label = INSET_LABELS[args.inset]
    cx = (lon - projection["central_longitude"]) * 111.195 * np.cos(np.radians(projection["central_latitude"]))
    cy = (lat - projection["central_latitude"]) * 111.195
    half = args.inset_size_km / 2
    bounds = (cx-half, cx+half, cy-half, cy+half)
    mask = (x >= bounds[0]) & (x < bounds[1]) & (y >= bounds[2]) & (y < bounds[3])
    sources = points["data_source"][mask]
    counts = {str(s): int(np.sum(sources == s)) for s in np.unique(sources)}
    total = len(sources)
    if not total:
        raise ValueError("The inset has no mapped tree records.")
    inventoried = sum(counts.get(s, 0) for s in INVENTORY)
    imagery = sum(counts.get(s, 0) for s in IMAGERY)
    other = total-inventoried-imagery
    percent = 100 * inventoried / total
    other_sources = tuple(s for s in counts if s not in INVENTORY + IMAGERY)
    other_label = "OpenStreetMap" if other_sources == ("OSM_USBOS",) else "Other mapped records"
    left, width, bottom = (.735, .245, .465) if args.compact else (.700, .275, .445)
    sidebar_shift = .035 if args.compact else 0
    if stacked_position is not None:
        bottom = .500 if stacked_position == 0 else .145
        sidebar_shift = 0
    def sidebar_text(xpos, ypos, text, wrap=False, **style):
        artist = fig.text(xpos, ypos + sidebar_shift, text, **style)
        if wrap and args.compact:
            renderer = fig.canvas.get_renderer()
            max_width = width * fig.bbox.width * .97
            def text_width(value):
                artist.set_text(value)
                return artist.get_window_extent(renderer).width
            lines = []
            for paragraph in text.split("\n"):
                wrapped, line = [], ""
                for word in paragraph.split():
                    candidate = f"{line} {word}".strip()
                    if line and text_width(candidate) > max_width:
                        wrapped.append(line)
                        line = word
                    else:
                        line = candidate
                wrapped.append(line)
                # Avoid a lone trailing word while still using the column width.
                if len(wrapped) > 1:
                    while text_width(wrapped[-1]) < max_width * .35:
                        previous = wrapped[-2].split()
                        if len(previous) < 2:
                            break
                        wrapped[-1] = previous.pop() + " " + wrapped[-1]
                        wrapped[-2] = " ".join(previous)
                lines.extend(wrapped)
            artist.set_text("\n".join(lines))
        return artist
    inset_height = width * fig.get_figwidth() / fig.get_figheight()
    top = bottom + inset_height
    ax = fig.add_axes((left, bottom + sidebar_shift, width, inset_height))
    canopy_widths, canopy_meta = load_canopy_widths(points, mask, args.crowns_input) if args.inset_canopy else (None, None)
    inset_layers = ([(tuple(counts), palette["aerial"], .85)] if args.unified else
                    [(IMAGERY, palette["aerial"], .70), (other_sources, palette["muted"], .8),
                     (INVENTORY, palette["records"], .95)])
    draw_map(ax, x[mask], y[mask], sources, bounds, inset_layers,
             .6, .10, water_paths, None, palette, canopy_widths)
    if municipalities:
        draw_boundaries(ax, municipalities, bounds, palette, scale=scale)
    # Border and locator use exactly the bounds used for the statistic.
    outline = dict(fill=False, edgecolor=palette["records"], linewidth=.75)
    ax.add_patch(Rectangle((bounds[0], bounds[2]), args.inset_size_km, args.inset_size_km, zorder=8, **outline))
    main_ax.add_patch(Rectangle((bounds[0], bounds[2]), args.inset_size_km, args.inset_size_km, zorder=8, **outline))
    map_label = main_ax.text((bounds[0]+bounds[1])/2, bounds[3]+.28, label.upper(), color=palette.get("map_label", palette["records"]),
                 ha="center", fontsize=8*scale, zorder=9)
    if "map_label_outline" in palette:
        map_label.set_path_effects([
            path_effects.withStroke(linewidth=1.4*scale, foreground=palette["map_label_outline"])
        ])
    for source_y, target_y in ((bounds[2], 0), (bounds[3], 1)):
        connector = ConnectionPatch(xyA=(bounds[1], source_y), coordsA=main_ax.transData,
                                    xyB=(0, target_y), coordsB=ax.transAxes,
                                    arrowstyle="-", lw=.6, color=palette["records"], alpha=.55)
        fig.add_artist(connector)
    sidebar_text(left, top+.025, label.upper(), color=palette["text"], fontsize=17*scale)
    area_label = f"{args.inset_size_km:g} × {args.inset_size_km:g} km"
    if stacked_position is not None:
        area_label = f"{total:,} trees · " + area_label
    sidebar_text(left, bottom-.019, area_label,
             color=palette["muted"], fontsize=8*scale)
    if args.inset_canopy:
        sidebar_text(left+width, bottom-.019, "Circles show estimated canopy size.",
                     ha="right", color=palette["muted"], fontsize=7.5*scale)
    # A 500 m scale bar in the inset for the default 2 km square.
    bar_km = args.inset_size_km / 4
    bx, by = bounds[0]+.06*args.inset_size_km, bounds[2]+.06*args.inset_size_km
    ax.plot([bx, bx+bar_km], [by, by], color=palette["text"], lw=1.3, zorder=9)
    ax.text(bx, by+.035*args.inset_size_km, f"{bar_km*1000:g} m", color=palette["text"], fontsize=7*scale, zorder=9)
    if stacked_position is not None:
        pass  # Two maps fill the sidebar; only the compact captions remain.
    elif args.unified:
        sidebar_text(left, .416, "Every mapped tree, in one view.", wrap=True,
                     va="top", color=palette["text"], fontsize=12*scale)
        sidebar_text(left, .355, f"{total:,}", color=palette["aerial"], fontsize=40*scale)
        sidebar_text(left, .333, "mapped trees in the area shown above.", wrap=True,
                     va="top", color=palette["text"], fontsize=10*scale, linespacing=1.5)
        sidebar_text(left, .278, "Inventories, community maps and aerial detections combined, with known duplicate records removed.",
                     wrap=True, va="top", color=palette["text"], fontsize=10*scale, linespacing=1.5)
        sidebar_text(left, .187, "Mapped trees are not a complete census. Aerial detections can miss trees beneath the canopy and include errors.",
                     wrap=True, va="top", color=palette["muted"], fontsize=9*scale, linespacing=1.5)
    else:
        if args.compact:
            sidebar_text(left, .416, "City tree inventories can leaf out much of the urban forest.",
                         wrap=True, va="top", linespacing=1.4, color=palette["text"], fontsize=12*scale)
            sidebar_text(left, .331, "of mapped trees in the area shown above are recorded in tree inventories.",
                         wrap=True, va="top", linespacing=1.5, color=palette["text"], fontsize=10*scale)
        else:
            sidebar_text(left, .404, "City tree inventories can leaf out", color=palette["text"], fontsize=12*scale)
            sidebar_text(left, .386, "much of the urban forest.", color=palette["text"], fontsize=12*scale)
            sidebar_text(left, .321, "of mapped trees in the area shown above", color=palette["text"], fontsize=10*scale)
            sidebar_text(left, .302, "are recorded in tree inventories.", color=palette["text"], fontsize=10*scale)
        sidebar_text(left, .343, f"{percent:.1f}%", color=palette["records"], fontsize=40*scale)
        sidebar_text(left, .277, f"Inset: {inventoried:,} inventoried / {total:,} total mapped", color=palette["muted"], fontsize=9*scale)
        bar = fig.add_axes((left, .250 + sidebar_shift, width, .012))
        bar.axis("off")
        start = 0
        for n, color in ((inventoried, palette["records"]), (imagery, palette["aerial"]), (other, palette["muted"])):
            bar.add_patch(Rectangle((start/total, 0), n/total, 1, facecolor=color, linewidth=0))
            start += n
        inventory_label = "Municipal + Arboretum inventories" if counts.get("ARNOLD_ARBORETUM", 0) else "Municipal inventories"
        for ypos, n, source_label, color in ((.227, inventoried, inventory_label, palette["records"]),
                                     (.204, imagery, "Aerial detections", palette["aerial"]),
                                     (.181, other, other_label, palette["muted"])):
            sidebar_text(left, ypos, f"●  {source_label}", color=color, fontsize=9*scale)
            sidebar_text(left+width, ypos, f"{n:,}", color=color, fontsize=9*scale, ha="right")
    kx = 111.195 * np.cos(np.radians(projection["central_latitude"]))
    result = {"location": args.inset, "centre_lonlat": [lon, lat], "size_km": args.inset_size_km,
              "projected_bounds_km": bounds,
              "bounds_lonlat": [bounds[0]/kx+projection["central_longitude"],
                                bounds[2]/111.195+projection["central_latitude"],
                                bounds[1]/kx+projection["central_longitude"],
                                bounds[3]/111.195+projection["central_latitude"]],
              "counts_by_source": counts, "inventory_sources": list(INVENTORY),
              "inventoried": inventoried, "imagery": imagery, "other": other, "total": total,
              "inventoried_percent": percent,
              "municipal": sum(counts.get(s, 0) for s in MUNICIPAL),
              "canopy": canopy_meta,
              "definition": "Municipal and institutional inventory survivors / all published mapped records in the square, after visibility filters. Includes Arnold Arboretum; OSM and community records remain separate. Not an estimate of inventory completeness against ground truth."}
    print(f"Inset: {args.inset}, {inventoried:,}/{total:,} = {percent:.2f}% inventoried", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Published URL or local parquet")
    parser.add_argument("--layout", choices=("overlay", "comparison"), default="overlay")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--width", type=float, default=18, help="Figure width in inches")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--dot-size", type=float, default=.11, help="Point area in points squared")
    parser.add_argument("--glow", type=float, default=.14, help="Density glow opacity, 0 to 1")
    parser.add_argument("--title", default="Treelines of Boston, MA, USA")
    parser.add_argument("--palette", choices=tuple(PALETTES), default="night", help="Colour scheme (default: night)")
    parser.add_argument("--inset", choices=("none", *INSET_CENTRES), default="none", help="Add a square neighborhood zoom and inventory share")
    parser.add_argument("--inset-size-km", type=float, default=2, help="Side length of the inset square (default: 2 km)")
    parser.add_argument("--inset-canopy", action="store_true", help="Show canopy circles to scale in the inset")
    parser.add_argument("--crowns-input", default=DEFAULT_CROWNS_INPUT, help="Crown prediction parquet for widths absent from the tree source")
    parser.add_argument("--extent", choices=("metro", "all"), default="metro", help="Keep the original metro framing, or include every outlying record")
    parser.add_argument("--north-shift-km", type=float, default=0, help="Move the main viewport and circular fade north in km; data and inset stay fixed")
    parser.add_argument("--frame-from", type=Path, help="Reuse projection and final viewport from an earlier render JSON; overrides extent and viewport shift")
    parser.add_argument("--compact", action="store_true", help="Enlarge the main map, narrow the inset column, and tighten title/footer spacing")
    parser.add_argument("--cartographic-details", action="store_true", help="Add a centered north marker and main-map kilometre scale")
    parser.add_argument("--unified", action="store_true", help="Show all mapped trees equally, with combined counts instead of source comparisons (compact layout)")
    parser.add_argument("--municipal-boundaries", action="store_true", help="Overlay MassGIS municipal outlines and names (overlay layout)")
    parser.add_argument("--second-inset", choices=tuple(INSET_CENTRES), help="Stack another neighborhood below the primary inset; compact unified layout only")
    parser.add_argument("--no-water", action="store_true", help="Omit MassGIS water shading")
    parser.add_argument("--no-circle-fade", action="store_true", help="Show the full rectangular frame without the circular vignette")
    parser.add_argument("--edge-feather", type=float, default=.16, help="Circular fade width as a fraction of the footprint radius (default: .16)")
    args = parser.parse_args()
    palette = PALETTES[args.palette]
    if args.width <= 0 or args.dpi <= 0 or args.dot_size <= 0 or not 0 <= args.glow <= 1:
        parser.error("Width, DPI and dot size must be positive; glow must be in [0, 1].")
    if not 0 < args.edge_feather < 1:
        parser.error("Edge feather must be between 0 and 1, exclusive.")
    if not 0 < args.inset_size_km <= 5:
        parser.error("Inset size must be greater than zero and at most 5 km.")
    if not np.isfinite(args.north_shift_km):
        parser.error("North shift must be finite.")
    if args.inset != "none" and args.layout != "overlay":
        parser.error("The neighborhood inset uses --layout overlay.")
    if args.inset_canopy and args.inset == "none":
        parser.error("--inset-canopy requires a neighborhood --inset.")
    if args.compact and args.inset == "none":
        parser.error("--compact requires a neighborhood --inset.")
    if args.cartographic_details and args.layout != "overlay":
        parser.error("--cartographic-details requires --layout overlay.")
    if args.unified and not args.compact:
        parser.error("--unified requires --compact.")
    if args.second_inset and not (args.compact and args.unified):
        parser.error("--second-inset requires --compact --unified.")
    if args.second_inset == args.inset:
        parser.error("Choose two different inset locations.")
    if args.municipal_boundaries and args.layout != "overlay":
        parser.error("--municipal-boundaries requires --layout overlay.")
    if args.unified:
        palette = {**palette, "records": palette["aerial"]}
    print(f"Reading {args.input}", flush=True)
    points, provenance = load_points(args.input)
    x, y, bounds, projection = project(points, args.extent)
    bounds = (bounds[0], bounds[1], bounds[2] + args.north_shift_km, bounds[3] + args.north_shift_km)
    if args.frame_from:
        saved = json.loads(args.frame_from.read_text(encoding="utf-8"))
        projection = saved["projection"]
        if projection["name"] != "local equirectangular" or projection["units"] != "km":
            parser.error("Saved frame must use the local equirectangular kilometre projection.")
        bounds = tuple(saved["projected_bounds_km"])
        if len(bounds) != 4 or not np.all(np.isfinite(bounds)) or bounds[0] >= bounds[1] or bounds[2] >= bounds[3]:
            parser.error("Saved frame has invalid bounds.")
        x = (points["longitude"] - projection["central_longitude"]) * 111.195 * np.cos(np.radians(projection["central_latitude"]))
        y = (points["latitude"] - projection["central_latitude"]) * 111.195
    for inset_name in ([args.inset] if args.inset != "none" else []) + ([args.second_inset] if args.second_inset else []):
        lon, lat = INSET_CENTRES[inset_name]
        ix = (lon - projection["central_longitude"]) * 111.195 * np.cos(np.radians(projection["central_latitude"]))
        iy = (lat - projection["central_latitude"]) * 111.195
        half = args.inset_size_km / 2
        if not (bounds[0] <= ix-half and ix+half <= bounds[1] and bounds[2] <= iy-half and iy+half <= bounds[3]):
            parser.error("The shifted main viewport must contain the entire inset.")
    in_frame = (x >= bounds[0]) & (x <= bounds[1]) & (y >= bounds[2]) & (y <= bounds[3])
    provenance["outside_frame_by_source"] = {str(s): int(np.sum((points["data_source"] == s) & ~in_frame))
                                            for s in np.unique(points["data_source"]) if np.any((points["data_source"] == s) & ~in_frame)}
    points = {k: v[in_frame] for k, v in points.items()}
    x, y = x[in_frame], y[in_frame]
    counts = {str(s): int(np.sum(points["data_source"] == s)) for s in np.unique(points["data_source"])}
    other_sources = tuple(s for s in counts if s not in IMAGERY)
    inventory_count = sum(counts[s] for s in other_sources)
    imagery_count = sum(counts.get(s, 0) for s in IMAGERY)
    provenance["in_frame_counts_by_source"] = counts
    print(f"Non-aerial records in frame: {inventory_count:,}; imagery: {imagery_count:,}", flush=True)
    water_paths, water_meta = ([], None) if args.no_water else load_water(bounds, projection)
    municipalities, boundary_meta = load_boundaries(bounds, projection) if args.municipal_boundaries else ([], None)
    circle = None if args.no_circle_fade else circle_fade_config(projection, args.edge_feather, args.north_shift_km)
    aspect = (bounds[1] - bounds[0]) / (bounds[3] - bounds[2])
    comparison = args.layout == "comparison"
    height = args.width * .86 if args.inset != "none" else args.width / aspect * (.56 if comparison else 1.12)
    fig = plt.figure(figsize=(args.width, height), facecolor=palette["bg"])
    scale = args.width / 18
    plt.rcParams["font.family"] = "DejaVu Sans"
    has_inset = args.inset != "none"
    fig.text(.5, .962 if args.compact else (.942 if has_inset else .962), "\u2009".join(args.title), ha="center", va="top", color=palette["text"], fontsize=25 * scale)
    subtitle_y = .928 if args.compact else (.899 if has_inset else (.901 if comparison else .918))
    subtitle_runs = [
        ("A city revealed by plotting only ", palette["muted"], 12*scale, "normal"),
        ("trees", palette.get("subtitle_trees", "#66b68b"), 12*scale, "normal"),
        (" and ", palette["muted"], 12*scale, "normal"),
        ("water", palette.get("subtitle_water", "#7eaec3"), 12*scale, "normal"),
    ]
    if args.municipal_boundaries:
        subtitle_runs[0] = ("A city revealed by ", palette["muted"], 12*scale, "normal")
        subtitle_runs[2] = (", ", palette["muted"], 12*scale, "normal")
        subtitle_runs.append((" and municipal boundaries", palette["muted"], 12*scale, "normal"))
    draw_centered_text_runs(fig, .5, subtitle_y, subtitle_runs)
    aerial = (IMAGERY, palette["aerial"], .47)
    inventory = (other_sources, palette["records"], .88)
    inset_meta = None
    second_inset_meta = None
    if comparison:
        for left, layer, title in ((.025, inventory, "INVENTORIES + COMMUNITY MAPS"),
                                   (.515, aerial, "AERIAL-IMAGERY DETECTIONS")):
            ax = fig.add_axes((left, .19, .46, .64))
            draw_map(ax, x, y, points["data_source"], bounds, [layer], args.dot_size, args.glow, water_paths, circle, palette)
            fig.text(left + .23, .855, title, ha="center", color=layer[1], fontsize=10 * scale)
    else:
        ax = fig.add_axes((-.056362, .08783, .842724, .88434) if args.compact else ((-.012, .155, .715, .755) if has_inset else (.025, .16, .95, .72)))
        layers = [(tuple(counts), palette["aerial"], .65)] if args.unified else [aerial, inventory]
        draw_map(ax, x, y, points["data_source"], bounds, layers, args.dot_size, args.glow, water_paths, circle, palette)
        if municipalities:
            draw_boundaries(ax, municipalities, bounds, palette, circle, scale)
        if args.inset != "none":
            inset_meta = draw_inset(fig, ax, points, x, y, projection, water_paths, palette, args, scale, municipalities,
                                   stacked_position=0 if args.second_inset else None)
            if args.second_inset:
                secondary_args = copy(args)
                secondary_args.inset = args.second_inset
                second_inset_meta = draw_inset(fig, ax, points, x, y, projection, water_paths, palette, secondary_args, scale,
                                              municipalities, stacked_position=1)
        if args.cartographic_details:
            draw_cartographic_details(fig, ax, bounds, palette, scale)
    summary_x = (.19, .53) if args.compact else ((.18, .50) if inset_meta else (.27, .73))
    summary_shift = .039 if args.compact else 0
    count_note = "Counts within frame, before edge fade" if circle else "Counts within frame"
    if inset_meta and not args.compact:
        fig.text(.34, .172, "BOSTON METRO TOTALS", ha="center", color=palette["muted"], fontsize=7*scale)
    summaries = (
        (summary_x[0], palette["records"], inventory_count, "INVENTORIES + COMMUNITY MAPS", "Municipal inventories / Arnold Arboretum / OpenStreetMap / community"),
        (summary_x[1], palette["aerial"], imagery_count, "AERIAL-IMAGERY DETECTIONS", "Tree detections by ML from NAIP imagery"),
    )
    if args.unified:
        summaries = ((.365, palette["aerial"], inventory_count+imagery_count, "",
                      "coverage varies by source; incomplete"),)
    for xpos, color, count, title, detail in summaries:
        draw_centered_text_runs(fig, xpos, .145-summary_shift if inset_meta else .137, [
            (f"{count:,}", color, 24*scale, "normal"),
            ("  trees", color, 9*scale, "normal"),
        ])
        if title:
            fig.text(xpos, .121-summary_shift if inset_meta else .113, title, ha="center", color=color, fontsize=9 * scale)
        if args.compact and title == "AERIAL-IMAGERY DETECTIONS":
            detail += "  ·  Undercounts trees, especially beneath the canopy."
        fig.text(xpos, INSET_SUMMARY_BOTTOM-summary_shift if inset_meta else .092, detail,
                 ha="center", va="bottom" if inset_meta else "baseline", color=palette["muted"], fontsize=6.8 * scale)
    tree_credit = "Trees: municipal inventories, Arnold Arboretum, Arborary & © OpenStreetMap contributors (ODbL)"
    imagery_credit = "Imagery: USDA National Agriculture Imagery Program (NAIP)"
    water_credit = f" / {WATER_CREDIT}" if water_paths else ""
    boundary_credit = " / Boundaries: MassGIS" if boundary_meta else ""
    if args.compact:
        fig.text(.475, .025, tree_credit + "  |  " + imagery_credit + water_credit + boundary_credit,
                 ha="center", va="bottom", color=palette["muted"], fontsize=6.8*scale)
    else:
        fig.text(summary_x[1], .079 if inset_meta else .077,
                 "Detections undercount trees, especially beneath the canopy.",
                 ha="center", va="bottom", color=palette["muted"], fontsize=6.8*scale)
        fig.text(.5, .065 if inset_meta else .055, f"Boston metro / {count_note} / Coverage differs by source / North up", ha="center", color=palette["muted"], fontsize=8*scale)
        fig.text(.5, .045 if inset_meta else .030, tree_credit, ha="center", color=palette["muted"], fontsize=6.8*scale)
        fig.text(.5, .030 if inset_meta else .016, imagery_credit + water_credit + boundary_credit, ha="center", color=palette["muted"], fontsize=6.8*scale)
    fig.text(.980 if args.compact else .975, .025 if args.compact else .015, "arborary.world", ha="right", va="bottom", color=palette["muted"], fontsize=8*scale)
    palette_suffix = (f"_{args.palette}" if args.palette != "night" else "") + ("_compact" if args.compact else "") + ("_cartographic" if args.cartographic_details else "")
    palette_suffix += ("_unified" if args.unified else "") + ("_municipal_boundaries" if args.municipal_boundaries else "")
    palette_suffix += f"_and_{args.second_inset}" if args.second_inset else ""
    output = args.output or Path(__file__).with_name(f"usbos_sources_{args.layout}{'_water' if not args.no_water else ''}{'_circle' if circle else ''}{palette_suffix}{'_'+args.inset if args.inset != 'none' else ''}{'_canopy' if args.inset_canopy else ''}.png")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=args.dpi, facecolor=palette["bg"])
    plt.close(fig)
    provenance.update({"input": args.input, "rendered_at_utc": datetime.now(timezone.utc).isoformat(),
                       "classification": {"non_aerial": list(other_sources), "imagery": list(IMAGERY)},
                       "counts": {"non_aerial": inventory_count, "imagery": imagery_count},
                       "water": water_meta,
                       "municipal_boundaries": boundary_meta,
                       "circle_fade": circle,
                       "palette": palette,
                       "inset": inset_meta,
                       "second_inset": second_inset_meta,
                       "category_semantics": "Surviving published source; merged imagery stays with its inventory survivor. Not independent surveys.",
                       "longitude_range": [float(points["longitude"].min()), float(points["longitude"].max())],
                       "latitude_range": [float(points["latitude"].min()), float(points["latitude"].max())],
                       "projection": projection, "projected_bounds_km": bounds,
                       "render_options": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}})
    output.with_suffix(".json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {output}", flush=True)


if __name__ == "__main__":
    main()
