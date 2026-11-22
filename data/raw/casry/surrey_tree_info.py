#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Surrey's City tree inventory.

Source: "Park Specimen Trees" on the City of Surrey's ArcGIS Online
organisation (item 626b2212e0594eadb8a86ccf0b9640c2, owner SurreyGIS, listed
on the City's open data catalogue opendata-surrey.hub.arcgis.com, which
data.surrey.ca now redirects to): "street trees and park trees that are
maintained and/or operated by the Urban Forestry Section of the City of Surrey
Parks Division ... only a partial inventory".  Updated monthly.  It is the
City's one published tree inventory: boulevard, median, island, frontage and
walkway trees are in it beside the park trees (`TREE_TYPE`), and the catalogue
lists no separate street-tree dataset.  (`SurreyTrees` in the same organisation
is a 2016 copy last edited 2016-02-11, not used.)

**The layer holds exactly 62,000 rows (2026-11-22), a truncated export.**  The
City's own COSMOS map service (gisservices.surrey.ca, Public/Parks/MapServer
layer 51, "Park Specimen Trees (Parks)") serves 115,237 trees of the same
inventory; 61,948 of this layer's rows sit within 2 m of one of them, so this
layer is a subset of it, cut at a round number.  That map service carries no
asset id and is not a catalogue dataset, so it is not read here; if the City
lifts the cap, this layer simply grows.

