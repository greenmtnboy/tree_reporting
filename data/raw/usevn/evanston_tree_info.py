#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Evanston, Illinois's public trees, from the City's own ArcGIS Server.

Licence: CC BY 4.0.  The layer's ArcGIS item
(https://www.arcgis.com/home/item.html?id=845a3acb3dc0442a80b219691889f992,
owner `EvanstonGIS`) gives its licence as the "City of Evanston ODP Terms of
Use" (https://data.cityofevanston.org/pages/terms), which say: "Unless
otherwise specified in the content metadata, all data and content on this
portal are licensed under the Creative Commons Attribution 4.0 International
License (CC BY 4.0)", and "The Data can be freely distributed as long as
metadata entry is not modified or deleted."

**Source: `OpenData/ArcGISOpenData/MapServer/8` "Trees"** on
maps.cityofevanston.org, 35,752 rows on 2026-11-23: "over 32,000 trees
located on City property and in the parkways", first inventoried in 2014.
A MapServer layer with `maxRecordCount` 2,100 and standardised paging; it is
stored in Illinois State Plane East feet (wkid 3435) and the server
reprojects it to WGS84.

**Only `LifeCycle = Active Tree` is published** (31,988).  The layer also
holds 1,746 `Planting Site`s (most of them `SPP = Vacant Site ...`, the rest
the species planned for the site), 1,542 `Removed Tree`s and 444 `Stump`s.
32 rows have no `LifeCycle`; those that name a species are kept as trees.
An `SPP` of `Vacant Site Large/Medium/Small`, `stump` or `small stump` is
dropped whatever the `LifeCycle` says.  An unseen `LifeCycle` raises.

**The id is the GlobalID** (`evanston-{guid}`), non-null and unique over the
whole layer (a `groupBy GlobalID having count > 1` statistics query returns
nothing; `check_ids` re-asks it before every run).  OBJECTIDs run
102,932..163,225 for 35,752 rows and are not used.

**Species is `SPP`**, a coded-value domain whose codes are binomials
(`Acer platanoides`, `Gleditsia triacanthos inermis`, `Ulmus x` for the
hybrid elms, `Malus spp.`).  `Genus` is a separate, often-null column and is
not read; `Common` is the common name and is not mapped.  Two `SPP` values
are common names typed into the species field (`American elm`, `White ash`)
and are read as their binomials (`NAME_FIXES`).  `unknown tree` goes through
the shared sanitiser.

**`CULTIVAR`** (a coded-value domain, blank on almost every row) holds
cultivar names and, on a handful of rows, epithets or remarks
(`pekinensis`, `octandra`, `densata`, `ellipsoid`, `cottonless`,
`DO NOT PLANT`).  Only the values in `CULTIVARS`, each with the genus it
belongs to, become `cultivar`; a cultivar on another genus's tree (`Frontier`
on a Kentucky coffeetree, `Accolade` on a green ash) is dropped.  Two remarks
name the species where `SPP` has only the genus (`NOTE_SPECIES`: Crataegus
`puncata`, Juniperus `scopulorum`), and the Regal Prince oak recorded as
`Quercus x` is `Quercus x warei`.

