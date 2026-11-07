"""Build the Fairfax County source bundle the imagery model's pilot reads.

The bundle contract is in `docs/FAIRFAX_TREE_DATA_HANDOFF.md`: a county
boundary, a county inventory (Fairfax County publishes none, so it is `null`)
and a calibration inventory of the neighbouring jurisdictions, each row
traceable to a raw snapshot kept in the bundle.

The bundle's `city` is `USFFX`, the model pilot's code for the county.  The
public pipeline has no USFFX: Fairfax County sits inside USNVA's territory,
whose two municipal sources are the Arlington and Falls Church ingests this
script reuses, so a calibration row's `species` is exactly what USNVA
publishes.  Calibration rows carry their own jurisdiction's code (USARL,
USFCH, USALX: bundle-local, chosen not to collide with the city registry)
plus `pipeline_city` and the pipeline's `data_source` label.

Run from `data/raw`:

    uv run --no-project --with pyarrow --with requests --with shapely \
        --with "pytrilogy>=0.3.439" --with "duckdb<2" \
        python tools/fairfax_source_bundle.py --out <new bundle directory>

The output directory must not exist; a rebuild is a new version.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import math
import shutil
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests
from shapely.geometry import Point, mapping, shape
from shapely.validation import explain_validity, make_valid
from shapely.prepared import prep

RAW = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAW))
from shared.ingest import (  # noqa: E402
    DEFAULT_HEADERS,
    UNKNOWN_SPECIES,
    enforce_tree_schema,
    normalize_species,
    normalize_tree_name,
    validate_coordinates,
)
from shared.platforms.arcgis import FeatureLayer, esri_ms_to_date, esri_point, iter_features  # noqa: E402

CITY = "USFFX"
JURISDICTION = "Fairfax County, Virginia"

BOUNDARY_LAYER = (
    "https://services1.arcgis.com/ioennV6PpG5Xodq0/arcgis/rest/services/"
    "Fairfax_County_Boundary/FeatureServer/0"
)
# VGIN's jurisdiction polygons as the Northern Virginia Regional Commission
# republishes them; used only to check each source lands in its own territory.
JURISDICTIONS_LAYER = (
    "https://services5.arcgis.com/6MUPhDX27Ne3DNOw/arcgis/rest/services/"
    "County_Boundaries_Northern_Virginia/FeatureServer/0"
)
ALEXANDRIA_LAYER = (
    "https://maps.alexandriava.gov/arcgis/rest/services/alxPublicParksWm/MapServer/0"
)

# Independent cities the county must not overlap (VGIN FIPS; Fairfax City is
# enclaved by the county, Falls Church borders it) and towns, which are county
# territory, it must cover.
MUST_EXCLUDE = {"51600": "Fairfax City", "51610": "Falls Church City", "51013": "Arlington County"}
MUST_INCLUDE = {"Vienna (town)": (38.9012, -77.2653), "Herndon (town)": (38.9696, -77.3861)}

INVENTORY_COLUMNS = [
    "tree_id", "city", "data_source", "species", "plant_date",
    "diameter_at_breast_height", "latitude", "longitude",
]
PROVENANCE_COLUMNS = [
    "pipeline_city", "jurisdiction", "jurisdiction_fips", "selection",
    "tree_name", "cultivar", "source_species_name", "source_common_name", "source_cultivar",
    "source_dbh", "source_dbh_unit", "source_record_date", "source_record_date_kind",
    "source_status", "source_object_id",
]


def load_adapter(name: str):
    path = RAW / "usnva" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"usnva_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def item_metadata(layer_url: str) -> dict:
    """The ArcGIS Online item behind a hosted layer: title, terms, modified."""
    service = layer_url.rsplit("/", 1)[0]
    meta = requests.get(service, params={"f": "json"}, headers=DEFAULT_HEADERS, timeout=60).json()
    item_id = meta.get("serviceItemId")
    if not item_id:
        return {}
    item = requests.get(
        f"https://www.arcgis.com/sharing/rest/content/items/{item_id}",
        params={"f": "json"}, headers=DEFAULT_HEADERS, timeout=60,
    ).json()
    return {"item_id": item_id, "item_modified": esri_ms_to_iso(item.get("modified")),
            "license_info": item.get("licenseInfo"), "access_information": item.get("accessInformation")}


def esri_ms_to_iso(value) -> str | None:
    return None if value is None else datetime.fromtimestamp(value / 1000, UTC).isoformat()


def layer_info(url: str) -> dict:
    return requests.get(url, params={"f": "json"}, headers=DEFAULT_HEADERS, timeout=60).json()


def snapshot(out: Path, name: str, payload) -> str:
    relative = f"snapshots/{name}.json.gz"
    path = out / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)
    return relative


def fetch_features(layer: str, *, order_by: str) -> list[dict]:
    features: list[dict] = []
    for page in iter_features(FeatureLayer(layer), out_fields="*", return_geometry=True, order_by=order_by):
        features.extend(page)
    return features


def fetch_geojson(layer: str) -> dict:
    response = requests.get(
        f"{layer}/query",
        params={"where": "1=1", "outFields": "*", "outSR": 4326, "f": "geojson"},
        headers=DEFAULT_HEADERS, timeout=120,
    )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Boundary
# ---------------------------------------------------------------------------

def build_boundary(out: Path, report: dict, jurisdictions: dict) -> tuple[object, dict]:
    retrieved = now()
    raw = fetch_geojson(BOUNDARY_LAYER)
    snap = snapshot(out, "fairfax-county-boundary", raw)
    features = raw["features"]
    if len(features) != 1:
        raise ValueError(f"expected one county feature, got {len(features)}")
    geometry = shape(features[0]["geometry"])
    repair = None
    if not geometry.is_valid:
        # The county owns an enclave inside Fairfax City (the Judicial Center
        # complex), so the published polygon is county / city hole / county
        # island.  Esri's ring list says that; GeoJSON's one-exterior polygon
        # reads it as a hole nested in a hole.  make_valid splits the island
        # into its own part, which is the actual territory.
        reason = explain_validity(geometry)
        repaired = make_valid(geometry)
        if repaired.geom_type != "MultiPolygon" or abs(repaired.area / geometry.area - 1) > 0.01:
            raise ValueError(f"county boundary invalid ({reason}) and not repairable as a MultiPolygon")
        repair = {"reason": reason, "parts_after": len(repaired.geoms),
                  "area_ratio": repaired.area / geometry.area}
        geometry = repaired
    if geometry.geom_type not in {"Polygon", "MultiPolygon"} or not geometry.is_valid:
        raise ValueError(f"county boundary is {geometry.geom_type}, valid={geometry.is_valid}")
    overlaps = {}
    for fips, name in MUST_EXCLUDE.items():
        other = jurisdictions[fips][1]
        share = geometry.intersection(other).area / other.area
        overlaps[name] = round(share, 5)
        if share > 0.01:
            raise ValueError(f"county boundary covers {share:.1%} of {name}")
    for name, (lat, lon) in MUST_INCLUDE.items():
        if not geometry.covers(Point(lon, lat)):
            raise ValueError(f"county boundary misses {name}")
    parts = list(getattr(geometry, "geoms", [geometry]))
    holes = [len(p.interiors) for p in parts]
    info = layer_info(BOUNDARY_LAYER)
    meta = item_metadata(BOUNDARY_LAYER)
    properties = {
        "name": features[0]["properties"].get("NAME"),
        "city": CITY,
        "jurisdiction": JURISDICTION,
        "source": BOUNDARY_LAYER,
        "retrieved_at": retrieved,
        "source_last_edited": esri_ms_to_iso((info.get("editingInfo") or {}).get("dataLastEditDate")),
    }
    if repair:
        properties["repair"] = "nested hole split into its own part by shapely make_valid; see source-audit.md"
    collection = {"type": "FeatureCollection",
                  "features": [{"type": "Feature", "properties": properties, "geometry": mapping(geometry)}]}
    (out / "boundary.geojson").write_text(json.dumps(collection), encoding="utf-8")
    report["boundary"] = {
        "geometry_type": geometry.geom_type, "parts": len(parts), "holes_per_part": holes,
        "bounds": list(geometry.bounds), "snapshot": snap, "repair": repair,
        "excluded_overlap_share": overlaps, "included_checks": list(MUST_INCLUDE),
        "source_last_edited": properties["source_last_edited"],
    }
    source = {
        "name": "Fairfax County GIS, Fairfax County Boundary",
        "url": BOUNDARY_LAYER,
        "retrieved_at": retrieved,
        "jurisdiction": JURISDICTION,
        "license": "https://www.fairfaxcounty.gov/maps/disclaimer",
        "coverage": "Official jurisdictional limit of Fairfax County (excludes the independent cities of Fairfax and Falls Church)",
        "role": "boundary",
        "item": meta,
        "snapshot": snap,
    }
    return geometry, source


def load_jurisdictions(out: Path) -> dict[str, tuple[str, object]]:
    raw = fetch_geojson(JURISDICTIONS_LAYER)
    snapshot(out, "nvrc-jurisdictions", raw)
    return {f["properties"]["STCOFIPS"]: (f["properties"]["NAMELSAD"], shape(f["geometry"]))
            for f in raw["features"]}


# ---------------------------------------------------------------------------
# Calibration sources
# ---------------------------------------------------------------------------

def finish(table: pa.Table, *, code: str, fips: str, jurisdiction: str, selection: str,
           data_source: str | None, pipeline_label: str, stages: dict) -> pa.Table:
    """Coordinates, the shared schema, then this bundle's identity columns."""
    before = table.num_rows
    table = validate_coordinates(table, city=jurisdiction, city_code="USNVA")
    stages["dropped_coordinates"] = before - table.num_rows
    before = table.num_rows
    if data_source is None:
        # Not a pipeline source, so not a registered label `enforce_tree_schema` may stamp.
        table = table.append_column("data_source", pa.array([pipeline_label] * table.num_rows))
    table = enforce_tree_schema(table, city=jurisdiction, data_source=data_source)
    stages["dropped_not_a_tree"] = before - table.num_rows
    stages["delivered"] = table.num_rows
    n = table.num_rows
    table = table.set_column(table.schema.get_field_index("city"), "city", pa.array([code] * n))
    for name, value in (("pipeline_city", "USNVA" if data_source else None), ("jurisdiction", jurisdiction),
                        ("jurisdiction_fips", fips), ("selection", selection)):
        table = table.append_column(name, pa.array([value] * n, type=pa.string()))
    return table.select(INVENTORY_COLUMNS + PROVENANCE_COLUMNS)


