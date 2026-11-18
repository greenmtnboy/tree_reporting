#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Antwerp's landmarks: its protected heritage, from the Flemish heritage register.

Source: the Agentschap Onroerend Erfgoed's designation layers on the Flemish
government's public Mercator WFS
(`https://www.mercator.vlaanderen.be/raadpleegdienstenmercatorpubliek/wfs`),
the service the agency's own download page points to for reuse
(https://geo.onroerenderfgoed.be/downloads).  Its capabilities give the terms:
"Gratis open data licentie Vlaanderen v1.2" -- the Modellicentie Gratis
Hergebruik, the same family of licence as the trees.  These are the statutory
designations under the Flemish heritage decree, each with its designation
object id, name, address and date in force: the runbook's first preference,
and the same register Ghent (`begnt/`) reads.  Three layers are read:

* `ps:ps_bes_monument` -- protected monuments (1,180 in Antwerp when this was
  written: the cathedral, the Rubenshuis, the Plantin-Moretus house, town
  houses, guild halls, the Atlantikwall bunkers of the port);
* `ps:ps_bes_land` -- protected cultural-historical landscapes (31: the
  Nachtegalenpark, the Hobokense Polder, Middelheim, the zoo, the Begijnhof);
* `ps:ps_bes_sd_gezicht` -- protected town and village views (36).

Not read: the inventories that are not protection (`vast_be`, the
established built-heritage inventory; the scientific inventory), archaeology,
and the transition zones.

**Antwerp's own rows** are selected server-side on the register's `locatie`,
which ends each address with its municipality in parentheses (`Kloosterstraat
15 (Antwerpen)`; an object spanning municipalities lists each, so the
Antitankgracht that runs from Berendrecht to Schilde is kept, whole).  The
register writes the post-2019 municipalities (`Beveren-Kruibeke-Zwijndrecht`),
so the municipality of Antwerpen covers its nine districts and Borsbeek; no
row is labelled `(Borsbeek)`.  (The brief's NIS code 11002 is not a column of
these layers; the municipality name is how the register says it.)

**Keyed on `aanduid_id`**, the designation object's id
(https://id.erfgoed.net/aanduidingsobjecten/{id}), unique across the three
layers; published as `antwerp-{id}`.  A row with no name or geometry, or an
end date already passed, is dropped with a count.

**Names.**  The register names many protected objects by type (`Burgerhuis`,
`Bunker type 57a`); a name that repeats in the layer set gets its address
appended (`Burgerhuis, Oudekerkstraat 40`), Vienna's and Ghent's rule, so
each label says which.

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
ANTWERP_FILTER = "locatie LIKE '%(Antwerpen)%'"
PROPERTIES = ["aanduid_id", "naam", "locatie", "geld_start", "geld_einde", "geom"]
ID_PREFIX = "antwerp-"

_MUNICIPALITY = re.compile(r"\s*\(Antwerpen\)\s*$")


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
    """`Kloosterstraat 15 (Antwerpen)` -> `Kloosterstraat 15`; a multi-municipality
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
        layer, sort_by="aanduid_id", properties=PROPERTIES, cql_filter=ANTWERP_FILTER
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
        raise RuntimeError(f"Antwerp landmarks: {type_name} returned no Antwerp features")
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
        f"Antwerp landmarks: {len(records)} kept ({dict(kept)}); {renamed} repeated "
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
            "city": pa.array(["BEANR"] * len(records), type=pa.string()),
            "name": col("name", pa.string()),
            "geometry_raw": col("geometry_raw", pa.string()),
            "category": col("category", pa.string()),
            "address": col("address", pa.string()),
            "designation_date": col("designation_date", pa.date32()),
        }
    )


if __name__ == "__main__":
    emit(transform(read_landmarks()))
