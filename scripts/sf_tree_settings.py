"""Polygon inputs and point-in-polygon rules for the SF land-setting poster."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import shapely
from shapely.geometry import shape


CACHE = Path(__file__).resolve().parents[1] / ".tmp" / "sf-tree-setting"
CPAD = ("https://services1.arcgis.com/4ZKi1B1zTblbwgWB/arcgis/rest/services/"
        "cpad_2024a_unitsgdb/FeatureServer/0")
ROLL_YEAR = 2025
LAYERS = {
    "land": ("txuc-3kzm", "the_geom", "true"),
    "streets": ("h8n7-e4ns", "the_geom", "true"),
    "city_property": ("gtnh-hgvs", "shape", "shape is not null"),
    "parcels": ("acdm-wktn", "blklot,mapblklot,shape", "active=true and shape is not null"),
    "roll": ("wv5m-vpq2", "parcel_number,use_code,property_class_code,property_class_code_definition,assessed_land_value",
             f"closed_roll_year={ROLL_YEAR}"),
}


def request_session():
    session = requests.Session()
    retry = Retry(total=4, backoff_factor=.6, status_forcelist=(429, 500, 502, 503, 504))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get_json(session, url, **params):
    response = session.get(url, params=params, timeout=(15, 180))
    response.raise_for_status()
    result = response.json()
    if isinstance(result, dict) and "error" in result:
        raise ValueError(result["error"])
    return result


def fetch_layer(name):
    dataset, fields, where = LAYERS[name]
    with request_session() as session:
        info = get_json(session, f"https://data.sf.gov/api/views/{dataset}")
        version = info["rowsUpdatedAt"]
        signature = hashlib.sha256(f"{fields}|{where}|{version}".encode()).hexdigest()[:12]
        cache = CACHE / f"{dataset}-{signature}.jsonl.gz"
        url = f"https://data.sf.gov/resource/{dataset}.json"
        if not cache.exists():
            query = {"$where": where}
            count = int(get_json(session, url, **query, **{"$select": "count(*) as n"})[0]["n"])
            with tempfile.NamedTemporaryFile(dir=CACHE, delete=False) as tmp:
                pending = Path(tmp.name)
            try:
                written = 0
                with gzip.open(pending, "wt", encoding="utf-8", compresslevel=1) as out:
                    for offset in range(0, count, 10000):
                        rows = get_json(session, url, **query, **{"$select": fields, "$order": ":id",
                                        "$limit": 10000, "$offset": offset})
                        for row in rows:
                            out.write(json.dumps(row, separators=(",", ":")) + "\n")
                        written += len(rows)
                if not written or written != count or get_json(session, f"https://data.sf.gov/api/views/{dataset}")["rowsUpdatedAt"] != version:
                    raise RuntimeError(f"{name}: incomplete or changing source, rerun to retry.")
                pending.replace(cache)
            finally:
                pending.unlink(missing_ok=True)
    print(f"Ready: {name}", flush=True)
    return name, (cache, {"url": f"https://data.sf.gov/d/{dataset}", "rows_updated_epoch": version,
                          "select": fields, "filter": where})


def fetch_inputs():
    CACHE.mkdir(exist_ok=True, parents=True)
    with ThreadPoolExecutor(max_workers=5) as workers:
        return dict(workers.map(fetch_layer, LAYERS))


def park_features():
    cache = CACHE / "cpad-2024a-sf-public-v2.geojson"
    where = ("COUNTY='San Francisco' AND AGNCY_LEV IN "
             "('City','County','Special District','State','Federal')")
    if not cache.exists():
        with request_session() as session:
            url = CPAD + "/query"
            ids = get_json(session, url, where=where, returnIdsOnly="true", f="json")["objectIds"]
            result = get_json(session, url, where=where, outSR=4326, maxAllowableOffset=.000005,
                              outFields="OBJECTID,UNIT_NAME", returnGeometry="true", f="geojson")
            features = result["features"]
            if not ids or {f["properties"]["OBJECTID"] for f in features} != set(ids):
                raise RuntimeError("Incomplete CPAD park response")
            with tempfile.NamedTemporaryFile(mode="w", dir=CACHE, delete=False, encoding="utf-8") as tmp:
                json.dump(features, tmp)
            Path(tmp.name).replace(cache)
    return json.loads(cache.read_text(encoding="utf-8"))


def rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def geometry(data):
    polygon = shape(data)
    return polygon if polygon.is_valid else shapely.make_valid(polygon)


def hits(points, polygons):
    # Query with polygons so GEOS can prepare their geometry for many trees.
    polygon_index, point_index = shapely.STRtree(points).query(polygons, predicate="covers")
    return point_index, polygon_index


def tax_evidence(row):
    code = row.get("property_class_code", "")
    description = row.get("property_class_code_definition", "").lower()
    use = row.get("use_code", "")
    if (use == "GOVT" or " gov" in description or code in
            {"CP", "P", "PI", "RDAP", "SP", "UCP", "USP", "VPUB", "Y"}):
        return "excluded"
    if float(row.get("assessed_land_value") or 0) <= 0:
        return "unknown"
    if use in {"SRES", "MRES"}:
        return "residential"
    if use in {"COMH", "COMM", "COMO", "COMR", "IND"}:
        return "business"
    return "unknown"


def classify(points, layers, parks):
    # The input points have already been clipped to SF land and the poster frame.
    setting = np.full(len(points), "unclassified", dtype="U12")
    park_counts = Counter()
    # Smaller park units get the name when two units cover the same location.
    park_rows = sorted([(geometry(f["geometry"]), f["properties"]["UNIT_NAME"], f["properties"]["OBJECTID"])
                        for f in parks], key=lambda row: (row[0].area, row[1], row[2]))
    tree_idx, poly_idx = hits(points, [row[0] for row in park_rows])
    order = np.argsort(poly_idx, kind="stable")
    matched, first = np.unique(tree_idx[order], return_index=True)
    setting[matched] = "park"
    park_counts.update(park_rows[i][1] for i in poly_idx[order][first])

    streets = [geometry(r["the_geom"]) for r in rows(layers["streets"][0])]
    street_hits = np.unique(hits(points, streets)[0])
    overlap = int((setting[street_hits] == "park").sum())
    setting[street_hits[setting[street_hits] == "unclassified"]] = "street"

    available = setting == "unclassified"
    city_land = [geometry(r["shape"]) for r in rows(layers["city_property"][0])]
    available[hits(points, city_land)[0]] = False

    tax = defaultdict(set)
    for record in rows(layers["roll"][0]):
        tax[record.get("parcel_number")].add(tax_evidence(record))
    # Condominium tax lots can share a single mapped footprint.
    footprints, evidence = {}, defaultdict(set)
    for parcel in rows(layers["parcels"][0]):
        mapped_lot = parcel.get("mapblklot") or parcel.get("blklot")
        if not mapped_lot:
            continue
        polygon = geometry(parcel["shape"])
        key = (mapped_lot, polygon.wkb)
        if key not in footprints:
            footprints[key] = polygon
        evidence[key].update(tax.get(parcel.get("blklot"), ()))
        evidence[key].update(tax.get(mapped_lot, ()))
    keys = list(footprints)
    candidates = np.flatnonzero(available)
    tree_idx, poly_idx = hits(points[candidates], list(footprints.values()))
    # Conservative at boundaries: a government or possessory-interest match
    # vetoes a private assignment, including on a shared condo footprint.
    def matched_trees(use):
        qualifying = np.array([use in evidence[key] for key in keys], dtype=bool)
        return candidates[tree_idx[qualifying[poly_idx]]]

    home = np.zeros(len(points), dtype=bool)
    business = home.copy()
    home[matched_trees("residential")] = True
    business[matched_trees("business")] = True
    veto = matched_trees("excluded")
    home[veto] = business[veto] = False
    setting[home | business] = "private"
    parcel_counts = {"residential": int((home & ~business).sum()),
                     "commercial or industrial": int((business & ~home).sum()),
                     "mixed": int((home & business).sum())}
    return setting, park_counts, parcel_counts, overlap
