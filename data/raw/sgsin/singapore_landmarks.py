#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Singapore's National Monuments and Historic Sites, from the National
Heritage Board on data.gov.sg.

Two NHB datasets, both GeoJSON points in WGS84 and both under the Singapore
Open Data Licence v1.0 (https://data.gov.sg/open-data-licence):

  * "Monuments (GEOJSON)", `d_b29c230ec6b609e29ed42f71ca9a8767` -- the
    National Monuments gazetted under the Preservation of Monuments Act, "the
    highest level of protection in Singapore" (77 on 2026-11-22).  This is the
    designation register, the runbook's first preference.
  * "Historic Sites (GEOJSON)", `d_31e16b12809e66673e90d8b04fdee1b2` -- the
    sites NHB marks with a plaque for what happened there (Fort Canning
    Command Centre, the Ho Chi Minh marker), 100 points.  Not a protection,
    but the same agency's own list of the places people name.

Singapore is a city-state, so the whole register is the city's own area.

**Keyed on the page slug.**  Each feature links its page on roots.gov.sg
(`.../national-monuments/saint-georges-church`), and the last path segment is
unique and non-null over both layers (77 and 100 distinct).  `OBJECTID` is an
export row number (the Historic Sites layer even names it `OBJECTID_1`), so it
is not used.  Ids are `sin-mon-{slug}` and `sin-hs-{slug}`.  A row with no
name, slug or point is dropped with a count.

The download is two hops: `poll-download` answers with a short-lived signed
S3 URL, and that URL serves the file.
"""

import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import UpstreamUnavailable, emit, get_json_with_retry, make_point_wkt  # noqa: E402

POLL_URL = "https://api-open.data.gov.sg/v1/public/api/datasets/{dataset}/poll-download"

# (dataset id, id prefix, category)
LAYERS = (
    ("d_b29c230ec6b609e29ed42f71ca9a8767", "sin-mon", "National Monument"),
    ("d_31e16b12809e66673e90d8b04fdee1b2", "sin-hs", "Historic Site"),
)


def download_features(dataset: str) -> list[dict]:
    url = POLL_URL.format(dataset=dataset)
    poll = get_json_with_retry(url)
    signed = (poll.get("data") or {}).get("url") if isinstance(poll, dict) else None
    if not signed:
        raise UpstreamUnavailable(f"{url} returned no download URL: {poll!r:.200}")
    collection = get_json_with_retry(signed, timeout=300)
    features = collection.get("features") if isinstance(collection, dict) else None
    if not isinstance(features, list):
        raise RuntimeError(f"Singapore landmarks: no `features` list in {dataset}")
    return features


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def address(props: dict) -> str | None:
    parts = [
        clean(props.get("ADDRESSBLOCKHOUSENUMBER")),
        clean(props.get("ADDRESSSTREETNAME")),
    ]
    street = " ".join(p for p in parts if p)
    pieces = [p for p in (clean(props.get("ADDRESSBUILDINGNAME")), street or None) if p]
    postal = clean(props.get("ADDRESSPOSTALCODE"))
    if postal:
        pieces.append(f"Singapore {postal}")
    return ", ".join(pieces) or None


def read_landmarks() -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    for dataset, prefix, category in LAYERS:
        features = download_features(dataset)
        kept = unusable = 0
        for feature in features:
            props = feature.get("properties") or {}
            name = clean(props.get("NAME"))
            link = clean(props.get("HYPERLINK")) or ""
            slug = link.rstrip("/").rsplit("/", 1)[-1].lower() if link else ""
            geometry = feature.get("geometry") or {}
            coords = geometry.get("coordinates") if geometry.get("type") == "Point" else None
            wkt = make_point_wkt(coords[0], coords[1]) if coords and len(coords) >= 2 else None
            key = f"{prefix}-{slug}"
            if not name or not slug or wkt is None or key in seen:
                unusable += 1
                continue
            seen.add(key)
            kept += 1
            records.append(
                {
                    "landmark_id": key,
                    "name": name,
                    "geometry_raw": wkt,
                    "category": category,
                    "address": address(props),
                }
            )
        print(
            f"Singapore {category.lower()}s: {kept} kept of {len(features)}, "
            f"{unusable} dropped (no name, page or point, or a repeated page)",
            file=sys.stderr,
        )
    return records


def transform(records: list[dict]) -> pa.Table:
    def col(key):
        return pa.array([r[key] for r in records], type=pa.string())

    return pa.table(
        {
            "landmark_id": col("landmark_id"),
            "city": pa.array(["SGSIN"] * len(records), type=pa.string()),
            "name": col("name"),
            "geometry_raw": col("geometry_raw"),
            "category": col("category"),
            "address": col("address"),
        }
    )


if __name__ == "__main__":
    emit(transform(read_landmarks()))