Licence: the item's licence names the City's "Open Data License" page
(https://opendata-surrey.hub.arcgis.com/pages/55089a19491a4fe59a41e059fd8af708),
the Open Government License - Surrey (based on OGL - British Columbia 2.0): a
worldwide, royalty-free, perpetual, non-exclusive licence to "copy, modify,
publish, translate, adapt, distribute or otherwise use the Information ... for
any lawful purpose", with the attribution "Contains information licensed under
the Open Government License - City of Surrey."

**The id is `FACILITYID`**, the City's ten-digit asset id (`sry-{FACILITYID}`):
non-null on every row and unique but for three values carried by two rows each
(2026-11-22; two pairs are different trees a few metres apart, one is a tree
recorded twice).  There is no GlobalID, and `OBJECTID` is a dense 1..N, the
sign of a layer reloaded whole.  Rows on a repeated `FACILITYID` take the
repeated id plus their rounded position (`coordinate_tree_id`), read up front
with one `groupBy ... having` statistics query; a repeat that also shares the
position is one tree recorded twice and only its first row is kept.

**Status**: every row is `STATUS = Operating` but six with no status.  Any
other status (a removed or proposed tree, should the City start publishing
them) is dropped with a logged count.  `YEAR_REMOVED` is set on 101 operating
rows, mostly 0 or with comments saying the tree was not removed; the status is
what is read.

**Species** is `TREE_GENUS` + `TREE_SPECIES`, with `TREE_VARIETY` the cultivar
(`Prunus serrulata` + `Kwanzan`).  Corrections made here, on the source's own
values: a hybrid formula (`Acer truncatum x platanoides`, `Quercus alba x
robur`) keeps only the genus, since `sanitize_species` would otherwise keep
the first parent; the Rutgers dogwoods' trade codes in the epithet
(`x rutban`, `x rutdan`, `x rutgan`, `x rutlan`) keep the genus, the variety
field holding their cultivar; `balsamifera ssp trichocarpa` is Populus
trichocarpa; `xCupressocyparis` gets its hybrid mark spaced; `Fruit Tree` is
the Unknown sentinel; the variety `Null` is no variety.  Misspellings are left
to the shared maps.  A parse-failure guard refuses more than 1% of named rows
whose genus is not a single Latin word.  Common names are not published.

**DBH is `TREE_DIAMETER_CM`**, recorded on 954 rows only (2026-11-22: median
6 cm, maximum 30 -- the calipers of recent plantings), centimetres converted to
inches; zero is no measurement.  `HEIGHT_M` (764 rows) has no canonical column.
No planting date is published: `STATUS_DATE` is a record timestamp (it carries
times of day and does not match the in-service date the City's map service
shows), so it is not read.

Geometry is stored in NAD83 / UTM 10N and requested as WGS84 (`outSR=4326`).
"""

import json
import re
import sys
from collections import Counter
from functools import cache
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    UNKNOWN_SPECIES,
    coordinate_tree_id,
    drop_stacked_coordinates,
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services/"
    "Park%20Specimen%20Trees/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = (
    "OBJECTID,FACILITYID,STATUS,TREE_GENUS,TREE_SPECIES,TREE_VARIETY,TREE_DIAMETER_CM"
)
ID_PREFIX = "sry"

OPERATING = "operating"

# Genus values that are not a genus.
GENUS_OVERRIDES: dict[str, str] = {
    "fruit tree": UNKNOWN_SPECIES,
    "xcupressocyparis": "x Cupressocyparis",
}

# Epithet values that are not an epithet: the replacement, or "" for the genus
# alone (the variety field then carries the cultivar).
EPITHET_OVERRIDES: dict[str, str] = {
    # Cornus kousa x florida 'Rutban' (Aurora) and its siblings: the patent
    # codes of the Rutgers Stellar series, written as a hybrid epithet.
    "x rutban": "",
    "x rutdan": "",
    "x rutgan": "",
    "x rutlan": "",
    "balsamifera ssp trichocarpa": "trichocarpa",
}

# Variety values that are not a variety.
NO_VARIETY = frozenset({"null", "none", "unknown"})

GENUS_RE = re.compile(r"^(x )?[A-Z][a-z]+$")
EPITHET_RE = re.compile(r"^(x )?[a-z][a-z-]+( [a-z][a-z-]+)*$")

# Refuse to publish when more than this share of named rows parse to no taxon.
MAX_UNPARSED_SHARE = 0.01

CM_PER_INCH = 2.54


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("‘", "'").replace("’", "'").split())
    return text or None


def parse_species(genus_raw, epithet_raw) -> tuple[str | None, bool]:
    """(species for the schema, whether the value failed to parse)."""
    genus = clean(genus_raw)
    if genus is None:
        return None, False
    genus = GENUS_OVERRIDES.get(genus.lower(), genus)
    if genus == UNKNOWN_SPECIES:
        return genus, False
    if not GENUS_RE.match(genus):
        return None, True
    epithet = clean(epithet_raw)
    if epithet is None:
        return genus, False
    epithet = epithet.lower()
    epithet = EPITHET_OVERRIDES.get(epithet, epithet)
    if not epithet:
        return genus, False
    # A hybrid formula between two species: neither parent is the tree.
    if " x " in f" {epithet} " and not epithet.startswith("x "):
        return genus, False
    if not EPITHET_RE.match(epithet):
        return genus, True
    return f"{genus} {epithet}", False


def parse_variety(raw) -> str | None:
    text = clean(raw)
    if text is None or text.lower() in NO_VARIETY:
        return None
    # Quotes would end the cultivar early; the source writes "Grime s Golden".
    return text.replace("'", "").replace('"', "") or None


@cache
def repeated_ids() -> frozenset[str]:
    """The `FACILITYID` values the layer assigns to more than one row."""
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "FACILITYID IS NOT NULL",
            "groupByFieldsForStatistics": "FACILITYID",
            "outStatistics": json.dumps(
                [{"statisticType": "count", "onStatisticField": "OBJECTID",
                  "outStatisticFieldName": "n"}]
            ),
            "having": "COUNT(OBJECTID) > 1",
            "f": "json",
        },
        timeout=LAYER.timeout,
    )
    if "features" not in payload or payload.get("exceededTransferLimit"):
        raise RuntimeError(
            f"Surrey FACILITYID duplicate query was incomplete: {payload!r}"[:500]
        )
    return frozenset(str(f["attributes"]["FACILITYID"]).strip() for f in payload["features"])


class Surrey:
    def __init__(self) -> None:
        self.repeated = repeated_ids()
        self.dropped: Counter[str] = Counter()
        self.unparsed: Counter[str] = Counter()
        self.named = 0
        self.positioned_ids = 0

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        status = (clean(rec.get("STATUS")) or "").lower()
        if status not in ("", OPERATING):
            self.dropped[f"STATUS {status!r}"] += 1
            return False
        if clean(rec.get("FACILITYID")) is None:
            self.dropped["no FACILITYID"] += 1
            return False
        return True

    def transform(self, features: list[dict]) -> pa.Table:
        tree_id: list[str | None] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            fid = clean(rec["FACILITYID"])
            lat, lon = esri_point(feature.get("geometry"))
            if fid in self.repeated:
                self.positioned_ids += 1
                tree_id.append(
                    coordinate_tree_id(f"{ID_PREFIX}-{fid}", lon, lat)
                    if lat is not None and lon is not None
                    else None
                )
            else:
                tree_id.append(f"{ID_PREFIX}-{fid}")
            name, failed = parse_species(rec.get("TREE_GENUS"), rec.get("TREE_SPECIES"))
            if clean(rec.get("TREE_GENUS")) is not None:
                self.named += 1
            if failed:
                self.unparsed[
                    f"{clean(rec.get('TREE_GENUS'))} | {clean(rec.get('TREE_SPECIES'))}"
                ] += 1
            species.append(name)
            cultivar.append(parse_variety(rec.get("TREE_VARIETY")) if name else None)
            latitude.append(lat)
            longitude.append(lon)
            cm = rec.get("TREE_DIAMETER_CM")
            dbh.append(float(cm) / CM_PER_INCH if cm is not None and cm > 0 else None)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["CASRY"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array([None] * n, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def report(self) -> None:
        print(
            "Surrey: dropped "
            + (", ".join(f"{v} {k}" for k, v in sorted(self.dropped.items())) or "nothing")
            + f"; {self.positioned_ids} row(s) on a repeated FACILITYID keyed with "
            "their position",
            file=sys.stderr,
        )
        failed = sum(self.unparsed.values())
        if failed:
            print(
                f"Surrey: {failed} row(s) with no readable scientific name: "
                + ", ".join(f"{k!r} x{v}" for k, v in sorted(self.unparsed.items())),
                file=sys.stderr,
            )
        if self.named and failed / self.named > MAX_UNPARSED_SHARE:
            raise RuntimeError(
                f"Surrey: {failed} of {self.named} genus/species values unreadable "
                f"(> {MAX_UNPARSED_SHARE:.0%}); has the layer changed shape?"
            )


def drop_unpositioned_repeats(table: pa.Table) -> pa.Table:
    """Rows on a repeated FACILITYID with no position have no id at all."""
    import pyarrow.compute as pc

    missing = table["tree_id"].null_count
    if missing:
        print(
            f"Surrey: dropped {missing} row(s) on a repeated FACILITYID with no position",
            file=sys.stderr,
        )
        table = table.filter(pc.is_valid(table["tree_id"]))
    return table


if __name__ == "__main__":
    city = Surrey()
    pages = iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326
    )
    table = stream_to_table(pages, city.transform, keep=city.keep, label="Surrey OpenData")
    city.report()
    table = drop_unpositioned_repeats(table)
    # Only rows on a repeated FACILITYID can share an id; one recorded twice
    # at one point (same taxon) keeps its first row.
    table = drop_stacked_coordinates(
        table, label="Surrey", identical=("species", "cultivar")
    )
    table = validate_coordinates(table, city="Surrey", city_code="CASRY")
    table = enforce_tree_schema(
        table, city="Surrey", data_source="SURREY_OPENDATA"
    )
    emit(table)
