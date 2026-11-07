#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Prague's tree register, from the city's shared green-space data.

Source: `Sdílená data o zeleni - vegetační prvky - body` ("shared green-space
data, vegetation elements, points"), the register IPR Praha (the city's
planning institute) assembles from the inventories of the fifty-seven city
districts, the Technical Road Administration (TSK) and two city-hall
departments.  Published on Geoportál Praha under CC BY ("datový podklad ©
IPR Praha"); 223,349 points when this was written, of which **190,015 are
trees** (`KOD_VP = 71`, "Strom").  The rest are shrubs, container plantings
and climbers and are not read.

**Two services publish the layer and only their union is an inventory.**
The ArcGIS Hub copy (`mp.iprpraha.cz/.../OPK_CUR_OPK_SMZ_VP_B/FeatureServer`)
answers an ordinary paged query with geometry but carries no species, no
id and no planting year.  The city's own MapServer
(`gs-pub.praha.eu/.../opk/smz_vegetacni_prvky/MapServer/0`) carries all
three -- `TAXON_ROD`, `TAXON_DRUH`, `TAXON_KULTIVAR`, `ID_CENTRAL`,
`VYSADBA` -- but its `query` operation returns attributes only: every
variant of `returnGeometry` (f=json, geojson, pbf, native and reprojected
SR, a dynamic layer) comes back without a geometry, and the server has no
WFS.  The portal says feature services are not public for this data; the
MapServer's `identify` operation, which is part of its *map* capability, is
the one call that returns a feature with both attributes and coordinates.

So this ingest **walks the city with `identify`**: an envelope query that
returns every tree inside it with raw attributes (`returnFieldName`,
`returnUnformattedValues`) and a WGS84 point, capped at the layer's
`maxRecordCount` (1,000).  A tile that comes back full is split into four
and each quarter asked again, down to a tile too small to hold a thousand
trees, at which point a full answer is a server-side truncation we cannot
see past and the ingest refuses rather than publish a hole.  The register
is dense in the centre and sparse at the edges, so the walk ends in some
six to eight hundred requests of up to 700 KB each, two or three seconds
apiece, run four at a time; a point on a shared tile edge arrives from
both tiles and is kept once, by `OBJECTID`.  `layerDefs` restricts the walk to `KOD_VP = 71`.  `_identify`
and `iter_tree_tiles` are the whole of it, and the paged `query` path in
`shared.platforms.arcgis` is still what the freshness probe and every
statistic use.

**The id is `ID_CENTRAL`**, the register's own asset id, prefixed `prg-`.
It is a string in each supplier's format (`PRAHA4|BIO|57089`,
`3673/GEPRO/5/drev`, a signed hash on TSK's rows) and is set on 177,416 of
the 190,015 trees; the 12,599 without one are all from one city-hall
department's export and are dropped with a logged count, since
`enforce_tree_schema` refuses a null `tree_id`.  `OBJECTID` is not used:
the MapServer is republished from a file geodatabase and renumbers.

**Five districts' exports are stacked, not replaced.**  17,057 of the ids
appear three times -- Praha 9, 14 and 15, Zličín and Dolní Chabry -- with
the same id, taxon and position and a different `AKTUALIZACE` on each copy,
one per monthly load; 177,416 rows with an id are 142,285 trees.  The copy
with the latest stamp is the one the district currently publishes and is
the one kept (`newest_per_asset`).

**Species is in two languages.**  `TAXON_DRUH` is a Latin binomial on the
rows whose supplier filled `TAXON_ROD` (the genus) beside it, and a Czech
vernacular (`lípa srdčitá`, `javor mléč`, `trnovník akát`) on the rest,
where `TAXON_ROD` is null; 90,503 trees name nothing.  A row with a genus
is read as Latin through `normalize_species` and the shared hygiene; a row
without one goes through `shared.species.czech`, the curated
Czech-vernacular table, which also resolves the register's `- neurčená`
("undetermined") forms to the genus.  A Czech name the table does not
know publishes as `Unknown` and is counted; more than five per cent of
named trees unresolved means the register's vocabulary moved and the
ingest raises.  `TAXON_KULTIVAR` is the cultivar where set, otherwise the
one quoted inside the name (`sakura ozdobná 'Kanzan'`).

Fifty-two rows carry `pařez` ("stump") as their cultivar: they describe a
stump, not a tree, and are dropped the way the shared rule drops a stump
named in the species column.

There is no diameter anywhere in the register, so `diameter_at_breast_height`
is null; `VYSADBA` is the planting year on 2,975 trees and is published as
January 1 of that year.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    CITY_BOUNDS,
    emit,
    enforce_tree_schema,
    extract_cultivar,
    get_json_with_retry,
    normalize_species,
    normalize_tree_name,
    parse_plant_date_year,
    validate_coordinates,
)
from shared.platforms.arcgis import FeatureLayer, max_record_count  # noqa: E402
from shared.species.czech import UNRESOLVED, czech_name_key, species_from_czech_name  # noqa: E402

