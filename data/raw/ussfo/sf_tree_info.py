#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""San Francisco's municipal street tree inventory, from the city's Socrata portal.

Source: "San Francisco Street Tree Inventory" (`tkzw-k3nq`) on **data.sf.gov**,
144,459 rows.

**The dataset under that id is not the one this script used to read.**  DataSF
migrated Public Works to a new asset-management system and *repurposed the
four-four*: the list this repo has read since the first commit is now
`uzd4-f6yf`, "[ARCHIVED] Street Tree List", and `tkzw-k3nq` serves the
replacement with a renamed schema --

    qspecies      -> species          (still "Scientific :: Common")
    plantdate     -> planteddate      (text, ISO; was a calendar_date)
    dbh           -> mapdbh           (text; was numeric)
    qlegalstatus  -> legalstatus
    qsiteinfo     -> siteinfo

-- so the old `$select` fails with `No such column: qspecies` while the
freshness probe, which reads view metadata and never names a column, kept
reporting the portal as freshly published.  The refresh was therefore told
San Francisco was stale every day and failed every day, and the failure was
invisible from the probe's side.  If another Socrata city ever goes quiet,
that is the shape to look for: a working probe over a 400 on the rows.

Two consequences of the migration are data, not plumbing, and are recorded
here because nothing else in the repo would say so:

* The replacement holds **144,459 rows against the archived list's 198,436**,
  and the shortfall is mostly scope rather than removals: 39,028 of the 62,457
  archived ids it does not carry are `Permitted Site` -- a planting permit,
  which the old list published alongside standing trees -- against 14,421
  `DPW Maintained`.  Switching source therefore retires about 30% of the
  San Francisco dots on the map.
* **Tree ids survived the migration**, so nothing churns: a sample of new ids
  resolved 200/200 against the archived list, and a check-in or a satellite
  link recorded against `sf-<treeid>` still points at the same tree.

The host moved too.  `data.sfgov.org` 301s to `data.sf.gov` for metadata but
answers the row query with a bare nginx **403**, which reads as a block rather
than as a redirect; address the new host directly.

Paging is `_socrata_shared.iter_rows` rather than this script's former
`$limit=500000` single request -- the defect that module's docstring names,
and one San Francisco was the example of.

**There is no dead/removed flag on a row.**  The inventory does not retire a
record when the tree comes down: of 5,640 trees carrying a removal notice in
`qrwx-q4gg` ("Street Tree Removal Notifications"), 86.7% are still listed,
with no gradient by age -- a 2017 notice is still in the inventory 83.3% of
the time.  What the row *does* say is said in the species field (`Stump`,
`Planting site`, `Potential Site`), which `is_not_a_tree` drops centrally for
every city.
"""

import sys
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    emit,
    enforce_tree_schema,
    normalize_species,
    stream_to_table,
    validate_coordinates,
)
from _socrata_shared import SocrataDataset, iter_rows

DATASET = SocrataDataset("data.sf.gov", "tkzw-k3nq", timeout=180)

SELECT = "treeid,species,planteddate,mapdbh,latitude,longitude,planttype"


def is_a_tree(record: dict) -> bool:
    """False for anything the portal does not call a tree.

    Compared case-insensitively: the column is overwhelmingly `Tree` but
    carries 26 rows of lowercase `tree`, and the archived list carried 3.  An
    exact `== "Tree"` dropped them silently, which is the cheap end of the same
    failure a mis-read column gives -- rows gone with nothing reporting it.

    The replacement dataset no longer publishes the archived list's
    `Landscaping` rows at all, so this is a guard rather than a filter today.
    """
    return (record.get("planttype") or "").strip().lower() == "tree"


def parse_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_dbh(value) -> float | None:
    """`mapdbh` is text in the replacement schema; inches, as before.

    Every populated value measured as a plain integer when this was written.
    A zero or a negative is nulled centrally by `enforce_tree_schema`, so it
    is not second-guessed here.
    """
    return parse_float(value)


def parse_plant_date(value) -> date | None:
    """`planteddate` is ISO text (`2002-02-27`) in the replacement schema."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def split_species(value: str | None) -> tuple[str | None, str | None]:
    """"Pinus radiata :: Monterey Pine" -> ("Pinus radiata", "Monterey Pine").

    The scientific half is the species key -- see "Species Key Rule" in
    EXTENDING.md -- and the common half is kept as `tree_name`.  A row with no
    common half falls back to the scientific name, which is what the map shows
    when nothing better exists.
    """
    raw = value or ""
    scientific = normalize_species(raw.split("::")[0])
    common = raw.split("::", 1)[1].strip() if "::" in raw else None
    return scientific, (common or scientific)


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for rec in rows:
        raw_id = (rec.get("treeid") or "").strip()
        if not raw_id:
            continue
        scientific, common = split_species(rec.get("species"))
        # A row with no species at all says nothing about a plant; the previous
        # ingest dropped these and so does this one, by declining to append.
        if scientific is None:
            continue

        tree_id.append(f"sf-{raw_id}")
        species.append(scientific)
        tree_name.append(common)
        plant_date.append(parse_plant_date(rec.get("planteddate")))
        latitude.append(parse_float(rec.get("latitude")))
        longitude.append(parse_float(rec.get("longitude")))
        dbh.append(parse_dbh(rec.get("mapdbh")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["USSFO"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = stream_to_table(
        iter_rows(DATASET, select=SELECT),
        transform,
        keep=is_a_tree,
        label="San Francisco OpenData",
    )
    table = validate_coordinates(table, city="San Francisco", city_code="USSFO")
    table = enforce_tree_schema(
        table, city="San Francisco", data_source="SF_OPENDATA"
    )
    emit(table)
