#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///

"""
Fetch City of Athens trees (National Garden inventory) and emit Arrow IPC.

Source: the city's GeoNode/GeoServer WFS behind opendata.cityofathens.gr —
dataset "Δένδρα Εθνικού Κήπου" (Trees of the National Garden), ~6,350 points.
It is the only tree inventory any Greek open data portal publishes (checked
opendata.cityofathens.gr, opendata.thessaloniki.gr, data.gov.gr and
geodata.gov.gr in Aug 2026), so Athens's municipal partition covers the
National Garden rather than the whole street network.

The layer's native CRS is Greek Grid (EPSG:2100); GeoServer reprojects
server-side via srsName=EPSG:4326, so no local pyproj step.  The endpoint is
plain http — the host's TLS listener does not speak TLS (verified: handshake
fails with "wrong version number").

Field mapping:
  gid                    -> tree_id (prefixed "ath-"; unique across the layer)
  desc                   -> species + tree_name.  The value is
                            "Latin binomial (greek common name)", e.g.
                            "Citrus aurantium (νερατζιά)"; the parentheticals
                            are stripped for species and the last one kept as
                            tree_name.  One Greek-only value
                            ("Αυτοφυής φοίνικας", self-sown palm) is routed to
                            the Palm sentinel by sanitize_species's Greek
                            aliases in _ingest_shared.
  Condition              -> row filter: "Cut" rows are felled trees and are
                            excluded; anything else ("Existing", "New", or a
                            value the source adds later) is kept, so a new
                            live-tree category cannot silently vanish.
No DBH or plant date is published; both are emitted as typed nulls.
"""

import re
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import (
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    validate_coordinates,
)

WFS_URL = "http://gis.cityofathens.gr/geoserver/ows"
WFS_PARAMS = {
    "service": "WFS",
    "version": "2.0.0",
    "request": "GetFeature",
    "typename": "geonode:dendra",
    "outputFormat": "json",
    "srsName": "EPSG:4326",
}

_PAREN_RE = re.compile(r"\(([^)]*)\)")


def split_desc(desc: str | None) -> tuple[str | None, str | None]:
    """Split "Latin name (greek common name)" into (species, tree_name).

    Every parenthetical is removed from the species half — the layer has values
    like "Ligustrum japonicum (tree) (λιγούστρο)" — and the last non-empty one
    becomes tree_name.  A value with no parenthetical (e.g. the Greek-only
    "Αυτοφυής φοίνικας") is passed through whole as the species for
    sanitize_species to judge, with no tree_name.
    """
    if desc is None or not desc.strip():
        return None, None
    parentheticals = [p.strip() for p in _PAREN_RE.findall(desc) if p.strip()]
    species = _PAREN_RE.sub(" ", desc).strip()
    species = re.sub(r"\s+", " ", species) or None
    tree_name = parentheticals[-1] if parentheticals else None
    return species, tree_name


def fetch_features() -> list[dict]:
    data = get_json_with_retry(WFS_URL, params=WFS_PARAMS, timeout=300)
    features = data.get("features")
    if features is None:
        raise RuntimeError("WFS GetFeature response has no 'features' key")
    return features


def build_table(features: list[dict]) -> pa.Table:
    tree_ids: list[str | None] = []
    cities: list[str] = []
    species_list: list[str | None] = []
    tree_names: list[str | None] = []
    latitudes: list[float | None] = []
    longitudes: list[float | None] = []

    for feature in features:
        props = feature.get("properties", {})
        if props.get("Condition") == "Cut":
            continue

        gid = props.get("gid")
        tree_ids.append(f"ath-{gid}" if gid is not None else None)
        cities.append("GRATH")

        species, tree_name = split_desc(props.get("desc"))
        species_list.append(species)
        tree_names.append(tree_name)

        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates")
        if coords and coords[0] is not None and coords[1] is not None:
            longitudes.append(float(coords[0]))
            latitudes.append(float(coords[1]))
        else:
            longitudes.append(None)
            latitudes.append(None)

    return pa.table(
        {
            "tree_id": pa.array(tree_ids, type=pa.string()),
            "city": pa.array(cities, type=pa.string()),
            "species": pa.array(species_list, type=pa.string()),
            "tree_name": pa.array(tree_names, type=pa.string()),
            "latitude": pa.array(latitudes, type=pa.float64()),
            "longitude": pa.array(longitudes, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = build_table(fetch_features())
    table = validate_coordinates(table, city="Athens", city_code="GRATH")
    table = enforce_tree_schema(table, city="Athens", data_source="ATHENS_OPENDATA")
    emit(table)
