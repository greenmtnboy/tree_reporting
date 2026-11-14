#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "requests", "rasterio"]
# ///
"""Public source downloads for the Boston trees, income and summer heat poster.

Income: Census 2026 Planning Database, carrying ACS 2020–2024 estimates.
Heat: Landsat 8/9 Collection 2 Level 2, clear-sky June–August observations.
Cached inputs are local; no source data is modified or published.
"""
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import rasterio
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds

HERE = Path(__file__).resolve().parent
CACHE = HERE.parent / ".tmp" / "boston-heat"
PDB_URL = "https://www2.census.gov/adrm/PDB/2026/pdb2026bg.csv"
TIGER = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_ACS2024/MapServer/10"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
TREE_URL = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/usbos_tree_info_v2.parquet"


def session():
    client = requests.Session()
    client.headers["User-Agent"] = "arborary.world Boston tree-income-heat research"
    client.mount("https://", HTTPAdapter(max_retries=Retry(total=4, backoff_factor=1,
                 status_forcelist=[429, 500, 502, 503, 504])))
    return client


def get_json(url, **params):
    with session() as client:
        response = client.get(url, params=params, timeout=(20, 180))
        response.raise_for_status()
        data = response.json()
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(data["error"])
    return data


def save_json(path, data):
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


def load_income():
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "pdb2026-massachusetts.json"
    if not path.exists():
        print("Reading Census income download ...", flush=True)
        result = []
        with session() as client, client.get(PDB_URL, stream=True, timeout=(20, 180)) as response:
            response.raise_for_status()
            response.encoding = "utf-8-sig"
            records = csv.DictReader(response.iter_lines(decode_unicode=True))
            for row in records:
                if row["State"] == "25":
                    result.append(row)
        if len(result) < 1000:
            raise RuntimeError("Incomplete Massachusetts Census download")
        save_json(path, result)
    return json.loads(path.read_text(encoding="utf-8"))


def load_block_groups(bbox):
    key = hashlib.sha256(json.dumps(bbox).encode()).hexdigest()[:12]
    path = CACHE / f"acs2024-blockgroups-{key}.geojson"
    if not path.exists():
        ids = get_json(TIGER + "/query", f="json", geometry=",".join(map(str, bbox)),
                       geometryType="esriGeometryEnvelope", inSR=4326,
                       spatialRel="esriSpatialRelIntersects", returnIdsOnly="true")["objectIds"]
        features = []
        for start in range(0, len(ids), 150):
            data = get_json(TIGER + "/query", f="geojson", objectIds=",".join(map(str, ids[start:start+150])),
                            outFields="GEOID,NAME,AREALAND,AREAWATER", outSR=4326, returnGeometry="true")
            if data.get("exceededTransferLimit"):
                raise RuntimeError("Truncated Census boundary query")
            features.extend(data["features"])
        if len(features) != len(ids) or not features:
            raise RuntimeError("Incomplete Census boundary query")
        save_json(path, {"type": "FeatureCollection", "features": features})
    return json.loads(path.read_text(encoding="utf-8"))["features"]


def tree_snapshot():
    # Explicitly cached snapshot: delete this file to refresh trees on a later run.
    path = CACHE / "usbos-trees.parquet"
    if not path.exists():
        print("Downloading published Boston trees ...", flush=True)
        with session() as client, client.get(TREE_URL, stream=True, timeout=(20, 180)) as response:
            response.raise_for_status()
            pending = path.with_suffix(".pending")
            with pending.open("wb") as stream:
                for chunk in response.iter_content(1024*1024):
                    stream.write(chunk)
            pending.replace(path)
            save_json(path.with_suffix(".source.json"), {"url": TREE_URL,
                      "last_modified": response.headers.get("Last-Modified"), "etag": response.headers.get("ETag")})
    return path


def clear_land_mask(dn, qa_pixel, uncertainty, cloud_distance):
    """USGS QA bits; omit cloud/shadow/snow/water and uncertain/near-cloud pixels."""
    return ((dn > 0) & ((qa_pixel & 191) == 0) & (((qa_pixel >> 8) & 3) < 2)
            & (uncertainty > 0) & (uncertainty <= 300) & (cloud_distance >= 100))


