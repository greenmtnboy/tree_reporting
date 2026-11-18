#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Sydney's public trees: the City of Sydney's street and park tree register.

**Source**: "Trees" (ArcGIS Online item 15c4713a688a48fcb604fc343118af05,
owner `cityofsydneyspatial`, `Trees/FeatureServer/0`), published on the City's
open data hub (https://data.cityofsydney.nsw.gov.au/datasets/cityofsydney::trees)
as "the location of trees in the City of Sydney including tree type, species,
common name, height, canopy, status, age and DBH".  Licence: Creative Commons
Attribution 4.0 (https://creativecommons.org/licenses/by/4.0/), stated in the
item's `licenseInfo` and as the dataset's `license` in the hub's DCAT feed;
attribution "City of Sydney".  The City of Sydney LGA only: the central
business district and the inner suburbs from Pyrmont and Glebe to Rosebery and
Zetland.  Trees in other councils' areas and on State land (the Royal Botanic
Garden, the Domain, Centennial Park) are not in it.

49,595 rows on 2026-11-18: 35,428 street trees and 14,167 park trees
(`TreeType`).  `Tree_Status` is `Tree` on every row; any other value would be
a new kind of row (a stump, a vacant site) and is refused rather than guessed
at.  The hosted layer is overwritten wholesale each night: `OBJECTID` runs
1..N with no gaps and the layer's `dataLastEditDate` moves daily, so OBJECTID
is a row number, not an identity.

**Id: `asset_id`**, the City's asset number (`TS07572` street, `TP00841`
park, a few `TR...`), published as `syd-{asset_id}`.  There is no GlobalID.
It is not clean over the whole layer: null on 190 rows, malformed on two
(`\\`, ` NHY`) -- those are dropped with a count -- and repeated on four
values, each pair two different trees (different species or positions a few
metres apart).  The repeated values are read up front with one `groupBy ...
having` statistics query and those rows take `syd-{asset_id}-{S}-{E}`, the
position in 1e-5 degrees south and east (`syd-TS00963-3388822-15120721`), not
OBJECTID, because OBJECTID is renumbered on every overwrite.

