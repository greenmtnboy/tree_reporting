#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Zürich's tree cadastre (Baumkataster), from the city's open-data WFS.

Source: `baumkataster_baumstandorte` on `www.ogd.stadt-zuerich.ch`, the City
of Zürich's open-government geoportal, catalogued on data.stadt-zuerich.ch as
`geo_baumkataster` ("Baumkataster") under CC0.  It is Grün Stadt Zürich's
register of the trees the city manages -- park trees (`Parkbaum`) and street
trees (`Strassenbaum`), with the fruit orchards and a set of trees on
cantonal land it looks after -- **81,142 points** when this was written.
1,568 of them sit just outside the municipal boundary (`quartier` =
`ausserhalb Stadtgebiet`, all numbered `nn-`): city-owned parks and orchards
in the neighbouring communes, inside `CITY_BOUNDS`, kept.

**The server is QGIS Server speaking WFS 1.1.0**, not the GeoServer 2.0 that
`shared.platforms.wfs` is written for: it rejects `version=2.0.0`, names the
page size `maxFeatures` and reports its total only in a `resultType=hits`
XML answer (`numberOfFeatures`), never in the GeoJSON.  So this pages the
layer itself -- sorted on `objectid`, the table's key, so `startIndex` paging
is stable -- and refuses to publish unless it read exactly the count the
`hits` request reported.

**Keyed on `poi_id`, the tree number** (`HG-4804`: a district prefix and a
running number).  Checked over the whole layer: 81,142 distinct values in
81,142 rows, none null, and identical to `baumnummer` on every row.
`objectid` is also unique but is a row number of the published table, not a
fact about the tree.

**Species is a genus and an epithet in two columns**, `baumgattunglat` and
`baumartlat`; `baumnamelat` is the full name with the cultivar, and is read
only for that cultivar -- its genus is sometimes misspelt (`Juglas regia cv.`
on 546 walnuts whose genus column says `Juglans`).

* `spec.` in the epithet column means the genus alone; a parenthetical after
  the epithet is a nursery note -- `(S)` a seedling, `(w)`/`(m)` a female or
  male clone, `(V)` -- and is dropped.
* the epithet column writes infraspecific names without a rank
  (`domestica insititia`, `domestica syriaca`), which the shared hygiene
  truncates to the species;
* a hybrid genus is written `x Sycoparrotia` in the genus column, but
  Leyland cypress only in the full name (`x Cupressocyparis leylandii`, genus
  column `Cupressocyparis`); the full name's mark is carried over.
* `Obstbaum` (fruit tree), `Laubbaum` (broadleaf) and `Gehölz (Laub oder
  Nadel)` in the genus column are not taxa and publish as Unknown.
* the cultivar is the quoted part of `baumnamelat` (`Tilia x europaea
  'Pallida'`), first quote to last, so `'Reineclaude d'Oullins'` survives;
  `cv.` means an unnamed cultivar and gives none.

`kronendurchmesser` is the crown diameter in whole metres on every row (1 to
40, median 7), published as `crown_width_m`.  `pflanzjahr` is a planting
year on 72,834 rows, heavily rounded to decades for the older trees (1900,
1920, 1930 each carry hundreds) -- an estimate the register states, not a
placeholder -- and publishes as January 1 of that year.  The cadastre
records no trunk diameter, so `diameter_at_breast_height` is null.  The
layer has no removed or planned trees and no not-a-tree records: `status`
is the maintaining unit (`Grünanlage`, `Strassenbaum`, `Obst`, `Kanton`,
...), not a condition.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    get_with_retry,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)

SOURCE_URL = "https://www.ogd.stadt-zuerich.ch/wfs/geoportal/Baumkataster"
TYPE_NAME = "baumkataster_baumstandorte"
PAGE_SIZE = 20000
TIMEOUT = 300

PROPERTIES = [
    "objectid",
    "poi_id",
    "baumgattunglat",
    "baumartlat",
    "baumnamelat",
    "kronendurchmesser",
    "pflanzjahr",
    "geometry",
]

# Genus-column values that name a kind of tree rather than a taxon.
NOT_A_GENUS = frozenset({"obstbaum", "laubbaum", "nadelbaum", "baum", "gehölz"})
_GENUS = re.compile(r"^(x )?[A-Z][a-z]+$")
GENUS_ONLY = frozenset({"spec.", "spec", "sp.", "sp", "spp.", "spp", "cv.", "cv"})
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")
_QUOTED = re.compile(r"'(.+)'")


def _params(**extra) -> dict[str, str]:
    params = {
        "service": "WFS",
        "version": "1.1.0",
        "request": "GetFeature",
        "typename": TYPE_NAME,
    }
    params.update({k: str(v) for k, v in extra.items()})
    return params


