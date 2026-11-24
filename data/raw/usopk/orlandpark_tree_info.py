#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Orland Park, Illinois's public trees, from the Village's ArcGIS Online organisation.

Licence: no licence stated; displayed with attribution only, not licensed for
reuse.  The ArcGIS item (https://www.arcgis.com/home/item.html?id=780d9c0a3883498b82e046ef231144c9,
owner `crannells_vop`, Village of Orland Park) has empty `licenseInfo` and
`accessInformation`, the layer no copyright text, and the tree layer is not in
either of the Village's GIS Data Hub sites, so no terms are published for it.

**Source: `Public_Trees_6-28-26/FeatureServer/1`**, a read-only *view*
("isView") over the Village's working layer `OrlandTrees_12_23_25`, 36,717
rows on 2026-11-23, every one with a point.  Despite the dated service name
the view is live: its `dataLastEditDate` was 2026-11-19 and rows carry
`InventoryDate`s into October 2026.  The dates in the names are when the
working layer (2025-12-23) and the public view (2026-06-28) were made; a
future republish under a new name is caught by the freshness probe, which
raises when the Village publishes another "Public Trees" service.  It is the
Village's only published tree layer.

**Rows kept: `TREE_STATUS = ACTIVE TREE`** (36,179).  `STUMP` (516),
`PLANTING SPACE` (21) and `OTHER` (1, a memorial plaque with no species) are
not trees, nor is `REMOVED TREE` (in the domain, unused today); `UNKNOWN SPP`
and `REVIEW` (in the domain, unused) are trees whose record is uncertain and
are kept.  Any other status raises.  Seven ACTIVE rows whose `LATIN_NAME` is
the `STUMP` code are dropped as well.

**The id is `GlobalID`** (`opk-{GlobalID}`): non-null on every row and unique
over the layer (`check_ids` re-asks the server before every run).  It
survived the 2025 rebuild: 32,717 rows carry it again in `GUID`, a copy taken
of the GlobalID in an earlier generation of the layer.  Neither `TREE_ID`
(the Village's tree number, 93 values repeated on 354 rows, one on 61) nor
`GUID` (null on 3,776 rows, and copied with features: 60 values repeated on
282 rows up to 300 m apart, so separate trees entered by copy-paste) is a
key.

**Species is `LATIN_NAME`**, a coded-value domain whose codes are already
binomials (`Acer rubrum`, `Acer miyabei 'Morton'`, `Ulmus x spp`); `UNKNOWN`
and `OTHER` are no species.  `COMMON_NAME` is not read.

**`VARIETY` is free text** (533 rows): cultivars (`Royal` on Norway maple,
`Regal Prince`, `Ivory Silk`, `Frontier`) mixed with common-name words
(`Japanese`, `black`, `lacebark`) and forms (`columnar`, `weeping`).  Only
the remarks in `VARIETY_NOTES`, read one by one off the 2026 layer, are used;
a note that names a species becomes the tree's species where `LATIN_NAME`
has only the genus, and is dropped where it disagrees with it.

**DBH is `DBH`, whole inches** (median 8, 95th percentile 23, max 84); one
`-1` is nulled.  `InventoryDate` is when the crew recorded the tree (2024 for
nine rows in ten), not a planting date, so `plant_date` is null.  No height
or crown spread is recorded.  The layer is stored in Illinois State Plane
East feet (wkid 3435); the server reprojects it to WGS84.
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
    "https://services3.arcgis.com/sVDuYsm37AMvFL49/arcgis/rest/services/"
    "Public_Trees_6-28-26/FeatureServer/1",
    timeout=180,
)
OUT_FIELDS = "OBJECTID,GlobalID,TREE_STATUS,LATIN_NAME,VARIETY,DBH"

KEEP_STATUS = frozenset({"ACTIVE TREE", "UNKNOWN SPP", "REVIEW"})
DROP_STATUS = frozenset({"STUMP", "PLANTING SPACE", "OTHER", "REMOVED TREE"})

# `LATIN_NAME` codes that are not a species.
NO_SPECIES = frozenset({"unknown", "other"})
NOT_A_TREE_SPECIES = frozenset({"stump", "planting space"})

