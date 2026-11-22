#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Singapore's Heritage Trees, from the National Parks Board on data.gov.sg.

Source: data.gov.sg dataset `d_644ff187b6d14d6316f47284a4a6c81f` ("Heritage
Trees", collection 2038, managed by the National Parks Board), one GeoJSON of
255 points in WGS84.  Singapore Open Data Licence v1.0
(https://data.gov.sg/open-data-licence): use, copy, distribute and adapt,
commercially or not, with a notice naming the dataset and linking the licence.

This register is the city's municipal partition because it is the only open
tree data the city publishes: NParks keeps a street and park tree inventory
(Trees.sg) but releases no open copy, so Singapore's bulk comes from OSM and
these are the trees an agency itself vouches for.  It is not a NOTABLE_SGSIN
register (shared/notable.py links a register into an inventory, and there is
no inventory to link into).

The download is two hops: `poll-download` answers with a short-lived signed
S3 URL, and that URL serves the file.  Each feature carries:

  * `HT` -- the heritage-tree number, `HT 2007-163` (year of designation, then
    a running number).  It is the register's own key and the tree's page on
    heritagetrees.nparks.gov.sg is named after it, so it is the id:
    `sin-ht-2007-163`.  Checked unique and non-null over the whole layer
    (255 of 255 distinct; the running numbers are distinct on their own too);
    a row whose number does not parse is dropped with a count, and more than
    1% of them refuses the build.  `OBJECTID` (16321..16575, dense) is a row
    number from the agency's export and is not used.
  * `NAME` -- the scientific name with its authority (`Samanea saman (Jacq.)
    Merr.`); the authority is dropped here and the binomial kept.
  * `DESCRIPTION` -- `Common Name : Rain Tree. Found in Fort Canning Park`.
    The common name is not mapped (those come from the enrichment table); the
    location sentence is the register's own words and goes into
    `significance` with the tree's page as `significance_source`, which the
    city model does not bind yet (see singapore_tree_info.preql).
  * No girth, height, crown or planting date: the register's pages hold the
    girth but sit behind a CDN that refuses scripted clients, and 255 page
    fetches per build is not what the open dataset licenses, so those columns
    are null.  The `HT` year is the year of designation, not of planting.

Two features (HT 2010-177 and HT 2010-1003, both Ficus drupacea with an empty
"Found in") share one point; they are distinct register entries and both
are kept.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    UpstreamUnavailable,
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    validate_coordinates,
)

DATASET_ID = "d_644ff187b6d14d6316f47284a4a6c81f"
POLL_URL = f"https://api-open.data.gov.sg/v1/public/api/datasets/{DATASET_ID}/poll-download"
SOURCE_URL = f"https://data.gov.sg/datasets/{DATASET_ID}/view"

HT_RE = re.compile(r"^\s*HT\s*(\d{4})\s*-\s*(\d+)\s*$", re.I)
# "Common Name : Rain Tree. Found in Fort Canning Park" -> the location.
FOUND_RE = re.compile(r"\bFound in\b\s*(.*)$", re.I | re.S)
HYBRID_MARKS = {"x", "×"}
QUOTES = ("'", "‘", "’", '"', "“", "”")
MAX_UNREADABLE_FRACTION = 0.01


def download_features() -> list[dict]:
    """The register's features: poll for the signed URL, then fetch it."""
    poll = get_json_with_retry(POLL_URL)
    url = (poll.get("data") or {}).get("url") if isinstance(poll, dict) else None
    if not url:
        # data.gov.sg answers a throttled or still-preparing request with a
        # non-zero `code` and no URL: an availability problem, not a schema one.
        raise UpstreamUnavailable(f"{POLL_URL} returned no download URL: {poll!r:.200}")
    collection = get_json_with_retry(url, timeout=300)
    features = collection.get("features") if isinstance(collection, dict) else None
    if not isinstance(features, list):
        raise RuntimeError(f"Singapore heritage trees: no `features` list in {DATASET_ID}")
    return features


def binomial(name: str | None) -> str | None:
    """`Samanea saman (Jacq.) Merr.` -> `Samanea saman`; authorities dropped.

    Keeps the genus, a hybrid mark and the epithet, and a quoted cultivar if
    one follows (enforce_tree_schema moves it to `cultivar`); everything else
    -- authorities, `subsp.`/`var.` and their epithets -- goes.  The schema
    truncates to species rank anyway; doing it here keeps an author's surname
    from ever being read as an epithet.
    """
    if not name or not name.strip():
        return None
    text = " ".join(name.split())
    cultivar = None
    cut = [text.find(q) for q in QUOTES if q in text]
    if cut:
        cultivar = text[min(cut):].strip()
        text = text[: min(cut)].strip()
    tokens = text.split()
    if not tokens:
        return None
    parts = [tokens[0]]
    rest = tokens[1:]
    if rest and rest[0].lower() in HYBRID_MARKS:
        parts.append("x")
        rest = rest[1:]
    if rest and re.fullmatch(r"[a-z][a-z-]+", rest[0]):
        parts.append(rest[0])
    elif parts[-1] == "x":
        parts.pop()
    out = " ".join(parts)
    return f"{out} {cultivar}" if cultivar else out


def location(description: str | None) -> str | None:
    if not description:
        return None
    m = FOUND_RE.search(description)
    if not m:
        return None
    text = " ".join(m.group(1).split()).rstrip(" .")
    return text or None


def transform(features: list[dict]) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    significance: list[str | None] = []
    significance_source: list[str | None] = []
    unreadable = no_position = 0

    for feature in features:
        props = feature.get("properties") or {}
        m = HT_RE.match(str(props.get("HT") or ""))
        if not m:
            unreadable += 1
            continue
        year, number = m.group(1), m.group(2)
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") if geometry.get("type") == "Point" else None
        if not coords or len(coords) < 2 or coords[0] is None or coords[1] is None:
            no_position += 1
            continue
        tree_id.append(f"sin-ht-{year}-{number}")
        species.append(binomial(props.get("NAME")))
        longitude.append(float(coords[0]))
        latitude.append(float(coords[1]))
        where = location(props.get("DESCRIPTION"))
        blurb = f"Heritage Tree HT {year}-{number}, designated by the National Parks Board in {year}."
        significance.append(f"{blurb} Found in {where}." if where else blurb)
        link = str(props.get("HYPERLINK") or "").strip()
        significance_source.append(link or SOURCE_URL)

    total = len(features)
    if total and unreadable / total > MAX_UNREADABLE_FRACTION:
        raise RuntimeError(
            f"Singapore heritage trees: {unreadable} of {total} rows have an "
            f"unreadable HT number; the register's format changed"
        )
    print(
        f"Singapore heritage trees: {len(tree_id)} kept of {total}; dropped "
        f"{unreadable} with no readable HT number, {no_position} with no point",
        file=sys.stderr,
    )
    n = len(tree_id)
    return pa.table({
        "tree_id": pa.array(tree_id, type=pa.string()),
        "city": pa.array(["SGSIN"] * n, type=pa.string()),
        "species": pa.array(species, type=pa.string()),
        "plant_date": pa.array([None] * n, type=pa.date32()),
        "latitude": pa.array(latitude, type=pa.float64()),
        "longitude": pa.array(longitude, type=pa.float64()),
        "diameter_at_breast_height": pa.array([None] * n, type=pa.float64()),
        "significance": pa.array(significance, type=pa.string()),
        "significance_source": pa.array(significance_source, type=pa.string()),
    })


if __name__ == "__main__":
    table = transform(download_features())
    table = validate_coordinates(table, city="Singapore", city_code="SGSIN")
    table = enforce_tree_schema(
        table, city="Singapore", data_source="SINGAPORE_NPARKS"
    )
    emit(table)