def feature_count() -> int:
    """`numberOfFeatures` from a `resultType=hits` request (XML)."""
    response = get_with_retry(SOURCE_URL, params=_params(resultType="hits"), timeout=TIMEOUT)
    match = re.search(r'numberOfFeatures="(\d+)"', response.text)
    if match is None:
        raise RuntimeError(
            f"{TYPE_NAME}: resultType=hits gave no numberOfFeatures: {response.text[:200]!r}"
        )
    return int(match.group(1))


def iter_feature_pages(counts: dict[str, int]):
    """Pages of GeoJSON features, sorted on `objectid`, until a short page."""
    start = 0
    while True:
        payload = get_json_with_retry(
            SOURCE_URL,
            params=_params(
                outputFormat="GeoJSON",
                srsName="EPSG:4326",
                sortBy="objectid",
                startIndex=start,
                maxFeatures=PAGE_SIZE,
                propertyName=",".join(PROPERTIES),
            ),
            timeout=TIMEOUT,
        )
        features = payload.get("features")
        if features is None:
            raise RuntimeError(f"{TYPE_NAME}: GetFeature answered without a features array")
        if not features:
            return
        counts["read"] += len(features)
        yield features
        start += len(features)
        if len(features) < PAGE_SIZE:
            return


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_species(gattung, art, namelat) -> str | None:
    """A Latin name from the genus and epithet columns, or None for a non-taxon."""
    genus = clean(gattung)
    if genus is None or genus.lower() in NOT_A_GENUS or not _GENUS.match(genus):
        return None
    full = clean(namelat) or ""
    if not genus.startswith("x ") and full.startswith(f"x {genus} "):
        genus = f"x {genus}"
    epithet = clean(_PARENTHETICAL.sub("", art or ""))
    if epithet is None or epithet.lower() in GENUS_ONLY:
        return genus
    return f"{genus} {epithet}"


def parse_cultivar(namelat) -> str | None:
    """The quoted selection in the full name, first quote to last."""
    text = clean(namelat)
    if text is None:
        return None
    match = _QUOTED.search(text.replace("''", "'"))
    if match is None:
        return None
    inner = clean(match.group(1).strip("'"))
    return inner


def parse_crown(value) -> float | None:
    if value is None:
        return None
    try:
        metres = float(value)
    except (TypeError, ValueError):
        return None
    return metres if metres > 0 else None


def make_transform(counts: dict[str, int]):
    def transform(features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float] = []
        longitude: list[float] = []
        crown: list[float | None] = []

        for feature in features:
            props = feature.get("properties") or {}
            number = clean(props.get("poi_id"))
            geometry = feature.get("geometry") or {}
            coords = geometry.get("coordinates") if geometry.get("type") == "Point" else None
            if number is None or not coords:
                counts["unusable"] += 1
                continue
            year = props.get("pflanzjahr")
            planted = parse_plant_date_year(year)
            if year not in (None, "") and planted is None:
                counts["bad_year"] += 1
            namelat = props.get("baumnamelat")
            tree_id.append(f"zurich-{number}")
            species.append(parse_species(props.get("baumgattunglat"), props.get("baumartlat"), namelat))
            cultivar.append(parse_cultivar(namelat))
            plant_date.append(planted)
            longitude.append(float(coords[0]))
            latitude.append(float(coords[1]))
            crown.append(parse_crown(props.get("kronendurchmesser")))

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["CHZUR"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array([None] * len(tree_id), type=pa.float64()),
                "crown_width_m": pa.array(crown, type=pa.float64()),
            }
        )

    return transform


def main() -> None:
    expected = feature_count()
    counts = {"read": 0, "unusable": 0, "bad_year": 0}
    table = stream_to_table(
        iter_feature_pages(counts), make_transform(counts), label="Zürich OpenData"
    )
    if counts["read"] != expected:
        raise RuntimeError(
            f"Zürich OpenData: read {counts['read']} feature(s) but the layer reports "
            f"{expected}; refusing to publish a partial cadastre"
        )
    if counts["bad_year"] > 0.01 * max(counts["read"], 1):
        raise RuntimeError(
            f"Zürich OpenData: {counts['bad_year']} planting year(s) unreadable "
            f"(>1% of {counts['read']}); the field changed shape"
        )
    print(
        f"Zürich OpenData: read {counts['read']} of {expected}; dropped "
        f"{counts['unusable']} row(s) with no tree number or no point; "
        f"{counts['bad_year']} unreadable planting year(s) nulled; leaving {table.num_rows}",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Zürich", city_code="CHZUR")
    table = enforce_tree_schema(table, city="Zürich", data_source="ZURICH_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