def with_extras(table: pa.Table, extras: dict[str, list]) -> pa.Table:
    for name, values in extras.items():
        if len(values) != table.num_rows:
            raise AssertionError(f"{name}: {len(values)} values for {table.num_rows} rows")
        table = table.append_column(name, pa.array(values, type=pa.string()))
    return table


def arlington(out: Path) -> tuple[pa.Table, dict, dict]:
    adapter = load_adapter("arlington_tree_info")
    retrieved = now()
    features = fetch_features(adapter.LAYER.url, order_by="OBJECTID")
    snap = snapshot(out, "arlington-dpr-trees", features)
    stages = {"raw": len(features)}
    attrs = [f.get("attributes") or {} for f in features]
    stages["raw_inactive_nonzero"] = sum(1 for a in attrs if a.get("Inactive") not in (0, None))
    stages["raw_retired_set"] = sum(1 for a in attrs if a.get("Retired") is not None)
    stages["raw_dead_flagged"] = sum(1 for a in attrs if (a.get("DeadTree") or "").strip())
    # The layer publishes standing assets only; keep that true rather than assume it.
    features = [f for f in features
                if (f["attributes"].get("Inactive") in (0, None)) and f["attributes"].get("Retired") is None
                and not (f["attributes"].get("DeadTree") or "").strip()]
    stages["after_status_filter"] = len(features)
    features = [f for f in features if adapter.is_tree(f)]
    stages["after_not_a_tree_filter"] = len(features)
    features = [f for f in features if str(f["attributes"].get("ID") or "").strip()]
    stages["after_id_filter"] = len(features)
    table = adapter.transform(features)
    attrs = [f["attributes"] for f in features]
    table = with_extras(table, {
        "source_species_name": [None] * len(attrs),
        "source_common_name": [a.get("CommonName") for a in attrs],
        "source_cultivar": [(a.get("CultivarVariety") or "").strip() or None for a in attrs],
        "source_dbh": [None if a.get("DBHIn") is None else repr(a["DBHIn"]) for a in attrs],
        "source_dbh_unit": ["in"] * len(attrs),
        "source_record_date": [iso_date(esri_ms_to_date(a.get("cgLastModified"))) for a in attrs],
        "source_record_date_kind": ["cartegraph_last_modified"] * len(attrs),
        "source_status": [a.get("SpecialCategory") or None for a in attrs],
        "source_object_id": [str(a.get("OBJECTID")) for a in attrs],
    })
    table = finish(table, code="USARL", fips="51013", jurisdiction="Arlington County, Virginia",
                   selection="municipal asset inventory (street, park, school, VDOT right-of-way)",
                   data_source="ARLINGTON", pipeline_label="ARLINGTON", stages=stages)
    info = layer_info(adapter.LAYER.url)
    source = {
        "name": "Arlington County DPR, DPR Trees",
        "url": adapter.LAYER.url,
        "retrieved_at": retrieved,
        "jurisdiction": "Arlington County, Virginia",
        "license": "Arlington County GIS data disclaimer (ArcGIS item licenseInfo; no open-data licence named)",
        "coverage": "County-maintained trees: street, median, park, school and VDOT/State right-of-way; private land absent",
        "role": "calibration_inventory",
        "bundle_city": "USARL", "pipeline_city": "USNVA", "data_source": "ARLINGTON",
        "source_last_edited": esri_ms_to_iso((info.get("editingInfo") or {}).get("dataLastEditDate")),
        "item": item_metadata(adapter.LAYER.url),
        "snapshot": snap,
        "extraction": "data/raw/usnva/arlington_tree_info.py transform, then shared.ingest validate_coordinates and enforce_tree_schema",
    }
    return table, stages, source


