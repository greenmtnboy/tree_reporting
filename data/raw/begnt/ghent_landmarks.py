#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Ghent's landmarks: its protected heritage, from the Flemish heritage register.

Source: the Agentschap Onroerend Erfgoed's designation layers on the Flemish
government's public Mercator WFS
(`https://www.mercator.vlaanderen.be/raadpleegdienstenmercatorpubliek/wfs`),
the service the agency's own download page points to for reuse
(https://geo.onroerenderfgoed.be/downloads).  Its capabilities give the terms:
"Gratis open data licentie Vlaanderen v1.2" -- the Modellicentie Gratis
Hergebruik, the same licence as the trees.  Stad Gent's own portal lists the
register ("Vastgesteld bouwkundig erfgoed Gent") only as a link to it.  These
are the statutory designations under the Flemish heritage decree, each with
its designation object id, name, address and date in force: the runbook's
first preference.  Three layers are read:

* `ps:ps_bes_monument` -- protected monuments (674 in Ghent when this was
  written: the belfry, the begijnhoven, churches, castles, town houses);
* `ps:ps_bes_land` -- protected cultural-historical landscapes (8: the
  Bourgoyen-Ossemeersen, Graslei en Korenlei, the Kastelensite);
* `ps:ps_bes_sd_gezicht` -- protected town and village views (71: the
  Plantentuin Michel Thiery, Dorpskern Mariakerke, the Miljoenenkwartier).

Not read: the inventories that are not protection (`vast_be`, the 10k-row
established built-heritage inventory; the scientific inventory), archaeology,
the UNESCO core and buffer, and the transition zones.

**Ghent's own rows** are selected server-side on the register's `locatie`,
which ends each address with its municipality in parentheses (`Sint-
Baafsplein (Gent)`; an object spanning municipalities lists each).  The
municipality of Gent includes its deelgemeenten (Drongen, Mariakerke ...).

**Keyed on `aanduid_id`**, the designation object's id
(https://id.erfgoed.net/aanduidingsobjecten/{id}), unique across the three
layers; published as `ghent-{id}`.  A row with no name or geometry, or an
end date already passed, is dropped with a count.

**Names.**  The register names many protected houses by type (`Burgerhuis`
19 times, `Stadswoning`, `Twaalfkamerenbeluik: arbeiderswoning` 32 times);
a name that repeats in the layer set gets its address appended
(`Burgerhuis, Onderbergen 39`), Vienna's rule, so each label says which.

The register's attributes ride along in existing `landmark_common`
properties: `category` (the designation type, in English), `address`
(`locatie` without the municipality) and `designation_date` (`geld_start`).
"""

import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit
from shared.platforms.wfs import WfsLayer, geojson_to_wkt, iter_wfs_features

BASE_URL = "https://www.mercator.vlaanderen.be/raadpleegdienstenmercatorpubliek/wfs"

LAYERS = (
    ("ps:ps_bes_monument", "Protected monument"),
    ("ps:ps_bes_land", "Protected cultural-historical landscape"),
    ("ps:ps_bes_sd_gezicht", "Protected town or village view"),
)

# The municipality as the register writes it at the end of `locatie`.
GHENT_FILTER = "locatie LIKE '%(Gent)%'"
PROPERTIES = ["aanduid_id", "naam", "locatie", "geld_start", "geld_einde", "geom"]
ID_PREFIX = "ghent-"

_MUNICIPALITY = re.compile(r"\s*\(Gent\)\s*$")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_date(value) -> date | None:
    text = clean(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def address_of(locatie) -> str | None:
    """`Sint-Baafsplein (Gent)` -> `Sint-Baafsplein`; a multi-municipality
    address keeps its parentheses."""
    text = clean(locatie)
    if text is None:
        return None
    return _MUNICIPALITY.sub("", text) or None


def read_layer(type_name: str, category: str, counts: Counter) -> list[dict]:
    layer = WfsLayer(BASE_URL, type_name, timeout=300)
    records: list[dict] = []
    today = date.today()
    for page in iter_wfs_features(
        layer, sort_by="aanduid_id", properties=PROPERTIES, cql_filter=GHENT_FILTER
    ):
        for feature in page:
            props = feature.get("properties") or {}
            key = props.get("aanduid_id")
            name = clean(props.get("naam"))
            wkt = geojson_to_wkt(feature.get("geometry"))
            if key is None or name is None or wkt is None:
                counts["no id, name or geometry"] += 1
                continue
            ends = parse_date(props.get("geld_einde"))
            if ends is not None and ends <= today:
                counts["designation ended"] += 1
                continue
            records.append(
                {
                    "landmark_id": f"{ID_PREFIX}{int(key)}",
                    "name": name,
                    "geometry_raw": wkt,
                    "category": category,
                    "address": address_of(props.get("locatie")),
                    "designation_date": parse_date(props.get("geld_start")),
                }
            )
    if not records:
        raise RuntimeError(f"Ghent landmarks: {type_name} returned no Ghent features")
    return records


def disambiguate(records: list[dict]) -> int:
    """Append the address to every name the set holds more than once."""
    repeats = Counter(r["name"] for r in records)
    renamed = 0
    for record in records:
        if repeats[record["name"]] > 1 and record["address"]:
            record["name"] = f"{record['name']}, {record['address']}"
            renamed += 1
    return renamed


def read_landmarks() -> list[dict]:
    counts: Counter = Counter()
    by_id: dict[str, dict] = {}
    for type_name, category in LAYERS:
        for record in read_layer(type_name, category, counts):
            if record["landmark_id"] in by_id:
                counts["same designation in an earlier layer"] += 1
                continue
            by_id[record["landmark_id"]] = record
    records = list(by_id.values())
    renamed = disambiguate(records)
    kept = Counter(r["category"] for r in records)
    print(
        f"Ghent landmarks: {len(records)} kept ({dict(kept)}); {renamed} repeated "
        f"names given their address; dropped {dict(counts)}",
        file=sys.stderr,
    )
    return records


def transform(records: list[dict]) -> pa.Table:
    def col(name: str, type_: pa.DataType) -> pa.Array:
        return pa.array([r[name] for r in records], type=type_)

    return pa.table(
        {
            "landmark_id": col("landmark_id", pa.string()),
            "city": pa.array(["BEGNT"] * len(records), type=pa.string()),
            "name": col("name", pa.string()),
            "geometry_raw": col("geometry_raw", pa.string()),
            "category": col("category", pa.string()),
            "address": col("address", pa.string()),
            "designation_date": col("designation_date", pa.date32()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_landmarks()))
