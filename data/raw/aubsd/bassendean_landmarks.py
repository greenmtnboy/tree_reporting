#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Bassendean's heritage places: the Town of Bassendean Heritage List, from
the Town's ArcGIS Online.

No licence stated; displayed with attribution only, not licensed for reuse.

Source: "Public_TOB_102_Council_Adopted_Heritage_26_Jul_2022 view" (item
225a54f515224e1996e6d5f6c6613f36, owned by a Town of Bassendean staff account
on the same organisation that publishes the trees: "Heritage areas as
adopted by Council on 26 July 2022"), layer 0.  It is the Town's statutory
Heritage List under its local planning scheme, one polygon per listed place
with the list number (`No`), the place name, its address, the statement of
significance and its management category (Exceptional Category 1,
Considerable Category 2).  The item states no licence.

The State Register of Heritage Places (Heritage Council of WA, DPLH-006 on
data.wa.gov.au) was not used: SLIP serves it only behind a login under a
"Custom (Active Acceptance)" licence (Perth and Cockburn hit the same), and
the State-registered places in the Town (Earlsferry, Daylesford, the
Guildford Road Bridge, Bassendean Oval, ...) are on this list, most of them
quoting their Register entry in the statement.

Measured 2026-11-22 (layer `dataLastEditDate` 2026-01-27): 52 polygons over
49 list numbers, all inside the Town: 14 Exceptional, 38 Considerable.  List
number 6 is Bassendean Oval with three separately mapped components (its
entrance gate and two grandstands); each publishes as its own landmark, as
the list describes each.  Every row publishes: the list carries no
demolished flag ("House Cleikum Inn (site)" is listed as a site).

Field mapping:

  No                 -> landmark_id ("bsd-h{No}", or "bsd-h{No}-{ogc_fid}"
                        where the number covers several components) and
                        register_number
  Placename          -> name; when the same text names more than one place
                        ("House" names 20) the street address is appended
  Address_Lo         -> address ("Lot 6 (No. 1) Earlsferry Court,
                        Bassendean" -> "1 Earlsferry Court, Bassendean")
  Management         -> category
  (constant)         -> protection_type "Town of Bassendean Heritage List"
  polygon geometry   -> geometry_raw (WKT), outSR 4326
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_geometry_to_wkt, iter_features
from shared.ingest import emit

# Kept in step with bassendean_landmarks_probe.py.
LAYER = FeatureLayer(
    "https://services-ap1.arcgis.com/551UnqKK1GZeDKxQ/arcgis/rest/services/"
    "Public_TOB_102_Council_Adopted_Heritage_26_Jul_2022_view/FeatureServer/0"
)

OUT_FIELDS = "FID,ogc_fid,No,Placename,Address_Lo,Management"

PROTECTION_TYPE = "Town of Bassendean Heritage List"

# Refuse a list that came back far smaller than the 52 places it held.
MIN_PLACES = 40

_HOUSE_NUMBER = re.compile(r"\(\s*Nos?\.?\s*([^)]+)\)\s*(.+)", re.IGNORECASE)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip(" '‘’")
    return text or None


def address_of(value) -> str | None:
    """A street address from the list's lot description.

    "Lot 187 (No. 1) Old Perth Road, Bassendean" -> "1 Old Perth Road,
    Bassendean".  A reserve description stays as written; a bare structure
    number in brackets ("(MRWA 910)") is not an address.  One entry records a
    correction inline ("ERROR - Lot 5 ... - - Lot 1 (No. 25) Old Perth Road"):
    the part after the last "- -" is the address.
    """
    text = clean(value)
    if text is None:
        return None
    if "- -" in text:
        text = text.rsplit("- -", 1)[1].strip()
    if text.startswith("("):
        return None
    text = text.replace(" ,", ", ").replace(",,", ",")
    text = " ".join(text.split())
    match = _HOUSE_NUMBER.search(text)
    if match:
        text = f"{match.group(1).strip()} {match.group(2).strip()}"
    return text


def transform(features: list[dict]) -> pa.Table:
    rows = []
    for feature in features:
        attrs = feature.get("attributes") or {}
        number = attrs.get("No")
        label = clean(attrs.get("Placename"))
        wkt = esri_geometry_to_wkt(feature.get("geometry"))
        if number is None or label is None or wkt is None:
            continue
        rows.append((int(number), label, wkt, attrs))

    if len(rows) < MIN_PLACES:
        raise RuntimeError(
            f"Town of Bassendean heritage list returned {len(rows)} places; "
            f"expected at least {MIN_PLACES}"
        )

    numbers = Counter(number for number, _, _, _ in rows)
    names = Counter(label for _, label, _, _ in rows)

    landmark_id, name, geometry_raw = [], [], []
    address, protection_type, register_number, category = [], [], [], []
    for number, label, wkt, attrs in rows:
        addr = address_of(attrs.get("Address_Lo"))
        if names[label] > 1 and addr:
            label = f"{label}, {addr.removesuffix(', Bassendean')}"
        ident = f"bsd-h{number}"
        if numbers[number] > 1:
            ident = f"{ident}-{int(attrs['ogc_fid'])}"

        landmark_id.append(ident)
        name.append(label)
        geometry_raw.append(wkt)
        address.append(addr)
        protection_type.append(PROTECTION_TYPE)
        register_number.append(str(number))
        category.append(clean(attrs.get("Management")))

    if len(set(landmark_id)) != len(landmark_id):
        raise RuntimeError("Town of Bassendean heritage list: landmark ids repeat")

    return pa.table(
        {
            "landmark_id": pa.array(landmark_id, type=pa.string()),
            "city": pa.array(["AUBSD"] * len(landmark_id), type=pa.string()),
            "name": pa.array(name, type=pa.string()),
            "geometry_raw": pa.array(geometry_raw, type=pa.string()),
            "address": pa.array(address, type=pa.string()),
            "protection_type": pa.array(protection_type, type=pa.string()),
            "register_number": pa.array(register_number, type=pa.string()),
            "category": pa.array(category, type=pa.string()),
        }
    )


if __name__ == "__main__":
    features: list[dict] = []
    for page in iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, order_by="FID"
    ):
        features.extend(page)
    table = transform(features)
    print(
        f"Bassendean landmarks: {table.num_rows} of {len(features)} listed places",
        file=sys.stderr,
    )
    emit(table)
