#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy", "tzdata"]
# ///
"""Ballarat, VIC's municipal tree inventory.

Source: the "Trees" dataset (`trees`) on the City of Ballarat's OpenDataSoft
portal, data.ballarat.vic.gov.au, published by the City of Ballarat under
CC BY 3.0 AU and "typically updated monthly on an automated schedule".
84,116 rows on 2026-11-16 (`metas.default.modified` 2026-11-15T15:42Z), every
one a point, all inside the City of Ballarat LGA (143.62..143.95 E,
-37.72..-37.35 N).  Read as the portal's whole-dataset parquet export, the
Melbourne pattern (`aumel/melbourne_tree_info.py`); OpenDataSoft has no
platform module.  The download goes through `get_with_retry`, because the
portal resets the odd connection mid-transfer.

**`ref` is the key.**  The portal publishes no record id of its own, so the
council's asset reference is the grain: an eight-digit string
(`00154605`), 84,116 distinct values over 84,116 rows, none null.  Prefixed
`bal-`.  `description` ("Pear 23 Josephine Way") is a common name and an
address, never an id.

**`species` and `genus` carry the same value**, the whole name (`Quercus
robur`, `Eucalyptus Sp.`, `Fraxinus pennsylvan 'Cimmaron'`): the two columns
are equal on all 84,116 rows.  So the name is read from `species` alone, not
recombined with `normalize_species_parts` (which would write `Quercus robur
quercus robur`).  The inventory abbreviates long epithets to fit a field
width (`Gleditsia triaca`, `Ulmus x holl`, `Eucalyptus leuc`, `Corymbia
fic.`) and writes some cultivars unquoted (`Callistemon Kings Park Speci`,
`Acer x freemanii Autumn Blaze`); `SOURCE_SPELLINGS` restores those, keyed
on the exact source value, so the shared rules see a binomial and a quoted
cultivar.  Left as written, `Callistemon Kings Park Speci` publishes as the
chimera `Callistemon kings` and `Fraxinus pennsylvan` as a species of its
own.  Misspellings proper (`Syzgium paniculata`, `Zelkova seratta`) are not
corrected here; they are the shared synonym/misspelling maps' job.

Values that are a record status rather than a taxon publish as `Unknown`
(`NOT_A_NAME`): `Not Assessed` (19,002 rows), `Not Recorded` (3,578),
`Native Sp.` (1,532), `Mixed Sp.`, `Unknown Sp.`, `Un-Named Sp.` and
`Garden Sp.`.  Left alone, `Native Sp.` and `Garden Sp.` would reach the
enrichment queue as genera called *Native* and *Garden*.  They are trees the
crew did not name, so they stay; the inventory records no empty sites.

**`dbh` is a published band in centimetres**, not a measurement: `<15cm`,
`16cm - 30cm`, `31cm - 60cm`, `61cm-90cm`, `> 90cm`, or `Not Assessed`
(39,249 rows, published null).  Each band is read as its midpoint and the
open-topped `> 90cm` as its bound plus half the common 30 cm width (105 cm),
Denver's rule, then converted to inches.  **`crown` is the crown spread in
metres, also banded** (`<3m` ... `25-40m`, `Not Assessed` on 25,188), read
the same way into `crown_width_m`.  An unknown band label counts as
unreadable, and more than 1% of rows unreadable in either column refuses the
build.  `height` is a height band and is not published.

**`planted`** is a timestamp in UTC on 14,073 rows (2012-2026; the portal
exports with `timezone=UTC`), so the planting date is the date it names in
Australia/Melbourne -- the UTC date of a morning planting in Ballarat is the
day before.  `updated` is the record's edit date, not a planting, and is not
published.  `aohplaque` is the Avenue of Honour plaque number on 2,912 trees
(Remembrance Drive and Avenue Road); the plaques are a register of their own
on the portal (`avenue-of-honour-plaques`) and are not mapped here.
"""

