#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["requests", "beautifulsoup4"]
# ///
"""One-off geocoder for Burlington VT State Register of Historic Places.

Scrapes https://www.burlingtonvt.gov/1000/State-Register-of-Historic-Places,
geocodes each entry via Nominatim, and writes/appends to burlington_landmarks.csv.

Re-runnable: entries already present in the CSV are skipped, so you can
interrupt and resume without re-hitting Nominatim.
"""

import csv
import time
from pathlib import Path
import requests
from bs4 import BeautifulSoup

REGISTER_URL = "https://www.burlingtonvt.gov/1000/State-Register-of-Historic-Places"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim ToS requires a descriptive User-Agent
HEADERS = {"User-Agent": "sf-tree-reporting/1.0 (burlington-landmarks-geocode; contact: your@email.com)"}
RATE_LIMIT_S = 1.1  # Nominatim asks for max 1 req/sec

OUTPUT = Path(__file__).parent / "burlington_landmarks.csv"
CSV_FIELDS = ["landmark_id", "name", "archive_id", "city", "latitude", "longitude", "geometry_raw"]


# ---------------------------------------------------------------------------
# Scrape
# ---------------------------------------------------------------------------

def scrape_entries() -> list[tuple[str, str]]:
    """Return list of (archive_id, name) from the state register page.
    Filters out 'Narrative' documentation entries.
    """
    r = requests.get(REGISTER_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    entries: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "Archives/?id=" not in href:
            continue
        name = a.get_text(strip=True)
        if not name or "Narrative" in name:
            continue
        archive_id = href.split("id=")[-1].split("&")[0]
        entries.append((archive_id, name))

    return entries


# ---------------------------------------------------------------------------
# Geocode
# ---------------------------------------------------------------------------

def geocode(name: str) -> tuple[float, float] | None:
    """Return (lat, lon) for a landmark name in Burlington VT, or None."""
    params = {
        "q": f"{name}, Burlington, VT",
        "format": "json",
        "limit": 1,
        "countrycodes": "us",
    }
    r = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=15)
    r.raise_for_status()
    results = r.json()
    if not results:
        return None
    lat = float(results[0]["lat"])
    lon = float(results[0]["lon"])
    return lat, lon


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def load_existing() -> set[str]:
    """Return set of archive_ids already in the CSV."""
    if not OUTPUT.exists():
        return set()
    with OUTPUT.open(newline="") as f:
        return {row["archive_id"] for row in csv.DictReader(f)}


def append_row(row: dict) -> None:
    write_header = not OUTPUT.exists()
    with OUTPUT.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Scraping state register page...", flush=True)
    entries = scrape_entries()
    print(f"  Found {len(entries)} entries", flush=True)

    already_done = load_existing()
    todo = [(aid, name) for aid, name in entries if aid not in already_done]
    print(f"  {len(already_done)} already geocoded, {len(todo)} remaining", flush=True)

    hit = miss = 0
    for i, (archive_id, name) in enumerate(todo, 1):
        time.sleep(RATE_LIMIT_S)
        coords = geocode(name)
        if coords:
            lat, lon = coords
            row = {
                "landmark_id": f"btv-{archive_id}",
                "name": name,
                "archive_id": archive_id,
                "city": "USBTV",
                "latitude": lat,
                "longitude": lon,
                "geometry_raw": f"POINT({lon} {lat})",
            }
            append_row(row)
            hit += 1
            print(f"  [{i}/{len(todo)}] ✓ {name} → {lat:.5f}, {lon:.5f}", flush=True)
        else:
            miss += 1
            print(f"  [{i}/{len(todo)}] ✗ {name} (no result)", flush=True)

    total = len(already_done) + hit
    print(f"\nDone. {hit} new geocoded, {miss} not found. CSV has {total} total rows.")
    print(f"Output: {OUTPUT}")


if __name__ == "__main__":
    main()
