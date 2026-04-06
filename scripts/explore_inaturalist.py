#!/usr/bin/env python3
"""
Throwaway script to explore iNaturalist API for tree species images.
Docs: https://api.inaturalist.org/v1/docs/
No API key needed for read-only requests (rate limit: 100 req/min).

Focus: can we reliably get an open-licensed photo per species?
Fallback chain:
  1. taxon default_photo, if license is acceptable
  2. best research-grade observation photo filtered to open licenses
"""

import json
import sys
import time
import urllib.request
import urllib.parse

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://api.inaturalist.org/v1"

# Licenses we're happy to display publicly, in preference order.
# cc-by-nc is non-commercial — fine for a free public app.
# Drop cc-by-nc* from this list if you want strictly commercial-safe only.
ACCEPTABLE_LICENSES = {"cc0", "cc-by", "cc-by-sa", "cc-by-nc", "cc-by-nc-sa"}


def get(path: str, params: dict) -> dict:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    print(f"  GET {url}")
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def search_taxon(name: str) -> dict | None:
    data = get("/taxa", {"q": name, "rank": "species,hybrid", "per_page": 3})
    results = data.get("results", [])
    if not results:
        return None
    for r in results:
        if r.get("name", "").lower() == name.lower():
            return r
    return results[0]


def taxon_default_photo(taxon: dict) -> dict | None:
    dp = taxon.get("default_photo")
    if not dp:
        return None
    return {
        "url_medium": dp["medium_url"],
        "url_large": dp.get("large_url") or dp["medium_url"].replace("medium", "large"),
        "attribution": dp.get("attribution"),
        "license": dp.get("license_code"),
        "source": "taxon_default",
    }


def best_open_observation_photo(taxon_id: int) -> dict | None:
    """
    Query observations filtered to open licenses, ranked by votes.
    The API accepts comma-separated license codes in the `license` param.
    """
    license_param = ",".join(sorted(ACCEPTABLE_LICENSES))
    data = get(
        "/observations",
        {
            "taxon_id": taxon_id,
            "photos": "true",
            "quality_grade": "research",
            "license": license_param,     # <-- filter here
            "photo_license": license_param,  # also filter the photo itself
            "per_page": 5,
            "order_by": "votes",
        },
    )
    for obs in data.get("results", []):
        for photo in obs.get("photos", []):
            lic = photo.get("license_code")
            if lic in ACCEPTABLE_LICENSES:
                url = photo["url"].replace("square", "medium")
                return {
                    "url_medium": url,
                    "url_large": url.replace("medium", "large"),
                    "attribution": photo.get("attribution"),
                    "license": lic,
                    "source": f"obs/{obs['id']}",
                }
    return None


def resolve_photo(taxon: dict) -> dict | None:
    """
    Return the best acceptable photo for a taxon.
    Prefers the curated default; falls back to open-licensed observation photo.
    """
    default = taxon_default_photo(taxon)
    if default and default["license"] in ACCEPTABLE_LICENSES:
        return default

    if default:
        print(f"    [default photo license '{default['license']}' not acceptable — falling back]")

    return best_open_observation_photo(taxon["id"])


# --- Test species ---
TEST_SPECIES = [
    "Platanus x hispanica",   # London plane
    "Lophostemon confertus",  # Brisbane box
    "Metrosideros excelsa",   # NZ Christmas tree
    "Magnolia grandiflora",   # Southern magnolia
    "Arbutus unedo",          # Strawberry tree
    "Ficus microcarpa",       # Chinese banyan
    "Eucalyptus globulus",    # Blue gum (default was all-rights-reserved)
    "Pinus radiata",          # Monterey pine
    "Quercus agrifolia",      # Coast live oak
    "BOGUS species xyz",      # Should fail gracefully
]

results = {}

for species in TEST_SPECIES:
    print(f"\n{'='*60}")
    print(f"Species: {species}")
    taxon = search_taxon(species)
    if taxon is None:
        print("  -> NOT FOUND")
        results[species] = None
        time.sleep(0.7)
        continue

    print(f"  -> id={taxon['id']}  name={taxon['name']}  obs={taxon.get('observations_count','?')}")

    photo = resolve_photo(taxon)
    if photo:
        print(f"  Photo ({photo['source']}):")
        print(f"    {photo['url_medium']}")
        print(f"    license: {photo['license']}")
        print(f"    credit : {photo['attribution']}")
    else:
        print("  NO acceptable photo found")

    results[species] = {
        "taxon_id": taxon["id"],
        "photo": photo,
    }
    time.sleep(0.7)

# --- Summary ---
print(f"\n\n{'='*60}")
print("SUMMARY")
print(f"{'='*60}")
found     = [s for s, v in results.items() if v is not None]
missing   = [s for s, v in results.items() if v is None]
has_photo = [s for s in found if results[s]["photo"] is not None]
no_photo  = [s for s in found if results[s]["photo"] is None]

print(f"Resolved taxon : {len(found)}/{len(TEST_SPECIES)}")
print(f"Has open photo : {len(has_photo)}/{len(found)}")
if no_photo:
    print(f"No open photo  : {no_photo}")

license_counts: dict[str, int] = {}
for s in has_photo:
    lic = results[s]["photo"]["license"]
    license_counts[lic] = license_counts.get(lic, 0) + 1
print(f"\nLicense breakdown: {license_counts}")

sources = [results[s]["photo"]["source"] for s in has_photo]
used_fallback = [s for s, src in zip(has_photo, sources) if src.startswith("obs/")]
print(f"Used fallback (obs photo): {used_fallback or 'none'}")

print("\nSample enrichment rows:")
for s in has_photo[:3]:
    p = results[s]["photo"]
    print(json.dumps({
        "species": s,
        "inat_taxon_id": results[s]["taxon_id"],
        "photo_url": p["url_medium"],
        "photo_license": p["license"],
        "photo_attribution": p["attribution"],
    }, indent=2))