**DBH is `DBH`, inches** (whole inches; median 13, 95th percentile ~33, max
76); 59 zeros are nulled by the schema.  The layer records no planting date
(`created_date` is the GIS record's), no height and no crown spread.
"""

import json
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features  # noqa: E402
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://maps.cityofevanston.org/arcgis/rest/services/OpenData/"
    "ArcGISOpenData/MapServer/8",
    timeout=180,
)
SOURCE_URL = LAYER.base
OUT_FIELDS = "OBJECTID,GlobalID,LifeCycle,SPP,CULTIVAR,DBH"

KEEP_LIFECYCLE = "Active Tree"
DROP_LIFECYCLE = frozenset({"Planting Site", "Removed Tree", "Stump"})
# `SPP` values that are not a tree, whatever `LifeCycle` says.
NOT_TREES = frozenset(
    {"vacant site large", "vacant site medium", "vacant site small", "stump", "small stump"}
)

# `SPP` values that are not a binomial: lowercased value -> species.
NAME_FIXES: dict[str, str] = {
    "american elm": "Ulmus americana",
    "white ash": "Fraxinus americana",
}

# `CULTIVAR` remarks that name the species where `SPP` has only the genus:
# (lowercased remark, genus) -> species.
NOTE_SPECIES: dict[tuple[str, str], str] = {
    ("puncata", "crataegus"): "Crataegus punctata",
    ("scopulorum", "juniperus"): "Juniperus scopulorum",
}

# `CULTIVAR` values that are cultivars: lowercased value -> (canonical, genus).
CULTIVARS: dict[str, tuple[str, str]] = {
    "accolade": ("Morton", "ulmus"),  # 'Accolade' is the trade name of Ulmus 'Morton'
    "alberta": ("Alberta", "picea"),
    "american sentry": ("American Sentry", "tilia"),
    "autumn brilliance": ("Autumn Brilliance", "amelanchier"),
    "autumn gold": ("Autumn Gold", "ginkgo"),
    "autumn splendor": ("Autumn Splendor", "aesculus"),
    "baumannii": ("Baumannii", "aesculus"),
    "bloodgood": ("Bloodgood", "platanus"),
    "chanticleer": ("Chanticleer", "pyrus"),
    "cherokee princess": ("Cherokee Princess", "cornus"),
    "coralburst": ("Coralburst", "malus"),
    "crimson king": ("Crimson King", "acer"),
    "danada charm": ("Danada Charm", "ulmus"),
    "dissectum red": ("Dissectum Red", "acer"),
    "espresso": ("Espresso", "gymnocladus"),
    "exclamation": ("Exclamation", "platanus"),
    "fall fiesta 'balista'": ("Fall Fiesta", "acer"),
    "frontier": ("Frontier", "ulmus"),
    "golden globe": ("Golden Globe", "ginkgo"),
    "golden raindrops": ("Golden Raindrops", "malus"),
    "green whisper": ("Green Whisper", "taxodium"),
    "happidaze": ("Happidaze", "liquidambar"),
    "jane": ("Jane", "magnolia"),
    "jefferson": ("Jefferson", "ulmus"),
    "merrill": ("Merrill", "magnolia"),
    "morton glossy": ("Morton Glossy", "ulmus"),
    "pendula": ("Pendula", "cercidiphyllum"),
    "perkins pink": ("Perkins Pink", "cladrastis"),
    "prairiefire": ("Prairifire", "malus"),
    "princeton": ("Princeton", "ulmus"),
    "princeton sentry": ("Princeton Sentry", "ginkgo"),
    "prospector": ("Prospector", "ulmus"),
    "purpurea tricolor": ("Purpurea Tricolor", "fagus"),
    "red jewel": ("Red Jewel", "malus"),
    "redmond": ("Redmond", "tilia"),
    "redpointe": ("Redpointe", "acer"),
    "redpointe 'frank jr'": ("Redpointe", "acer"),
    "regal prince": ("Regal Prince", "quercus"),
    "rohanii": ("Rohanii", "fagus"),
    "schwedler": ("Schwedleri", "acer"),
    "snowdrift": ("Snowdrift", "malus"),
    "triumph": ("Triumph", "ulmus"),
    "vanderwolf": ("Vanderwolf's Pyramid", "pinus"),
    "white princess": ("White Princess", "prunus"),
}


def check_ids() -> None:
    """Refuse a layer whose GlobalID is no longer a key."""
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "1=1",
            "groupByFieldsForStatistics": "GlobalID",
            "outStatistics": json.dumps(
                [{"statisticType": "count", "onStatisticField": "OBJECTID",
                  "outStatisticFieldName": "n"}]
            ),
            "having": "COUNT(OBJECTID) > 1",
            "f": "json",
        },
        timeout=LAYER.timeout,
    )
    if "features" not in payload:
        raise RuntimeError(f"Evanston GlobalID duplicate query failed: {payload!r}"[:500])
    if payload["features"]:
        repeated = [f["attributes"].get("GlobalID") for f in payload["features"][:10]]
        raise RuntimeError(
            f"Evanston: GlobalID repeats on {len(payload['features'])} values "
            f"(e.g. {repeated}); it is no longer the layer's key"
        )


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def genus_only(species: str | None) -> bool:
    if species is None:
        return True
    tokens = species.split()
    return len(tokens) == 1 or tokens[1].lower() in {"spp", "spp.", "sp", "sp.", "x"}


class Evanston:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.notes: Counter[str] = Counter()

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        stage = clean(rec.get("LifeCycle"))
        spp = (clean(rec.get("SPP")) or "").lower()
        if stage in DROP_LIFECYCLE:
            self.dropped[f"LifeCycle {stage}"] += 1
            return False
        if stage is not None and stage != KEEP_LIFECYCLE:
            raise RuntimeError(
                f"Evanston: unseen LifeCycle {stage!r}; read what it means before "
                "deciding whether it is a tree"
            )
        if spp in NOT_TREES:
            self.dropped[f"SPP {spp!r} on a {stage or 'no-LifeCycle'} row"] += 1
            return False
        if stage is None and not spp:
            self.dropped["no LifeCycle and no SPP"] += 1
            return False
        if not str(rec.get("GlobalID") or "").strip("{} "):
            self.dropped["no GlobalID"] += 1
            return False
        return True

    def species_and_cultivar(self, rec: dict) -> tuple[str | None, str | None]:
        species = clean(rec.get("SPP"))
        if species is not None and species.lower() in NAME_FIXES:
            self.notes["common name in SPP read as its binomial"] += 1
            species = NAME_FIXES[species.lower()]
        remark = clean(rec.get("CULTIVAR"))
        if remark is None:
            return species, None
        key = remark.lower()
        genus = species.split()[0].lower() if species else None
        named = NOTE_SPECIES.get((key, genus or ""))
        if named is not None and genus_only(species):
            self.notes["species from CULTIVAR remark"] += 1
            return named, None
        hit = CULTIVARS.get(key)
        if hit is None:
            self.notes[f"CULTIVAR {remark!r} not a cultivar, dropped"] += 1
            return species, None
        cultivar, cultivar_genus = hit
        if genus != cultivar_genus:
            self.notes[f"CULTIVAR {remark!r} on a {species} (another genus), dropped"] += 1
            return species, None
        if species == "Quercus x" and cultivar == "Regal Prince":
            species = "Quercus x warei"
        self.notes["cultivar"] += 1
        return species, cultivar

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            guid = str(rec["GlobalID"]).strip().strip("{}").lower()
            tree_id.append(f"evanston-{guid}")
            sp, cv = self.species_and_cultivar(rec)
            species.append(sp)
            cultivar.append(cv)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            raw_dbh = rec.get("DBH")
            dbh.append(float(raw_dbh) if raw_dbh is not None else None)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USEVN"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.nulls(n, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Evanston: dropped {n:,} rows: {reason}", file=sys.stderr)
        for kind, n in sorted(self.notes.items()):
            print(f"Evanston: {n:,} {kind}", file=sys.stderr)


def iter_row_chunks():
    """One page (the layer's 2,100-row cap) at a time, reprojected to WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


if __name__ == "__main__":
    check_ids()
    evanston = Evanston()
    table = stream_to_table(
        iter_row_chunks(), evanston, keep=evanston.keep, label="Evanston OpenData"
    )
    evanston.report()
    table = validate_coordinates(table, city="Evanston", city_code="USEVN")
    table = enforce_tree_schema(table, city="Evanston", data_source="EVANSTON_OPENDATA")
    emit(table)
