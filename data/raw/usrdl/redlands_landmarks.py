#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Redlands, California's designated historic buildings and National Register listings.

Two registers, both read live from ArcGIS layers:

* **The City's designated historic resources and landmarks**, read from its
  "Historic Buildings & Structures" layer (ArcGIS item
  3701fa66edc9456d87a0584c9c011122, owner `ccaceres@cityofredlands.org`,
  the hosted `Historic_Buildings___Structures/FeatureServer/0` on the same
  server as the trees, building footprints in EPSG:2229 read as WGS84).
  1,359 footprints on 2026-11-23; `HISTORIC_TYPE` is a list of codes --
  `HL{n}` (a City historic landmark, 13 numbers) and `HR{n}` (a City
  historic resource, 75), `HD{n}`/`SPHD`/`SFDD` (inside a historic
  district), `NR` (National Register) and `Historic Inventory` (surveyed,
  670, not designated).  Only the footprints with an `HL`/`HR` designation
  are published, one row per designation, its footprints merged into one
  geometry (the House of Neighborly Service is three buildings), keyed
  `redlands-lm-{code}` (`redlands-lm-hr67`); a designation whose footprints
  carry two different names (HR81, HR91, HR98) publishes one row per name,
  the name slug appended.  A designation with no name (HR8-HR11, HR66) or
  only a description (HR23, `wall and structure`) is not published.  District members and surveyed-only buildings are not
  published (districts are areas -- Durham's rule).  The code is the
  `register_number`; the layer records no designation date.
  Licence: none stated -- displayed with attribution only, not licensed for
  reuse.  The item has no licence field; the City's GeoHub Disclaimer
  (https://datahub-coredlands.hub.arcgis.com/pages/disclaimer) makes data
  available "as a public service, on an 'AS-IS' basis, and for informational
  purposes only" -- a disclaimer, not a licence.
* **The National Register of Historic Places** (the National Park Service's
  `cultural_resources/nrhp_locations` MapServer, points then polygons) within
  `CITY_BOUNDS["USRDL"]` and with `City = Redlands`.  Listed buildings, sites
  and structures, keyed `redlands-nr-{NRIS_Refnum}` with `CertDate` as
  `year_designated`; districts (Smiley Park, Santa Fe Depot, Mill Creek
  Zanja) are areas and are not published.  NPS data is a US federal work.

Several buildings are on both (Kimberly Crest, the Academy of Music, the
Barton House ...); both rows are kept, as Columbus keeps them, because the
two records are separate facts.  `lm_type` says which register each row is
from.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit
from shared.nrhp import national_register as shared_national_register  # noqa: E402

BUILDINGS = FeatureLayer(
    "https://services.arcgis.com/FLM8UAw9y5MmuVTV/arcgis/rest/services/"
    "Historic_Buildings___Structures/FeatureServer/0"
)
BUILDINGS_FIELDS = "OBJECTID,HISTORIC_TYPE,HISTORIC_NAME"
DESIGNATION = re.compile(r"^(HL|HR)(\d*)$")


LOCAL_TYPES = {
    "HL": "Redlands historic landmark",
    "HR": "Redlands historic resource",
}

COLUMNS = (
    "landmark_id",
    "name",
    "geometry_raw",
    "address",
    "lm_type",
    "register_number",
    "year_designated",
)

# `HISTORIC_NAME` values that describe a footprint rather than name it (HR23).
GENERIC_NAMES = frozenset({"wall and structure"})

# Words kept upper-case when the City's all-caps names are title-cased.
KEEP_UPPER = frozenset({"US"})


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("\\", "/").split()).strip("'\"")
    return text or None


def title(text: str) -> str:
    """`MOREY MANSION` -> `Morey Mansion`; mixed-case names unchanged."""
    if text != text.upper():
        return text
    words = [w if w in KEEP_UPPER else w[:1] + w[1:].lower() for w in text.split()]
    text = " ".join(words).replace("'S ", "'s ")
    # Initials: `A.g. Hubbard` -> `A.G. Hubbard`.
    return re.sub(r"\.([a-z])", lambda m: "." + m.group(1).upper(), text)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def fetch_all(layer: FeatureLayer, **kwargs) -> list[dict]:
    return [feature for page in iter_features(layer, **kwargs) for feature in page]


def designations(raw) -> list[tuple[str, str]]:
    """The `(series, code)` designations in a `HISTORIC_TYPE` value.

    Examples:
        "SPHD, HD8, HR50" -> [("HR", "HR50")]
        "HL86, NR"        -> [("HL", "HL86")]
        "SPHD, HR, HD8"   -> [("HR", "HR")]
        "Historic Inventory" -> []
    """
    out = []
    for token in (clean(raw) or "").split(","):
        match = DESIGNATION.match(token.strip())
        if match:
            out.append((match.group(1), token.strip()))
    return out


def city_register() -> dict[str, list]:
    features = fetch_all(
        BUILDINGS, out_fields=BUILDINGS_FIELDS, return_geometry=True, out_sr=4326
    )
    if not features:
        raise RuntimeError(f"{BUILDINGS.base} returned no buildings")
    # (code, name) -> (series, rings)
    groups: dict[tuple[str, str], tuple[str, list]] = {}
    names_per_code: dict[str, set[str]] = defaultdict(set)
    unnamed = 0
    for feature in features:
        attrs = feature.get("attributes") or {}
        found = designations(attrs.get("HISTORIC_TYPE"))
        if not found:
            continue
        name = clean(attrs.get("HISTORIC_NAME"))
        rings = (feature.get("geometry") or {}).get("rings") or []
        if not name or name.lower() in GENERIC_NAMES:
            unnamed += 1
            continue
        name = title(name)
        for series, code in found:
            key = (code, name)
            names_per_code[code].add(name)
            groups.setdefault(key, (series, []))[1].extend(rings)
    print(
        f"Redlands landmarks: {len(groups)} City designations, {unnamed} unnamed "
        "designated footprints skipped",
        file=sys.stderr,
    )
    rows: dict[str, list] = {k: [] for k in COLUMNS}
    for (code, name), (series, rings) in sorted(groups.items()):
        wkt = esri_geometry_to_wkt({"rings": rings})
        if wkt is None:
            continue
        # A bare `HR` (no number; one footprint, the Charles C. Wood House)
        # is keyed by its name, as is a number carried by two names.
        numbered = code not in LOCAL_TYPES
        key = code.lower()
        if len(names_per_code[code]) > 1 or not numbered:
            key = f"{key}-{slug(name)}"
        rows["landmark_id"].append(f"redlands-lm-{key}")
        rows["name"].append(name)
        rows["geometry_raw"].append(wkt)
        rows["address"].append(None)
        rows["lm_type"].append(LOCAL_TYPES[series])
        rows["register_number"].append(code if numbered else None)
        rows["year_designated"].append(None)
    if len(set(rows["landmark_id"])) != len(rows["landmark_id"]):
        raise RuntimeError("Redlands landmarks: a City landmark id is repeated")
    return rows


def national_register() -> dict[str, list]:
    return shared_national_register("USRDL", "Redlands", "redlands", COLUMNS, required=False)


def build() -> pa.Table:
    local = city_register()
    national = national_register()
    data = {k: local[k] + national[k] for k in COLUMNS}
    n = len(data["landmark_id"])
    return pa.table(
        {
            "landmark_id": pa.array(data["landmark_id"], type=pa.string()),
            "name": pa.array(data["name"], type=pa.string()),
            "city": pa.array(["USRDL"] * n, type=pa.string()),
            "geometry_raw": pa.array(data["geometry_raw"], type=pa.string()),
            "address": pa.array(data["address"], type=pa.string()),
            "lm_type": pa.array(data["lm_type"], type=pa.string()),
            "register_number": pa.array(data["register_number"], type=pa.string()),
            "year_designated": pa.array(data["year_designated"], type=pa.int64()),
        }
    )


if __name__ == "__main__":
    emit(build())
