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
    leaf_habit       string   deciduous | conifer | evergreen when every top
                              species candidate shares it, else null (optional)

What reaches the canonical columns, and what deliberately does not:

* `species` is the model's species when its confidence is at least
  NAME_CONFIDENCE, else its genus when that confidence is, else Unknown.
  Top-1 species is right under half the time overall (and the genus head no
  more often), but both are calibrated: on the three-city v13 run's Boston
  validation, names at 0.8 or more were right about 80% of the time, on
  about 30% of matched trees.  `species` is shown as an identification
  everywhere and feeds the species charts, so an unsure name stays out.  A
  file without the confidence columns names nothing.  An unnamed row whose
  `leaf_habit` is set publishes that habit's sentinel (`Unknown deciduous`,
  `Unknown conifer`, `Unknown evergreen`; shared.ingest.LEAF_HABIT_SENTINELS)
  instead of `Unknown`, so the map can draw its form and season; plain
  `Unknown` stays for a detection whose candidates disagree.
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
from typing import Any, Iterator, Mapping

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests

sys.path.insert(0, str(Path(__file__).parent))
from shared.ingest import (  # noqa: E402
    LEAF_HABIT_SENTINELS,
    CITY_TERRITORY,
    PREDICTED_DATA_SOURCES,
    PREDICTED_STAGED_WITH,
    emit,
    enforce_tree_schema,
    in_city_territory,
    normalize_species,
    parse_pushdown_filters,
    report_species_cleanup,
    staging_url,
)

ID_PREFIX = "pred-"
# The probability a species or genus label needs before it is published.
NAME_CONFIDENCE = 0.8
# Rows turned into Python records at a time.  Boston's three million staged
# rows as one list of dicts, and a second list built from it, ran the 6 GB
# city job out of memory; a batch is a few hundred megabytes at its peak.
BATCH_ROWS = 100_000


def staged_city(city_code: str) -> str:
    """The city whose staged file holds *city_code*'s predictions: itself,
    unless its ground was predicted as part of a larger run."""
    return PREDICTED_STAGED_WITH.get(city_code, city_code)


def staging_name(city_code: str) -> str:
    return f"{staged_city(city_code).lower()}_predicted_staging.parquet"


def staged_location(city_code: str) -> str:
    template = os.environ.get("PREDICTED_STAGING_URL")
    if template:
        return template.replace("{code}", staged_city(city_code).lower())
    return staging_url(staging_name(city_code))


def staged_file(city_code: str) -> pq.ParquetFile | None:
    """One city's staged predictions, unread; None when there is no file."""
    location = staged_location(city_code)
    if not location.startswith(("http://", "https://")):
        path = Path(location)
        if not path.exists():
            print(f"Predicted ingest: no staged file at {path}; emitting 0 rows", file=sys.stderr)
            return None
        return pq.ParquetFile(path)
    response = requests.get(location, timeout=120)
    if response.status_code == 404:
        print(f"Predicted ingest: no staged file at {location}; emitting 0 rows", file=sys.stderr)
        return None
    response.raise_for_status()
    return pq.ParquetFile(io.BytesIO(response.content))


def load_staged(city_code: str) -> list[Mapping[str, Any]]:
    """Read one city's staged predictions; a missing file is no rows."""
    staged = staged_file(city_code)
    return [] if staged is None else staged.read().to_pylist()


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
    """A confident species, else a confident genus, else the leaf-habit
    sentinel, else None (Unknown)."""
    species = _confident(record, "species")
    if species:
        return normalize_species(species)
    genus = _confident(record, "genus")
    # normalize_species capitalises a bare genus like any other name.
    if genus:
        return normalize_species(genus.split()[0])
    habit = str(record.get("leaf_habit") or "").strip().lower()
    return LEAF_HABIT_SENTINELS.get(habit)


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
        ("plant_date", pa.date32()),
        ("diameter_at_breast_height", pa.float64()),
        ("crown_width_m", pa.float64()),
        ("latitude", pa.float64()),
        ("longitude", pa.float64()),
        ("submission_photo_url", pa.string()),
        ("borough", pa.string()),
    ]
)


