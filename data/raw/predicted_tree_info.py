#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""Emit bulk aerial-imagery predictions in the canonical tree schema.

Unlike `satellite_tree_info.py`, nobody looked at these rows one by one: they
are a model run's detections over a city, written by the imagery side as one
staged parquet per city (`{code}_predicted_staging.parquet` in the staging
prefix, beside the OSM extracts).  So the row is published as a *prediction*,
the `PREDICTED_{code}` partition, and the shared merge treats it as the
weakest source there is (tree_dedup.preql): any tree another source has
absorbs it, and it lends that tree nothing.  It only shows on the map where
no other source has a tree.

The staged parquet's contract, one row per detection:

    tree_id          string   stable id, `pred-` prefixed
    city             string   five-letter city code
    latitude         double   crown centre
    longitude        double
    genus            string   the model's genus label, or null
    species          string   the model's species label, or null
    score            double   detection confidence
    crown_width_m    double   the model's crown-head width, or null
    run_id           string   the training run that predicted it
    imagery_version  string
    species_confidence double the model's probability for `species`, or null
    genus_confidence double   the model's probability for `genus`, or null

What reaches the canonical columns, and what deliberately does not:

* `species` is the model's species when its confidence is at least
  NAME_CONFIDENCE, else its genus when that confidence is, else Unknown.
  Top-1 species is right under half the time overall (and the genus head no
  more often), but both are calibrated: on the three-city v13 run's Boston
  validation, names at 0.8 or more were right about 80% of the time, on
  about 30% of matched trees.  `species` is shown as an identification
  everywhere and feeds the species charts, so an unsure name stays out.  A
  file without the confidence columns names nothing.
* `diameter_at_breast_height` is always null: the column means a measurement.
* `crown_width_m` is the crown-head width, an observation of this crown from
  the image, as for reviewed detections.
* `score`, `run_id` and `imagery_version` are not published: they are for
  the imagery side, which owns the staged file.

A missing staged file is zero rows, not an error, like a missing satellite
export.  `PREDICTED_STAGING_URL` overrides where a city's file is read from
(`{code}` is substituted; a local path works), for building a city against a
file that is not uploaded yet.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from typing import Any, Mapping

import pyarrow as pa
import pyarrow.parquet as pq
import requests

sys.path.insert(0, str(Path(__file__).parent))
from shared.ingest import (  # noqa: E402
    PREDICTED_DATA_SOURCES,
    emit,
    enforce_tree_schema,
    in_city_territory,
    normalize_species,
    parse_pushdown_filters,
    staging_url,
)

ID_PREFIX = "pred-"
# The probability a species or genus label needs before it is published.
NAME_CONFIDENCE = 0.8


def staging_name(city_code: str) -> str:
    return f"{city_code.lower()}_predicted_staging.parquet"


def staged_location(city_code: str) -> str:
    template = os.environ.get("PREDICTED_STAGING_URL")
    if template:
        return template.replace("{code}", city_code.lower())
    return staging_url(staging_name(city_code))


def load_staged(city_code: str) -> list[Mapping[str, Any]]:
    """Read one city's staged predictions; a missing file is no rows."""
    location = staged_location(city_code)
    if not location.startswith(("http://", "https://")):
        path = Path(location)
        if not path.exists():
            print(f"Predicted ingest: no staged file at {path}; emitting 0 rows", file=sys.stderr)
            return []
        return pq.read_table(path).to_pylist()
    response = requests.get(location, timeout=120)
    if response.status_code == 404:
        print(f"Predicted ingest: no staged file at {location}; emitting 0 rows", file=sys.stderr)
        return []
    response.raise_for_status()
    return pq.read_table(io.BytesIO(response.content)).to_pylist()


def _confident(record: Mapping[str, Any], label: str) -> str | None:
    """The record's *label* ("species" or "genus") when its confidence clears
    NAME_CONFIDENCE, else None."""
    raw = record.get(label)
    try:
        confidence = float(record.get(f"{label}_confidence"))
    except (TypeError, ValueError):
        return None
    if not raw or not str(raw).strip() or not confidence >= NAME_CONFIDENCE:
        return None
    return str(raw).strip()


def _name(record: Mapping[str, Any]) -> str | None:
    """A confident species, else a confident genus, else None (Unknown)."""
    species = _confident(record, "species")
    if species:
        return normalize_species(species)
    genus = _confident(record, "genus")
    # normalize_species capitalises a bare genus like any other name.
    return normalize_species(genus.split()[0]) if genus else None


def _positive_float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


SCHEMA = pa.schema(
    [
        ("tree_id", pa.string()),
        ("city", pa.string()),
        ("data_source", pa.string()),
        ("species", pa.string()),
        ("tree_name", pa.string()),
        ("plant_date", pa.date32()),
        ("diameter_at_breast_height", pa.float64()),
        ("crown_width_m", pa.float64()),
        ("latitude", pa.float64()),
        ("longitude", pa.float64()),
        ("submission_photo_url", pa.string()),
        ("borough", pa.string()),
    ]
)


def records_to_table(records, city_code: str) -> pa.Table:
    data_source = PREDICTED_DATA_SOURCES[city_code]
    rows: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for record in records:
        tree_id = str(record.get("tree_id") or "").strip()
        if not tree_id.startswith(ID_PREFIX):
            skip(f"id without the {ID_PREFIX!r} prefix")
            continue
        if str(record.get("city") or "").upper().strip() != city_code:
            skip("another city's row")
            continue
        try:
            latitude = float(record["latitude"])
            longitude = float(record["longitude"])
        except (KeyError, TypeError, ValueError):
            skip("invalid coordinates")
            continue
        if not in_city_territory(city_code, latitude, longitude):
            skip(f"outside {city_code}")
            continue
        rows.append(
            {
                "tree_id": tree_id,
                "city": city_code,
                "data_source": data_source,
                "species": _name(record),
                "tree_name": None,
                "plant_date": None,
                "diameter_at_breast_height": None,
                "crown_width_m": _positive_float_or_none(record.get("crown_width_m")),
                "latitude": latitude,
                "longitude": longitude,
                "submission_photo_url": None,
                "borough": None,
            }
        )
    for reason, count in sorted(skipped.items()):
        print(f"Predicted ingest: skipped {count} {city_code} rows: {reason}", file=sys.stderr)
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    return enforce_tree_schema(table, city="Predicted")


def main(argv: list[str] | None = None) -> None:
    filters = parse_pushdown_filters(list(sys.argv[1:] if argv is None else argv))
    city = filters.get("city")
    codes = [city.upper()] if city else sorted(PREDICTED_DATA_SOURCES)
    tables = [
        records_to_table(load_staged(code), code)
        for code in codes
        if code in PREDICTED_DATA_SOURCES
    ]
    emit(pa.concat_tables(tables) if tables else records_to_table([], next(iter(PREDICTED_DATA_SOURCES))))


if __name__ == "__main__":
    main()
