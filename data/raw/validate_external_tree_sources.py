#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["requests"]
# ///

import json
import sys
from dataclasses import dataclass

import requests

HEADERS = {"User-Agent": "sf-tree-enrichment-validation/1.0 (github.com/greenmtnboy/tree_reporting)"}


@dataclass
class SourceResult:
    source: str
    species: str
    ok: bool
    detail: str


def normalize_candidates(species: str) -> list[str]:
    s = species.strip()
    variants = [s]
    if "×" in s:
        variants.append(s.replace("×", "x"))
    if " x " in s:
        variants.append(s.replace(" x ", " × "))
    # preserve order while deduplicating
    out = []
    seen = set()
    for v in variants:
        key = v.lower()
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def fetch_powo_text(scientific_name: str) -> str | None:
    r = requests.get(
        "https://powo.science.kew.org/api/2/search",
        params={"q": scientific_name, "f": "species_f"},
        headers=HEADERS,
        timeout=15,
    )
    if r.status_code != 200:
        return None
    results = r.json().get("results", [])
    if not results:
        return None
    fq_id = results[0].get("fqId")
    if not fq_id:
        return None

    r2 = requests.get(
        f"https://powo.science.kew.org/api/2/taxon/{fq_id}",
        params={"fields": "descriptions,distribution"},
        headers=HEADERS,
        timeout=15,
    )
    if r2.status_code != 200:
        return None
    data = r2.json()

    parts = []
    descriptions = data.get("descriptions", {})
    if isinstance(descriptions, dict):
        for source_name, source_payload in descriptions.items():
            if not isinstance(source_payload, dict):
                continue
            source_descriptions = source_payload.get("descriptions", {})
            if not isinstance(source_descriptions, dict):
                continue
            for characteristic, items in source_descriptions.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    text = item.get("description", "")
                    if text:
                        parts.append(f"{source_name}/{characteristic}: {text}")

    dist = data.get("distribution", {})
    natives = [region.get("name") for region in dist.get("natives", []) if region.get("name")]
    if natives:
        parts.append(f"Native distribution: {', '.join(natives)}")

    return "\n".join(parts) if parts else None


def fetch_gbif_text(scientific_name: str) -> str | None:
    r = requests.get(
        "https://api.gbif.org/v1/species/match",
        params={"name": scientific_name, "verbose": "true"},
        headers=HEADERS,
        timeout=15,
    )
    if r.status_code != 200:
        return None
    data = r.json()
    if not data.get("usageKey") and not data.get("speciesKey"):
        return None

    parts = []
    if data.get("scientificName"):
        parts.append(f"Matched scientific name: {data['scientificName']}")
    if data.get("canonicalName"):
        parts.append(f"Canonical name: {data['canonicalName']}")
    if data.get("family"):
        parts.append(f"Family: {data['family']}")
    if data.get("order"):
        parts.append(f"Order: {data['order']}")
    if data.get("status"):
        parts.append(f"Taxonomic status: {data['status']}")
    if data.get("confidence") is not None:
        parts.append(f"Match confidence: {data['confidence']}")

    return "\n".join(parts) if parts else None


def validate_source(source: str, species: str) -> SourceResult:
    fetcher = fetch_powo_text if source == "POWO" else fetch_gbif_text
    for candidate in normalize_candidates(species):
        try:
            text = fetcher(candidate)
            if text:
                return SourceResult(source=source, species=candidate, ok=True, detail=text[:240])
        except Exception as e:
            return SourceResult(source=source, species=candidate, ok=False, detail=f"error: {e}")
    return SourceResult(source=source, species=species, ok=False, detail="no result")


def main() -> int:
    # Common SF species / genera-friendly examples.
    species_to_test = [
        "Lophostemon confertus",
        "Platanus acerifolia",
        "Quercus agrifolia",
    ]

    print("Validating non-Wikipedia sources (POWO, GBIF)\n")
    failures = 0
    report: dict[str, dict[str, object]] = {}

    for species in species_to_test:
        powo_result = validate_source("POWO", species)
        gbif_result = validate_source("GBIF", species)
        species_ok = powo_result.ok or gbif_result.ok
        if not species_ok:
            failures += 1

        report[species] = {
            "powo": {
                "ok": powo_result.ok,
                "species_used": powo_result.species,
                "preview": powo_result.detail,
            },
            "gbif": {
                "ok": gbif_result.ok,
                "species_used": gbif_result.species,
                "preview": gbif_result.detail,
            },
            "any_non_wikipedia_source_ok": species_ok,
        }

        print(f"Species: {species}")
        print(f"  POWO: {'OK' if powo_result.ok else 'MISS'} ({powo_result.species})")
        print(f"  GBIF: {'OK' if gbif_result.ok else 'MISS'} ({gbif_result.species})")
        print()

    print("JSON report:")
    print(json.dumps(report, indent=2, ensure_ascii=False))

    if failures > 0:
        print(f"\nValidation failed for {failures} species.", file=sys.stderr)
        return 1

    print("\nValidation passed: each test species resolved in at least one non-Wikipedia source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