def load_heat(bbox, year=2025):
    """Return one masked 30 m UTM grid per acquisition date, not per scene.

    The thermal sensor has 100 m native sampling. USGS distributes the product
    on a 30 m grid; adjacent cells are not independent 30 m measurements.
    """
    key = hashlib.sha256(json.dumps([bbox, year, "qa-v1"]).encode()).hexdigest()[:12]
    path = CACHE / f"landsat-{year}-{key}.npz"
    meta_path = path.with_suffix(".json")
    if path.exists() and meta_path.exists():
        with np.load(path) as data:
            return data["temperature"], json.loads(meta_path.read_text())
    catalog_path = CACHE / f"landsat-{year}-{key}-catalog.json"
    if catalog_path.exists():
        items = json.loads(catalog_path.read_text())
    else:
        result = get_json(STAC, collections="landsat-c2-l2", bbox=",".join(map(str, bbox)),
                          datetime=f"{year}-06-01T00:00:00Z/{year}-08-31T23:59:59Z", limit=100)
        items = result["features"]
        while next_links := [link for link in result["links"] if link["rel"] == "next"]:
            result = get_json(next_links[0]["href"])
            items.extend(result["features"])
        items = [item for item in items if item["id"].startswith(("LC08", "LC09"))
                 and item["id"].endswith("T1") and "lwir11" in item["assets"]]
        save_json(catalog_path, items)
    left, bottom, right, top = transform_bounds("EPSG:4326", "EPSG:32619", *bbox)
    left, bottom = np.floor(np.array([left, bottom])/30)*30
    right, top = np.ceil(np.array([right, top])/30)*30
    transform = from_origin(left, top, 30, 30)
    width, height = int((right-left)/30), int((top-bottom)/30)

    def scene(item):
        cached = CACHE / f"{item['id']}-{key}.npy"
        if cached.exists():
            return np.load(cached)
        arrays = {}
        for band in ("lwir11", "qa_pixel", "qa", "cdist"):
            href = get_json("https://planetarycomputer.microsoft.com/api/sas/v1/sign",
                            href=item["assets"][band]["href"])["href"]
            with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="3",
                              GDAL_HTTP_RETRY_DELAY="2", GDAL_HTTP_TIMEOUT="120"):
                with rasterio.open(href) as source, WarpedVRT(source, crs="EPSG:32619",
                        transform=transform, width=width, height=height, nodata=0) as warped:
                    arrays[band] = warped.read(1)
        valid = clear_land_mask(arrays["lwir11"], arrays["qa_pixel"], arrays["qa"], arrays["cdist"])
        result = np.where(valid, arrays["lwir11"]*.00341802+149-273.15, np.nan).astype("float32")
        np.save(cached, result)
        print(f"Heat: {item['id']} ({valid.mean():.0%} usable pixels)", flush=True)
        return result

    with ThreadPoolExecutor(max_workers=3) as pool:
        arrays = list(pool.map(scene, items))
    dates = sorted({item["properties"]["datetime"][:10] for item in items})
    mosaics = []
    for date in dates:
        same_day = np.stack([a for a, item in zip(arrays, items) if item["properties"]["datetime"][:10] == date])
        n = np.isfinite(same_day).sum(axis=0)
        mosaics.append(np.divide(np.nansum(same_day, axis=0), n,
                       out=np.full((height, width), np.nan, dtype="float32"), where=n > 0))
    temperature = np.stack(mosaics)
    metadata = {"year": year, "dates": dates, "crs": "EPSG:32619", "transform": list(transform)[:6],
                "bbox": bbox, "grid_m": 30, "native_thermal_sampling_m": 100,
                "items": [{"id": item["id"], "datetime": item["properties"]["datetime"],
                           "temperature_url": item["assets"]["lwir11"]["href"]} for item in items],
                "quality": "Exclude fill/cloud/cirrus/shadow/snow/water, medium/high cloud confidence; ST uncertainty <=3 K; >=1 km from cloud.",
                "interpretation": "Clear-sky late-morning satellite land surface temperature, not air temperature or an all-weather daily average."}
    np.savez_compressed(path, temperature=temperature)
    save_json(meta_path, metadata)
    return temperature, metadata


def poster_bbox():
    frame = json.loads((HERE / "boston_sharing_frame.json").read_text())
    projection = frame["projection"]
    lon, lat = projection["central_longitude"], projection["central_latitude"]
    kx = 111.195*np.cos(np.radians(lat))
    west, east, south, north = frame["projected_bounds_km"]
    return [lon+west/kx, lat+south/111.195, lon+east/kx, lat+north/111.195]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2025)
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    bbox = poster_bbox()
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = [pool.submit(load_income), pool.submit(load_block_groups, bbox),
                 pool.submit(tree_snapshot), pool.submit(load_heat, bbox, args.year)]
        for task in tasks:
            result = task.result()
            print("Prepared:", type(result).__name__, flush=True)