def falls_church(out: Path) -> tuple[pa.Table, dict, dict]:
    adapter = load_adapter("falls_church_tree_info")
    retrieved = now()
    features = fetch_features(adapter.LAYER.url, order_by="FID")
    snap = snapshot(out, "falls-church-public-trees", features)
    stages = {"raw": len(features)}
    stages["raw_vacant_sites"] = sum(1 for f in features if f["attributes"].get("VACANT") == "V")
    stages["raw_condition_dead"] = sum(1 for f in features if (f["attributes"].get("CONDITION") or "").strip() == "Dead")
    features = [f for f in features if adapter.is_tree(f)]
    stages["after_vacant_filter"] = len(features)
    features = [f for f in features if f["attributes"].get("SITE_ID") is not None]
    stages["after_id_filter"] = len(features)
    table = adapter.transform(features)
    attrs = [f["attributes"] for f in features]
    table = with_extras(table, {
        "source_species_name": [a.get("SPECIES") for a in attrs],
        "source_common_name": [a.get("COMMON_NAM") for a in attrs],
        "source_cultivar": [None] * len(attrs),
        "source_dbh": [None if a.get("DBH") is None else repr(a["DBH"]) for a in attrs],
        "source_dbh_unit": ["in"] * len(attrs),
        "source_record_date": [(a.get("INV_DATE") or "").strip() or None for a in attrs],
        "source_record_date_kind": ["inventory_survey_date"] * len(attrs),
        "source_status": [(a.get("CONDITION") or "").strip() or None for a in attrs],
        "source_object_id": [str(a.get("FID")) for a in attrs],
    })
    table = finish(table, code="USFCH", fips="51610", jurisdiction="City of Falls Church, Virginia",
                   selection="municipal inventory, 2004 survey (street, park, school, facility)",
                   data_source="FALLS_CHURCH", pipeline_label="FALLS_CHURCH", stages=stages)
    info = layer_info(adapter.LAYER.url)
    source = {
        "name": "City of Falls Church, Public_Trees",
        "url": adapter.LAYER.url,
        "retrieved_at": retrieved,
        "jurisdiction": "City of Falls Church, Virginia",
        "license": "Public ArcGIS Online layer; no licence or terms published on the item",
        "coverage": "2004 inventory of public trees: street, park, school and facility sites; private land absent",
        "role": "calibration_inventory",
        "bundle_city": "USFCH", "pipeline_city": "USNVA", "data_source": "FALLS_CHURCH",
        "source_last_edited": esri_ms_to_iso((info.get("editingInfo") or {}).get("dataLastEditDate")),
        "item": item_metadata(adapter.LAYER.url),
        "snapshot": snap,
        "extraction": "data/raw/usnva/falls_church_tree_info.py transform, then shared.ingest validate_coordinates and enforce_tree_schema",
    }
    return table, stages, source