# Kept in step with `prague_update_time.py`.
LAYER = FeatureLayer(
    "https://gs-pub.praha.eu/arcgis/rest/services/opk/smz_vegetacni_prvky/MapServer/0",
    timeout=180,
)
IDENTIFY_URL = LAYER.base.rsplit("/", 1)[0] + "/identify"
TREE_DEFINITION = "KOD_VP = 71"
# Below ~50 m a side, a thousand trees in one tile is not a city block, it is
# the server truncating; splitting further would only ask the same question.
MIN_TILE_DEGREES = 0.0005
# Concurrent identify requests.  Four is well inside what the server gives one
# client and takes the walk from half an hour to under ten minutes.
IDENTIFY_WORKERS = 4
STUMP_MARKERS = frozenset({"pařez", "parez"})
CULTIVAR_PLACEHOLDERS = frozenset({"cv.", "cv"})
UNRESOLVED_SPECIES_LIMIT = 0.05

Box = tuple[float, float, float, float]  # lat_min, lat_max, lon_min, lon_max


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _identify(box: Box) -> list[dict]:
    """Every tree inside *box*, with raw attributes and a WGS84 point."""
    lat_min, lat_max, lon_min, lon_max = box
    extent = f"{lon_min},{lat_min},{lon_max},{lat_max}"
    payload = get_json_with_retry(
        IDENTIFY_URL,
        params={
            "geometry": json.dumps(
                {"xmin": lon_min, "ymin": lat_min, "xmax": lon_max, "ymax": lat_max,
                 "spatialReference": {"wkid": 4326}}
            ),
            "geometryType": "esriGeometryEnvelope",
            "sr": "4326",
            "layers": "all:0",
            "layerDefs": json.dumps({"0": TREE_DEFINITION}),
            "tolerance": "0",
            "mapExtent": extent,
            "imageDisplay": "1000,1000,96",
            "returnGeometry": "true",
            "returnFieldName": "true",
            "returnUnformattedValues": "true",
            "f": "json",
        },
        timeout=LAYER.timeout,
    )
    if "error" in payload:
        raise RuntimeError(f"identify failed on {IDENTIFY_URL}: {payload['error']}")
    return payload.get("results") or []


def _quarters(box: Box) -> list[Box]:
    lat_min, lat_max, lon_min, lon_max = box
    lat_mid = (lat_min + lat_max) / 2
    lon_mid = (lon_min + lon_max) / 2
    return [
        (lat_min, lat_mid, lon_min, lon_mid),
        (lat_min, lat_mid, lon_mid, lon_max),
        (lat_mid, lat_max, lon_min, lon_mid),
        (lat_mid, lat_max, lon_mid, lon_max),
    ]


def iter_tree_tiles(box: Box, cap: int, counts: dict[str, int]) -> Iterator[list[dict]]:
    """Walk *box* with identify, splitting any tile the cap truncated.

    Breadth-first over a small thread pool: a dense tile answers in two to
    three seconds, the walk is six to eight hundred of them, and the server
    serves them side by side as readily as in turn.  Tiles are yielded as
    they complete, in no particular order; a shared edge is reconciled by
    `OBJECTID` downstream.
    """
    with ThreadPoolExecutor(max_workers=IDENTIFY_WORKERS) as pool:
        pending = {pool.submit(_identify, box): box}
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                tile = pending.pop(future)
                results = future.result()
                counts["requests"] += 1
                if len(results) < cap:
                    if results:
                        yield results
                    continue
                lat_min, lat_max, lon_min, lon_max = tile
                if min(lat_max - lat_min, lon_max - lon_min) <= MIN_TILE_DEGREES:
                    raise RuntimeError(
                        f"Prague OpenData: identify returned {len(results)} trees for a "
                        f"tile {tile} already {MIN_TILE_DEGREES} degrees a side; the "
                        f"layer's cap ({cap}) is truncating something smaller than a "
                        f"city block"
                    )
                for quarter in _quarters(tile):
                    pending[pool.submit(_identify, quarter)] = quarter


def parse_species(genus, name) -> str | None:
    """The row's taxon, read as Latin when the register gave a genus and as Czech otherwise."""
    text = clean(name)
    if clean(genus) is not None:
        return normalize_species(text or clean(genus))
    return species_from_czech_name(text)


def parse_cultivar(cultivar, name) -> str | None:
    text = clean(cultivar)
    # `cv.` alone is the rank abbreviation with the selection left blank.
    if text is not None and text.casefold() in CULTIVAR_PLACEHOLDERS:
        text = None
    return text or extract_cultivar(clean(name))