**Species is `SpeciesName`**, a scientific name with the cultivar quoted
(`Robinia pseudoacacia 'Frisia'`); `CommonName` is not mapped.  The column is
30 characters wide and the register's habits are handled here: names cut off
or abbreviated to fit (`Waterhousea flori. 'Green Ave'`, `Gleditsia trican
Sunburst`, `Mag. grandiflora 'Little Gem'`, `Lophostemon confertus Variegat`)
are read through `SPECIES_OVERRIDES`; a cultivar written unquoted after the
binomial in capitals (`Platanus orientalis Digitata`, `Populus nigra Italica`,
`Populus x canadensis Aurea`) is split into the cultivar column; a `Z_` prefix
(the register's retired picklist entries, `Z_Eucalyptus camaldulensis`) is
removed; a non-breaking space stored double-encoded (`MetasequoiaÂ\\xa0...`)
is repaired.  `Unknown` (18) and `Not Required` (3) publish as Unknown; the
one `Z_Rose sp.` (a rose bush) as the Shrub sentinel.  A cross of two named
species (`Eucalyptus saligna x botryoides`) publishes as the genus, as in
Canberra.

**DBH is `DBH_in_cm`**, a diameter in centimetres despite nothing but the name
saying so: median 26, 95th percentile 84, and the 250-350 cm values are all
Moreton Bay and Port Jackson figs, whose buttressed trunks are that wide; a
circumference would put the median mature tree at 8 cm.  No zero or null
values; the one 1,004 cm fig is past the schema's 5 m guard and is nulled
there.  **`TreeCanopyNS` is the crown spread north-south in metres** (median
5, max 41) and is published as `crown_width_m`; the 41 m one is past the
schema's 40 m guard.  `TreeHeight` (metres) has no canonical column and is
not read; nor are `Tree_Age` (Young / Semi-Mature / Mature / Over-Mature) and
`TreeType`.  The layer records no planting date.
"""

import json
import math
import re
import sys
from collections import Counter
from functools import cache
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    SHRUB_SPECIES,
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features

LAYER = FeatureLayer(
    "https://services1.arcgis.com/cNVyNtjGVZybOQWZ/arcgis/rest/services/Trees/FeatureServer/0",
    timeout=180,
)
SOURCE_URL = LAYER.base
FIELDS = ("OBJECTID", "asset_id", "SpeciesName", "DBH_in_cm", "TreeCanopyNS", "Tree_Status")
ID_PREFIX = "syd-"

# The City's asset numbers: letters then digits (`TS07572`, `TP00841`,
# `JHTS09057`, one `TP12419LL`).  Anything else (`\`, ` NHY`) is a keying
# slip, not an id.
_ASSET_ID = re.compile(r"^[A-Z]+\d+[A-Z]*$")

# The only `Tree_Status` the layer publishes.
TREE_STATUSES = frozenset({"Tree"})

# `SpeciesName` values that are not a taxon as written, or were cut off at
# the column's 30 characters: (species, cultivar).
SPECIES_OVERRIDES: dict[str, tuple[str | None, str | None]] = {
    "Waterhousea flori. 'Green Ave'": ("Waterhousea floribunda", "Green Avenue"),
    "Mag. grandiflora 'Little Gem'": ("Magnolia grandiflora", "Little Gem"),
    "Gleditsia trican Shademaster": ("Gleditsia triacanthos", "Shademaster"),
    "Gleditsia trican Sunburst": ("Gleditsia triacanthos", "Sunburst"),
    "Koelreuteria elegans subsp For": ("Koelreuteria elegans", None),
    "Melaleuca bracteata 'Rev. Gold": ("Melaleuca bracteata", "Revolution Gold"),
    "Lophostemon confertus Variegat": ("Lophostemon confertus", "Variegata"),
    # `Durazz.` is the authority, not a cultivar.
    "Albizia julibrissin Durazz": ("Albizia julibrissin", None),
    # A cross of two named species is a hybrid of no one species.
    "Eucalyptus saligna x botryoid": ("Eucalyptus", None),
    # The hyphen went missing, and `rosa` alone is a different reading.
    "Hibiscus rosa sinensis": ("Hibiscus rosa-sinensis", None),
    # Eucalyptus linearis is the older name of E. pulchella.
    "Eucalyptus linearis pulchella": ("Eucalyptus pulchella", None),
    "Not Required": (UNKNOWN_SPECIES, None),
    "Rose sp.": (SHRUB_SPECIES, None),
}

# Refuse when more than this share of the layer has no usable asset id; on
# 2026-11-18 it was 0.4%.
MAX_UNIDENTIFIED_SHARE = 0.02


def clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).replace("Â\xa0", " ").replace("\xa0", " ")
    text = " ".join(text.split())
    return text or None


def parse_species(value) -> tuple[str | None, str | None]:
    """The species string and, when the register wrote one unquoted, the
    cultivar.  A quoted cultivar is left for `enforce_tree_schema`."""
    text = clean(value)
    if text is None:
        return None, None
    if text.startswith("Z_"):
        text = text[2:]
    if text in SPECIES_OVERRIDES:
        return SPECIES_OVERRIDES[text]
    if "'" in text:
        return text, None
    tokens = text.split(" ")
    # Genus, an optional hybrid mark, the epithet: a capitalised word after
    # that is a cultivar written without its quotes.
    head = 3 if len(tokens) > 2 and tokens[1].lower() == "x" else 2
    if len(tokens) > head and tokens[head][:1].isupper() and tokens[head].isalpha():
        return " ".join(tokens[:head]), " ".join(tokens[head:])
    return text, None


def positive(value) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number


@cache
def repeated_ids() -> frozenset[str]:
    """The `asset_id` values the layer gives to more than one row."""
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "asset_id IS NOT NULL",
            "groupByFieldsForStatistics": "asset_id",
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
        raise RuntimeError(f"Sydney asset_id duplicate query was incomplete: {payload!r}"[:500])
    return frozenset(
        clean(f["attributes"]["asset_id"]) for f in payload["features"]
        if clean(f["attributes"]["asset_id"])
    )


class Sydney:
    def __init__(self) -> None:
        self.repeated = repeated_ids()
        self.dropped: Counter[str] = Counter()
        self.seen = 0

    def keep(self, feature: dict) -> bool:
        self.seen += 1
        rec = feature.get("attributes") or {}
        missing = set(FIELDS) - set(rec)
        if missing:
            raise RuntimeError(f"Sydney ingest: the Trees layer is missing field(s) {sorted(missing)}")
        status = clean(rec["Tree_Status"])
        if status not in TREE_STATUSES:
            raise RuntimeError(
                f"Sydney ingest: unexpected Tree_Status {status!r} (OBJECTID {rec['OBJECTID']}); "
                "decide whether it is a tree before publishing it"
            )
        asset = clean(rec["asset_id"])
        if asset is None or not _ASSET_ID.match(asset):
            self.dropped["no usable asset_id"] += 1
            return False
        return True

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []
        crown: list[float | None] = []

        for feature in features:
            rec = feature["attributes"]
            asset = clean(rec["asset_id"])
            lat, lon = esri_point(feature.get("geometry"))
            if asset in self.repeated:
                if lat is None or lon is None:
                    raise RuntimeError(f"Sydney ingest: repeated asset_id {asset} has no position")
                tree_id.append(f"{ID_PREFIX}{asset}-{round(-lat * 1e5)}-{round(lon * 1e5)}")
            else:
                tree_id.append(f"{ID_PREFIX}{asset}")
            name, selection = parse_species(rec["SpeciesName"])
            species.append(name)
            cultivar.append(selection)
            latitude.append(lat)
            longitude.append(lon)
            diameter_cm = positive(rec["DBH_in_cm"])
            dbh.append(diameter_cm / 2.54 if diameter_cm is not None else None)
            crown.append(positive(rec["TreeCanopyNS"]))

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["AUSYD"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array([None] * n, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
                "crown_width_m": pa.array(crown, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Sydney: dropped {n:,} rows: {reason}", file=sys.stderr)
        unidentified = self.dropped["no usable asset_id"]
        if self.seen and unidentified / self.seen > MAX_UNIDENTIFIED_SHARE:
            raise RuntimeError(
                f"Sydney: {unidentified:,} of {self.seen:,} rows have no usable asset_id; "
                "the id column may have changed"
            )


def iter_pages():
    yield from iter_features(
        LAYER,
        out_fields=",".join(FIELDS),
        return_geometry=True,
        out_sr=4326,
        order_by="OBJECTID",
    )


def main() -> None:
    sydney = Sydney()
    table = stream_to_table(iter_pages(), sydney, keep=sydney.keep, label="Sydney OpenData")
    sydney.report()
    table = validate_coordinates(table, city="Sydney", city_code="AUSYD")
    table = enforce_tree_schema(table, city="Sydney", data_source="SYDNEY_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