# `VARIETY` remarks, normalised (`note_key`) -> (implied species or None,
# cultivar or None).  With no implied species the cultivar applies whatever
# `LATIN_NAME` says.  With one, it applies where `LATIN_NAME` is that species
# or only its genus (which it then replaces), and is dropped where it
# disagrees.  Trade names are folded to the cultivar name where Salem's
# ingest folds them (`Accolade` -> `Morton`, `Regal Prince` -> `Long`), and
# `State Street` is Acer miyabei 'Morton'.
_LONG = (None, "Long")
VARIETY_NOTES: dict[str, tuple[str | None, str | None]] = {
    # 152 Norway maples recorded `Royal`: 'Royal Red', the purple-leaved
    # Norway maple the trade sells under that name.
    "royal": ("Acer platanoides", "Royal Red"),
    "crimson king": ("Acer platanoides", "Crimson King"),
    "deborah": ("Acer platanoides", "Deborah"),
    "regal prince": _LONG,
    "regal prince oak": _LONG,
    "royal prince": _LONG,
    "kindred spirit": (None, "Kindred Spirit"),
    "kindred spirit oak": (None, "Kindred Spirit"),
    "heritage": ("Quercus x macdanielii", "Heritage"),
    "heritage oak": ("Quercus x macdanielii", "Heritage"),
    "ivory silk": ("Syringa", "Ivory Silk"),
    "china snow": ("Syringa pekinensis", "China Snow"),
    "greenspire": ("Tilia", "Greenspire"),
    "redmond": ("Tilia americana", "Redmond"),
    "american sentry": ("Tilia americana", "American Sentry"),
    "skyline": ("Gleditsia triacanthos", "Skyline"),
    "royal raindrops": ("Malus", "Royal Raindrops"),
    "royal raindrop": ("Malus", "Royal Raindrops"),
    "spring snow": ("Malus", "Spring Snow"),
    "adirondack": ("Malus", "Adirondack"),
    "gladiator": ("Malus", "Gladiator"),
    "prairie fire": ("Malus", "Prairifire"),
    "toringo": ("Malus toringo", None),
    "frontier": ("Ulmus", "Frontier"),
    "frontier elm": ("Ulmus", "Frontier"),
    "pioneer": ("Ulmus", "Pioneer"),
    "valley forge": ("Ulmus", "Valley Forge"),
    "princeton": ("Ulmus", "Princeton"),
    "princeton american elm": ("Ulmus", "Princeton"),
    "triumph": ("Ulmus", "Triumph"),
    "new horizon": ("Ulmus", "New Horizon"),
    "commendation": ("Ulmus", "Commendation"),
    "accolade": ("Ulmus", "Morton"),
    "accolade elm": ("Ulmus", "Morton"),
    "state street": ("Acer miyabei", "Morton"),
    "state street maple": ("Acer miyabei", "Morton"),
    "red pointe": ("Acer rubrum", "Red Pointe"),
    "armstrong": ("Acer", "Armstrong"),
    "sun valley": ("Acer rubrum", "Sun Valley"),
    "marmo": ("Acer x freemanii", "Marmo"),
    "marmo - acer freemanii": ("Acer x freemanii", "Marmo"),
    "matador": ("Acer x freemanii", "Matador"),
    "fall fiesta": ("Acer saccharum", "Fall Fiesta"),
    "flashfire": ("Acer saccharum", "Flashfire"),
    "autumn brilliance": ("Amelanchier", "Autumn Brilliance"),
    "laevis allegheny": ("Amelanchier laevis", "Allegheny"),
    "autumn splendor": ("Aesculus", "Autumn Splendor"),
    "early glow": ("Aesculus glabra", "Early Glow"),
    "fort mcnair": ("Aesculus x carnea", "Fort McNair"),
    "skinny latte": ("Gymnocladus dioicus", "Skinny Latte"),
    "skinny latte coffeetree": ("Gymnocladus dioicus", "Skinny Latte"),
    "espresso": ("Gymnocladus dioicus", "Espresso"),
    "expressing": ("Gymnocladus dioicus", "Espresso"),
    "decaf": ("Gymnocladus dioicus", "Decaf"),
    "shawnee brave": ("Taxodium distichum", "Shawnee Brave"),
    "shawnee brave bald cypress": ("Taxodium distichum", "Shawnee Brave"),
    "cleveland select": ("Pyrus calleryana", "Cleveland Select"),
    "exclamation!": ("Platanus x acerifolia", "Exclamation"),
    "musashino": ("Zelkova serrata", "Musashino"),
    "chicagoland": ("Celtis occidentalis", "Chicagoland"),
    "princeton sentry": ("Ginkgo biloba", "Princeton Sentry"),
    "autumn gold": ("Ginkgo biloba", "Autumn Gold"),
    "leonard messel": ("Magnolia x loebneri", "Leonard Messel"),
    "magnolia leonard messel": ("Magnolia x loebneri", "Leonard Messel"),
    "montmorency": ("Prunus cerasus", "Montmorency"),
    "serrulata": ("Prunus serrulata", None),
}


