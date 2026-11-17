#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Hamburg's street-tree cadastre (Straßenbaumkataster), from the city's WFS.

Source: `de.hh.up:strassenbaumkataster` on
`geodienste.hamburg.de/HH_WFS_Strassenbaumkataster`, published by the Freie
und Hansestadt Hamburg, Behörde für Umwelt, Klima, Energie und
Agrarwirtschaft (BUKEA) under the Datenlizenz Deutschland -- Namensnennung --
2.0 (catalogued on transparenz.hamburg.de as "Straßenbaumkataster Hamburg",
metadata id C1C61928-C602-4E37-AF31-2D23901E2540).  It is every tree on a
public street parcel -- the basis of the city's road-safety tree inspections
-- **229,876 points** when this was written.  Park and green-space trees are
not in it and are not published as open data.

**The server is deegree, not GeoServer**, so `shared.platforms.wfs` does not
fit: it rejects `outputFormat=application/json` (it speaks
`application/geo+json`) and its GeoJSON carries no `numberMatched`.  So this
pages the layer itself -- sorted on `baumid`, the table's key, so `startIndex`
paging is stable -- and refuses to publish unless it read exactly the count a
`resultType=hits` request reported.  `srsName=EPSG:4326` answers in
longitude/latitude order, as GeoJSON requires.

**Keyed on `baumid`**, the register's integer tree id (`100000117`):
checked over the whole layer, 229,876 distinct values in 229,876 rows, none
null.  `baumnummer` (`F122-83-1`, a street section and position) is the
inspector's label and repeats (228,429 distinct, 156 null); the GeoJSON
feature id (`..._STRASSENBAUMKATASTER_1`) is a row number of the response.

**Species is `art_latein`**, the binomial without the cultivar
(`Tilia x europaea`); `sorte_latein` is the same name with the cultivar
quoted (`Tilia x europaea 'Pallida'`) and is read only for that cultivar.

* `spec.` (and `spec`) means the genus alone;
* a parenthetical is a nursery note -- `(s)` a seedling, `(v)` grafted -- as
  is a trailing `veredlung` (grafted), and both are dropped;
* `Mischbestand` (a mixed stand) and a blank name are not a taxon and publish
  as Unknown;
* the cultivar is first quote to last, so `'Faassen's Black'` survives and
  the register's doubled quotes (`''Autumn Purple''`) are folded.

`stammumfang` is the trunk **circumference** in whole centimetres (median 116,
young trees 18-25), converted to a DBH in inches; `stammumfang_z` is the same
value as text.  `kronendurchmesser` is the crown diameter in whole metres
(median 8), published as `crown_width_m`; zeros are unmeasured and are nulled
by the shared schema.  `pflanzjahr` is a planting year (0 on 185 rows, which
the year parser nulls; a few hundred-year values like 175 and a 2104 are
nulled by the shared schema), published as January 1 of that year.  The layer
has no status column: no removed trees, stumps or planting sites are flagged
in it (`daten_aus_vorjahr` is the data year, 2026 on every row).
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    circumference_cm_to_dbh_inches,
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    get_with_retry,
    parse_plant_date_year,
    stream_to_table,
    validate_coordinates,
)

SOURCE_URL = "https://geodienste.hamburg.de/HH_WFS_Strassenbaumkataster"
TYPE_NAME = "de.hh.up:strassenbaumkataster"
PAGE_SIZE = 25000
TIMEOUT = 300

GENUS_ONLY = frozenset({"spec.", "spec", "sp.", "sp", "spp.", "spp"})
NOT_A_TAXON = frozenset({"mischbestand"})
NURSERY_NOTES = frozenset({"veredlung", "sämling"})
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")
_QUOTED = re.compile(r"'(.+)'")


def _params(**extra) -> dict[str, str]:
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": TYPE_NAME,
    }
    params.update({k: str(v) for k, v in extra.items()})
    return params


def feature_count() -> int:
    """`numberMatched` from a `resultType=hits` request (XML)."""
    response = get_with_retry(SOURCE_URL, params=_params(resultType="hits"), timeout=TIMEOUT)
    match = re.search(r'numberMatched="(\d+)"', response.text)
    if match is None:
        raise RuntimeError(
            f"{TYPE_NAME}: resultType=hits gave no numberMatched: {response.text[:200]!r}"
        )
    return int(match.group(1))


