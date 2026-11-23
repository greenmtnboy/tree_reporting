#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Missoula, MT's public trees, from the City's own ArcGIS Online organisation.

Licence: none stated.  The ArcGIS item (478d76729bfc4236ae12bafb644fb635,
"TreeLocations_mso", snippet "City of Missoula authoritative data service
displaying the locations of publicy-owned trees") has an empty `licenseInfo`
and `accessInformation`, and the Missoula Maps hub that lists it
(https://missoulamaps-cityofmissoula.hub.arcgis.com/pages/terms) carries only
a reference-use disclaimer, which is not a licence.  The source is displayed
with attribution only and is not licensed for reuse.

**Source: `TreeLocations_mso/FeatureServer/0`**, 30,478 rows on 2026-11-22.
The owner, `svcgisdatainterop`, is the City's Data Interoperability service
account: the layer is a scheduled copy of the forestry inventory's own WFS
(every row's `gml_id` is `MissoulaMT_WFS.fid-...`, and the `*_xsi_nil`
columns are the GML reader's), truncated and reloaded whole -- `OBJECTID` is
dense 1..30,478 and the schema was re-created minutes before the last data
edit.  It is the only tree inventory the City publishes (the org's other tree
items are storm-damage and hazard-removal surveys).

**The id is `id`** (`mso-{id}`), the inventory system's own site number: an
integer on every row and unique over the layer (a `groupBy id having count >
1` statistics query returns nothing; the ingest re-checks it), sparse (2 to
34,052 for 30,478 rows: sites deleted over the years leave their numbers
unused) and kept by a site across re-inspections -- sites first inventoried in
2001 and revisited in 2026 still carry four-digit numbers -- so it survives
the reload that renumbers `OBJECTID`.  `remote_id` is unique too but is a
mix of legacy numbers and collector-timestamp strings (`CG20230928130927`);
`gml_id` is the WFS server's per-response feature id.

**Rows are sites; `site_type` says what is on one.**  `Tree` (28,454) is
kept.  `Stump` (458), `Planting` (683: a planting site; 242 of them say
`Vacant Site Small/Medium/Large`, 369 `Unknown` with DBH 0, and the rest a
species chosen for it) and `Removal` (883: 852 `Unknown` with DBH 0, nearly
all last inventoried in 2012-2014 -- the record of a removed tree) are
dropped with a count, and more than `MAX_SITE_SHARE` of the layer dropped
raises, because it would mean `site_type` changed meaning.

**Species is `species_botanical`**, title-cased (`Acer Platanoides`) with the
cultivar appended unquoted and often abbreviated to fit a column
(`Fraxinus Americana Autmn Prp`, `Malus Species Spring Snow`).  `parse_name`
splits genus, epithet and cultivar; `CULTIVAR_EXPANSIONS` spells the
abbreviated cultivars out, and `NAME_FIXES` reads the values that lost their
genus (`Deltoides Siouxland`, `Maacki`, `Saccharum Fall Fiesta`) or name an
elm cultivar by its trade name (`Frontier Elm`, `Accolade Elm`), each checked
against `species_common` beside it.  The common name is not mapped.

**DBH is `dbh`, whole inches** (trees: median 8, 75th percentile 17, 95th
28, max 130); 118 trees carry 0, which the schema nulls.  There is no planting
date: `inventory_date` (`2013-08-27Z`) is the last inventory visit.  No
height or crown spread is recorded.

Coordinates are the layer geometry (stored in Montana State Plane feet, wkid
2256) reprojected to WGS84 by the server; the `latitude`/`longitude`
attributes agree with it to under a metre.
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
    extract_cultivar,
    get_json_with_retry,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services.arcgis.com/HfwHS0BxZBQ1E5DY/arcgis/rest/services/"
    "TreeLocations_mso/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,id,site_type,species_botanical,dbh"

KEEP_SITE_TYPE = "Tree"
# The other site types the layer uses (module docstring); a value outside
# this set and `Tree` raises, since it would be a meaning nobody has read.
DROP_SITE_TYPES = frozenset({"Stump", "Planting", "Removal"})

# Refuse when more than this share of the layer is dropped as a non-tree
# site: the 2026 layer is 6.6% sites.
MAX_SITE_SHARE = 0.25

# Second tokens that mean "the genus, species not recorded".
GENUS_ONLY = frozenset({"species", "spp", "spp.", "sp", "sp."})

# Whole `species_botanical` values (lower-cased) the general parse would read
# wrong: (species, cultivar).  The common name each carries is in the comment.
NAME_FIXES: dict[str, tuple[str, str | None]] = {
    "deltoides siouxland": ("Populus deltoides", "Siouxland"),  # Siouxland Poplar
    "saccharum": ("Acer saccharum", None),  # Maple, Sugar
    "saccharum fall fiesta": ("Acer saccharum", "Fall Fiesta"),  # Mpl, Sugar "Fall Fiesta"
    "autumn fest sugar": ("Acer saccharum", "Autumn Fest"),  # Maple, Autumn Festival
    "maacki": ("Prunus maackii", None),  # Chokecherry, Amur
    "frontier elm": ("Ulmus", "Frontier"),  # Elm, Frontier
    "accolade elm": ("Ulmus", "Accolade"),  # Accolade Elm
    "morton new horizon": ("Ulmus", "New Horizon"),  # New Horizon Elm
    "sapporo": ("Ulmus", "Sapporo Autumn Gold"),  # Elm Autumn Gold
    "princeton elm": ("Ulmus americana", "Princeton"),  # Elm, Princeton
    "jefferson elm": ("Ulmus americana", "Jefferson"),  # Elm, Jefferson
    "malus gladiator": ("Malus", "Gladiator"),  # Crabapple Gladiator
    # 'Dwarf Alberta' is the common name of the cultivar 'Conica'.
    "picea glauca dwarf alberta": ("Picea glauca", "Conica"),
    # The oak-leaved mountain ash is a hybrid selection (sold as Sorbus x
    # hybrida or x thuringiaca 'Quercifolia'); "Sorbus quercifolia" is no
    # species, so the genus is published with the selection.
    "sorbus quercifolia": ("Sorbus", "Quercifolia"),  # Mountain Ash, Oak Leaf
    "cerocarpus spp": ("Cercocarpus", None),  # Mtn Mahogany Spec
    "quercus ellipsoides": ("Quercus ellipsoidalis", None),  # Oak, Northern Pin
}

# Cultivars the source cut short to fit its column, as the general parse
# leaves them (title case), spelled out from the common name beside them.
CULTIVAR_EXPANSIONS: dict[str, str] = {
    "Patmr": "Patmore",
    "Crimson Kng": "Crimson King",
    "Autmn Prp": "Autumn Purple",
    "Autmn Blz": "Autumn Blaze",
    "Autumn Blz": "Autumn Blaze",
    "Green Mountn": "Green Mountain",
    "Emrld Queen": "Emerald Queen",
    "Ivry Silk": "Ivory Silk",
    "Thundercld": "Thundercloud",
    "Shadem": "Shademaster",
    "Skylin": "Skyline",
    "Sunbur": "Sunburst",
    "Chanticlear": "Chanticleer",
    "Bloodgd": "Bloodgood",
    "Marsh": "Marshall's Seedless",
    "Shubert": "Schubert",
}


def parse_name(raw: str) -> tuple[str | None, str | None]:
    """`species_botanical` as (species, cultivar).

    >>> parse_name("Fraxinus Americana Autmn Prp")
    ('Fraxinus americana', 'Autumn Purple')
    >>> parse_name("Malus Species Spring Snow")
    ('Malus', 'Spring Snow')
    >>> parse_name("Tilia Americana 'McKsentry'")
    ('Tilia americana', 'McKsentry')
    """
    text = " ".join(raw.split())
    if not text:
        return None, None
    fix = NAME_FIXES.get(text.lower())
    if fix is not None:
        return fix
    quoted = extract_cultivar(text)
    if quoted is not None:
        cut = min(text.find(q) for q in "'\"" if q in text)
        text = text[:cut].strip()
    tokens = text.split(" ")
    genus = tokens[0][:1].upper() + tokens[0][1:].lower()
    rest = tokens[1:]
    if rest and rest[0].lower() in GENUS_ONLY:
        species, rest = genus, rest[1:]
    elif len(rest) >= 2 and rest[0].lower() in ("x", "×"):
        species, rest = f"{genus} x {rest[1].lower()}", rest[2:]
    elif rest:
        species, rest = f"{genus} {rest[0].lower()}", rest[1:]
    else:
        species = genus
    cultivar = quoted
    if cultivar is None and rest:
        words = " ".join(w[:1].upper() + w[1:] for w in rest)
        cultivar = CULTIVAR_EXPANSIONS.get(words, words)
    return species, cultivar


def check_ids() -> None:
    """Refuse a layer whose `id` is no longer a key."""
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "1=1",
            "groupByFieldsForStatistics": "id",
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
        raise RuntimeError(f"Missoula id duplicate query failed: {payload!r}"[:500])
    if payload["features"]:
        repeated = [f["attributes"].get("id") for f in payload["features"][:10]]
        raise RuntimeError(
            f"Missoula: id repeats on {len(payload['features'])} values "
            f"(e.g. {repeated}); it is no longer the layer's key"
        )


class Missoula:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.seen = 0

    def keep(self, feature: dict) -> bool:
        self.seen += 1
        rec = feature.get("attributes") or {}
        site_type = (rec.get("site_type") or "").strip()
        if site_type != KEEP_SITE_TYPE:
            if site_type not in DROP_SITE_TYPES:
                raise RuntimeError(
                    f"Missoula: unknown site_type {site_type!r}; read what it "
                    "means before deciding whether it is a tree"
                )
            self.dropped[f"site_type {site_type}"] += 1
            return False
        if rec.get("id") is None:
            self.dropped["no id"] += 1
            return False
        return True

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            tree_id.append(f"mso-{int(rec['id'])}")
            sp, cv = parse_name(str(rec.get("species_botanical") or ""))
            species.append(sp)
            cultivar.append(cv)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            raw_dbh = rec.get("dbh")
            dbh.append(float(raw_dbh) if raw_dbh is not None else None)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USMSO"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array([None] * n, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Missoula: dropped {n:,} rows: {reason}", file=sys.stderr)
        sites = sum(n for r, n in self.dropped.items() if r.startswith("site_type"))
        if self.seen and sites / self.seen > MAX_SITE_SHARE:
            raise RuntimeError(
                f"Missoula: {sites:,} of {self.seen:,} rows are not trees; "
                "site_type has changed meaning"
            )


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


if __name__ == "__main__":
    check_ids()
    missoula = Missoula()
    table = stream_to_table(
        iter_row_chunks(), missoula, keep=missoula.keep, label="Missoula OpenData"
    )
    missoula.report()
    table = validate_coordinates(table, city="Missoula", city_code="USMSO")
    table = enforce_tree_schema(table, city="Missoula", data_source="MISSOULA_OPENDATA")
    emit(table)