import io
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    emit,
    enforce_tree_schema,
    get_with_retry,
    normalize_species,
    parse_wkb_point,
    validate_coordinates,
)

DATASET_URL = (
    "https://data.ballarat.vic.gov.au/api/explore/v2.1/catalog/datasets/"
    "trees/exports/parquet?lang=en&timezone=UTC"
)

COLUMNS = ["ref", "species", "dbh", "crown", "planted", "point"]

LOCAL_TZ = ZoneInfo("Australia/Melbourne")

# The inventory's truncations and unquoted cultivars, restored to a binomial
# plus a quoted cultivar.  Keyed on the exact source value (after whitespace
# is collapsed).  Each is an abbreviation of a name the same inventory also
# writes in full (`Gleditsia triacanthos`, `Ulmus x hollandica`, ...), or a
# cultivar whose name is in the value.
SOURCE_SPELLINGS: dict[str, str] = {
    "Fraxinus pennsylvan 'Cimmaron'": "Fraxinus pennsylvanica 'Cimmaron'",
    "Fraxinus penn. 'Urbanite'": "Fraxinus pennsylvanica 'Urbanite'",
    "Fraxinus pennsylvanica'Urbanit": "Fraxinus pennsylvanica 'Urbanite'",
    "Fraxinus americ 'Aut Applause'": "Fraxinus americana 'Autumn Applause'",
    "Fraxinus raywoodii": "Fraxinus angustifolia 'Raywood'",
    "Eucalyptus leuc megalocarpa": "Eucalyptus leucoxylon megalocarpa",
    "Eucalyptus leuc 'Euky Dwarf'": "Eucalyptus leucoxylon 'Euky Dwarf'",
    "Eucalyptus pau. little snowman": "Eucalyptus pauciflora 'Little Snowman'",
    "Eucalyptus cin.'Silver shadow'": "Eucalyptus cinerea 'Silver Shadow'",
    "Eucalyptus sub bicostata": "Eucalyptus globulus bicostata",
    "Gleditsia triaca 'Shademaster'": "Gleditsia triacanthos 'Shademaster'",
    "Gleditsia triacanth 'Sunburst'": "Gleditsia triacanthos 'Sunburst'",
    "Gleditsia triacant 'Ruby Lace'": "Gleditsia triacanthos 'Ruby Lace'",
    "Ulmus x holl 'Purpurescens'": "Ulmus x hollandica 'Purpurascens'",
    "Callistemon Kings Park Speci": "Callistemon 'Kings Park Special'",
    "Callistemon Harkness": "Callistemon 'Harkness'",
    "Callistemon v Dawson River": "Callistemon viminalis 'Dawson River'",
    "Liquidambar styr 'Canb Gem'": "Liquidambar styraciflua 'Canberra Gem'",
    "Platanus xacerifolia'Columbia'": "Platanus x acerifolia 'Columbia'",
    "Pittosporum eugenio variegatum": "Pittosporum eugenioides 'Variegatum'",
    "Pittosporum undulat variegatum": "Pittosporum undulatum 'Variegatum'",
    "Corymbia fic 'Vermilion Blaze'": "Corymbia ficifolia 'Vermilion Blaze'",
    "Corymbia fic. 'Baby Orange'": "Corymbia ficifolia 'Baby Orange'",
    "Corymbia fic. 'Wildfire'": "Corymbia ficifolia 'Wildfire'",
    "Acer x freemanii Autumn Blaze": "Acer x freemanii 'Autumn Blaze'",
    "Salix ba pekinensis 'Tortuosa'": "Salix babylonica pekinensis 'Tortuosa'",
}

# Record statuses written where a name goes; they publish as `Unknown`.
NOT_A_NAME = frozenset(
    {
        "not assessed", "not recorded", "native sp.", "mixed sp.",
        "unknown sp.", "un-named sp.", "garden sp.", "unknown",
    }
)