def iter_feature_pages(counts: dict[str, int]):
    """Pages of GeoJSON features, sorted on `baumid`, until a short page."""
    start = 0
    while True:
        payload = get_json_with_retry(
            SOURCE_URL,
            params=_params(
                outputFormat="application/geo+json",
                SRSNAME="EPSG:4326",
                SORTBY="baumid",
                STARTINDEX=start,
                COUNT=PAGE_SIZE,
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


def parse_species(art_latein) -> str | None:
    """The register's binomial, or None for a non-taxon (published as Unknown)."""
    text = clean(_PARENTHETICAL.sub("", art_latein or ""))
    if text is None or text.lower() in NOT_A_TAXON:
        return None
    words = [w for w in text.split() if w.lower() not in NURSERY_NOTES]
    if len(words) >= 2 and words[1].lower() in GENUS_ONLY:
        return words[0]
    return " ".join(words) or None


def parse_cultivar(sorte_latein) -> str | None:
    """The quoted selection in the full name, first quote to last."""
    text = clean(sorte_latein)
    if text is None:
        return None
    match = _QUOTED.search(text.replace("''", "'"))
    if match is None:
        return None
    return clean(match.group(1).strip("'"))


def parse_number(value, counts: dict[str, int], key: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        counts[key] += 1
        return None


def make_transform(counts: dict[str, int]):
    def transform(features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float] = []
        longitude: list[float] = []
        dbh: list[float | None] = []
        crown: list[float | None] = []

        for feature in features:
            props = feature.get("properties") or {}
            baumid = props.get("baumid")
            geometry = feature.get("geometry") or {}
            coords = geometry.get("coordinates")
            # Every point is a one-point MultiPoint.
            if geometry.get("type") == "MultiPoint" and coords:
                coords = coords[0]
            elif geometry.get("type") != "Point":
                coords = None
            if baumid in (None, "") or not coords or len(coords) < 2:
                counts["unusable"] += 1
                continue
            year = props.get("pflanzjahr")
            planted = parse_plant_date_year(year)
            if year not in (None, "") and planted is None:
                try:
                    int(year)
                except (TypeError, ValueError):
                    counts["bad_number"] += 1
            circ = parse_number(props.get("stammumfang"), counts, "bad_number")
            tree_id.append(f"hamburg-{int(baumid)}")
            species.append(parse_species(props.get("art_latein")))
            cultivar.append(parse_cultivar(props.get("sorte_latein")))
            plant_date.append(planted)
            longitude.append(float(coords[0]))
            latitude.append(float(coords[1]))
            dbh.append(circumference_cm_to_dbh_inches(circ))
            crown.append(parse_number(props.get("kronendurchmesser"), counts, "bad_number"))

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["DEHAM"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
                "crown_width_m": pa.array(crown, type=pa.float64()),
            }
        )

    return transform


def main() -> None:
    expected = feature_count()
    counts = {"read": 0, "unusable": 0, "bad_number": 0}
    table = stream_to_table(
        iter_feature_pages(counts), make_transform(counts), label="Hamburg OpenData"
    )
    if counts["read"] != expected:
        raise RuntimeError(
            f"Hamburg OpenData: read {counts['read']} feature(s) but the layer reports "
            f"{expected}; refusing to publish a partial cadastre"
        )
    if counts["bad_number"] > 0.01 * max(counts["read"], 1):
        raise RuntimeError(
            f"Hamburg OpenData: {counts['bad_number']} unreadable year/girth/crown value(s) "
            f"(>1% of {counts['read']}); the fields changed shape"
        )
    print(
        f"Hamburg OpenData: read {counts['read']} of {expected}; dropped "
        f"{counts['unusable']} row(s) with no baumid or no point; "
        f"{counts['bad_number']} unreadable numeric value(s) nulled; leaving {table.num_rows}",
        file=sys.stderr,
    )
    table = validate_coordinates(table, city="Hamburg", city_code="DEHAM")
    table = enforce_tree_schema(table, city="Hamburg", data_source="HAMBURG_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