def records_to_table(
    records,
    city_code: str,
    *,
    seen_ids: set[str] | None = None,
    summary: dict[str, int] | None = None,
    skipped: dict[str, int] | None = None,
) -> pa.Table:
    """The canonical rows of *records*.  A caller working through a city in
    batches passes the same *seen_ids*, *summary* and *skipped* each time and
    reports them once at the end (`city_tables`); without them this reports
    its own."""
    data_source = PREDICTED_DATA_SOURCES[city_code]
    rows: list[dict[str, Any]] = []
    report = skipped is None
    skipped = {} if skipped is None else skipped

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for record in records:
        tree_id = str(record.get("tree_id") or "").strip()
        if not tree_id.startswith(ID_PREFIX):
            skip(f"id without the {ID_PREFIX!r} prefix")
            continue
        if str(record.get("city") or "").upper().strip() != staged_city(city_code):
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
                "plant_date": None,
                "diameter_at_breast_height": None,
                "crown_width_m": _positive_float_or_none(record.get("crown_width_m")),
                "latitude": latitude,
                "longitude": longitude,
                "submission_photo_url": None,
                "borough": None,
            }
        )
    if report:
        report_skipped(skipped, city_code)
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    return enforce_tree_schema(table, city="Predicted", seen_ids=seen_ids, summary=summary)


def report_skipped(skipped: Mapping[str, int], city_code: str) -> None:
    for reason, count in sorted(skipped.items()):
        print(f"Predicted ingest: skipped {count} {city_code} rows: {reason}", file=sys.stderr)


def near_territory(table: pa.Table, city_code: str) -> tuple[pa.Table, int]:
    """The rows inside the envelope of the city's rectangles, and how many
    were outside it.  A town reading a metro's staged file keeps a few rows in
    a hundred; dropping the rest as Arrow, before they become Python records,
    is what keeps that read to seconds.  `records_to_table` still decides
    membership row by row."""
    if "latitude" not in table.column_names or "longitude" not in table.column_names:
        return table, 0
    boxes = CITY_TERRITORY[city_code]
    lat, lon = table["latitude"], table["longitude"]
    mask = pc.and_(
        pc.and_(pc.greater_equal(lat, min(b[0] for b in boxes)), pc.less(lat, max(b[1] for b in boxes))),
        pc.and_(pc.greater_equal(lon, min(b[2] for b in boxes)), pc.less(lon, max(b[3] for b in boxes))),
    )
    # A null or unparsable coordinate is kept for the row loop to report.
    near = table.filter(pc.fill_null(mask, True))
    return near, table.num_rows - near.num_rows


def city_tables(city_code: str) -> Iterator[pa.Table]:
    """One city's canonical rows, a batch of the staged file at a time."""
    staged = staged_file(city_code)
    if staged is None:
        return
    seen: set[str] = set()
    summary: dict[str, int] = {}
    skipped: dict[str, int] = {}
    for batch in staged.iter_batches(batch_size=BATCH_ROWS):
        near, far = near_territory(pa.Table.from_batches([batch]), city_code)
        if far:
            skipped[f"outside {city_code}"] = skipped.get(f"outside {city_code}", 0) + far
        yield records_to_table(
            near.to_pylist(), city_code, seen_ids=seen, summary=summary, skipped=skipped
        )
    report_skipped(skipped, city_code)
    report_species_cleanup(summary, city="Predicted")


def main(argv: list[str] | None = None) -> None:
    filters = parse_pushdown_filters(list(sys.argv[1:] if argv is None else argv))
    city = filters.get("city")
    codes = [city.upper()] if city else sorted(PREDICTED_DATA_SOURCES)
    tables = [
        table for code in codes if code in PREDICTED_DATA_SOURCES for table in city_tables(code)
    ]
    emit(pa.concat_tables(tables) if tables else records_to_table([], next(iter(PREDICTED_DATA_SOURCES))))


if __name__ == "__main__":
    main()