def alexandria(out: Path) -> tuple[pa.Table, dict, dict]:
    retrieved = now()
    features = fetch_features(ALEXANDRIA_LAYER, order_by="OBJECTID")
    snap = snapshot(out, "alexandria-notable-trees", features)
    stages = {"raw": len(features)}
    features = [f for f in features if (f["attributes"].get("GlobalID") or "").strip()]
    stages["after_id_filter"] = len(features)
    attrs = [f["attributes"] for f in features]
    points = [esri_point(f.get("geometry")) for f in features]
    # CBHinInches is the girth in inches (`CBH` is the same as feet'inches).
    dbh = [None if not a.get("CBHinInches") else round(a["CBHinInches"] / math.pi, 1) for a in attrs]
    table = pa.table({
        "tree_id": [f"alx-{a['GlobalID'].strip('{}').lower()}" for a in attrs],
        "city": ["USALX"] * len(attrs),
        "species": [normalize_species(a.get("SpeciesName")) for a in attrs],
        "tree_name": [normalize_tree_name(a.get("CommonName")) for a in attrs],
        "plant_date": pa.nulls(len(attrs), type=pa.date32()),
        "latitude": pa.array([p[0] for p in points], type=pa.float64()),
        "longitude": pa.array([p[1] for p in points], type=pa.float64()),
        "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
    })
    table = with_extras(table, {
        "source_species_name": [a.get("SpeciesName") for a in attrs],
        "source_common_name": [a.get("CommonName") for a in attrs],
        "source_cultivar": [None] * len(attrs),
        "source_dbh": [None if a.get("CBHinInches") is None else repr(a["CBHinInches"]) for a in attrs],
        "source_dbh_unit": ["in circumference (DBH = CBH / pi)"] * len(attrs),
        "source_record_date": [None] * len(attrs),
        "source_record_date_kind": [None] * len(attrs),
        "source_status": [a.get("Status") for a in attrs],
        "source_object_id": [str(a.get("OBJECTID")) for a in attrs],
    })
    table = finish(table, code="USALX", fips="51510", jurisdiction="City of Alexandria, Virginia",
                   selection="NOTABLE TREES ONLY: champion, significant and notable nominations, public and private",
                   data_source=None, pipeline_label="ALEXANDRIA_NOTABLE", stages=stages)
    source = {
        "name": "City of Alexandria, alxPublicParksWm Notable Trees",
        "url": ALEXANDRIA_LAYER,
        "retrieved_at": retrieved,
        "jurisdiction": "City of Alexandria, Virginia",
        "license": "Public City of Alexandria map service (copyright City of Alexandria, Virginia); no licence published",
        "coverage": "Notable, champion and significant trees only; a selection of large specimens, not an inventory",
        "role": "calibration_inventory",
        "bundle_city": "USALX", "pipeline_city": None, "data_source": "ALEXANDRIA_NOTABLE",
        "snapshot": snap,
        "extraction": "data/raw/tools/fairfax_source_bundle.py alexandria(): shared.ingest normalize_species, validate_coordinates, enforce_tree_schema",
    }
    return table, stages, source


