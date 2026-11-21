#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Brest Métropole's tree inventory ("Les arbres").

Source: layer 1411070, *ESPACES VERTS - Gestion arbres*, of Brest Métropole's
ArcGIS MapServer `public/GPB_ESP_Intra_EV_Arbres`
(https://geo.brest-metropole.fr/arcgis/rest/services/public/GPB_ESP_Intra_EV_Arbres/MapServer/1411070),
the trees maintained by the Métropole's Espaces Verts service.  Catalogued on
data.gouv.fr as `les-arbres` (https://www.data.gouv.fr/datasets/les-arbres/)
by Brest métropole under the Licence Ouverte v2.0 (licence id `lov2`),
updated "continuous"; the catalogue's only resources are this MapServer and
its WMS (the ZIP the Métropole's own portal links, echanges.brest-metropole.fr
`MYB41.zip`, did not answer from here).  **28,661 rows** when this was written,
stored in Lambert 93 (EPSG:2154) and read reprojected with `outSR=4326`.

The extent is the Métropole's eight communes (Brest, Bohars, Gouesnou,
Guilers, Guipavas, Le Relecq-Kerhuon, Plougastel-Daoulas, Plouzané): every
row lies in 48.3306-48.4570 N, 4.6235-4.3388 W.  It includes the Conservatoire
botanique's collection at Stang Alar, hence the long tail of exotic genera
(`Banksia`, `Protea`, `Tibouchina`).

**Keyed on `IDARB`** ("N° de l'arbre"), the tree's asset number: a string of
digits (`011014`, sector-prefixed) or `PA4517` for the *Plan Arbre* plantings.
Non-null and non-blank on every row and unique but for one value (`08324`, a
red oak and a southern beech planted in 1970 and 2018: two trees).  As
Baltimore does, the values the layer repeats are read up front with one
`groupBy ... having` statistics query and those rows take
`brest-{IDARB}-{OBJECTID}`, every other row `brest-{IDARB}`.  `OBJECTID` is not
the key: it runs 32,641 to 492,435 over 28,661 rows, a layer edited in place,
but it is the GIS's internal row id ("Id interne SIG").

**Rows kept.**  `SUPPR` ("supprimé") is `non` on every row; a `oui` row is a
removed tree and is dropped with a count, any other value raises.  `LIBRE`
(oui/non: free-grown or trained form) and `PROGRAM` (`PLAN ARBRE`, `ECOLE`)
are not used.

**Species is `GENRE` + `ESPEC`**, a scientific name split in two, with
`VARIE` the cultivar.  The fields are hand-typed:

  * `GENRE` `non defini` (1,704) or `non renseigné` is unknown and published
    as `Unknown`; two French names stand where the genus belongs (`Aulne`,
    alder, and `Nashi`, the Asian pear) and are written out in
    `FRENCH_GENERA`.  Misspelt genera (`Albizzia`, `Thuya`, `Camelia`,
    `Bettula`) are left to the shared misspelling map.
  * `ESPEC` sometimes holds a cultivar instead of an epithet (`Prunus`
    `Accolade`, `Ulmus` `Lutèce`, `Magnolia` `"Vulcan"`): a quoted value, or
    one in `CULTIVAR_EPITHETS`, moves to the cultivar.  A hybrid formula
    (`Prunus` `campanulata x incisa`), `hybride`, a bare `X`, `sp`, an `aff`
    determination or `non renseigné` leaves the genus alone.  A named
    nothospecies (`x yedoensis`, `x loebneri`) is kept.
  * `VARIE` is blank (`' '`) or `non renseigné` when absent; quotes are
    stripped, and where it holds a variety and a quoted cultivar
    (`jacquemontii 'Doorenbos'`) the quoted part is the cultivar.

`ESSFR` is the French common name and is not used.

**No diameter.**  The layer records no trunk measure (no circumference or
diameter field), so `diameter_at_breast_height` is null throughout.  `HAUTT`
is a height in whole metres (10 m on 14% of rows, the likely default) and the
schema has no column for it.

**Planting date is withheld.**  The Métropole's ArcGIS portal item for this
layer (geo.brest-metropole.fr/portal, item 35299817dd35411aabd95cfb44663ed5)
states as its licence info "Seules les informations Genre, variété, espèce et
essence peuvent être diffusées" -- only genus, variety, species and common
name may be disseminated -- while data.gouv.fr lists the same layer under the
Licence Ouverte.  Until that is settled the ingest publishes the species
fields and the position only, and `PUBLISH_PLANTING_YEAR` stays False.  The
field is `DTPLA`, a year stored as a double (27.8k rows), parsed here as
1 January of that year: 0 (27 rows) is "unknown" and null; older plantings
are estimated to the half-decade (1970: 4.1k, 1975: 3.3k, 1980: 2.5k,
1960: 1.9k), a real if coarse year rather than a stamp; the 2026 rows are the
current season's *Plan Arbre* plantings.  A value that is not a whole year in
range raises past 1% of rows either way.
"""

import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import date
from functools import cache
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (  # noqa: E402
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    normalize_species_parts,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features  # noqa: E402

LAYER = FeatureLayer(
    "https://geo.brest-metropole.fr/arcgis/rest/services/public/"
    "GPB_ESP_Intra_EV_Arbres/MapServer/1411070",
    timeout=180,
)
SOURCE_URL = LAYER.base
OUT_FIELDS = "OBJECTID,IDARB,GENRE,ESPEC,VARIE,SUPPR,DTPLA"

ID_PREFIX = "brest"

# What a blank or unknown field holds, accent- and case-folded.
UNKNOWN_VALUES = frozenset({"", "non defini", "non renseigne", "inconnu", "indetermine"})

# French common names typed where the genus belongs.
FRENCH_GENERA = {
    "aulne": "Alnus",   # alder; its one row is `Aulne` `Spaethii` (Alnus x spaethii)
    "nashi": "Pyrus",   # Asian pear; its rows are `Nashi` `Pyrifolia`
}

# `ESPEC` values that are cultivar or trade names, not epithets.  Folded.
CULTIVAR_EPITHETS = frozenset(
    {
        "accolade",            # Prunus 'Accolade'
        "umineko",             # Prunus 'Umineko'
        "columella",           # Ulmus 'Columella'
        "lutece",              # Ulmus 'Nanguen', sold as Lutèce
        "suzan",               # Magnolia 'Susan'
        "vulcan",              # Magnolia 'Vulcan'
        "apollo",              # Magnolia 'Apollo'
        "betty",               # Magnolia 'Betty'
        "black tulip",         # Magnolia 'Black Tulip'
        "fairy blush",         # Magnolia 'Fairy Blush'
        "shirazz",             # Magnolia 'Shiraz'
        "courtarou",           # Malus, a Breton cider apple
        "illinois everbearing",  # Morus 'Illinois Everbearing'
        "indian charms",       # Lagerstroemia 'Indian Charm'
        "sharon wesley",       # Bougainvillea
        "robyn gordon",        # Grevillea 'Robyn Gordon'
    }
)

# `ESPEC` values that leave the genus alone: no epithet, or one the field
# cannot carry.  Folded.
GENUS_ONLY_EPITHETS = frozenset(
    {
        "hybride", "x", "sp", "spp", "sp.", "spp.",
        "kumquat",                 # Citrus: a common name, not an epithet
        "specto glabra",           # Bougainvillea: garbled
        "platanoidextruncatum",    # Acer platanoides x truncatum, a formula
    }
)

_QUOTES = "'\"‘’“”´"
# `campanulata x incisa`, `x jubaea x syagrus`, `cordifolium x glabrum x`: a
# formula naming two parents, not one nothospecies.
_HYBRID_FORMULA = re.compile(r"\S\s+[x×]\b", re.IGNORECASE)
# `jacquemontii 'Doorenbos'`: a word, a space, then a cultivar quoted at both
# ends.  The space is what tells it from an apostrophe (`Pigeonnet d'été`).
_TRAILING_CULTIVAR = re.compile(r"\s[" + _QUOTES + r"]([^" + _QUOTES + r"]+)[" + _QUOTES + r"]\s*$")

KEPT_SUPPR = "non"
DROPPED_SUPPR = "oui"

# Planting years outside this window are not years.  0 is "unknown".
FIRST_YEAR = 1700
MAX_UNREADABLE_YEARS = 0.01
# See the module docstring: the publisher's portal limits dissemination to the
# species fields.
PUBLISH_PLANTING_YEAR = False


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def unquote(text: str | None) -> str | None:
    if text is None:
        return None
    return clean(text.strip(_QUOTES))


def parse_cultivar(value) -> str | None:
    """`VARIE` as a cultivar name, or None when blank or `non renseigné`."""
    text = clean(value)
    if text is None or fold(unquote(text) or "") in UNKNOWN_VALUES:
        return None
    # `jacquemontii 'Doorenbos'`: a variety, then the quoted cultivar.
    if text[0] not in _QUOTES:
        quoted = _TRAILING_CULTIVAR.search(text)
        if quoted:
            return clean(quoted.group(1))
    return unquote(text)


def parse_species(genre, espec, varie) -> tuple[str | None, str | None]:
    """(species string for the shared hygiene, cultivar) from the three fields."""
    cultivar = parse_cultivar(varie)
    genus = clean(genre)
    if genus is None or fold(genus) in UNKNOWN_VALUES:
        return None, cultivar
    genus = FRENCH_GENERA.get(fold(genus), genus)

    raw_epithet = clean(espec)
    epithet = unquote(raw_epithet)
    if epithet is not None:
        folded = fold(epithet)
        if raw_epithet[0] in _QUOTES or folded in CULTIVAR_EPITHETS:
            cultivar = cultivar or epithet
            epithet = None
        elif (
            folded in UNKNOWN_VALUES
            or folded in GENUS_ONLY_EPITHETS
            or folded.startswith(("aff ", "sp ", "sp. "))
            or _HYBRID_FORMULA.search(epithet)
        ):
            epithet = None
    return normalize_species_parts(genus, epithet), cultivar


def parse_year(value) -> tuple[date | None, bool]:
    """(1 January of `DTPLA`, readable).  0 and null are unknown, not unreadable."""
    if value is None or value == 0:
        return None, True
    try:
        year = float(value)
    except (TypeError, ValueError):
        return None, False
    if year != int(year) or not FIRST_YEAR <= year <= date.today().year + 1:
        return None, False
    return date(int(year), 1, 1), True


@cache
def repeated_ids() -> frozenset[str]:
    """The `IDARB` values the layer assigns to more than one row."""
    payload = get_json_with_retry(
        LAYER.query_url,
        params={
            "where": "IDARB IS NOT NULL",
            "groupByFieldsForStatistics": "IDARB",
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
        raise RuntimeError(f"Brest IDARB duplicate query was incomplete: {payload!r}"[:500])
    return frozenset(str(f["attributes"]["IDARB"]).strip() for f in payload["features"])


class Brest:
    def __init__(self) -> None:
        self.repeated = repeated_ids()
        self.dropped: Counter[str] = Counter()
        self.seen = 0
        self.years = 0
        self.unreadable_years = 0

    def keep(self, feature: dict) -> bool:
        self.seen += 1
        rec = feature.get("attributes") or {}
        suppr = clean(rec.get("SUPPR"))
        if suppr == DROPPED_SUPPR:
            self.dropped["removed (SUPPR = oui)"] += 1
            return False
        if suppr != KEPT_SUPPR:
            raise RuntimeError(
                f"Brest ingest: unknown SUPPR {suppr!r} on OBJECTID {rec.get('OBJECTID')}; "
                "the layer's removal flag changed"
            )
        if clean(rec.get("IDARB")) is None:
            self.dropped["no IDARB"] += 1
            return False
        return True

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        plant_date: list[date | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            raw_id = clean(rec["IDARB"])
            if raw_id in self.repeated:
                tree_id.append(f"{ID_PREFIX}-{raw_id}-{rec['OBJECTID']}")
            else:
                tree_id.append(f"{ID_PREFIX}-{raw_id}")
            name, selection = parse_species(rec.get("GENRE"), rec.get("ESPEC"), rec.get("VARIE"))
            species.append(name)
            cultivar.append(selection)
            planted, readable = parse_year(rec.get("DTPLA"))
            if rec.get("DTPLA") is not None:
                self.years += 1
                self.unreadable_years += not readable
            plant_date.append(planted if PUBLISH_PLANTING_YEAR else None)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)

        n = len(tree_id)
        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["FRBES"] * n, type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array(plant_date, type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array([None] * n, type=pa.float64()),
            }
        )

    def report(self) -> None:
        print(
            f"Brest OpenData: {self.seen:,} rows; "
            + (", ".join(f"dropped {n:,} {why}" for why, n in sorted(self.dropped.items()))
               or "none dropped")
            + f"; {len(self.repeated)} repeated IDARB value(s) keyed with OBJECTID; "
            f"{self.unreadable_years:,} unreadable DTPLA of {self.years:,}",
            file=sys.stderr,
        )
        if self.years and self.unreadable_years / self.years > MAX_UNREADABLE_YEARS:
            raise RuntimeError(
                f"Brest ingest: {self.unreadable_years:,} of {self.years:,} DTPLA values "
                "are not a planting year; the field changed meaning"
            )


def main() -> None:
    brest = Brest()
    table = stream_to_table(
        iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326),
        brest,
        keep=brest.keep,
        label="Brest OpenData",
    )
    brest.report()
    table = validate_coordinates(table, city="Brest", city_code="FRBES")
    table = enforce_tree_schema(table, city="Brest", data_source="BREST_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()
