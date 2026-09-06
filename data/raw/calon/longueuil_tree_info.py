#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Longueuil's tree inventory, from Donnees Quebec.

Source: "Arbres" on `www.donneesquebec.ca/recherche`, the GeoJSON resource
`23cde69a`, 99,345 features under CC-BY.  Longueuil has **no CKAN datastore**,
so this is the one city here read through `_ckan_shared.read_geojson_features`
rather than the paged datastore search.

**The source publishes no tree id, and this file synthesises one.  That is
against the standing rule in EXTENDING.md and it is a deliberate exception, so
the reasoning is here in full.**

What the source actually contains is two properties -- `Espece` and
`Diametre_Tronc` -- and a point.  There is no id anywhere: no property, no
GeoJSON feature-level `id` member, nothing in the shapefile (an older
63,773-record extract of the same two fields), and the KMZ's `kml_1`, `kml_2`
are sequence numbers its exporter assigns, which is the `OBJECTID` trap rather
than an id.  Longueuil runs no ArcGIS or WFS service, and publishes exactly one
tree dataset.  So the choice is a synthesised id or no city at all, and the
call taken here is that 97,475 mapped trees beat zero.

**The id is the rounded coordinate, and nothing else.**  `tree_id` is the
declared grain and the key a community check-in is recorded against, so what
matters most is that it survives a republish.  Position is the most stable
thing this source has: a re-survey updates `Diametre_Tronc` -- which is exactly
what a tree inventory exists to do -- so folding the attributes into the key
would churn the id of every re-measured tree.  Keying on position alone churns
only when a coordinate is corrected.

**Rounded to 7 decimal places (~1 cm), because the portal already publishes two
precisions.**  The same tree is `-73.50224994604028` in the GeoJSON and
`-73.5022499460403` in the KMZ; an unrounded key would have changed the day
somebody regenerated the export with a different writer.  7 dp is far below any
real positional correction and collides only where the source has genuinely
stacked trees.

**Stacked coordinates are dropped, not resolved.**  662 coordinates carry more
than one tree and one carries 58 -- trees whose individual positions were never
surveyed, mapped to a block or park centroid.  Publishing one of the 58 would
assert a species for a point where the source records several; publishing all
58 needs an id per tree, which is the thing that does not exist.  So every row
at a shared coordinate is dropped: 1,870 rows, 1.9% of the file, leaving
97,475.  `enforce_tree_schema` would refuse them anyway -- a duplicate
`tree_id` fans out every join built on the grain.

If Longueuil ever publishes an id, switch to it and accept the one-time churn.
"""

import collections
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, read_geojson_features
from _ingest_shared import (
    cm_to_inches,
    emit,
    enforce_tree_schema,
    normalize_species,
    validate_coordinates,
)

RESOURCE = CkanResource(
    "www.donneesquebec.ca/recherche",
    "23cde69a-a1d7-4775-8271-e3b46b3a6d83",
    timeout=300,
)

# ~1 cm.  See the docstring: the portal publishes this dataset at two float
# precisions already, so the key has to be insensitive to the difference.
COORD_DP = 7

# `Espece` is "Latin - French common name": "Tilia sp. - Tilleul sp.".
SPECIES_SEPARATOR = " - "

# 74,906 of 99,345 rows carry a diameter; the column tops out well inside these.
MIN_DBH_CM = 1.0
MAX_DBH_CM = 300.0


def coord_key(feature: dict) -> tuple[float, float] | None:
    """The rounded `(lon, lat)` this tree's id is built from, or None."""
    geometry = feature.get("geometry") or {}
    coords = geometry.get("coordinates")
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None
    try:
        return round(float(coords[0]), COORD_DP), round(float(coords[1]), COORD_DP)
    except (TypeError, ValueError):
        return None


def parse_species(value) -> str | None:
    """The Latin half of "Acer rubrum - Erable rouge".

    The trailing strip matters for one value: `Autre espece -` is the portal's
    "other species" placeholder with an empty French half, and the dangling
    separator keeps it from matching `_SPECIES_PLACEHOLDERS` -- it would
    otherwise survive `sanitize_species` as a plausible two-word binomial and
    be handed to the enrichment model, on 685 rows.
    """
    if not value:
        return None
    latin = str(value).split(SPECIES_SEPARATOR)[0].strip().strip("-").strip()
    return normalize_species(latin) or None


def parse_dbh(value) -> float | None:
    """Centimetres to inches, with the implausible ends dropped to null."""
    try:
        cm = float(value)
    except (TypeError, ValueError):
        return None
    if cm < MIN_DBH_CM or cm > MAX_DBH_CM:
        return None
    return cm_to_inches(cm)


def usable_features(features: list[dict]) -> list[dict]:
    """Features at a coordinate no other feature shares.  See the docstring."""
    counts = collections.Counter()
    for feature in features:
        key = coord_key(feature)
        if key is not None:
            counts[key] += 1

    kept = [
        feature
        for feature in features
        if (key := coord_key(feature)) is not None and counts[key] == 1
    ]
    stacked_points = sum(1 for n in counts.values() if n > 1)
    stacked_rows = sum(n for n in counts.values() if n > 1)
    no_point = len(features) - sum(counts.values())
    print(
        f"Longueuil OpenData: {len(features)} feature(s); dropped {stacked_rows} "
        f"at {stacked_points} shared coordinate(s) and {no_point} without one, "
        f"leaving {len(kept)}",
        file=sys.stderr,
    )
    return kept


def transform(rows: list[dict]) -> pa.Table:
    tree_id: list[str | None] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []

    for feature in rows:
        lon, lat = coord_key(feature)
        props = feature.get("properties") or {}

        # The coordinate *is* the id -- rendered rather than hashed, so a row
        # in the parquet can be traced back to the source feature by eye.
        # `lgl-`, not `lon-`: London already publishes `lon-{uniqueid}` and
        # tree_id is a single global namespace the rollup unions.
        tree_id.append(f"lgl-{lon:.7f}_{lat:.7f}")
        species.append(parse_species(props.get("Espece")))
        # The portal publishes the French common name only, joined into
        # `Espece`.  tree_name is the map's card title and sits behind the
        # enrichment table's English name, so a French one would surface only
        # on an unenriched taxon -- see caque/quebec_tree_info.py.
        tree_name.append(None)
        plant_date.append(None)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(parse_dbh(props.get("Diametre_Tronc")))

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CALON"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = transform(usable_features(read_geojson_features(RESOURCE)))
    table = validate_coordinates(table, city="Longueuil", city_code="CALON")
    table = enforce_tree_schema(
        table, city="Longueuil", data_source="LONGUEUIL_OPENDATA"
    )
    emit(table)
