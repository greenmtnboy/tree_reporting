#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pillow", "numpy", "requests"]
# ///
"""
Score iNaturalist photos for "tree profile" likelihood.

Given a species name, fetches candidate photos from iNat and scores each
on how likely it is to be a full-tree profile shot vs a closeup.

Heuristics:
  1. Aspect ratio — prefer portrait (tall) images
  2. Sky detection — blue in the top third
  3. Color variance — high variance = tree against sky, low = closeup
"""

import json
import sys
import time
import urllib.request
import urllib.parse
from io import BytesIO

from PIL import Image
import numpy as np

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://api.inaturalist.org/v1"
ACCEPTABLE_LICENSES = {"cc0", "cc-by", "cc-by-sa", "cc-by-nc", "cc-by-nc-sa"}


def inat_get(path: str, params: dict) -> dict:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def fetch_candidate_photos(scientific_name: str, max_photos: int = 10) -> list[dict]:
    """Fetch candidate photos from iNat for a species."""
    data = inat_get("/taxa", {"q": scientific_name, "rank": "species,hybrid", "per_page": 3})
    results = data.get("results", [])
    if not results:
        print(f"  No taxon found for {scientific_name!r}")
        return []

    lower_name = scientific_name.lower().replace(" x ", " × ")
    taxon = None
    for r in results:
        r_name = r.get("name", "").lower()
        if r_name == lower_name or r_name == scientific_name.lower():
            taxon = r
            break
    if taxon is None:
        taxon = results[0]

    taxon_id = taxon["id"]
    print(f"  Taxon: {taxon['name']} (id={taxon_id})")

    photos = []

    # Taxon default photo
    dp = taxon.get("default_photo")
    if dp and dp.get("license_code") in ACCEPTABLE_LICENSES:
        photos.append({
            "url": dp.get("medium_url", "").replace("square", "medium"),
            "source": "default",
            "license": dp.get("license_code"),
            "attribution": dp.get("attribution"),
        })

    # Research-grade observation photos
    license_param = ",".join(sorted(ACCEPTABLE_LICENSES))
    obs_data = inat_get("/observations", {
        "taxon_id": taxon_id,
        "photos": "true",
        "quality_grade": "research",
        "license": license_param,
        "photo_license": license_param,
        "per_page": 10,
        "order_by": "votes",
    })
    for obs in obs_data.get("results", []):
        for photo in obs.get("photos", []):
            if photo.get("license_code") in ACCEPTABLE_LICENSES and len(photos) < max_photos:
                url = photo["url"].replace("square", "medium")
                photos.append({
                    "url": url,
                    "source": f"obs/{obs['id']}",
                    "license": photo.get("license_code"),
                    "attribution": photo.get("attribution"),
                })

    return photos


def download_image(url: str) -> Image.Image:
    req = urllib.request.Request(url, headers={"User-Agent": "tree-photo-scorer/0.1"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return Image.open(BytesIO(resp.read())).convert("RGB")


def score_photo(img: Image.Image) -> dict:
    """Score an image on tree-profile likelihood. Returns component scores and total."""
    w, h = img.size
    arr = np.array(img, dtype=np.float32)  # (H, W, 3)

    # 1. Aspect ratio score: portrait = good, landscape = bad
    ratio = h / w
    # 1.0 = square (neutral), >1.3 = tall (good), <0.8 = wide (bad)
    aspect_score = min(max((ratio - 0.7) / 0.8, 0.0), 1.0)

    # 2. Sky detection: is the top third predominantly blue-ish?
    top_third = arr[: h // 3]
    r, g, b = top_third[:, :, 0], top_third[:, :, 1], top_third[:, :, 2]
    brightness = top_third.mean(axis=2)
    # "Sky" pixel: blue > red, blue > green*0.8, reasonably bright
    sky_mask = (b > r) & (b > g * 0.8) & (brightness > 120)
    sky_fraction = sky_mask.mean()
    sky_score = min(sky_fraction / 0.3, 1.0)  # 30%+ sky in top third = max score

    # 3. Color variance across image regions
    # Split into a 3x3 grid, compute mean color per cell, then variance across cells
    grid_colors = []
    gh, gw = h // 3, w // 3
    for row in range(3):
        for col in range(3):
            cell = arr[row * gh : (row + 1) * gh, col * gw : (col + 1) * gw]
            grid_colors.append(cell.mean(axis=(0, 1)))
    grid_colors = np.array(grid_colors)  # (9, 3)
    color_var = grid_colors.var(axis=0).mean()  # mean variance across R,G,B
    # Normalize: closeups ~100-500, tree profiles ~500-2000+
    variance_score = min(max((color_var - 200) / 800, 0.0), 1.0)

    # 4. Vertical brightness gradient (bright top, dark bottom)
    top_brightness = brightness.mean()
    bot_third = arr[2 * h // 3 :]
    bot_brightness = bot_third.mean(axis=2).mean()
    gradient = top_brightness - bot_brightness
    gradient_score = min(max(gradient / 60, 0.0), 1.0)  # 60+ point difference = max

    total = (
        aspect_score * 0.15
        + sky_score * 0.35
        + variance_score * 0.25
        + gradient_score * 0.25
    )

    return {
        "aspect_ratio": round(ratio, 2),
        "aspect_score": round(aspect_score, 2),
        "sky_fraction": round(sky_fraction, 2),
        "sky_score": round(sky_score, 2),
        "color_variance": round(color_var, 1),
        "variance_score": round(variance_score, 2),
        "gradient": round(gradient, 1),
        "gradient_score": round(gradient_score, 2),
        "total": round(total, 3),
    }


def evaluate_species(name: str):
    print(f"\n{'=' * 60}")
    print(f"Species: {name}")
    photos = fetch_candidate_photos(name)
    if not photos:
        return

    scored = []
    for i, p in enumerate(photos):
        try:
            img = download_image(p["url"])
            scores = score_photo(img)
            scored.append((p, scores))
            marker = " <-- BEST" if i == 0 else ""
            print(f"\n  [{i+1}] {p['source']:20s}  total={scores['total']:.3f}{marker if p['source']=='default' else ''}")
            print(f"      aspect={scores['aspect_ratio']}({scores['aspect_score']})  "
                  f"sky={scores['sky_fraction']}({scores['sky_score']})  "
                  f"var={scores['color_variance']}({scores['variance_score']})  "
                  f"grad={scores['gradient']}({scores['gradient_score']})")
            print(f"      {p['url']}")
        except Exception as e:
            print(f"\n  [{i+1}] {p['source']:20s}  ERROR: {e}")
        time.sleep(0.3)

    if scored:
        best = max(scored, key=lambda x: x[1]["total"])
        print(f"\n  >> Best profile candidate: {best[0]['source']}  score={best[1]['total']:.3f}")
        print(f"     {best[0]['url']}")


if __name__ == "__main__":
    species_list = sys.argv[1:] or [
        "Platanus x hispanica",
        "Quercus agrifolia",
        "Pinus radiata",
    ]
    for name in species_list:
        evaluate_species(name)
        time.sleep(0.7)
