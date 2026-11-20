"""Street-level evidence for the local training curator."""
import io
import json
import math
import os
import re
import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path

import requests
from PIL import Image


def credential(*names):
    for name in names:
        value = os.environ.get(name, "").strip()
        if value and not value.startswith("vault:"):
            return value
    return None


def mapillary_get(url, params=None):
    token = credential("MAPILLARY_KEY")
    if not token:
        raise RuntimeError("Mapillary key unavailable")
    response = requests.get(url, params=params, headers={"Authorization": "OAuth " + token}, timeout=20)
    if not response.ok:
        raise RuntimeError(f"Mapillary returned HTTP {response.status_code}")
    return response


def coordinates(lat, lon):
    lat, lon = float(lat), float(lon)
    if not (-85 <= lat <= 85 and -180 <= lon <= 180):
        raise ValueError("Invalid tree coordinates")
    return lat, lon


def camera_pose(image):
    for field in ("computed_geometry", "geometry"):
        point = (image.get(field) or {}).get("coordinates")
        if isinstance(point, list) and len(point) >= 2:
            lon, lat = point[:2]
            if all(isinstance(n, (int, float)) and math.isfinite(n) for n in (lat, lon)) and abs(lat) <= 90 and abs(lon) <= 180:
                break
    else:
        return None
    heading = next((v % 360 for v in (image.get("computed_compass_angle"), image.get("compass_angle"))
                    if isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 360), None)
    return {"lat": lat, "lng": lon, "position_source": field, "heading": heading,
            "is_pano": bool(image.get("is_pano"))}


def nearby(lat, lon):
    lat, lon = coordinates(lat, lon)
    fields = "id,geometry,computed_geometry,compass_angle,computed_compass_angle,is_pano"
    response = mapillary_get("https://graph.mapillary.com/images", {
        "lat": lat, "lng": lon, "radius": 45, "limit": 50, "fields": fields,
    })
    ranked = []
    for image in response.json()["data"]:
        camera = camera_pose(image)
        if camera is None:
            continue
        camera_lon, camera_lat = camera["lng"], camera["lat"]
        east = (lon-camera_lon) * math.cos(math.radians(lat)) * 111320
        north = (lat-camera_lat) * 111320
        distance = math.hypot(east, north)
        heading = camera["heading"]
        bearing = math.degrees(math.atan2(east, north))
        error = 0 if image.get("is_pano") or heading is None else abs((bearing-heading+180) % 360-180)
        if error < 75 and distance <= 50:
            ranked.append((distance + 0.4*error, {"id": str(image["id"]), "meters": round(distance), "camera": camera}))
    return [frame for _, frame in sorted(ranked, key=lambda row: row[0])[:10]]


@lru_cache(maxsize=8)
def photo(image_id):
    if not re.fullmatch(r"[0-9]{1,22}", image_id):
        raise ValueError("Invalid Mapillary image ID")
    fields = "id,geometry,computed_geometry,compass_angle,computed_compass_angle,is_pano,captured_at,sequence,creator,thumb_2048_url"
    meta = mapillary_get("https://graph.mapillary.com/" + image_id, {"fields": fields}).json()
    url = meta.pop("thumb_2048_url", None)
    if not url:
        raise RuntimeError("Mapillary has no preview for this frame")
    response = requests.get(url, timeout=20)
    if not response.ok:
        raise RuntimeError(f"Mapillary preview returned HTTP {response.status_code}")
    return meta, response.content


def cropped(payload):
    meta, content = photo(str(payload["image_id"]))
    box = payload.get("crop")
    with Image.open(io.BytesIO(content)) as image:
        if not (isinstance(box, list) and len(box) == 4 and all(type(n) is int for n in box)
                and 0 <= box[0] < box[2] <= image.width and 0 <= box[1] < box[3] <= image.height):
            raise ValueError("Select a crop inside the Mapillary frame")
        output = io.BytesIO()
        image.crop(box).convert("RGB").save(output, "JPEG", quality=94)
        return meta, image.size, output.getvalue()


def identify(payload):
    key = credential("PLANTNET_API_KEY", "VITE_PLANTNET_API_KEY")
    if not key:
        raise RuntimeError("Pl@ntNet key unavailable; enter the scientific name manually")
    _, _, crop = cropped(payload)
    result = requests.post("https://my-api.plantnet.org/v2/identify/all", params={"api-key": key},
                           files={"images": ("tree.jpg", crop, "image/jpeg")}, data={"organs": "auto"}, timeout=30)
    if result.status_code == 404:
        return []
    if result.status_code == 403 and "remote ip not allowed" in result.text.lower():
        raise RuntimeError("Pl@ntNet does not allow this workstation’s IP. Enter a label or configure a curator API key.")
    if not result.ok:
        raise RuntimeError(f"Pl@ntNet returned HTTP {result.status_code}")
    return [{"species": r["species"]["scientificNameWithoutAuthor"], "score": r["score"]}
            for r in result.json().get("results", [])[:5]]


def save(root: Path, city: str, payload: dict):
    scene, tree, label = (str(payload.get(k) or "").strip() for k in ("scene", "tree", "species"))
    words = label.split()
    if (not scene or not tree or len(words) < 2 or words[0].lower() == "unknown"
            or words[1].lower().rstrip(".") in {"sp", "spp", "species", "unknown"}
            or max(map(len, (scene, tree, label))) > 512):
        raise ValueError("A selected tree and scientific species name are required")
    lat, lon = coordinates(payload["latitude"], payload["longitude"])
    meta, size, crop = cropped(payload)
    source = payload.get("label_source")
    if source not in {"curator", "inventory", "curated", "plantnet"}:
        raise ValueError("Invalid label source")
    image_id = str(meta["id"])
    record = {"provider": "Mapillary", "image": meta, "source_url": "https://www.mapillary.com/app/?pKey="+image_id,
              "license": "CC BY-SA 4.0", "frame_size": size, "latitude": lat, "longitude": lon,
              "camera": camera_pose(meta),
              "label_source": source, "city": city, "scene": scene, "tree": tree}
    directory = root / "street-species"
    directory.mkdir(exist_ok=True, parents=True)
    with closing(sqlite3.connect(directory / "examples.sqlite3", timeout=15)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS examples (
            city TEXT, scene TEXT, tree TEXT, image_id TEXT, crop_box TEXT, species TEXT NOT NULL,
            provenance TEXT NOT NULL, jpeg BLOB NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(city,scene,tree,image_id,crop_box))""")
        db.execute("""INSERT INTO examples(city,scene,tree,image_id,crop_box,species,provenance,jpeg)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(city,scene,tree,image_id,crop_box) DO UPDATE SET
            species=excluded.species, provenance=excluded.provenance, jpeg=excluded.jpeg, updated_at=CURRENT_TIMESTAMP""",
                   (city, scene, tree, image_id, json.dumps(payload["crop"]), label, json.dumps(record), crop))
    return {"saved": True, "species": label}