def note_key(value: str) -> str:
    return " ".join(value.split()).lower().strip(" .'`\"")


def check_ids() -> None:
    """Refuse a layer whose `GlobalID` is no longer a key."""
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
        raise RuntimeError(f"Orland Park GlobalID duplicate query failed: {payload!r}"[:500])
    if payload["features"]:
        raise RuntimeError(
            f"Orland Park: GlobalID repeats on {len(payload['features'])} values; "
            "it is no longer the layer's key"
        )


def genus_of(name: str) -> str:
    return name.split()[0].lower()


def is_genus_only(name: str) -> bool:
    tokens = name.split()
    return len(tokens) == 1 or tokens[1].lower() in {"spp", "spp.", "sp", "sp."} or (
        len(tokens) >= 3 and tokens[1].lower() == "x" and tokens[2].lower() in {"spp", "spp."}
    )


class OrlandPark:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.notes: Counter[str] = Counter()
        self.bad_dbh = 0

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        status = (rec.get("TREE_STATUS") or "").strip().upper()
        if status in DROP_STATUS:
            self.dropped[f"TREE_STATUS {status}"] += 1
            return False
        if status not in KEEP_STATUS:
            raise RuntimeError(
                f"Orland Park: unseen TREE_STATUS {status!r}; read what it means "
                "before deciding whether it is a tree"
            )
        if (rec.get("LATIN_NAME") or "").strip().lower() in NOT_A_TREE_SPECIES:
            self.dropped["LATIN_NAME is a stump/planting-space code"] += 1
            return False
        if not (rec.get("GlobalID") or "").strip():
            self.dropped["no GlobalID"] += 1
            return False
        return True

    def species_and_cultivar(self, rec: dict) -> tuple[str | None, str | None]:
        latin = " ".join((rec.get("LATIN_NAME") or "").split()) or None
        if latin is not None and latin.lower() in NO_SPECIES:
            latin = None
        raw_note = rec.get("VARIETY")
        if not isinstance(raw_note, str) or not raw_note.strip():
            return latin, None
        mapped = VARIETY_NOTES.get(note_key(raw_note))
        if mapped is None:
            self.notes["remark not a cultivar, dropped"] += 1
            return latin, None
        implied, cultivar = mapped
        if implied is None:
            self.notes["cultivar"] += 1
            return latin, cultivar
        if latin is None:
            self.notes["remark disagrees with species, dropped"] += 1
            return latin, None
        if latin.split("'")[0].strip().lower() == implied.lower() or (
            len(implied.split()) == 1 and genus_of(latin) == implied.lower()
        ):
            self.notes["cultivar"] += 1
            return latin, cultivar
        if is_genus_only(latin) and genus_of(latin) == genus_of(implied) and len(implied.split()) > 1:
            self.notes["species from remark"] += 1
            return implied, cultivar
        self.notes["remark disagrees with species, dropped"] += 1
        return latin, None

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            tree_id.append(f"opk-{rec['GlobalID'].strip().strip('{}').lower()}")
            sp, cv = self.species_and_cultivar(rec)
            species.append(sp)
            cultivar.append(cv)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            raw_dbh = rec.get("DBH")
            if raw_dbh is not None and raw_dbh <= 0:
                self.bad_dbh += 1
                raw_dbh = None
            dbh.append(float(raw_dbh) if raw_dbh is not None else None)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["USOPK"] * n, type=pa.string()),
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
            print(f"Orland Park: dropped {n:,} rows: {reason}", file=sys.stderr)
        for kind, n in sorted(self.notes.items()):
            print(f"Orland Park: VARIETY remarks: {n:,} {kind}", file=sys.stderr)
        print(f"Orland Park: nulled {self.bad_dbh} non-positive DBH values", file=sys.stderr)


def iter_row_chunks():
    """One ArcGIS page (1,000 features) at a time, geometry reprojected to WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


if __name__ == "__main__":
    check_ids()
    orland = OrlandPark()
    table = stream_to_table(
        iter_row_chunks(), orland, keep=orland.keep, label="Orland Park OpenData"
    )
    orland.report()
    table = validate_coordinates(table, city="Orland Park", city_code="USOPK")
    table = enforce_tree_schema(
        table, city="Orland Park", data_source="ORLAND_PARK_OPENDATA"
    )
    emit(table)
