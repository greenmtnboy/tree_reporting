"""When a city's derived tables' inputs last published.

A city's `derived-{code}` job (city_derived.preql) builds its predictions and
browser projection from three kinds of published object: the city's own tree
parquet, the species enrichment table and the four fit tables. Its watermark is
the newest of their `Last-Modified` times, so the job rebuilds when the city,
the enrichment or the fits move, and not when another city does.

Object publication time rather than a column inside the objects, for the reason
`city_parquet_probe.py` gives: a rebuild that changes the shape and not the
data watermark still republishes the object, and this notices. Every HEAD
carries a cache-buster (the bucket serves `Cache-Control: max-age=3600`).

Each city's probe is a two-line script beside its model,
`raw/{code}/{slug}_derived_probe.py`, calling `main(code)`; a missing object or
a transport failure degrades to the epoch through `UpstreamUnavailable`, and the
city sits the tick out.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from shared.ingest import UpstreamUnavailable, emit_freshness

DATA_VERSION = "2"
TREES_BASE_URL = "https://storage.googleapis.com/trilogy_public_models/duckdb/trees"
FIT_TABLES = ("age_genus_fits", "age_class_fits", "height_genus_fits", "height_class_fits")


def published_at(label: str, url: str) -> datetime:
    """The object's Last-Modified, read with a cache-buster."""
    try:
        response = requests.head(f"{url}?cb={int(time.time())}", timeout=30, allow_redirects=True)
    except requests.RequestException as err:
        raise UpstreamUnavailable(f"Failed to HEAD the {label} parquet: {err}") from err
    if response.status_code >= 400:
        raise UpstreamUnavailable(f"HEAD {url} returned HTTP {response.status_code}")
    header = response.headers.get("Last-Modified")
    if not header:
        # Not availability: GCS always sends this, so its absence means the
        # URL is not the object we think it is.
        raise RuntimeError(f"the {label} parquet has no Last-Modified")
    return parsedate_to_datetime(header).astimezone(timezone.utc)


def city_inputs(city_code: str) -> dict[str, str]:
    """Every object a city's derived tables read, by label."""
    return {
        "city": f"{TREES_BASE_URL}/{city_code.lower()}_tree_info_v{DATA_VERSION}.parquet",
        "enrichment": f"{TREES_BASE_URL}/tree_enrichment_v{DATA_VERSION}.parquet",
        **{name: f"{TREES_BASE_URL}/{name}_v{DATA_VERSION}.parquet" for name in FIT_TABLES},
    }


def newest_input_publication(city_code: str) -> datetime:
    return max(published_at(label, url) for label, url in city_inputs(city_code).items())


def main(city_code: str) -> None:
    emit_freshness(city_code, lambda: newest_input_publication(city_code), label=f"{city_code} derived inputs")