# DBH bands, centimetres -> midpoint.  `> 90cm` is open-topped: its bound plus
# half the common 30 cm width.
DBH_BAND_CM: dict[str, float] = {
    "<15cm": 7.5,
    "16cm - 30cm": 22.5,
    "31cm - 60cm": 45.0,
    "61cm-90cm": 75.0,
    "> 90cm": 105.0,
}

# Crown-spread bands, metres -> midpoint.  The top band is closed (25-40 m).
CROWN_BAND_M: dict[str, float] = {
    "<3m": 1.5,
    "3-6m": 4.5,
    "6-10m": 8.0,
    "10-15m": 12.5,
    "15-25m": 20.0,
    "25-40m": 32.5,
}

NOT_MEASURED = frozenset({"", "not assessed"})
MAX_BAND_PARSE_FAILURES = 0.01


def _band_key(value: str) -> str:
    """A band label with its internal whitespace collapsed."""
    return " ".join(value.split())


class BandReader:
    """Reads one banded column, counting the labels it does not know."""

    def __init__(self, name: str, bands: dict[str, float]):
        self.name = name
        self.bands = {_band_key(k): v for k, v in bands.items()}
        self.read = 0
        self.failures: dict[str, int] = {}

    def __call__(self, value: str | None) -> float | None:
        self.read += 1
        text = _band_key(value or "")
        if text.lower() in NOT_MEASURED:
            return None
        mid = self.bands.get(text)
        if mid is None:
            self.failures[text] = self.failures.get(text, 0) + 1
        return mid

    def check(self) -> None:
        bad = sum(self.failures.values())
        if not bad:
            return
        share = bad / max(self.read, 1)
        print(
            f"Ballarat ingest: {bad} unreadable `{self.name}` values "
            f"({share:.2%}): {sorted(self.failures.items(), key=lambda kv: -kv[1])[:10]}",
            file=sys.stderr,
        )
        if share > MAX_BAND_PARSE_FAILURES:
            raise RuntimeError(
                f"Ballarat `{self.name}`: {share:.2%} of rows carry a band label "
                "this ingest does not know; the portal has changed its bands"
            )


def species_of(value: str | None) -> str | None:
    """The source name with its truncations restored; None publishes `Unknown`."""
    text = " ".join((value or "").split())
    if not text or text.lower() in NOT_A_NAME:
        return None
    return normalize_species(SOURCE_SPELLINGS.get(text, text))


def local_date(value: datetime | None) -> date | None:
    """The calendar date a UTC timestamp names in Ballarat."""
    if value is None:
        return None
    return value.astimezone(LOCAL_TZ).date()


def download() -> pa.Table:
    response = get_with_retry(DATASET_URL, timeout=300)
    return pq.read_table(io.BytesIO(response.content), columns=COLUMNS)


def transform(raw: pa.Table) -> pa.Table:
    dbh_reader = BandReader("dbh", DBH_BAND_CM)
    crown_reader = BandReader("crown", CROWN_BAND_M)

    tree_id: list[str] = []
    species: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    missing_ref = 0
    for rec in raw.to_pylist():
        ref = (rec.get("ref") or "").strip()
        if not ref:
            missing_ref += 1
            continue
        lon, lat = parse_wkb_point(rec.get("point"))
        dbh_cm = dbh_reader(rec.get("dbh"))

        tree_id.append(f"bal-{ref}")
        species.append(species_of(rec.get("species")))
        plant_date.append(local_date(rec.get("planted")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(dbh_cm / 2.54 if dbh_cm is not None else None)
        crown.append(crown_reader(rec.get("crown")))

    if missing_ref:
        print(
            f"Ballarat ingest: dropped {missing_ref} rows with no `ref`",
            file=sys.stderr,
        )
    dbh_reader.check()
    crown_reader.check()

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["AUBAL"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


if __name__ == "__main__":
    table = transform(download())
    table = validate_coordinates(table, city="Ballarat", city_code="AUBAL")
    table = enforce_tree_schema(
        table, city="Ballarat", data_source="BALLARAT_OPENDATA"
    )
    emit(table)
