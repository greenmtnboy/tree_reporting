#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["requests"]
# ///
"""One-off geocoder for Milos landmarks.

Milos publishes no landmark dataset (and no tree inventory — the city is
community-submission-only, see milos_tree_info.preql), so this uses the
Burlington pattern with a curated list in place of a scrape: geocode each
entry via Nominatim and write milos_landmarks.csv.

Re-runnable: entries already present in the CSV are skipped, so you can
interrupt and resume without re-hitting Nominatim.  After changing the CSV,
publish it so the refresh sees it (the preql reads the GCS staging copy, and
uploading is what moves the freshness watermark):

    python -c "import sys; sys.path.insert(0, '..'); from _ingest_shared \
        import upload_staging; upload_staging('milos_landmarks.csv', 'milos_landmarks.csv')"
"""

import csv
import re
import time
from pathlib import Path

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim ToS requires a descriptive User-Agent
HEADERS = {"User-Agent": "sf-tree-reporting/1.0 (milos-landmarks-geocode)"}
RATE_LIMIT_S = 1.1  # Nominatim asks for max 1 req/sec

OUTPUT = Path(__file__).parent / "milos_landmarks.csv"
CSV_FIELDS = ["landmark_id", "name", "city", "latitude", "longitude", "geometry_raw"]

# Curated: the island's archaeological sites, villages, and named coastal
# formations — the reference points a tree recorded on Milos is likely to be
# described against.
LANDMARKS = [
    "Catacombs of Milos",
    "Ancient Theatre of Milos",
    "Sarakiniko",
    "Kleftiko",
    "Klima",
    "Plaka",
    "Kastro of Milos",
    "Panagia Korfiatissa",
    "Firopotamos",
    "Papafragas",
    "Tripiti",
    "Adamantas",
    "Milos Mining Museum",
    "Archaeological Museum of Milos",
    "Pollonia",
    "Paliochori",
    "Firiplaka",
    "Mandrakia",
    "Zefyria",
    "Achivadolimni",
]


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def geocode(name: str) -> tuple[float, float] | None:
    """Return (lat, lon) for a landmark name on Milos, or None."""
    params = {
        "q": f"{name}, Milos, Greece",
        "format": "json",
        "limit": 1,
        "countrycodes": "gr",
    }
    r = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=15)
    r.raise_for_status()
    results = r.json()
    if not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"])


def load_existing() -> set[str]:
    if not OUTPUT.exists():
        return set()
    with OUTPUT.open(newline="", encoding="utf-8") as f:
        return {row["landmark_id"] for row in csv.DictReader(f)}


def append_row(row: dict) -> None:
    write_header = not OUTPUT.exists()
    with OUTPUT.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def main() -> None:
    already_done = load_existing()
    todo = [n for n in LANDMARKS if f"mlo-{slugify(n)}" not in already_done]
    print(f"{len(already_done)} already geocoded, {len(todo)} remaining", flush=True)

    hit = miss = 0
    for i, name in enumerate(todo, 1):
        time.sleep(RATE_LIMIT_S)
        coords = geocode(name)
        if coords:
            lat, lon = coords
            append_row(
                {
                    "landmark_id": f"mlo-{slugify(name)}",
                    "name": name,
                    "city": "GRMLO",
                    "latitude": lat,
                    "longitude": lon,
                    "geometry_raw": f"POINT({lon} {lat})",
                }
            )
            hit += 1
            print(f"  [{i}/{len(todo)}] ok {name} -> {lat:.5f}, {lon:.5f}", flush=True)
        else:
            miss += 1
            print(f"  [{i}/{len(todo)}] MISS {name} (no result)", flush=True)

    total = len(already_done) + hit
    print(f"\nDone. {hit} new geocoded, {miss} not found. CSV has {total} total rows.")
    print(f"Output: {OUTPUT}")


if __name__ == "__main__":
    main()