def common_name(name, taxon) -> str | None:
    """The Czech vernacular as the tree card's name, for a name at species rank.

    A genus-level entry (`lípa - neurčená`) says less than the resolved
    `Tilia` already does, and its ` - ` would read as an inversion mark.
    """
    if taxon is None or len(taxon.split()) < 2:
        return None
    return normalize_tree_name(clean(name))


def is_stump(cultivar) -> bool:
    return (clean(cultivar) or "").casefold() in STUMP_MARKERS


def newest_per_asset(tiles: Iterator[list[dict]], counts: dict[str, int]) -> list[dict]:
    """One identify result per `ID_CENTRAL`, the most recently updated.

    Five districts' exports are stacked in the register rather than replaced:
    17,057 trees appear three times with the same id, taxon and position and
    only a different `AKTUALIZACE`, one per monthly load.  The latest stamp
    is the row the district currently publishes.
    """
    newest: dict[str, dict] = {}
    seen_objects: set[int] = set()
    for tile in tiles:
        for result in tile:
            attrs = result.get("attributes") or {}
            oid = attrs.get("OBJECTID")
            if oid in seen_objects:
                continue
            seen_objects.add(oid)
            asset = clean(attrs.get("ID_CENTRAL"))
            if asset is None:
                counts["no_id"] += 1
                continue
            stamp = attrs.get("AKTUALIZACE") or 0
            held = newest.get(asset)
            if held is not None:
                counts["repeated_id"] += 1
                if (held["attributes"].get("AKTUALIZACE") or 0) >= stamp:
                    continue
            newest[asset] = result
    return list(newest.values())


def transform(results: list[dict], counts: dict[str, int], unresolved: Counter) -> pa.Table:
    tree_id: list[str] = []
    species: list[str | None] = []
    tree_name: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []

    for result in results:
        attrs = result.get("attributes") or {}
        if is_stump(attrs.get("TAXON_KULTIVAR")):
            counts["stump"] += 1
            continue
        points = (result.get("geometry") or {}).get("points") or []
        if len(points) != 1 or len(points[0]) < 2:
            counts["no_point"] += 1
            continue
        genus, name = attrs.get("TAXON_ROD"), attrs.get("TAXON_DRUH")
        taxon = parse_species(genus, name)
        czech = clean(name) is not None and clean(genus) is None
        if czech:
            counts["named_czech"] += 1
            if taxon is None and czech_name_key(name) not in UNRESOLVED:
                unresolved[clean(name)] += 1
        tree_id.append(f"prg-{clean(attrs.get('ID_CENTRAL'))}")
        species.append(taxon)
        tree_name.append(common_name(name, taxon) if czech else None)
        cultivar.append(parse_cultivar(attrs.get("TAXON_KULTIVAR"), name))
        plant_date.append(parse_plant_date_year(attrs.get("VYSADBA")))
        longitude.append(float(points[0][0]))
        latitude.append(float(points[0][1]))

    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["CZPRG"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "tree_name": pa.array(tree_name, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array([None] * n, type=pa.float64()),
        }
    )


def main() -> None:
    counts: dict[str, int] = Counter()
    unresolved: Counter = Counter()
    tiles = iter_tree_tiles(CITY_BOUNDS["CZPRG"], max_record_count(LAYER), counts)
    results = newest_per_asset(tiles, counts)
    if not results:
        raise RuntimeError("Prague OpenData: the identify walk produced no trees")
    table = transform(results, counts, unresolved)
    print(
        f"Prague OpenData: {counts['requests']} identify tiles; dropped "
        f"{counts['no_id']} tree(s) with no ID_CENTRAL, {counts['repeated_id']} "
        f"earlier export(s) of one, {counts['stump']} stump(s) and {counts['no_point']} "
        f"without a point, leaving {table.num_rows}",
        file=sys.stderr,
    )
    missed = sum(unresolved.values())
    if unresolved:
        print(
            f"Prague OpenData: {missed} tree(s) under {len(unresolved)} Czech name(s) "
            f"shared.species.czech does not resolve: "
            + ", ".join(f"{name!r} ({n})" for name, n in unresolved.most_common(10)),
            file=sys.stderr,
        )
    if counts["named_czech"] and missed / counts["named_czech"] > UNRESOLVED_SPECIES_LIMIT:
        raise RuntimeError(
            f"Prague OpenData: {missed} of {counts['named_czech']} Czech-named trees "
            f"are unresolved, above {UNRESOLVED_SPECIES_LIMIT:.0%}; the register's "
            f"vocabulary has moved, extend shared/species/czech.py"
        )
    table = validate_coordinates(table, city="Prague", city_code="CZPRG")
    table = enforce_tree_schema(table, city="Prague", data_source="PRAGUE_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