def iso_date(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def species_profile(table: pa.Table) -> dict:
    species = table.column("species").to_pylist()
    original = [o or c for o, c in zip(table.column("source_species_name").to_pylist(),
                                       table.column("source_common_name").to_pylist())]
    counts = Counter(species)
    unknown = counts.get(UNKNOWN_SPECIES, 0)
    genus_only = sum(n for s, n in counts.items() if s != UNKNOWN_SPECIES and " " not in s and s[:1].isupper())
    unresolved = Counter(o for s, o in zip(species, original) if s == UNKNOWN_SPECIES and o)
    genus_rows = Counter((o, s) for s, o in zip(species, original)
                         if s != UNKNOWN_SPECIES and " " not in s and o and o != s)
    hybrids = Counter((o, s) for s, o in zip(species, original)
                      if o and (" x " in f" {o.lower()} " or "×" in o or "hybrid" in o.lower()))
    return {
        "rows": len(species), "distinct": len(counts), "unknown": unknown,
        "genus_only": genus_only, "species_level": len(species) - unknown - genus_only,
        "cultivar_rows": table.num_rows - table.column("cultivar").null_count,
        "top": counts.most_common(15),
        "unresolved": unresolved.most_common(),
        "genus_only_sources": genus_rows.most_common(20),
        "hybrids": hybrids.most_common(),
    }


def coordinate_profile(table: pa.Table, county, jurisdictions) -> dict:
    lats = table.column("latitude").to_pylist()
    lons = table.column("longitude").to_pylist()
    fips = table.column("jurisdiction_fips")[0].as_py()
    own = jurisdictions.get(fips, (None, None))[1]
    county, own = prep(county), own is not None and prep(own) or None
    others = [(name, prep(g)) for f, (name, g) in jurisdictions.items() if f != fips]
    inside_own = inside_county = 0
    elsewhere: Counter = Counter()
    for lat, lon in zip(lats, lons):
        point = Point(lon, lat)
        if county.covers(point):
            inside_county += 1
        if own is not None and own.covers(point):
            inside_own += 1
            continue
        hit = next((name for name, g in others if g.covers(point)), "outside Northern Virginia")
        elsewhere[hit] += 1
    exact = Counter(zip(lats, lons))
    return {"finite": all(math.isfinite(v) for v in lats + lons), "inside_own_jurisdiction": inside_own,
            "inside_county_boundary": inside_county, "outside_own": dict(elsewhere),
            "duplicate_coordinate_rows": sum(n - 1 for n in exact.values() if n > 1),
            "bounds": [min(lons), min(lats), max(lons), max(lats)]}


def near_pairs(tables: dict[str, pa.Table], metres: float) -> dict:
    """Cross-source point pairs closer than *metres*, on a local grid."""
    lat0 = 38.85
    kx, ky = 111_320 * math.cos(math.radians(lat0)), 110_540
    grid: dict[tuple[int, int], list[tuple[str, float, float]]] = defaultdict(list)
    for name, table in tables.items():
        for lat, lon in zip(table.column("latitude").to_pylist(), table.column("longitude").to_pylist()):
            x, y = lon * kx, lat * ky
            grid[(int(x // metres), int(y // metres))].append((name, x, y))
    pairs: Counter = Counter()
    for (cx, cy), members in grid.items():
        for dx in (0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == -1:
                    continue
                other = members if (dx, dy) == (0, 0) else grid.get((cx + dx, cy + dy), [])
                for i, (a, ax, ay) in enumerate(members):
                    for j, (b, bx, by) in enumerate(other):
                        if (dx, dy) == (0, 0) and j <= i:
                            continue
                        if a != b and math.hypot(ax - bx, ay - by) < metres:
                            pairs[" / ".join(sorted((a, b)))] += 1
    return dict(pairs)


def dbh_profile(table: pa.Table) -> dict:
    values = [v for v in table.column("diameter_at_breast_height").to_pylist() if v is not None]
    if not values:
        return {"present": 0}
    return {"present": len(values), "null": table.num_rows - len(values), "min": min(values),
            "median": statistics.median(values), "max": max(values)}


def date_profile(table: pa.Table) -> dict:
    planted = table.column("plant_date")
    present = table.num_rows - planted.null_count
    out = {"plant_date_present": present}
    if present:
        out["plant_date_min"] = pc.min(planted).as_py().isoformat()
        out["plant_date_max"] = pc.max(planted).as_py().isoformat()
    record = [v for v in table.column("source_record_date").to_pylist() if v]
    if record:
        out["record_date_kind"] = table.column("source_record_date_kind")[0].as_py()
        out["record_date_min"], out["record_date_max"] = min(record), max(record)
        out["record_date_distinct"] = len(set(record))
    return out


def write_audit(out: Path, report: dict, manifest: dict) -> None:
    lines = [
        "# Fairfax County source bundle audit", "",
        f"Bundle `{out.name}`, built {manifest['built_at']} from repo commit `{manifest['extraction']['repo_commit']}`"
        f" by `{manifest['extraction']['script']}`.", "",
        f"Bundle city `{CITY}` ({JURISDICTION}). The public pipeline has no `{CITY}`: the county is part of "
        "`USNVA` (Northern Virginia), which publishes Arlington and Falls Church plus OpenStreetMap for the rest.", "",
        "## Boundary", "",
    ]
    b = report["boundary"]
    lines += [
        f"- Source: Fairfax County GIS `Fairfax County Boundary`, last edited {b['source_last_edited']}.",
        f"- {b['geometry_type']}, {b['parts']} part(s), holes per part {b['holes_per_part']}; bounds {b['bounds']}.",
        f"- Share of each neighbour's VGIN polygon the boundary overlaps (edge slivers between two surveys): "
        f"{b['excluded_overlap_share']}; covers {', '.join(b['included_checks'])}.",
        (f"- Repaired: the published polygon is invalid ({b['repair']['reason']}). The county owns an enclave "
         "inside Fairfax City (the Judicial Center complex), which GeoJSON's single-exterior polygon reads as a "
         f"hole nested in the city's hole; `make_valid` splits it into its own part ({b['repair']['parts_after']} "
         f"parts, area ratio {b['repair']['area_ratio']:.5f}). The raw snapshot is kept unrepaired."
         if b["repair"] else "- The published polygon is valid as delivered."),
        "- Fairfax City (independent, enclaved) and Falls Church (independent, on the eastern edge) are outside it, "
        "except the county's own enclave inside Fairfax City; the towns of Vienna, Herndon and Clifton are inside it, being county territory.", "",
        "## County inventory", "",
        "`inventory` is `null`. Fairfax County publishes no tree inventory: its open data carries canopy polygons "
        "(`Tree Canopy 2021`) only, and the `Trees2` feature service holds two test records. Tree presence on "
        "every county parcel is therefore unknown until curated imagery tiles supply labels.", "",
        "## Calibration inventories", "",
        "| source | bundle city | raw | delivered | inside own jurisdiction | inside county | IDs unique |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, r in report["sources"].items():
        c = r["coordinates"]
        lines.append(f"| {name} | {r['city']} | {r['stages']['raw']} | {r['stages']['delivered']} | "
                     f"{c['inside_own_jurisdiction']} | {c['inside_county_boundary']} | {r['ids_unique']} |")
    lines.append("")
    for name, r in report["sources"].items():
        s, sp, c = r["stages"], r["species"], r["coordinates"]
        lines += [f"### {name}", "", f"- Selection: {r['selection']}.", f"- Filtering: `{json.dumps(s)}`."]
        lines.append(f"- Coordinates: all finite = {c['finite']}; outside own jurisdiction {c['outside_own'] or 'none'}; "
                     f"{c['duplicate_coordinate_rows']} rows share an exact coordinate with another row of the source.")
        lines.append(f"- Dates: `{json.dumps(r['dates'])}`.")
        lines.append(f"- DBH ({r['dbh_unit']}): `{json.dumps(r['dbh'])}`.")
        lines.append(f"- Species: {sp['rows']} rows, {sp['distinct']} distinct values; {sp['species_level']} species-level, "
                     f"{sp['genus_only']} genus-only, {sp['unknown']} `Unknown`; {sp['cultivar_rows']} rows keep a cultivar.")
        lines.append("- Top species: " + ", ".join(f"{k} ({v})" for k, v in sp["top"]) + ".")
        if sp["unresolved"]:
            lines.append("- Unresolved source names (published as `Unknown`): "
                         + ", ".join(f"`{k}` ({v})" for k, v in sp["unresolved"]) + ".")
        if sp["genus_only_sources"]:
            lines.append("- Genus-only, kept as the genus: "
                         + ", ".join(f"`{o}` -> {g} ({n})" for (o, g), n in sp["genus_only_sources"]) + ".")
        if sp["hybrids"]:
            lines.append("- Hybrid names: " + ", ".join(f"`{o}` -> {g} ({n})" for (o, g), n in sp["hybrids"]) + ".")
        lines.append("")
    pairs = report["near_pairs"]
    lines += [
        "## Overlap and duplication", "",
        f"The sources are disjoint jurisdictions. Cross-source point pairs closer than {report['near_pair_metres']} m: "
        f"{pairs or 'none'}. The pipeline's own cell dedup (`tree_dedup.preql`, USNVA cell "
        "0.00008983 x 0.00011531 degrees) applies to the published USNVA trees, which merge OSM as well; OSM rows "
        "are not in this bundle.", "",
        "## Acquisition terms", "",
    ]
    for src in manifest["sources"]:
        lines.append(f"- {src['name']}: {src['license']} ({src['url']}).")
    lines += ["", "## Limitations", ""] + [f"- {item}" for item in manifest["limitations"]] + [""]
    (out / "source-audit.md").write_text("\n".join(lines), encoding="utf-8")


LIMITATIONS = [
    "No Fairfax County tree inventory exists in public data (canopy polygons only; the Trees2 service has two test records), so `inventory` is null and county tree presence is unknown outside curated tiles.",
    "Fairfax City's 2023 inventory (11,487 trees, 146 classifications per the city's summary PDF) is held in a PlanIT Geo TreePlotter viewer (pg-cloud.com/FairfaxVA) with no published export or terms; it is not included and needs a data request to the City.",
    "Calibration inventories cover public trees only (streets, parks, schools, rights-of-way); private and residential trees are absent, so they calibrate species mix of managed trees, not of the whole canopy.",
    "Falls Church's inventory is a 2004 survey last edited in 2015; species mix and sizes are 20+ years old and planting dates are not published.",
    "Arlington publishes common names only; species come from shared/species/english.py, and the names it cannot resolve are listed per source above as Unknown.",
    "Arlington's `CultivarVariety` (1,660 rows) is not read by the pipeline adapter, so `cultivar` comes only from the common name; the raw value is kept as `source_cultivar`.",
    "About 300 Falls Church rows (2004 survey) and a dozen Arlington rows lie inside the current Fairfax County boundary, clustered just west of the Falls Church line; the survey predates the 2014 Falls Church / Fairfax County boundary adjustment. They stay calibration rows under their publisher's identity and are not county inventory.",
    "Alexandria's notable-tree layer has 12 records without a location, dropped.",
    "Species spellings were checked against GBIF's backbone (`species/match?strict=true`) because POWO answered 403 on 2026-11-06; seven typos were folded centrally in SPECIES_MISSPELLINGS. `Halesia caroliniana` (Falls Church, 35) and `Rhus copallinum` (Arlington, 8) are spelling variants of accepted names and are kept as published; `tools/species_audit.py` should re-adjudicate them once Kew answers.",
    "Unresolved mappings: Arlington common names that resolve to no taxon (listed above, about 120 rows, mostly `Other Species`) and the ambiguous hybrid `Oak, Hybrid Black/Red` publish as Unknown; Alexandria's varieties (`Fraxinus americana var biltmoreana`, `Crataegus intricata var. Bilt.`, `Cedrus atlantica glauca`) are reduced to the species by the central rule.",
    "Arlington's planting dates (`Installed`) start in 2012 and exist only for trees the county planted since; older trees have none.",
    "Alexandria's layer is notable/champion trees only (large specimens, public and private); it is a heavily size-biased selection, its DBH is derived from girth, and it should not be used to estimate species frequency.",
    "Calibration jurisdiction codes USARL, USFCH and USALX are bundle-local; the public pipeline carries Arlington and Falls Church as city USNVA with data_source ARLINGTON / FALLS_CHURCH and does not publish Alexandria.",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--near-metres", type=float, default=2.0)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)

    report: dict = {"sources": {}}
    jurisdictions = load_jurisdictions(out)
    county, boundary_source = build_boundary(out, report, jurisdictions)
    tables: dict[str, pa.Table] = {}
    sources = [boundary_source]
    for name, build in (("Arlington DPR Trees", arlington), ("Falls Church Public_Trees", falls_church),
                        ("Alexandria Notable Trees", alexandria)):
        table, stages, source = build(out)
        tables[name] = table
        sources.append(source)
        ids = table.column("tree_id").to_pylist()
        report["sources"][name] = {
            "city": table.column("city")[0].as_py(),
            "selection": table.column("selection")[0].as_py(),
            "stages": stages,
            "ids_unique": len(ids) == len(set(ids)) and all(ids),
            "coordinates": coordinate_profile(table, county, jurisdictions),
            "species": species_profile(table),
            "dbh": dbh_profile(table),
            "dbh_unit": table.column("source_dbh_unit")[0].as_py(),
            "dates": date_profile(table),
        }
    report["near_pair_metres"] = args.near_metres
    report["near_pairs"] = near_pairs(tables, args.near_metres)

    calibration = pa.concat_tables(tables.values())
    pq.write_table(calibration, out / "calibration-inventory.parquet")

    extraction = out / "extraction"
    extraction.mkdir()
    for path in (Path(__file__), RAW / "usnva" / "arlington_tree_info.py", RAW / "usnva" / "falls_church_tree_info.py"):
        shutil.copy2(path, extraction / path.name)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=RAW, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=RAW, capture_output=True, text=True).stdout.strip()

    manifest = {
        "schema_version": 1,
        "city": CITY,
        "jurisdiction": JURISDICTION,
        "boundary": "boundary.geojson",
        "inventory": None,
        "calibration_inventory": "calibration-inventory.parquet",
        "sources": sources,
        "limitations": LIMITATIONS,
        "sha256": {name: sha256(out / name) for name in ("boundary.geojson", "calibration-inventory.parquet")},
        "built_at": now(),
        "jurisdiction_codes": {
            "USFFX": {"jurisdiction": JURISDICTION, "fips": "51059", "pipeline_city": "USNVA (territory, OSM only)"},
            "USARL": {"jurisdiction": "Arlington County, Virginia", "fips": "51013", "pipeline_city": "USNVA", "data_source": "ARLINGTON"},
            "USFCH": {"jurisdiction": "City of Falls Church, Virginia", "fips": "51610", "pipeline_city": "USNVA", "data_source": "FALLS_CHURCH"},
            "USALX": {"jurisdiction": "City of Alexandria, Virginia", "fips": "51510", "pipeline_city": None, "data_source": "ALEXANDRIA_NOTABLE"},
        },
        "extraction": {"script": "data/raw/tools/fairfax_source_bundle.py", "repo_commit": commit,
                       "repo_dirty": bool(dirty), "copies": "extraction/"},
        "snapshots": sorted(str(p.relative_to(out)).replace("\\", "/") for p in (out / "snapshots").iterdir()),
    }
    write_audit(out, report, manifest)
    (out / "data-audit.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "rows": calibration.num_rows,
                      "per_source": {k: v["stages"]["delivered"] for k, v in report["sources"].items()}}, indent=2))


if __name__ == "__main__":
    main()
